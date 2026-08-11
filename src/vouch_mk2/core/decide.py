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
    AmendDeviation,
    BeginReverify,
    CloseDeviation,
    Command,
    FreezeAcceptance,
    MarkDone,
    MarkReady,
    OpenDeviation,
    Publish,
    RejectDeviation,
    Respecify,
    SpecifyUnit,
    StartWork,
    Supersede,
    UnitTransitionCommand,
)
from vouch_mk2.core.events import (
    AcceptanceFrozen,
    DeviationAmended,
    DeviationClosed,
    DeviationOpened,
    DeviationRejected,
    Event,
    GateCheck,
    GateRecord,
    ReverificationStarted,
    UnitBlocked,
    UnitInvalidated,
    UnitMarkedDone,
    UnitMarkedReady,
    UnitPublished,
    UnitRespecified,
    UnitSpecified,
    UnitSuperseded,
    WorkStarted,
)
from vouch_mk2.core.model import (
    Deviation,
    DeviationStatus,
    Snapshot,
    Unit,
    UnitStatus,
    blocking_deviations,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Transition, Verdict, Violation

_UNIT_MACHINE = mk2.MK2.machine(mk2.UNIT_MACHINE)
_DEVIATION_MACHINE = mk2.MK2.machine(mk2.DEVIATION_MACHINE)
_SATISFIED_DEPENDENCY_STATUSES = frozenset(
    mk2.MK2.state_set("unit.satisfied-dependency").states
)


def _index(machine_name: str) -> Mapping[tuple[str, str], Transition]:
    machine = mk2.MK2.machine(machine_name)
    return {(t.source, t.command): t for t in machine.command_transitions}


_UNIT_EDGES = _index(mk2.UNIT_MACHINE)
_DEVIATION_EDGES = _index(mk2.DEVIATION_MACHINE)


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


@dataclass(frozen=True, slots=True)
class Accepted:
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class Refused:
    violations: tuple[Violation, ...]


type Decision = Accepted | Refused


def _refuse(code: str, detail: str) -> Refused:
    return Refused(violations=(Violation(code=code, detail=detail),))


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


def _check_gate_registry() -> None:
    """Fail at import, not mid-decision: an IR gate without an evaluator would
    otherwise surface as a KeyError escaping decide()'s Accepted|Refused contract."""
    bound = {
        gate
        for machine in mk2.MK2.machines
        for transition in machine.command_transitions
        for gate in transition.gates
    }
    unregistered = bound - set(GATE_EVALUATORS)
    if unregistered:
        raise LookupError(
            f"IR gates without registered evaluators: {sorted(unregistered)}"
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


# --- deviations ----------------------------------------------------------------


def _decide_open_deviation(snapshot: Snapshot, command: OpenDeviation) -> Decision:
    if not command.deviation_id:
        return _refuse("deviation.identity-required", "deviation id is required")
    if snapshot.deviation(command.deviation_id) is not None:
        return _refuse(
            "deviation.exists", f"deviation {command.deviation_id!r} already exists"
        )
    unit = snapshot.unit(command.unit.name)
    if unit is None:
        return _refuse("unit.unknown", f"unit {command.unit.name!r} does not exist")
    if unit.version != command.unit.version:
        return _refuse(
            "deviation.binding-stale",
            f"deviation binds {command.unit.label()!r} but the unit is at "
            f"version {unit.version!r}",
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

    Traversal crosses every declared dependency edge regardless of status;
    which members actually invalidate is decided by the caller (D-3)."""
    consumers: dict[str, list[str]] = {}
    for unit in snapshot.units:
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
    invalidated: list[str] = []
    for name in _amendment_impact(snapshot, found.unit.name):
        unit = snapshot.unit(name)
        if unit is not None and unit.status.value in _AMENDMENT_SOURCES:
            invalidated.append(name)
    events: list[Event] = [
        DeviationAmended(
            deviation_id=command.deviation_id,
            amendment_version=command.amendment_version,
            impact_set=tuple(invalidated),
        )
    ]
    events.extend(
        UnitInvalidated(name=name, trigger_deviation_id=command.deviation_id)
        for name in invalidated
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
        case OpenDeviation():
            return _decide_open_deviation(snapshot, command)
        case AmendDeviation():
            return _decide_amend(snapshot, command)
        case RejectDeviation():
            return _decide_reject(snapshot, command)
        case CloseDeviation():
            return _decide_close(snapshot, command)
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
            return _decide_unit_transition(snapshot, command)
        case _:
            assert_never(command)


# --- apply ---------------------------------------------------------------------


def _with_unit(units: tuple[Unit, ...], name: str, changed: Unit) -> tuple[Unit, ...]:
    return tuple(changed if unit.name == name else unit for unit in units)


def _with_deviation(
    deviations: tuple[Deviation, ...], deviation_id: str, changed: Deviation
) -> tuple[Deviation, ...]:
    return tuple(
        changed if deviation.deviation_id == deviation_id else deviation
        for deviation in deviations
    )


def _apply_one(snapshot: Snapshot, event: Event) -> Snapshot:
    units = snapshot.units
    deviations = snapshot.deviations
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
        case _:
            assert_never(event)
    return Snapshot(revision=snapshot.revision, units=units, deviations=deviations)


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


def _with_status(snapshot: Snapshot, name: str, status: UnitStatus) -> tuple[Unit, ...]:
    unit = _existing(snapshot, name)
    return _with_unit(snapshot.units, name, replace(unit, status=status))


def apply(snapshot: Snapshot, events: tuple[Event, ...]) -> Snapshot:
    """Fold accepted events into the next snapshot (revision + 1)."""
    current = snapshot
    for event in events:
        current = _apply_one(current, event)
    return Snapshot(
        revision=snapshot.revision + 1,
        units=current.units,
        deviations=current.deviations,
    )
