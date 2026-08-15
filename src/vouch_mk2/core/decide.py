"""``decide(Snapshot, Command) → Accepted(events) | Refused(violations)``.

Pure and table-driven: transition legality is looked up in the mk2 Policy IR, and
the gate obligations bound to a transition are evaluated by the registry below —
a transition and its gate are one code path. A command the tables do not license is
refused with a named violation; nothing is ever silently coerced.

``apply(Snapshot, events)`` is the pure fold that produces the next snapshot. The
only writer of durable state (Phase 2's store) persists exactly ``apply``'s result
under CAS; nothing else interprets events.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable, Mapping
from typing import assert_never

from vouch_mk2.core.commands import (
    AdvanceAnchor,
    AmendDeviation,
    BeginReverify,
    BindBundle,
    CloseDeviation,
    Command,
    CompleteRelease,
    FreezeAcceptance,
    MarkFailedUnrecovered,
    MarkDone,
    MarkReady,
    OpenDeviation,
    PrepareRelease,
    Publish,
    RecordFormalCertificate,
    RecordEvidence,
    RejectDeviation,
    ReleaseTransitionCommand,
    RetireRelease,
    Respecify,
    RollBack,
    SetReleaseCriteria,
    SetAnchorMode,
    SpecifyUnit,
    StartDeploy,
    StartVerification,
    StartWork,
    Supersede,
    UnitTransitionCommand,
)
from vouch_mk2.core.events import (
    AcceptanceFrozen,
    AnchorAdvanced,
    AnchorModeSet,
    BundleBound,
    DeploymentStarted,
    DeviationAmended,
    DeviationClosed,
    DeviationOpened,
    DeviationRejected,
    Event,
    EvidenceRecorded,
    FormalCertificateRecorded,
    GateCheck,
    GateRecord,
    ReleaseCriteriaSet,
    ReleaseFailedUnrecovered,
    ReleaseInvalidated,
    ReleasePrepared,
    ReleaseRetired,
    ReleaseRolledBack,
    ReleaseSucceeded,
    ReverificationStarted,
    UnitBlocked,
    UnitInvalidated,
    UnitMarkedDone,
    UnitMarkedReady,
    UnitPublished,
    UnitReleaseBound,
    UnitRespecified,
    UnitSpecified,
    UnitSuperseded,
    VerificationStarted,
    WorkStarted,
)
from vouch_mk2.core.model import (
    ClosureRecord,
    ClosureTriggerKind,
    Deviation,
    DeviationStatus,
    EvidenceHead,
    EvidenceKind,
    EvidenceOutcome,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Snapshot,
    Unit,
    UnitStatus,
    blocking_deviations,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Transition, Verdict, Violation

_UNIT_MACHINE = mk2.MK2.machine(mk2.UNIT_MACHINE)
_DEVIATION_MACHINE = mk2.MK2.machine(mk2.DEVIATION_MACHINE)
_RELEASE_MACHINE = mk2.MK2.machine(mk2.RELEASE_O_MACHINE)
_SATISFIED_DEPENDENCY_STATUSES = frozenset(
    mk2.MK2.state_set("unit.satisfied-dependency").states
)


def _index(machine_name: str) -> Mapping[tuple[str, str], Transition]:
    machine = mk2.MK2.machine(machine_name)
    return {(t.source, t.command): t for t in machine.command_transitions}


_UNIT_EDGES = _index(mk2.UNIT_MACHINE)
_DEVIATION_EDGES = _index(mk2.DEVIATION_MACHINE)
_RELEASE_EDGES = _index(mk2.RELEASE_O_MACHINE)


def _amendment_entry() -> tuple[str, frozenset[str]]:
    for entry in _UNIT_MACHINE.adjudication_entries:
        if entry.trigger == mk2.TRIGGER_AMENDMENT_IMPACT:
            return entry.target, frozenset(entry.sources) - frozenset(
                entry.excluded_sources
            )
    raise LookupError(
        f"mk2 unit machine declares no {mk2.TRIGGER_AMENDMENT_IMPACT!r} entry"
    )


_AMENDMENT_TARGET, _AMENDMENT_SOURCES = _amendment_entry()


def _release_amendment_entry() -> tuple[str, frozenset[str]]:
    for entry in _RELEASE_MACHINE.adjudication_entries:
        if entry.trigger == ClosureTriggerKind.AMENDMENT.value:
            return entry.target, frozenset(entry.sources) - frozenset(
                entry.excluded_sources
            )
    raise LookupError("mk2 release machine declares no amendment entry")


_RELEASE_AMENDMENT_TARGET, _RELEASE_AMENDMENT_SOURCES = _release_amendment_entry()


@dataclass(frozen=True, slots=True)
class Accepted:
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class Refused:
    violations: tuple[Violation, ...]


type Decision = Accepted | Refused


def _refuse(code: str, detail: str) -> Refused:
    return Refused(violations=(Violation(code=code, detail=detail),))


def _valid_digest(digest: str) -> bool:
    return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def _evidence_kind_outcome_coherent(
    kind: EvidenceKind, outcome: EvidenceOutcome
) -> bool:
    match kind:
        case EvidenceKind.RED:
            match outcome:
                case EvidenceOutcome.TESTS_FAILED:
                    return True
                case EvidenceOutcome.TESTS_PASSED:
                    return False
                case _:
                    assert_never(outcome)
        case EvidenceKind.GREEN | EvidenceKind.REFACTOR:
            match outcome:
                case EvidenceOutcome.TESTS_FAILED:
                    return False
                case EvidenceOutcome.TESTS_PASSED:
                    return True
                case _:
                    assert_never(outcome)
        case _:
            assert_never(kind)


# --- gate evaluation -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _GateContext:
    """What every Phase 1 gate can see. ``pack_version`` is the pack version the
    transition would record (the command's, for freeze; the unit's otherwise)."""

    snapshot: Snapshot
    unit: Unit
    pack_version: str


def _gate_pack_recorded(ctx: _GateContext) -> tuple[Violation, ...]:
    if ctx.pack_version:
        return ()
    return (
        Violation(
            code="gate.pack-version-missing",
            detail=f"unit {ctx.unit.name!r} has no recorded acceptance pack version",
        ),
    )


def _unknown_dependencies(
    snapshot: Snapshot, depends_on: tuple[str, ...]
) -> tuple[str, ...]:
    """The one dependencies-exist predicate — shared by specify and the DoR/DoD
    gates so the rule cannot drift between its call sites."""
    return tuple(dep for dep in depends_on if snapshot.unit(dep) is None)


def _unknown_dependency_violation(owner: str, missing: tuple[str, ...]) -> Violation:
    return Violation(
        code="gate.dependency-unknown",
        detail=f"unit {owner!r} depends on unknown units {missing!r}",
    )


def _gate_dependencies_exist(ctx: _GateContext) -> tuple[Violation, ...]:
    missing = _unknown_dependencies(ctx.snapshot, ctx.unit.depends_on)
    if not missing:
        return ()
    return (_unknown_dependency_violation(ctx.unit.name, missing),)


def _gate_dependencies_satisfied(ctx: _GateContext) -> tuple[Violation, ...]:
    missing = _unknown_dependencies(ctx.snapshot, ctx.unit.depends_on)
    unsatisfied: list[str] = []
    for dep in ctx.unit.depends_on:
        upstream = ctx.snapshot.unit(dep)
        if (
            upstream is not None
            and upstream.status.value not in _SATISFIED_DEPENDENCY_STATUSES
        ):
            unsatisfied.append(dep)
    violations: list[Violation] = []
    if missing:
        violations.append(_unknown_dependency_violation(ctx.unit.name, missing))
    if unsatisfied:
        violations.append(
            Violation(
                code="gate.dependency-unsatisfied",
                detail=(
                    f"unit {ctx.unit.name!r} has dependencies outside the "
                    f"satisfied statuses: {tuple(unsatisfied)!r}"
                ),
            )
        )
    return tuple(violations)


def _gate_no_blocking_deviations(ctx: _GateContext) -> tuple[Violation, ...]:
    blockers = blocking_deviations(ctx.snapshot, ctx.unit)
    if not blockers:
        return ()
    ids = tuple(d.deviation_id for d in blockers)
    return (
        Violation(
            code="gate.blocking-deviations",
            detail=f"unit {ctx.unit.name!r} is blocked on deviations {ids!r}",
        ),
    )


GATE_EVALUATORS: Mapping[str, Callable[[_GateContext], tuple[Violation, ...]]] = {
    mk2.GATE_DOR_PACK_RECORDED: _gate_pack_recorded,
    mk2.GATE_DOR_DEPENDENCIES_EXIST: _gate_dependencies_exist,
    mk2.GATE_DOR_NO_BLOCKING_DEVIATIONS: _gate_no_blocking_deviations,
    mk2.GATE_DOD_PACK_FROZEN: _gate_pack_recorded,
    mk2.GATE_DOD_DEPENDENCIES_SATISFIED: _gate_dependencies_satisfied,
    mk2.GATE_DOD_NO_BLOCKING_DEVIATIONS: _gate_no_blocking_deviations,
    mk2.GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS: _gate_no_blocking_deviations,
    mk2.GATE_REVERIFY_PACK_RECORDED: _gate_pack_recorded,
}


@dataclass(frozen=True, slots=True)
class _ReleaseGateContext:
    """Release-gate inputs, including a candidate release during preparation."""

    snapshot: Snapshot
    release: Release
    trigger: str = ""


def _gate_release_criteria(ctx: _ReleaseGateContext) -> tuple[Violation, ...]:
    if ctx.release.criteria is not None:
        return ()
    return (
        Violation(
            code="gate.release.criteria-missing",
            detail=f"release {ctx.release.release_id!r} has no launch criteria",
        ),
    )


def _gate_release_roster_resolves(
    ctx: _ReleaseGateContext,
) -> tuple[Violation, ...]:
    stale: list[str] = []
    for ref in ctx.release.includes:
        unit = ctx.snapshot.unit_ref(ref)
        if unit is None:
            stale.append(f"{ref.label()} (missing)")
    if not stale:
        return ()
    return (
        Violation(
            code="gate.release.roster-stale",
            detail=(
                f"release {ctx.release.release_id!r} has stale roster bindings "
                f"{tuple(stale)!r}"
            ),
        ),
    )


def _gate_release_completion_units(
    ctx: _ReleaseGateContext,
) -> tuple[Violation, ...]:
    offenders: list[str] = []
    for ref in ctx.release.includes:
        unit = ctx.snapshot.unit_ref(ref)
        # Binding failures belong exclusively to roster-resolves, so one defect
        # produces the one named violation operators need to repair.
        if unit is None:
            continue
        reasons: list[str] = []
        if unit.status is not UnitStatus.RELEASE_BOUND:
            reasons.append(f"status {unit.status.value!r}")
        if not unit.acceptance_pack_version:
            reasons.append("empty acceptance pack")
        blockers = blocking_deviations(ctx.snapshot, unit)
        if blockers:
            reasons.append(
                f"blocking deviations {tuple(d.deviation_id for d in blockers)!r}"
            )
        if reasons:
            offenders.append(f"{unit.name}: {', '.join(reasons)}")
    if not offenders:
        return ()
    return (
        Violation(
            code="gate.release.member-not-done",
            detail=(
                f"release {ctx.release.release_id!r} has members that cannot "
                f"support completion: {tuple(offenders)!r}"
            ),
        ),
    )


def _gate_release_closure_record(
    ctx: _ReleaseGateContext,
) -> tuple[Violation, ...]:
    if ctx.trigger:
        return ()
    return (
        Violation(
            code="gate.release.closure-trigger-required",
            detail=f"release {ctx.release.release_id!r} closure trigger is required",
        ),
    )


def _gate_release_roster_dependency_closed(
    ctx: _ReleaseGateContext,
) -> tuple[Violation, ...]:
    roster = {ref.name for ref in ctx.release.includes}
    missing: set[str] = set()
    for ref in ctx.release.includes:
        unit = ctx.snapshot.unit_ref(ref)
        if unit is not None:
            missing.update(dep for dep in unit.depends_on if dep not in roster)
    if not missing:
        return ()
    return (
        Violation(
            code="gate.release.roster-not-closed",
            detail=(
                f"release {ctx.release.release_id!r} omits dependencies "
                f"{tuple(sorted(missing))!r}"
            ),
        ),
    )


def _gate_release_roster_units_eligible(
    ctx: _ReleaseGateContext,
) -> tuple[Violation, ...]:
    eligible = {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}
    offenders: list[str] = []
    for ref in ctx.release.includes:
        unit = ctx.snapshot.unit_ref(ref)
        if unit is None:
            continue
        reasons: list[str] = []
        if unit.status not in eligible:
            reasons.append(f"status {unit.status.value!r}")
        if not unit.acceptance_pack_version:
            reasons.append("empty acceptance pack")
        blockers = blocking_deviations(ctx.snapshot, unit)
        if blockers:
            reasons.append(
                f"blocking deviations {tuple(d.deviation_id for d in blockers)!r}"
            )
        if reasons:
            offenders.append(f"{unit.name}: {', '.join(reasons)}")
    if not offenders:
        return ()
    return (
        Violation(
            code="gate.release.roster-ineligible",
            detail=(
                f"release {ctx.release.release_id!r} has ineligible members "
                f"{tuple(offenders)!r}"
            ),
        ),
    )


RELEASE_GATE_EVALUATORS: Mapping[
    str, Callable[[_ReleaseGateContext], tuple[Violation, ...]]
] = {
    mk2.GATE_RELEASE_CRITERIA_BEFORE_PROGRESSION: _gate_release_criteria,
    mk2.GATE_RELEASE_COMPLETION_UNITS_RECHECK: _gate_release_completion_units,
    mk2.GATE_RELEASE_COMPLETION_CRITERIA_RECORDED: _gate_release_criteria,
    mk2.GATE_RELEASE_COMPLETION_ROSTER_RESOLVES: _gate_release_roster_resolves,
    mk2.GATE_RELEASE_CLOSURE_RECORD_COMPLETE: _gate_release_closure_record,
    mk2.GATE_RELEASE_ROSTER_DEPENDENCY_CLOSED: (
        _gate_release_roster_dependency_closed
    ),
    mk2.GATE_RELEASE_ROSTER_UNITS_ELIGIBLE: _gate_release_roster_units_eligible,
}


def _check_gate_registry() -> None:
    """Fail at import, not mid-decision: an IR gate without an evaluator would
    otherwise surface as a KeyError escaping decide()'s Accepted|Refused contract."""
    bound = {
        gate
        for machine in mk2.MK2.machines
        for transition in machine.command_transitions
        for gate in transition.gates
    }
    release_bound = {gate for gate in bound if gate.startswith("gate.release.")}
    unit_bound = bound - release_bound
    missing_unit = unit_bound ^ set(GATE_EVALUATORS)
    missing_release = release_bound ^ set(RELEASE_GATE_EVALUATORS)
    if missing_unit or missing_release:
        raise LookupError(
            "IR gate/evaluator mismatch: "
            f"unit={sorted(missing_unit)}, release={sorted(missing_release)}"
        )


