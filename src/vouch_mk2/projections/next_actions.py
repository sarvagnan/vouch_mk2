"""Pure, table-derived next work owed by a snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from vouch_mk2.core.model import (
    AnchorMode,
    DeviationStatus,
    ReleaseState,
    Snapshot,
    Unit,
    UnitStatus,
    blocking_deviations,
)
from vouch_mk2.ir import mk2


@dataclass(frozen=True, slots=True)
class NextAction:
    subject_kind: str
    subject_id: str
    command: str
    reason: str


_UNIT_MACHINE = mk2.MK2.machine(mk2.UNIT_MACHINE)
_DEVIATION_MACHINE = mk2.MK2.machine(mk2.DEVIATION_MACHINE)
_RELEASE_MACHINE = mk2.MK2.machine(mk2.RELEASE_O_MACHINE)
_SATISFIED_DEPENDENCY_STATES = frozenset(
    mk2.MK2.state_set("unit.satisfied-dependency").states
)

_UNIT_REASONS = {
    mk2.CMD_FREEZE: "Record the acceptance pack and freeze.",
    mk2.CMD_MARK_READY: "Mark the acceptance-frozen unit ready.",
    mk2.CMD_START: "Start work on the ready unit.",
    mk2.CMD_MARK_DONE: "Mark the completed work done pending acceptance.",
    mk2.CMD_PUBLISH: "Publish the unit after its acceptance gates pass.",
    mk2.CMD_RESPECIFY: "Respecify the unit now that no blocking deviations remain.",
    mk2.CMD_BEGIN_REVERIFY: "Begin re-verification using the recorded acceptance pack.",
}

_DEVIATION_REASONS = {
    mk2.CMD_ADJUDICATE_AMEND: "Adjudicate the open deviation by amending the contract.",
    mk2.CMD_ADJUDICATE_REJECT: "Adjudicate the open deviation by rejecting the request.",
    mk2.CMD_CLOSE_DEVIATION: "Close the adjudicated deviation.",
}

_RELEASE_REASONS = {
    mk2.CMD_SET_CRITERIA: "Record launch criteria for the prepared release.",
    mk2.CMD_START_DEPLOY: "Start deployment now that launch criteria are recorded.",
    mk2.CMD_START_VERIFICATION: "Start verification of the deployed release.",
    mk2.CMD_COMPLETE_RELEASE: "Complete the release after every completion gate passes.",
    mk2.CMD_ROLL_BACK: "Roll back because the release completion gates do not pass.",
}


def _outgoing(machine_name: str, source: str) -> tuple[str, ...]:
    machine = mk2.MK2.machine(machine_name)
    return tuple(
        transition.command
        for transition in machine.command_transitions
        if transition.source == source
    )


def _dependencies_exist(snapshot: Snapshot, unit: Unit) -> bool:
    return all(snapshot.unit(name) is not None for name in unit.depends_on)


def _dependencies_satisfied(snapshot: Snapshot, unit: Unit) -> bool:
    for name in unit.depends_on:
        dependency = snapshot.unit(name)
        if (
            dependency is None
            or dependency.status.value not in _SATISFIED_DEPENDENCY_STATES
        ):
            return False
    return True


def _publication_gates_pass(snapshot: Snapshot, unit: Unit) -> bool:
    return (
        bool(unit.acceptance_pack_version)
        and _dependencies_satisfied(snapshot, unit)
        and not blocking_deviations(snapshot, unit)
    )


def _unit_commands(snapshot: Snapshot, unit: Unit) -> tuple[str, ...]:
    outgoing = _outgoing(mk2.UNIT_MACHINE, unit.status.value)
    allowed: list[str] = []
    for command in outgoing:
        if command in (mk2.CMD_SUPERSEDE, mk2.CMD_OPEN_DEVIATION, mk2.CMD_PREPARE_RELEASE):
            continue
        if command == mk2.CMD_FREEZE:
            if _dependencies_exist(snapshot, unit) and not blocking_deviations(
                snapshot, unit
            ):
                allowed.append(command)
        elif command == mk2.CMD_PUBLISH:
            if _publication_gates_pass(snapshot, unit):
                allowed.append(command)
        elif command == mk2.CMD_RESPECIFY:
            if not blocking_deviations(snapshot, unit):
                allowed.append(command)
        elif command == mk2.CMD_BEGIN_REVERIFY:
            if (
                not blocking_deviations(snapshot, unit)
                and unit.acceptance_pack_version
            ):
                allowed.append(command)
        elif command in (mk2.CMD_MARK_READY, mk2.CMD_START, mk2.CMD_MARK_DONE):
            allowed.append(command)
    return tuple(allowed)


def _release_completion_gates_pass(snapshot: Snapshot, release_id: str) -> bool:
    release = snapshot.release(release_id)
    if release is None or release.criteria is None:
        return False
    for ref in release.includes:
        unit = snapshot.unit(ref.name)
        if (
            unit is None
            or unit.version != ref.version
            or unit.status is not UnitStatus.RELEASE_BOUND
            or not unit.acceptance_pack_version
            or blocking_deviations(snapshot, unit)
        ):
            return False
    return True


def _release_commands(snapshot: Snapshot, release_id: str) -> tuple[str, ...]:
    release = snapshot.release(release_id)
    if release is None:
        return ()
    outgoing = _outgoing(mk2.RELEASE_O_MACHINE, release.state.value)
    match release.state:
        case ReleaseState.PREPARED:
            command = (
                mk2.CMD_SET_CRITERIA
                if release.criteria is None
                else mk2.CMD_START_DEPLOY
            )
            return (command,) if command in outgoing else ()
        case ReleaseState.DEPLOYING:
            return (
                (mk2.CMD_START_VERIFICATION,)
                if mk2.CMD_START_VERIFICATION in outgoing
                else ()
            )
        case ReleaseState.VERIFYING:
            command = (
                mk2.CMD_COMPLETE_RELEASE
                if _release_completion_gates_pass(snapshot, release_id)
                else mk2.CMD_ROLL_BACK
            )
            return (command,) if command in outgoing else ()
        case (
            ReleaseState.ROLLED_BACK
            | ReleaseState.SUCCEEDED
            | ReleaseState.FAILED_UNRECOVERED
            | ReleaseState.RETIRED
            | ReleaseState.INVALIDATED
        ):
            return ()
        case _:
            assert_never(release.state)


def next_actions(snapshot: Snapshot) -> tuple[NextAction, ...]:
    """Return only binding work whose IR edge and current-state gates are legal."""
    actions: list[NextAction] = []
    for unit in snapshot.units:
        for command in _unit_commands(snapshot, unit):
            actions.append(
                NextAction("unit", unit.name, command, _UNIT_REASONS[command])
            )

    for deviation in snapshot.deviations:
        outgoing = _outgoing(mk2.DEVIATION_MACHINE, deviation.status.value)
        match deviation.status:
            case DeviationStatus.OPEN:
                commands = (
                    mk2.CMD_ADJUDICATE_AMEND,
                    mk2.CMD_ADJUDICATE_REJECT,
                )
            case DeviationStatus.AMENDED | DeviationStatus.REJECTED:
                commands = (mk2.CMD_CLOSE_DEVIATION,)
            case DeviationStatus.CLOSED:
                commands = ()
            case _:
                assert_never(deviation.status)
        for command in commands:
            if command in outgoing:
                actions.append(
                    NextAction(
                        "deviation",
                        deviation.deviation_id,
                        command,
                        _DEVIATION_REASONS[command],
                    )
                )

    for release in snapshot.releases:
        for command in _release_commands(snapshot, release.release_id):
            actions.append(
                NextAction(
                    "release",
                    release.release_id,
                    command,
                    _RELEASE_REASONS[command],
                )
            )

    match snapshot.anchor_mode:
        case AnchorMode.NONE:
            pass
        case AnchorMode.CI:
            for head in snapshot.evidence_heads:
                if head.anchored != head.head:
                    actions.append(
                        NextAction(
                            "evidence",
                            head.unit_name,
                            mk2.CMD_ADVANCE_ANCHOR,
                            "Advance the CI anchor to the current evidence head.",
                        )
                    )
        case _:
            assert_never(snapshot.anchor_mode)

    return tuple(
        sorted(
            actions,
            key=lambda action: (
                action.subject_kind,
                action.subject_id,
                action.command,
            ),
        )
    )
