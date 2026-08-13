"""Exhaustive property tests against the IR, not the code (plan §4 Phase 1):

- every (state × command) cell of the unit and deviation matrices behaves exactly
  as the IR tables license — accepted onto the IR target, or refused with a named
  violation;
- every legal transition is reachable by driving ``decide``/``apply`` from the
  empty snapshot (BFS over a small command universe), with only true self-loops
  unobservable as status changes.
"""

from __future__ import annotations

import pytest

from vouch_mk2.core import (
    Accepted,
    AmendDeviation,
    BeginReverify,
    CloseDeviation,
    Command,
    CompleteRelease,
    Deviation,
    DeviationStatus,
    DeviationType,
    FreezeAcceptance,
    LaunchCriteria,
    MarkFailedUnrecovered,
    MarkDone,
    MarkReady,
    OpenDeviation,
    PrepareRelease,
    Publish,
    Refused,
    RejectDeviation,
    RetireRelease,
    Respecify,
    RollBack,
    SetReleaseCriteria,
    Snapshot,
    SpecifyUnit,
    StartDeploy,
    StartVerification,
    StartWork,
    Supersede,
    Unit,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.ir.mk2 import DEVIATION_MACHINE, MK2, RELEASE_O_MACHINE, UNIT_MACHINE

UNIT = MK2.machine(UNIT_MACHINE)
DEVIATION = MK2.machine(DEVIATION_MACHINE)
RELEASE = MK2.machine(RELEASE_O_MACHINE)

REF = UnitRef(name="u", version="v1")

UNIT_COMMANDS: tuple[Command, ...] = (
    FreezeAcceptance(name="u", pack_version="p2"),
    MarkReady(name="u"),
    StartWork(name="u"),
    MarkDone(name="u"),
    Publish(name="u"),
    Supersede(name="u"),
    Respecify(name="u"),
    BeginReverify(name="u"),
    OpenDeviation(
        deviation_id="d9", deviation_type=DeviationType.PACK_DEFECT, unit=REF
    ),
)


def unit_in(status: UnitStatus) -> Snapshot:
    """A minimal snapshot with one unit in ``status`` and satisfiable gates."""
    pack = "" if status is UnitStatus.SPECIFIED else "p1"
    return Snapshot(
        units=(
            Unit(name="u", version="v1", status=status, acceptance_pack_version=pack),
        )
    )


def ir_target(source: str, command: str) -> str | None:
    for transition in UNIT.command_transitions:
        if transition.source == source and transition.command == command:
            return transition.target
    return None


@pytest.mark.parametrize("status", list(UnitStatus))
@pytest.mark.parametrize("command", UNIT_COMMANDS, ids=lambda c: type(c).__name__)
def test_unit_matrix_cell(status: UnitStatus, command: Command) -> None:
    snapshot = unit_in(status)
    decision = decide(snapshot, command)
    target = ir_target(status.value, command.ir_command)
    if target is None:
        assert isinstance(decision, Refused), (status, command)
        assert decision.violations[0].code == "transition.illegal"
        return
    assert isinstance(decision, Accepted), (status, command)
    after = apply(snapshot, decision.events)
    unit = after.unit("u")
    assert unit is not None
    assert unit.status.value == target
    assert after.revision == snapshot.revision + 1


DEVIATION_COMMANDS: tuple[Command, ...] = (
    AmendDeviation(deviation_id="d1", amendment_version="v2"),
    RejectDeviation(deviation_id="d1"),
    CloseDeviation(deviation_id="d1"),
)


def deviation_in(status: DeviationStatus) -> Snapshot:
    return Snapshot(
        units=(
            Unit(
                name="u",
                version="v1",
                status=UnitStatus.PUBLISHED,
                acceptance_pack_version="p1",
            ),
        ),
        deviations=(
            Deviation(
                deviation_id="d1",
                deviation_type=DeviationType.PACK_DEFECT,
                unit=REF,
                status=status,
            ),
        ),
    )


@pytest.mark.parametrize("status", list(DeviationStatus))
@pytest.mark.parametrize(
    "command", DEVIATION_COMMANDS, ids=lambda c: type(c).__name__
)
def test_deviation_matrix_cell(status: DeviationStatus, command: Command) -> None:
    snapshot = deviation_in(status)
    decision = decide(snapshot, command)
    label = command.ir_command
    target = next(
        (
            t.target
            for t in DEVIATION.command_transitions
            if t.source == status.value and t.command == label
        ),
        None,
    )
    if target is None:
        assert isinstance(decision, Refused), (status, command)
        assert decision.violations[0].code == "transition.illegal"
        return
    assert isinstance(decision, Accepted), (status, command)
    after = apply(snapshot, decision.events)
    deviation = after.deviation("d1")
    assert deviation is not None
    assert deviation.status.value == target


# --- reachability --------------------------------------------------------------

COMMAND_UNIVERSE: tuple[Command, ...] = (
    SpecifyUnit(name="u", version="v1"),
    FreezeAcceptance(name="u", pack_version="p1"),
    MarkReady(name="u"),
    StartWork(name="u"),
    MarkDone(name="u"),
    Publish(name="u"),
    PrepareRelease(release_id="r1", includes=("u",)),
    SetReleaseCriteria(
        release_id="r1",
        criteria=LaunchCriteria(
            launch_criteria=("healthy",),
            verification_window="1h",
            rollback_triggers=(),
            go_no_go_authority="operator",
        ),
    ),
    StartDeploy(release_id="r1"),
    StartVerification(release_id="r1"),
    CompleteRelease(release_id="r1"),
    RollBack(release_id="r1", trigger="trigger"),
    MarkFailedUnrecovered(release_id="r1", trigger="trigger"),
    RetireRelease(release_id="r1", trigger="trigger"),
    Supersede(name="u"),
    Respecify(name="u"),
    BeginReverify(name="u"),
    OpenDeviation(
        deviation_id="d1", deviation_type=DeviationType.PACK_DEFECT, unit=REF
    ),
    AmendDeviation(deviation_id="d1", amendment_version="v2"),
    RejectDeviation(deviation_id="d1"),
    CloseDeviation(deviation_id="d1"),
    OpenDeviation(
        deviation_id="d2", deviation_type=DeviationType.PLAN_GAP, unit=REF
    ),
    AmendDeviation(deviation_id="d2", amendment_version="v2"),
    RejectDeviation(deviation_id="d2"),
    CloseDeviation(deviation_id="d2"),
)


def snapshot_key(snapshot: Snapshot) -> tuple[object, ...]:
    return (
        tuple(
            (u.name, u.status.value, u.acceptance_pack_version)
            for u in snapshot.units
        ),
        tuple((d.deviation_id, d.status.value) for d in snapshot.deviations),
        tuple(
            (
                release.release_id,
                release.state.value,
                release.criteria is not None,
                release.closure is not None,
            )
            for release in snapshot.releases
        ),
    )


def test_every_legal_transition_is_reachable() -> None:
    seen_unit_edges: set[tuple[str, str, str]] = set()
    seen_adjudication_edges: set[tuple[str, str]] = set()
    seen_deviation_edges: set[tuple[str, str, str]] = set()
    seen_release_edges: set[tuple[str, str, str]] = set()
    visited = {snapshot_key(Snapshot())}
    frontier = [Snapshot()]
    while frontier:
        snapshot = frontier.pop()
        for command in COMMAND_UNIVERSE:
            decision = decide(snapshot, command)
            if not isinstance(decision, Accepted):
                continue
            after = apply(snapshot, decision.events)
            before_unit = snapshot.unit("u")
            after_unit = after.unit("u")
            if (
                before_unit is not None
                and after_unit is not None
                and before_unit.status is not after_unit.status
            ):
                if isinstance(command, AmendDeviation):
                    # Invalidation is the adjudication entry firing, not a
                    # command transition of the unit machine.
                    seen_adjudication_edges.add(
                        (before_unit.status.value, after_unit.status.value)
                    )
                else:
                    label: str = command.ir_command
                    seen_unit_edges.add(
                        (before_unit.status.value, label, after_unit.status.value)
                    )
            for deviation_id in ("d1", "d2"):
                before_d = snapshot.deviation(deviation_id)
                after_d = after.deviation(deviation_id)
                if (
                    before_d is not None
                    and after_d is not None
                    and before_d.status is not after_d.status
                ):
                    label = command.ir_command
                    seen_deviation_edges.add(
                        (before_d.status.value, label, after_d.status.value)
                    )
            before_release = snapshot.release("r1")
            after_release = after.release("r1")
            if (
                before_release is not None
                and after_release is not None
                and before_release.state is not after_release.state
                and not isinstance(command, AmendDeviation)
            ):
                seen_release_edges.add(
                    (
                        before_release.state.value,
                        command.ir_command,
                        after_release.state.value,
                    )
                )
            key = snapshot_key(after)
            if key not in visited:
                visited.add(key)
                frontier.append(after)

    ir_unit_edges = {
        (t.source, t.command, t.target) for t in UNIT.command_transitions
    }
    # The BFS records status *changes*, so only true self-loops (deviation
    # accumulation on a blocked unit) are expected to be absent.
    unobservable = {
        edge
        for edge in ir_unit_edges
        if edge[0] == edge[2]
    }
    assert seen_unit_edges == ir_unit_edges - unobservable
    assert seen_deviation_edges == {
        (t.source, t.command, t.target) for t in DEVIATION.command_transitions
    }
    # Self-loops (set-criteria in prepared) do not change state and are
    # therefore invisible to a status-change recorder.
    assert seen_release_edges == {
        (t.source, t.command, t.target)
        for t in RELEASE.command_transitions
        if t.source != t.target
    }
    # The adjudication entry fired, its targets are all `invalidated`, and its
    # sources respect the IR entry's source/exclusion lists.
    entry = UNIT.adjudication_entries[0]
    allowed = set(entry.sources) - set(entry.excluded_sources)
    assert seen_adjudication_edges
    assert {target for _, target in seen_adjudication_edges} == {entry.target}
    assert {source for source, _ in seen_adjudication_edges} <= allowed
    # And invalidated units recover: invalidated-source command edges appeared.
    assert any(edge[0] == "invalidated" for edge in seen_unit_edges)


def test_all_reachable_snapshots_pass_standing_checks() -> None:
    # Property: no snapshot produced purely by legal decide()/apply() sequences
    # may FAIL the standing checks — the at-rest invariants and the mutation-time
    # gates must agree. (This is the test that catches a standing check whose
    # predicate is stricter than the lifecycle it audits.)
    from vouch_mk2.checks import run_checks
    from vouch_mk2.ir.schema import Verdict

    visited = {snapshot_key(Snapshot())}
    frontier = [Snapshot()]
    while frontier:
        snapshot = frontier.pop()
        report = run_checks(snapshot)
        assert report.composed() is not Verdict.FAIL, (
            snapshot,
            [
                (r.check_id, r.detail)
                for r in report.mechanical
                if r.verdict is Verdict.FAIL
            ],
        )
        for command in COMMAND_UNIVERSE:
            decision = decide(snapshot, command)
            if not isinstance(decision, Accepted):
                continue
            after = apply(snapshot, decision.events)
            key = snapshot_key(after)
            if key not in visited:
                visited.add(key)
                frontier.append(after)


def test_reachable_multi_unit_release_snapshots_pass_standing_checks() -> None:
    # A sweep over a RICHER universe than the exhaustive test above: two
    # units with a dependency edge, a release over both, and a deviation.
    # The reachable space under this universe saturates (~612 distinct states
    # by depth 30 — calibrated), so the depth bound below is a safety valve,
    # not a silent cap: the sweep is effectively exhaustive for this universe.
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core import (
        CompleteRelease,
        LaunchCriteria,
        PrepareRelease,
        RetireRelease,
        RollBack,
        SetReleaseCriteria,
        StartDeploy,
        StartVerification,
    )
    from vouch_mk2.ir.schema import Verdict

    b_ref = UnitRef(name="b", version="v1")
    criteria = LaunchCriteria(
        launch_criteria=("healthy",),
        verification_window="1h",
        rollback_triggers=("errors",),
        go_no_go_authority="ops",
    )
    universe: tuple[Command, ...] = (
        SpecifyUnit(name="b", version="v1"),
        SpecifyUnit(name="a", version="v1", depends_on=("b",)),
        FreezeAcceptance(name="b", pack_version="p"),
        FreezeAcceptance(name="a", pack_version="p"),
        MarkReady(name="b"),
        MarkReady(name="a"),
        StartWork(name="b"),
        StartWork(name="a"),
        MarkDone(name="b"),
        MarkDone(name="a"),
        Publish(name="b"),
        Publish(name="a"),
        Supersede(name="b"),
        BeginReverify(name="b"),
        Respecify(name="b"),
        OpenDeviation(
            deviation_id="d1", deviation_type=DeviationType.PACK_DEFECT, unit=b_ref
        ),
        AmendDeviation(deviation_id="d1", amendment_version="v2"),
        RejectDeviation(deviation_id="d1"),
        CloseDeviation(deviation_id="d1"),
        PrepareRelease(release_id="r1", includes=("a", "b")),
        SetReleaseCriteria(release_id="r1", criteria=criteria),
        StartDeploy(release_id="r1"),
        StartVerification(release_id="r1"),
        CompleteRelease(release_id="r1"),
        RollBack(release_id="r1", trigger="errors"),
        RetireRelease(release_id="r1", trigger="done"),
    )

    def key(snapshot: Snapshot) -> tuple[object, ...]:
        return (
            tuple(
                (u.name, u.status.value, u.acceptance_pack_version)
                for u in snapshot.units
            ),
            tuple((d.deviation_id, d.status.value) for d in snapshot.deviations),
            tuple(
                (r.release_id, r.state.value, r.criteria is not None)
                for r in snapshot.releases
            ),
        )

    max_depth = 30
    visited = {key(Snapshot()): 0}
    frontier: list[tuple[Snapshot, int]] = [(Snapshot(), 0)]
    checked = 0
    while frontier:
        snapshot, depth = frontier.pop()
        report = run_checks(snapshot)
        checked += 1
        assert report.composed() is not Verdict.FAIL, (
            snapshot,
            [
                (r.check_id, r.detail)
                for r in report.mechanical
                if r.verdict is Verdict.FAIL
            ],
        )
        if depth >= max_depth:
            continue
        for command in universe:
            decision = decide(snapshot, command)
            if not isinstance(decision, Accepted):
                continue
            after = apply(snapshot, decision.events)
            after_key = key(after)
            if after_key not in visited or visited[after_key] > depth + 1:
                visited[after_key] = depth + 1
                frontier.append((after, depth + 1))
    assert checked > 500