_check_gate_registry()


def _evaluate_gates(
    transition: Transition, ctx: _GateContext
) -> tuple[GateRecord, tuple[Violation, ...]]:
    checks: list[GateCheck] = []
    violations: list[Violation] = []
    for obligation_id in transition.gates:
        found = GATE_EVALUATORS[obligation_id](ctx)
        verdict = Verdict.PASS if not found else Verdict.FAIL
        checks.append(GateCheck(obligation_id=obligation_id, verdict=verdict))
        violations.extend(found)
    record = GateRecord(
        transition=f"{transition.source}->{transition.target}",
        checks=tuple(checks),
    )
    return record, tuple(violations)


def _evaluate_release_gates(
    transition: Transition, ctx: _ReleaseGateContext
) -> tuple[GateRecord, tuple[Violation, ...]]:
    checks: list[GateCheck] = []
    violations: list[Violation] = []
    for obligation_id in transition.gates:
        found = RELEASE_GATE_EVALUATORS[obligation_id](ctx)
        checks.append(
            GateCheck(
                obligation_id=obligation_id,
                verdict=Verdict.PASS if not found else Verdict.FAIL,
            )
        )
        violations.extend(found)
    return (
        GateRecord(
            transition=f"{transition.source}->{transition.target}",
            checks=tuple(checks),
        ),
        tuple(violations),
    )


