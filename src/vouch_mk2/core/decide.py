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
    BindPack,
    RebindPack,
    BindBundle,
    ClearOpenInCi,
    CloseDeviation,
    Command,
    CompleteRelease,
    FreezeAcceptance,
    MarkFailedUnrecovered,
    MarkDone,
    MarkOpenInCi,
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
    SetCiPolicy,
    SetTier,
    SpecifyUnit,
    StartDeploy,
    StartVerification,
    StartWork,
    Supersede,
    UnitTransitionCommand,
)
from vouch_mk2.core.events import (
    AcceptanceFrozen,
    AcceptancePackBound,
    AcceptancePackRebound,
    AnchorAdvanced,
    AnchorModeSet,
    BundleBound,
    CiPolicySet,
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
    OpenInCiCleared,
    OpenInCiMarked,
    PreLogHistoryRecorded,
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
    TierSet,
)
from vouch_mk2.core.invariants import (
    current_pack_evidence_is_green,
    evidence_kind_outcome_coherent,
    valid_command_line,
    valid_identifier,
    valid_unit_identifier,
)
from vouch_mk2.core.evidence import fold_evidence_pack_summaries
from vouch_mk2.core.model import (
    ClosureRecord,
    ClosureTriggerKind,
    Deviation,
    DeviationStatus,
    EvidenceHead,
    EvidenceKind,
    CiException,
    PackRebound,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Snapshot,
    Tier,
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
_CI_TERMINAL_UNIT_STATUSES = frozenset(
    {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND, UnitStatus.SUPERSEDED}
)


def _index(machine_name: str) -> Mapping[tuple[str, str], Transition]:
    machine = mk2.MK2.machine(machine_name)
    indexed = {(t.source, t.command): t for t in machine.command_transitions}
    assert len(indexed) == len(machine.command_transitions)
    return indexed


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


def _tier_evidence_checks(snapshot: Snapshot) -> frozenset[str]:
    requirement = next(
        requirement
        for requirement in mk2.MK2.tier_requirements
        if requirement.control_id == mk2.TIER_EVIDENCE_CONTROL
    )
    return frozenset(requirement.cell(snapshot.tier).mechanical_checks)


def _gate_tier_evidence(ctx: _GateContext) -> tuple[Violation, ...]:
    checks = _tier_evidence_checks(ctx.snapshot)
    reasons: list[str] = []
    head = ctx.snapshot.evidence_head(ctx.unit.name)
    if mk2.CHECK_TIER_EVIDENCE_REQUIRED in checks and head is None:
        reasons.append("an evidence head is required")
    if mk2.CHECK_TIER_HIGH_ATTESTATION in checks:
        if head is None:
            reasons.append("an anchored evidence head is required")
        elif head.anchored == "GENESIS":
            reasons.append("the evidence head remains at the GENESIS anchor")
        if not ctx.snapshot.formal_certificate_digest:
            reasons.append("a formal certificate is required")
    if not reasons:
        return ()
    return (
        Violation(
            code=mk2.GATE_DOD_TIER_EVIDENCE,
            detail=(
                f"{ctx.snapshot.tier.value} publication of unit "
                f"{ctx.unit.name!r} refused: {'; '.join(reasons)}"
            ),
        ),
    )


def _gate_current_pack_green(ctx: _GateContext) -> tuple[Violation, ...]:
    if mk2.GATE_DOD_CURRENT_PACK_GREEN not in _tier_evidence_checks(ctx.snapshot):
        return ()
    if not ctx.unit.acceptance_test_set_digest:
        return (
            Violation(
                code=mk2.GATE_DOD_CURRENT_PACK_GREEN,
                detail=(
                    f"{ctx.snapshot.tier.value} publication of unit "
                    f"{ctx.unit.ref().label()!r} refused: the current acceptance "
                    "pack is label-only, so there is nothing to bind evidence to "
                    "(no test-set digest)"
                ),
            ),
        )
    if current_pack_evidence_is_green(ctx.snapshot, ctx.unit):
        return ()
    return (
        Violation(
            code=mk2.GATE_DOD_CURRENT_PACK_GREEN,
            detail=(
                f"{ctx.snapshot.tier.value} publication of unit "
                f"{ctx.unit.ref().label()!r} requires RED followed by GREEN "
                "for the current acceptance pack, with no later RED"
            ),
        ),
    )


GATE_EVALUATORS: Mapping[str, Callable[[_GateContext], tuple[Violation, ...]]] = {
    mk2.GATE_DOR_PACK_RECORDED: _gate_pack_recorded,
    mk2.GATE_DOR_DEPENDENCIES_EXIST: _gate_dependencies_exist,
    mk2.GATE_DOR_NO_BLOCKING_DEVIATIONS: _gate_no_blocking_deviations,
    mk2.GATE_DOD_PACK_FROZEN: _gate_pack_recorded,
    mk2.GATE_DOD_DEPENDENCIES_SATISFIED: _gate_dependencies_satisfied,
    mk2.GATE_DOD_NO_BLOCKING_DEVIATIONS: _gate_no_blocking_deviations,
    mk2.GATE_DOD_TIER_EVIDENCE: _gate_tier_evidence,
    mk2.GATE_DOD_CURRENT_PACK_GREEN: _gate_current_pack_green,
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
    if not ctx.release.includes:
        return (
            Violation(
                code="gate.release.roster-empty",
                detail=f"release {ctx.release.release_id!r} has an empty roster",
            ),
        )
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
    if not ctx.release.includes:
        return (
            Violation(
                code="gate.release.roster-empty",
                detail=f"release {ctx.release.release_id!r} has an empty roster",
            ),
        )
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
    if not valid_unit_identifier(command.name) or not valid_unit_identifier(
        command.version
    ):
        return _refuse(
            "identifier.invalid",
            "unit name and version must be printable identifiers of at most 200 "
            "characters without surrounding whitespace or '@'",
        )
    same_name = tuple(unit for unit in snapshot.units if unit.name == command.name)
    if any(
        unit.status is not UnitStatus.SUPERSEDED
        or unit.version == command.version
        for unit in same_name
    ):
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
        case FreezeAcceptance(
            pack_version=pack_version,
            pack_digest=pack_digest,
            test_set_digest=test_set_digest,
        ):
            return AcceptanceFrozen(
                name=unit.name,
                pack_version=pack_version,
                pack_digest=pack_digest,
                gate=record,
                test_set_digest=test_set_digest or pack_digest,
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
    if isinstance(command, FreezeAcceptance) and command.pack_version and not valid_identifier(
        command.pack_version
    ):
        # Grammar at the ingress: a pack version decide() accepts must be one
        # the validator (hence commit) will accept, or apply() produces a
        # snapshot the store refuses.
        return _refuse(
            "identifier.invalid",
            f"pack version {command.pack_version!r} is not a valid identifier",
        )
    if (
        isinstance(command, FreezeAcceptance)
        and command.pack_version
        and command.pack_version in (
            *unit.pack_history,
            unit.acceptance_pack_version,
        )
    ):
        return _refuse(
            "pack.version-reused",
            f"acceptance pack version {command.pack_version!r} was already used by "
            f"unit {unit.ref().label()!r}",
        )
    if isinstance(command, FreezeAcceptance) and command.pack_digest and not _valid_digest(
        command.pack_digest
    ):
        return _refuse(
            "pack.digest-invalid",
            "acceptance pack digest must be empty or 64 lowercase hexadecimal characters",
        )
    if (
        isinstance(command, FreezeAcceptance)
        and command.test_set_digest
        and not _valid_digest(command.test_set_digest)
    ):
        return _refuse(
            "pack.test-set-digest-invalid",
            "acceptance test-set digest must be 64 lowercase hexadecimal characters",
        )
    if (
        isinstance(command, FreezeAcceptance)
        and command.test_set_digest
        and not command.pack_digest
    ):
        return _refuse(
            "pack.test-set-without-pack",
            "an acceptance test-set digest requires a bound pack digest",
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


def _decide_bind_pack(snapshot: Snapshot, command: BindPack) -> Decision:
    unit = snapshot.unit(command.name)
    if unit is None:
        return _refuse("unit.unknown", f"unit {command.name!r} does not exist")
    if unit.status is UnitStatus.SUPERSEDED:
        return _refuse(
            "pack.superseded",
            f"unit {unit.ref().label()!r} is superseded history and cannot be bound",
        )
    if not unit.acceptance_pack_version:
        return _refuse(
            "pack.no-version",
            f"unit {unit.ref().label()!r} has no acceptance pack version",
        )
    if unit.acceptance_pack_digest:
        return _refuse(
            "pack.already-bound",
            f"unit {unit.ref().label()!r} acceptance pack is already bound",
        )
    if not _valid_digest(command.pack_digest):
        return _refuse(
            "pack.digest-invalid",
            "acceptance pack digest must be 64 lowercase hexadecimal characters",
        )
    test_set_digest = command.test_set_digest or command.pack_digest
    if not _valid_digest(test_set_digest):
        return _refuse(
            "pack.test-set-digest-invalid",
            "acceptance test-set digest must be 64 lowercase hexadecimal characters",
        )
    return Accepted(
        events=(
            AcceptancePackBound(
                name=unit.name,
                pack_digest=command.pack_digest,
                test_set_digest=test_set_digest,
            ),
        )
    )


def _decide_rebind_pack(snapshot: Snapshot, command: RebindPack) -> Decision:
    unit = snapshot.unit(command.name)
    if unit is None:
        return _refuse("unit.unknown", f"unit {command.name!r} does not exist")
    if unit.status not in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}:
        return _refuse(
            "rebind.not-published",
            f"unit {unit.ref().label()!r} is not published or release-bound",
        )
    if not unit.acceptance_pack_digest or not unit.acceptance_test_set_digest:
        return _refuse(
            "rebind.no-pack",
            f"unit {unit.ref().label()!r} has no bound acceptance pack to rebind",
        )
    if not command.reason.strip():
        return _refuse(
            "rebind.reason-required",
            "rebind-pack requires a non-empty reason",
        )
    if not _valid_digest(command.pack_digest):
        return _refuse(
            "pack.digest-invalid",
            "acceptance pack digest must be 64 lowercase hexadecimal characters",
        )
    if command.pack_digest == unit.acceptance_pack_digest:
        return _refuse(
            "rebind.no-change",
            "rebind-pack requires a changed acceptance pack inventory",
        )
    test_set_digest = command.test_set_digest or command.pack_digest
    if not _valid_digest(test_set_digest):
        return _refuse(
            "pack.test-set-digest-invalid",
            "acceptance test-set digest must be 64 lowercase hexadecimal characters",
        )
    if command.old_inventory is None or command.new_inventory is None:
        return _refuse(
            "rebind.inventory-required",
            "rebind-pack requires both old and new acceptance pack inventories",
        )
    old_paths = {entry.path for entry in command.old_inventory.entries}
    new_paths = {entry.path for entry in command.new_inventory.entries}
    removed = tuple(sorted(old_paths - new_paths))
    if removed:
        return _refuse(
            "rebind.paths-removed",
            "rebind-pack cannot remove bound path(s): " + ", ".join(removed),
        )
    old_tests = {
        entry.path
        for entry in command.old_inventory.entries
        if command.old_inventory.pack_format == 1 or entry.role == "test"
    }
    new_tests = {
        entry.path
        for entry in command.new_inventory.entries
        if command.new_inventory.pack_format == 1 or entry.role == "test"
    }
    demoted = tuple(sorted((old_tests & new_paths) - new_tests))
    if demoted:
        return _refuse(
            "rebind.test-demoted",
            "rebind-pack cannot demote executable path(s): " + ", ".join(demoted),
        )
    head = snapshot.evidence_head_ref(unit.ref())
    return Accepted(
        events=(
            AcceptancePackRebound(
                name=unit.name,
                old_pack_digest=unit.acceptance_pack_digest,
                new_pack_digest=command.pack_digest,
                old_test_set_digest=unit.acceptance_test_set_digest,
                new_test_set_digest=test_set_digest,
                reason=command.reason,
                evidence_head_sequence=0 if head is None else head.head_sequence,
            ),
        )
    )


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
    if not valid_identifier(command.release_id):
        return _refuse("identifier.invalid", "release id is not a valid identifier")
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
    # Evidence may bind only the CURRENT record. Heads are exact NAME@VERSION
    # bindings, so superseded history stays inspectable but inert (D-9/D-17).
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
    if unit.status is UnitStatus.SPECIFIED or not unit.acceptance_pack_version:
        return _refuse(
            "evidence.no-pack",
            f"unit {event.unit.label()!r} has no frozen acceptance pack",
        )
    if event.pack_digest != unit.acceptance_test_set_digest:
        return _refuse(
            "evidence.pack-mismatch",
            f"evidence pack digest does not match unit {event.unit.label()!r}'s "
            "current acceptance test-set digest",
        )
    if event.evidence_format != 2:
        return _refuse(
            "evidence.format-legacy",
            "legacy evidence objects are readable history but cannot be recorded anew",
        )
    if not valid_command_line(event.command_line):
        return _refuse(
            "evidence.command-line-invalid",
            "evidence command line must be 1..4096 printable characters",
        )
    if not _valid_digest(command.event_digest):
        return _refuse(
            "evidence.digest-invalid",
            "evidence digest must be 64 lowercase hexadecimal characters",
        )
    if not evidence_kind_outcome_coherent(event.kind, event.outcome):
        return _refuse(
            "evidence.kind-outcome-mismatch",
            "kind/outcome coherence violated: "
            f"kind {event.kind.value!r} contradicts outcome {event.outcome.value!r}",
        )
    head = snapshot.evidence_head_ref(event.unit)
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


def _decide_set_tier(snapshot: Snapshot, command: SetTier) -> Decision:
    order = tuple(Tier)
    lowering = order.index(command.tier) < order.index(snapshot.tier)
    if lowering and any(
        unit.status is not UnitStatus.SPECIFIED for unit in snapshot.units
    ):
        return _refuse(
            "tier.lowering-refused",
            "assurance tier cannot lower once any unit is beyond specified",
        )
    return Accepted(events=(TierSet(command.tier),))


def _decide_set_ci_policy(command: SetCiPolicy) -> Decision:
    return Accepted(events=(CiPolicySet(command.policy),))


def _decide_mark_open_in_ci(snapshot: Snapshot, command: MarkOpenInCi) -> Decision:
    unit = snapshot.unit(command.name)
    if unit is None:
        return _refuse("unit.unknown", f"unit {command.name!r} does not exist")
    if not command.reason.strip():
        return _refuse(
            "ci-policy.reason-required",
            "mark-open-in-ci requires a non-empty reason",
        )
    if (
        unit.status is UnitStatus.SPECIFIED
        or unit.status in _CI_TERMINAL_UNIT_STATUSES
    ):
        return _refuse(
            "ci-policy.exception-not-open",
            f"unit {unit.ref().label()!r} is not post-specified and nonterminal",
        )
    return Accepted(
        events=(OpenInCiMarked(unit.ref().label(), command.reason),)
    )


def _decide_clear_open_in_ci(snapshot: Snapshot, command: ClearOpenInCi) -> Decision:
    unit = snapshot.unit(command.name)
    if unit is None:
        return _refuse("unit.unknown", f"unit {command.name!r} does not exist")
    exact_ref = unit.ref().label()
    if not any(item.unit_name == exact_ref for item in snapshot.ci_exceptions):
        return _refuse(
            "ci-policy.exception-missing",
            f"unit {exact_ref!r} has no open-in-CI exception",
        )
    return Accepted(events=(OpenInCiCleared(exact_ref),))


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
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                (
                    ref.name,
                    ref.version,
                    unit.status.value
                    if (unit := snapshot.unit_ref(ref))
                    else "missing",
                )
                for ref in release.includes
            ),
            key=lambda row: (row[0], row[1]),
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
    if not valid_identifier(command.deviation_id):
        return _refuse("identifier.invalid", "deviation id is not a valid identifier")
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
        case BindPack():
            return _decide_bind_pack(snapshot, command)
        case RebindPack():
            return _decide_rebind_pack(snapshot, command)
        case RecordEvidence():
            return _decide_record_evidence(snapshot, command)
        case AdvanceAnchor():
            return _decide_advance_anchor(snapshot, command)
        case SetAnchorMode():
            return _decide_set_anchor_mode(command)
        case SetTier():
            return _decide_set_tier(snapshot, command)
        case SetCiPolicy():
            return _decide_set_ci_policy(command)
        case MarkOpenInCi():
            return _decide_mark_open_in_ci(snapshot, command)
        case ClearOpenInCi():
            return _decide_clear_open_in_ci(snapshot, command)
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
    retained = tuple(head for head in heads if head.ref() != changed.ref())
    return tuple(
        sorted(retained + (changed,), key=lambda head: (head.unit_name, head.unit_version))
    )


def _apply_one(snapshot: Snapshot, event: Event) -> Snapshot:
    if isinstance(event, PreLogHistoryRecorded):
        if snapshot != Snapshot():
            raise ValueError("pre-log history can only be replayed from the empty snapshot")
        if event.baseline.event_head:
            raise ValueError("pre-log baseline must have an empty event head")
        return event.baseline
    units = snapshot.units
    deviations = snapshot.deviations
    releases = snapshot.releases
    evidence_heads = snapshot.evidence_heads
    anchor_mode = snapshot.anchor_mode
    tier = snapshot.tier
    ci_policy = snapshot.ci_policy
    ci_exceptions = snapshot.ci_exceptions
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
        case AcceptanceFrozen(
            name=name,
            pack_version=pack_version,
            pack_digest=pack_digest,
            test_set_digest=test_set_digest,
        ):
            unit = _existing(snapshot, name)
            units = _with_unit(
                units,
                name,
                replace(
                    unit,
                    status=UnitStatus.ACCEPTANCE_FROZEN,
                    acceptance_pack_version=pack_version,
                    acceptance_pack_digest=pack_digest,
                    acceptance_test_set_digest=test_set_digest or pack_digest,
                    pack_binding="at-freeze" if pack_digest else "",
                ),
            )
        case AcceptancePackBound(
            name=name,
            pack_digest=pack_digest,
            test_set_digest=test_set_digest,
        ):
            unit = _existing(snapshot, name)
            units = _with_unit(
                units,
                name,
                replace(
                    unit,
                    acceptance_pack_digest=pack_digest,
                    acceptance_test_set_digest=test_set_digest or pack_digest,
                    pack_binding="late",
                ),
            )
        case AcceptancePackRebound(
            name=name,
            old_pack_digest=old_pack_digest,
            new_pack_digest=new_pack_digest,
            old_test_set_digest=old_test_set_digest,
            new_test_set_digest=new_test_set_digest,
            reason=reason,
            evidence_head_sequence=evidence_head_sequence,
        ):
            unit = _existing(snapshot, name)
            units = _with_unit(
                units,
                name,
                replace(
                    unit,
                    acceptance_pack_digest=new_pack_digest,
                    acceptance_test_set_digest=new_test_set_digest,
                    pack_binding="rebound",
                    rebound_history=unit.rebound_history
                    + (
                        PackRebound(
                            old_inventory_digest=old_pack_digest,
                            new_inventory_digest=new_pack_digest,
                            old_test_set_digest=old_test_set_digest,
                            new_test_set_digest=new_test_set_digest,
                            reason=reason,
                            evidence_head_sequence=evidence_head_sequence,
                        ),
                    ),
                ),
            )
        case UnitMarkedReady(name=name):
            units = _with_status(snapshot, name, UnitStatus.READY)
        case WorkStarted(name=name):
            units = _with_status(snapshot, name, UnitStatus.IN_PROGRESS)
        case UnitMarkedDone(name=name):
            units = _with_status(snapshot, name, UnitStatus.DONE_PENDING_ACCEPTANCE)
        case UnitPublished(name=name):
            unit = _existing(snapshot, name)
            units = _with_unit(
                units,
                name,
                replace(unit, status=UnitStatus.PUBLISHED, published_once=True),
            )
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
                    unit,
                    status=UnitStatus.SPECIFIED,
                    acceptance_pack_version="",
                    acceptance_pack_digest="",
                    acceptance_test_set_digest="",
                    pack_history=(
                        unit.pack_history + (unit.acceptance_pack_version,)
                        if unit.acceptance_pack_version
                        else unit.pack_history
                    ),
                    pack_binding="",
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
            prior = snapshot.evidence_head_ref(recorded.unit)
            changed = EvidenceHead(
                unit_name=recorded.unit.name,
                head=event_digest,
                head_sequence=recorded.sequence,
                anchored="GENESIS" if prior is None else prior.anchored,
                unit_version=recorded.unit.version,
                pack_summaries=fold_evidence_pack_summaries(
                    (recorded,),
                    initial=() if prior is None else prior.pack_summaries,
                ),
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
        case TierSet(tier=next_tier):
            tier = next_tier
        case CiPolicySet(policy=policy):
            ci_policy = policy
        case OpenInCiMarked(name=name, reason=reason):
            ci_exceptions = tuple(
                sorted(
                    (
                        item
                        for item in ci_exceptions
                        if item.unit_name != name
                    ),
                    key=lambda item: item.unit_name,
                )
            ) + (CiException(name, reason),)
            ci_exceptions = tuple(
                sorted(ci_exceptions, key=lambda item: item.unit_name)
            )
        case OpenInCiCleared(name=name):
            ci_exceptions = tuple(
                item for item in ci_exceptions if item.unit_name != name
            )
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
    active_exception_refs = {
        unit.ref().label()
        for unit in units
        if unit.status is not UnitStatus.SPECIFIED
        and unit.status not in _CI_TERMINAL_UNIT_STATUSES
    }
    ci_exceptions = tuple(
        item
        for item in ci_exceptions
        if item.unit_name in active_exception_refs
    )
    return Snapshot(
        revision=snapshot.revision,
        event_head=snapshot.event_head,
        units=units,
        deviations=deviations,
        releases=releases,
        evidence_heads=evidence_heads,
        anchor_mode=anchor_mode,
        tier=tier,
        formal_certificate_digest=formal_certificate_digest,
        ci_policy=ci_policy,
        ci_exceptions=ci_exceptions,
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


def apply(
    snapshot: Snapshot,
    events: tuple[Event, ...],
    *,
    event_digests: tuple[str, ...] = (),
) -> Snapshot:
    """Fold accepted events, advancing one revision per event.

    Runtime recording and replay pass the content digest corresponding to each
    event.  Pure policy callers omit them; the existing event head is then
    retained because hashing remains outside the core.
    """
    if event_digests and len(event_digests) != len(events):
        raise ValueError("event_digests must contain one digest per event")
    current = snapshot
    for index, event in enumerate(events):
        current = _apply_one(current, event)
        revision = (
            current.revision
            if isinstance(event, PreLogHistoryRecorded)
            else current.revision + 1
        )
        current = replace(
            current,
            revision=revision,
            event_head=(
                event_digests[index]
                if event_digests
                else current.event_head
            ),
        )
    return current