# --- unit lifecycle ------------------------------------------------------------


def _decide_specify(snapshot: Snapshot, command: SpecifyUnit) -> Decision:
    if not command.name or not command.version:
        return _refuse("unit.identity-required", "unit name and version are required")
    if snapshot.unit(command.name) is not None:
        return _refuse("unit.exists", f"unit {command.name!r} already exists")
    missing = _unknown_dependencies(snapshot, command.depends_on)
    if missing:
        return Refused(
            violations=(_unknown_dependency_violation(command.name, missing),)
        )
    return Accepted(
        events=(
            UnitSpecified(
                name=command.name,
                version=command.version,
                depends_on=command.depends_on,
            ),
        )
    )


def _transition_event(
    command: UnitTransitionCommand, unit: Unit, record: GateRecord
) -> Event:
    match command:
        case FreezeAcceptance(pack_version=pack_version):
            return AcceptanceFrozen(
                name=unit.name, pack_version=pack_version, gate=record
            )
        case MarkReady():
            return UnitMarkedReady(name=unit.name)
        case StartWork():
            return WorkStarted(name=unit.name)
        case MarkDone():
            return UnitMarkedDone(name=unit.name)
        case Publish():
            return UnitPublished(name=unit.name, gate=record)
        case Supersede():
            return UnitSuperseded(name=unit.name)
        case Respecify():
            return UnitRespecified(name=unit.name, gate=record)
        case BeginReverify():
            return ReverificationStarted(name=unit.name, gate=record)
        case _:
            assert_never(command)


def _decide_unit_transition(
    snapshot: Snapshot, command: UnitTransitionCommand
) -> Decision:
    unit = snapshot.unit(command.name)
    if unit is None:
        return _refuse("unit.unknown", f"unit {command.name!r} does not exist")
    transition = _UNIT_EDGES.get((unit.status.value, command.ir_command))
    if transition is None:
        return _refuse(
            "transition.illegal",
            f"{command.ir_command!r} is not a legal unit command from "
            f"{unit.status.value!r}",
        )
    pack_version = (
        command.pack_version
        if isinstance(command, FreezeAcceptance)
        else unit.acceptance_pack_version
    )
    record, violations = _evaluate_gates(
        transition,
        _GateContext(snapshot=snapshot, unit=unit, pack_version=pack_version),
    )
    if violations:
        return Refused(violations=violations)
    return Accepted(events=(_transition_event(command, unit, record),))


# --- operational releases -----------------------------------------------------


def _prepare_transition() -> Transition:
    for transition in _UNIT_MACHINE.command_transitions:
        if (
            transition.source == UnitStatus.PUBLISHED.value
            and transition.command == mk2.CMD_PREPARE_RELEASE
        ):
            return transition
    raise LookupError("mk2 unit machine declares no prepare-release binding edge")


_PREPARE_TRANSITION = _prepare_transition()


def _candidate_release(
    snapshot: Snapshot, release_id: str, includes: tuple[str, ...]
) -> Release:
    refs = tuple(
        sorted(
            (
                unit.ref()
                for name in includes
                if (unit := snapshot.unit(name)) is not None
            ),
            key=lambda ref: ref.name,
        )
    )
    return Release(
        release_id=release_id,
        archetype=ReleaseArchetype.OPERATIONAL,
        state=ReleaseState.PREPARED,
        includes=refs,
        criteria=None,
        closure=None,
    )


def _decide_prepare_release(snapshot: Snapshot, command: PrepareRelease) -> Decision:
    if not command.release_id:
        return _refuse("release.identity-required", "release id is required")
    if snapshot.release(command.release_id) is not None:
        return _refuse(
            "release.exists", f"release {command.release_id!r} already exists"
        )
    if not command.includes:
        # An empty roster would let a completion claim succeed with every
        # roster gate passing vacuously — the vacuous-coverage class.
        return _refuse("release.roster-empty", "a release roster must be non-empty")
    duplicates = tuple(
        sorted(
            {name for name in command.includes if command.includes.count(name) > 1}
        )
    )
    if duplicates:
        return _refuse(
            "release.roster-duplicate",
            f"roster binds units more than once: {duplicates!r}",
        )
    unknown = tuple(
        sorted({name for name in command.includes if snapshot.unit(name) is None})
    )
    if unknown:
        return _refuse("unit.unknown", f"unknown release roster units {unknown!r}")
    release = _candidate_release(snapshot, command.release_id, command.includes)
    _record, violations = _evaluate_release_gates(
        _PREPARE_TRANSITION,
        _ReleaseGateContext(snapshot=snapshot, release=release),
    )
    if violations:
        return Refused(violations=violations)
    events: list[Event] = [
        ReleasePrepared(
            release_id=release.release_id,
            archetype=release.archetype,
            includes=release.includes,
        )
    ]
    events.extend(
        UnitReleaseBound(name=ref.name)
        for ref in release.includes
        if (unit := snapshot.unit_ref(ref)) is not None
        and unit.status is UnitStatus.PUBLISHED
    )
    return Accepted(events=tuple(events))


def _decide_set_release_criteria(
    snapshot: Snapshot, command: SetReleaseCriteria
) -> Decision:
    release = snapshot.release(command.release_id)
    if release is None:
        return _refuse(
            "release.unknown", f"release {command.release_id!r} does not exist"
        )
    if _RELEASE_EDGES.get((release.state.value, command.ir_command)) is None:
        return _refuse(
            "transition.illegal",
            f"set-criteria is not legal from {release.state.value!r}",
        )
    if (
        not command.criteria.go_no_go_authority
        or not command.criteria.launch_criteria
    ):
        return _refuse(
            "release.criteria-incomplete",
            "launch criteria and go/no-go authority are required",
        )
    return Accepted(
        events=(
            ReleaseCriteriaSet(
                release_id=release.release_id, criteria=command.criteria
            ),
        )
    )


def _decide_bind_bundle(snapshot: Snapshot, command: BindBundle) -> Decision:
    release = snapshot.release(command.release_id)
    if release is None:
        return _refuse(
            "release.unknown", f"release {command.release_id!r} does not exist"
        )
    if _RELEASE_EDGES.get((release.state.value, command.ir_command)) is None:
        return _refuse(
            "transition.illegal",
            f"bind-bundle is not legal from {release.state.value!r}",
        )
    if not _valid_digest(command.inventory_digest):
        return _refuse(
            "release.bundle-digest-invalid",
            "bundle inventory digest must be 64 lowercase hexadecimal characters",
        )
    return Accepted(
        events=(
            BundleBound(
                release_id=release.release_id,
                inventory_digest=command.inventory_digest,
            ),
        )
    )


def _decide_record_evidence(
    snapshot: Snapshot, command: RecordEvidence
) -> Decision:
    event = command.event
    unit = snapshot.unit_ref(event.unit)
    # Evidence heads are keyed by unit NAME (one chain per unit), so evidence may
    # bind only the CURRENT record: a superseded version is inert history (D-9)
    # and recording against it would fold a stale version into the live head.
    if unit is None or unit.status is UnitStatus.SUPERSEDED:
        current_unit = snapshot.unit(event.unit.name)
        current = (
            "missing"
            if current_unit is None or current_unit.status is UnitStatus.SUPERSEDED
            else current_unit.ref().label()
        )
        return _refuse(
            "evidence.binding-stale",
            f"evidence binds {event.unit.label()!r}; current unit is {current!r}",
        )
    if not _valid_digest(command.event_digest):
        return _refuse(
            "evidence.digest-invalid",
            "evidence digest must be 64 lowercase hexadecimal characters",
        )
    if not _evidence_kind_outcome_coherent(event.kind, event.outcome):
        return _refuse(
            "evidence.kind-outcome-mismatch",
            f"kind {event.kind.value!r} contradicts outcome {event.outcome.value!r}",
        )
    head = snapshot.evidence_head(event.unit.name)
    expected_parent = "GENESIS" if head is None else head.head
    expected_sequence = 1 if head is None else head.head_sequence + 1
    if event.parent != expected_parent or event.sequence != expected_sequence:
        return _refuse(
            "evidence.chain-mismatch",
            f"expected parent {expected_parent!r}, sequence {expected_sequence}; "
            f"got parent {event.parent!r}, sequence {event.sequence}",
        )
    return Accepted(events=(EvidenceRecorded(event, command.event_digest),))


def _decide_advance_anchor(snapshot: Snapshot, command: AdvanceAnchor) -> Decision:
    head = snapshot.evidence_head(command.unit_name)
    if head is None:
        return _refuse(
            "evidence.no-chain",
            f"unit {command.unit_name!r} has no evidence chain",
        )
    if head.anchored == head.head:
        return _refuse(
            "evidence.anchor-current",
            f"unit {command.unit_name!r} anchor is already current",
        )
    return Accepted(events=(AnchorAdvanced(command.unit_name),))


def _decide_set_anchor_mode(command: SetAnchorMode) -> Decision:
    return Accepted(events=(AnchorModeSet(command.mode),))


def _decide_record_formal_certificate(
    command: RecordFormalCertificate,
) -> Decision:
    if not _valid_digest(command.certificate_digest):
        return _refuse(
            "formal.digest-invalid",
            "formal certificate digest must be 64 lowercase hexadecimal characters",
        )
    return Accepted(events=(FormalCertificateRecorded(command.certificate_digest),))


def _roster_snapshot(
    snapshot: Snapshot, release: Release
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                ref.name,
                unit.status.value if (unit := snapshot.unit_ref(ref)) else "missing",
            )
            for ref in release.includes
        )
    )


def _closure_command_fields(
    command: RollBack | MarkFailedUnrecovered | RetireRelease,
) -> tuple[str, tuple[str, ...]]:
    match command:
        case RollBack(trigger=trigger, observations=observations):
            return trigger, observations
        case MarkFailedUnrecovered(trigger=trigger, observations=observations):
            return trigger, observations
        case RetireRelease(trigger=trigger, observations=observations):
            return trigger, observations
        case _:
            assert_never(command)


def _release_transition_event(
    command: ReleaseTransitionCommand,
    release: Release,
    record: GateRecord,
    closure: ClosureRecord | None,
) -> Event:
    match command:
        case StartDeploy():
            return DeploymentStarted(release_id=release.release_id, gate=record)
        case StartVerification():
            return VerificationStarted(release_id=release.release_id)
        case CompleteRelease():
            return ReleaseSucceeded(release_id=release.release_id, gate=record)
        case RollBack():
            if closure is None:
                raise AssertionError("roll-back requires a closure record")
            return ReleaseRolledBack(
                release_id=release.release_id, closure=closure, gate=record
            )
        case MarkFailedUnrecovered():
            if closure is None:
                raise AssertionError("mark-failed-unrecovered requires a closure record")
            return ReleaseFailedUnrecovered(
                release_id=release.release_id, closure=closure, gate=record
            )
        case RetireRelease():
            if closure is None:
                raise AssertionError("retire requires a closure record")
            return ReleaseRetired(
                release_id=release.release_id, closure=closure, gate=record
            )
        case _:
            assert_never(command)


def _decide_release_transition(
    snapshot: Snapshot, command: ReleaseTransitionCommand
) -> Decision:
    release = snapshot.release(command.release_id)
    if release is None:
        return _refuse(
            "release.unknown", f"release {command.release_id!r} does not exist"
        )
    transition = _RELEASE_EDGES.get((release.state.value, command.ir_command))
    if transition is None:
        return _refuse(
            "transition.illegal",
            f"{command.ir_command!r} is not a legal release command from "
            f"{release.state.value!r}",
        )
    trigger = ""
    observations: tuple[str, ...] = ()
    if isinstance(command, RollBack | MarkFailedUnrecovered | RetireRelease):
        trigger, observations = _closure_command_fields(command)
    record, violations = _evaluate_release_gates(
        transition,
        _ReleaseGateContext(
            snapshot=snapshot, release=release, trigger=trigger
        ),
    )
    if violations:
        return Refused(violations=violations)
    # I3 inversion: protective closure legality never inspects roster health.
    # A missing, blocked, or invalidated roster member is evidence captured in
    # the snapshot below, never a reason to trap the release in a contaminated state.
    closure = (
        ClosureRecord(
            trigger=trigger,
            trigger_kind=ClosureTriggerKind.OPERATOR,
            roster_snapshot=_roster_snapshot(snapshot, release),
            no_completion_claim=True,
            observations=observations,
        )
        if isinstance(command, RollBack | MarkFailedUnrecovered | RetireRelease)
        else None
    )
    return Accepted(
        events=(_release_transition_event(command, release, record, closure),)
    )


# --- deviations ----------------------------------------------------------------


def _decide_open_deviation(snapshot: Snapshot, command: OpenDeviation) -> Decision:
    if not command.deviation_id:
        return _refuse("deviation.identity-required", "deviation id is required")
    if snapshot.deviation(command.deviation_id) is not None:
        return _refuse(
            "deviation.exists", f"deviation {command.deviation_id!r} already exists"
        )
    unit = snapshot.unit_ref(command.unit)
    if unit is None:
        current = snapshot.unit(command.unit.name)
        if current is None:
            return _refuse("unit.unknown", f"unit {command.unit.name!r} does not exist")
        return _refuse(
            "deviation.binding-stale",
            f"deviation binds {command.unit.label()!r} but the unit is at "
            f"version {current.version!r}",
        )
    transition = _UNIT_EDGES.get((unit.status.value, command.ir_command))
    if transition is None:
        return _refuse(
            "transition.illegal",
            f"a deviation cannot be opened against a unit in "
            f"{unit.status.value!r}",
        )
    _record, violations = _evaluate_gates(
        transition,
        _GateContext(
            snapshot=snapshot,
            unit=unit,
            pack_version=unit.acceptance_pack_version,
        ),
    )
    if violations:
        return Refused(violations=violations)
    opened = DeviationOpened(
        deviation_id=command.deviation_id,
        deviation_type=command.deviation_type,
        unit=command.unit,
    )
    if unit.status is UnitStatus.BLOCKED:
        # The blocked->blocked self-loop: the deviation accumulates in the
        # derived blocker set without a status change, so no UnitBlocked event.
        return Accepted(events=(opened,))
    return Accepted(events=(opened, UnitBlocked(name=unit.name)))


def _deviation_edge_or_refusal(
    snapshot: Snapshot, deviation_id: str, ir_command: str
) -> Deviation | Refused:
    deviation = snapshot.deviation(deviation_id)
    if deviation is None:
        return _refuse("deviation.unknown", f"deviation {deviation_id!r} does not exist")
    if _DEVIATION_EDGES.get((deviation.status.value, ir_command)) is None:
        return _refuse(
            "transition.illegal",
            f"{ir_command!r} is not legal for a deviation in "
            f"{deviation.status.value!r}",
        )
    return deviation


def _amendment_impact(snapshot: Snapshot, origin: str) -> tuple[str, ...]:
    """The reflexive transitive consumer closure of ``origin``, in name order.

    Traversal crosses every declared dependency edge of ACTIVE records
    regardless of their status; which members actually invalidate is decided
    by the caller (D-3). Superseded records are history and inert (D-9): their
    dependency edges no longer connect anything, so an amendment upstream of a
    superseded version must not reach the CURRENT version through it."""
    consumers: dict[str, list[str]] = {}
    for unit in snapshot.units:
        if unit.status is UnitStatus.SUPERSEDED:
            continue
        for dep in unit.depends_on:
            consumers.setdefault(dep, []).append(unit.name)
    reached = {origin}
    frontier = [origin]
    while frontier:
        current = frontier.pop()
        for consumer in consumers.get(current, ()):
            if consumer not in reached:
                reached.add(consumer)
                frontier.append(consumer)
    return tuple(sorted(reached))


def _decide_amend(snapshot: Snapshot, command: AmendDeviation) -> Decision:
    found = _deviation_edge_or_refusal(
        snapshot, command.deviation_id, command.ir_command
    )
    if isinstance(found, Refused):
        return found
    if not command.amendment_version:
        return _refuse(
            "deviation.amendment-version-required",
            "adjudicating amend requires the amendment version",
        )
    impacted = _amendment_impact(snapshot, found.unit.name)
    invalidated: list[str] = []
    for name in impacted:
        unit = snapshot.unit(name)
        if unit is not None and unit.status.value in _AMENDMENT_SOURCES:
            invalidated.append(name)
    affected_releases = tuple(
        sorted(
            (
                release
                for release in snapshot.releases
                if release.state.value in _RELEASE_AMENDMENT_SOURCES
                and any(ref.name in impacted for ref in release.includes)
            ),
            key=lambda release: release.release_id,
        )
    )
    # Unit and release impact are separate typed fields — 0.6's "release:"
    # string-prefix convention is an ambiguous encoding (a unit may legally be
    # named "release:r1") and dies here.
    events: list[Event] = [
        DeviationAmended(
            deviation_id=command.deviation_id,
            amendment_version=command.amendment_version,
            impact_set=tuple(invalidated),
            release_impact_set=tuple(
                release.release_id for release in affected_releases
            ),
        )
    ]
    events.extend(
        UnitInvalidated(name=name, trigger_deviation_id=command.deviation_id)
        for name in invalidated
    )
    events.extend(
        ReleaseInvalidated(
            release_id=release.release_id,
            closure=(
                ClosureRecord(
                    trigger=f"amendment:{command.deviation_id}",
                    trigger_kind=ClosureTriggerKind.AMENDMENT,
                    roster_snapshot=_roster_snapshot(snapshot, release),
                    no_completion_claim=True,
                    observations=(),
                )
                if release.closure is None
                else None
            ),
        )
        for release in affected_releases
    )
    return Accepted(events=tuple(events))


def _decide_reject(snapshot: Snapshot, command: RejectDeviation) -> Decision:
    found = _deviation_edge_or_refusal(
        snapshot, command.deviation_id, command.ir_command
    )
    if isinstance(found, Refused):
        return found
    return Accepted(events=(DeviationRejected(deviation_id=command.deviation_id),))


def _decide_close(snapshot: Snapshot, command: CloseDeviation) -> Decision:
    found = _deviation_edge_or_refusal(
        snapshot, command.deviation_id, command.ir_command
    )
    if isinstance(found, Refused):
        return found
    # D-3: close is only close. Leaving `blocked` is an explicit, gated command
    # (respecify / begin-reverify), never a side effect here.
    return Accepted(events=(DeviationClosed(deviation_id=command.deviation_id),))


def decide(snapshot: Snapshot, command: Command) -> Decision:
    match command:
        case SpecifyUnit():
            return _decide_specify(snapshot, command)
        case PrepareRelease():
            return _decide_prepare_release(snapshot, command)
        case SetReleaseCriteria():
            return _decide_set_release_criteria(snapshot, command)
        case BindBundle():
            return _decide_bind_bundle(snapshot, command)
        case RecordEvidence():
            return _decide_record_evidence(snapshot, command)
        case AdvanceAnchor():
            return _decide_advance_anchor(snapshot, command)
        case SetAnchorMode():
            return _decide_set_anchor_mode(command)
        case RecordFormalCertificate():
            return _decide_record_formal_certificate(command)
        case OpenDeviation():
            return _decide_open_deviation(snapshot, command)
        case AmendDeviation():
            return _decide_amend(snapshot, command)
        case RejectDeviation():
            return _decide_reject(snapshot, command)
        case CloseDeviation():
            return _decide_close(snapshot, command)
        case (
            StartDeploy()
            | StartVerification()
            | CompleteRelease()
            | RollBack()
            | MarkFailedUnrecovered()
            | RetireRelease()
        ):
            return _decide_release_transition(snapshot, command)
        case (
            FreezeAcceptance()
            | MarkReady()
            | StartWork()
            | MarkDone()
            | Publish()
            | Supersede()
            | Respecify()
            | BeginReverify()
        ):
            # Plan §1 non-goal: the exposed supersession policy retains releases;
            # superseding a unit therefore changes no release state in Phase 4.
            return _decide_unit_transition(snapshot, command)
        case _:
            assert_never(command)


# --- apply ---------------------------------------------------------------------


def _with_unit(units: tuple[Unit, ...], name: str, changed: Unit) -> tuple[Unit, ...]:
    del name
    return tuple(changed if unit.ref() == changed.ref() else unit for unit in units)


def _with_deviation(
    deviations: tuple[Deviation, ...], deviation_id: str, changed: Deviation
) -> tuple[Deviation, ...]:
    return tuple(
        changed if deviation.deviation_id == deviation_id else deviation
        for deviation in deviations
    )


def _with_release(
    releases: tuple[Release, ...], release_id: str, changed: Release
) -> tuple[Release, ...]:
    return tuple(
        changed if release.release_id == release_id else release
        for release in releases
    )


def _with_evidence_head(
    heads: tuple[EvidenceHead, ...], changed: EvidenceHead
) -> tuple[EvidenceHead, ...]:
    retained = tuple(head for head in heads if head.unit_name != changed.unit_name)
    return tuple(sorted(retained + (changed,), key=lambda head: head.unit_name))


def _apply_one(snapshot: Snapshot, event: Event) -> Snapshot:
    units = snapshot.units
    deviations = snapshot.deviations
    releases = snapshot.releases
    evidence_heads = snapshot.evidence_heads
    anchor_mode = snapshot.anchor_mode
    formal_certificate_digest = snapshot.formal_certificate_digest
    match event:
        case UnitSpecified(name=name, version=version, depends_on=depends_on):
            units = units + (
                Unit(
                    name=name,
                    version=version,
                    status=UnitStatus(_UNIT_MACHINE.initial),
                    depends_on=depends_on,
                ),
            )
        case AcceptanceFrozen(name=name, pack_version=pack_version):
            unit = _existing(snapshot, name)
            units = _with_unit(
                units,
                name,
                replace(
                    unit,
                    status=UnitStatus.ACCEPTANCE_FROZEN,
                    acceptance_pack_version=pack_version,
                ),
            )
        case UnitMarkedReady(name=name):
            units = _with_status(snapshot, name, UnitStatus.READY)
        case WorkStarted(name=name):
            units = _with_status(snapshot, name, UnitStatus.IN_PROGRESS)
        case UnitMarkedDone(name=name):
            units = _with_status(snapshot, name, UnitStatus.DONE_PENDING_ACCEPTANCE)
        case UnitPublished(name=name):
            units = _with_status(snapshot, name, UnitStatus.PUBLISHED)
        case UnitSuperseded(name=name):
            units = _with_status(snapshot, name, UnitStatus.SUPERSEDED)
        case UnitReleaseBound(name=name):
            units = _with_status(snapshot, name, UnitStatus.RELEASE_BOUND)
        case UnitRespecified(name=name):
            unit = _existing(snapshot, name)
            units = _with_unit(
                units,
                name,
                replace(
                    unit, status=UnitStatus.SPECIFIED, acceptance_pack_version=""
                ),
            )
        case ReverificationStarted(name=name):
            units = _with_status(snapshot, name, UnitStatus.RE_VERIFYING)
        case UnitBlocked(name=name):
            units = _with_status(snapshot, name, UnitStatus.BLOCKED)
        case UnitInvalidated(name=name):
            units = _with_status(snapshot, name, UnitStatus(_AMENDMENT_TARGET))
        case DeviationOpened(
            deviation_id=deviation_id, deviation_type=deviation_type, unit=ref
        ):
            deviations = deviations + (
                Deviation(
                    deviation_id=deviation_id,
                    deviation_type=deviation_type,
                    unit=ref,
                    status=DeviationStatus.OPEN,
                ),
            )
        case DeviationAmended(
            deviation_id=deviation_id,
            amendment_version=amendment_version,
            impact_set=impact_set,
            release_impact_set=release_impact_set,
        ):
            deviation = _existing_deviation(snapshot, deviation_id)
            deviations = _with_deviation(
                deviations,
                deviation_id,
                replace(
                    deviation,
                    status=DeviationStatus.AMENDED,
                    amendment_version=amendment_version,
                    impact_set=impact_set,
                    release_impact_set=release_impact_set,
                ),
            )
        case DeviationRejected(deviation_id=deviation_id):
            deviation = _existing_deviation(snapshot, deviation_id)
            deviations = _with_deviation(
                deviations,
                deviation_id,
                replace(deviation, status=DeviationStatus.REJECTED),
            )
        case DeviationClosed(deviation_id=deviation_id):
            deviation = _existing_deviation(snapshot, deviation_id)
            deviations = _with_deviation(
                deviations,
                deviation_id,
                replace(deviation, status=DeviationStatus.CLOSED),
            )
        case ReleasePrepared(
            release_id=release_id, archetype=archetype, includes=includes
        ):
            releases = releases + (
                Release(
                    release_id=release_id,
                    archetype=archetype,
                    state=ReleaseState(_RELEASE_MACHINE.initial),
                    includes=includes,
                    criteria=None,
                    closure=None,
                ),
            )
        case ReleaseCriteriaSet(release_id=release_id, criteria=criteria):
            release = _existing_release(snapshot, release_id)
            releases = _with_release(
                releases, release_id, replace(release, criteria=criteria)
            )
        case BundleBound(release_id=release_id, inventory_digest=inventory_digest):
            release = _existing_release(snapshot, release_id)
            releases = _with_release(
                releases,
                release_id,
                replace(release, bundle_inventory_digest=inventory_digest),
            )
        case EvidenceRecorded(event=recorded, event_digest=event_digest):
            prior = snapshot.evidence_head(recorded.unit.name)
            changed = EvidenceHead(
                unit_name=recorded.unit.name,
                head=event_digest,
                head_sequence=recorded.sequence,
                anchored="GENESIS" if prior is None else prior.anchored,
            )
            evidence_heads = _with_evidence_head(evidence_heads, changed)
        case AnchorAdvanced(unit_name=unit_name):
            head = snapshot.evidence_head(unit_name)
            if head is None:
                raise LookupError(f"event references unknown evidence head {unit_name!r}")
            # Promotion-only by construction: the fold derives the target from
            # the head itself (D-4).
            evidence_heads = _with_evidence_head(
                evidence_heads, replace(head, anchored=head.head)
            )
        case AnchorModeSet(mode=mode):
            anchor_mode = mode
        case FormalCertificateRecorded(certificate_digest=certificate_digest):
            formal_certificate_digest = certificate_digest
        case DeploymentStarted(release_id=release_id):
            releases = _with_release_status(
                snapshot, release_id, ReleaseState.DEPLOYING
            )
        case VerificationStarted(release_id=release_id):
            releases = _with_release_status(
                snapshot, release_id, ReleaseState.VERIFYING
            )
        case ReleaseSucceeded(release_id=release_id):
            releases = _with_release_status(
                snapshot, release_id, ReleaseState.SUCCEEDED
            )
        case ReleaseRolledBack(release_id=release_id, closure=closure):
            release = _existing_release(snapshot, release_id)
            releases = _with_release(
                releases,
                release_id,
                replace(
                    release, state=ReleaseState.ROLLED_BACK, closure=closure
                ),
            )
        case ReleaseFailedUnrecovered(release_id=release_id, closure=closure):
            release = _existing_release(snapshot, release_id)
            releases = _with_release(
                releases,
                release_id,
                replace(
                    release,
                    state=ReleaseState.FAILED_UNRECOVERED,
                    closure=closure,
                ),
            )
        case ReleaseRetired(release_id=release_id, closure=closure):
            release = _existing_release(snapshot, release_id)
            releases = _with_release(
                releases,
                release_id,
                replace(release, state=ReleaseState.RETIRED, closure=closure),
            )
        case ReleaseInvalidated(release_id=release_id, closure=closure):
            release = _existing_release(snapshot, release_id)
            releases = _with_release(
                releases,
                release_id,
                replace(
                    release,
                    state=ReleaseState(_RELEASE_AMENDMENT_TARGET),
                    closure=release.closure if closure is None else closure,
                ),
            )
        case _:
            assert_never(event)
    return Snapshot(
        revision=snapshot.revision,
        units=units,
        deviations=deviations,
        releases=releases,
        evidence_heads=evidence_heads,
        anchor_mode=anchor_mode,
        formal_certificate_digest=formal_certificate_digest,
    )


def _existing(snapshot: Snapshot, name: str) -> Unit:
    unit = snapshot.unit(name)
    if unit is None:
        raise LookupError(f"event references unknown unit {name!r}")
    return unit


def _existing_deviation(snapshot: Snapshot, deviation_id: str) -> Deviation:
    deviation = snapshot.deviation(deviation_id)
    if deviation is None:
        raise LookupError(f"event references unknown deviation {deviation_id!r}")
    return deviation


def _existing_release(snapshot: Snapshot, release_id: str) -> Release:
    release = snapshot.release(release_id)
    if release is None:
        raise LookupError(f"event references unknown release {release_id!r}")
    return release


def _with_status(snapshot: Snapshot, name: str, status: UnitStatus) -> tuple[Unit, ...]:
    unit = _existing(snapshot, name)
    return _with_unit(snapshot.units, name, replace(unit, status=status))


def _with_release_status(
    snapshot: Snapshot, release_id: str, state: ReleaseState
) -> tuple[Release, ...]:
    release = _existing_release(snapshot, release_id)
    return _with_release(
        snapshot.releases, release_id, replace(release, state=state)
    )


def apply(snapshot: Snapshot, events: tuple[Event, ...]) -> Snapshot:
    """Fold accepted events into the next snapshot (revision + 1)."""
    current = snapshot
    for event in events:
        current = _apply_one(current, event)
    return Snapshot(
        revision=snapshot.revision + 1,
        units=current.units,
        deviations=current.deviations,
        releases=current.releases,
        evidence_heads=current.evidence_heads,
        anchor_mode=current.anchor_mode,
        formal_certificate_digest=current.formal_certificate_digest,
    )
