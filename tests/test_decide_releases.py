"""Phase 4 operational-release decisions, gates, claims, and standing checks."""

from __future__ import annotations

import pytest

from vouch_mk2.checks.standing import (
    check_release_bound_units_rostered,
    check_release_closure_records,
    check_release_criteria_before_progression,
    check_release_escape_property,
    check_release_rosters_resolve,
)
from vouch_mk2.core import (
    Accepted,
    AmendDeviation,
    ClosureRecord,
    ClosureTriggerKind,
    Command,
    CompleteRelease,
    Deviation,
    DeviationAmended,
    DeviationStatus,
    DeviationType,
    LaunchCriteria,
    MarkFailedUnrecovered,
    PrepareRelease,
    Refused,
    Release,
    ReleaseArchetype,
    ReleaseInvalidated,
    ReleaseRetired,
    ReleaseRolledBack,
    ReleaseState,
    ReleaseSucceeded,
    RetireRelease,
    RollBack,
    SetReleaseCriteria,
    Snapshot,
    StartDeploy,
    StartVerification,
    Supersede,
    Unit,
    UnitReleaseBound,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict


CRITERIA = LaunchCriteria(
    launch_criteria=("healthy",),
    verification_window="1h",
    rollback_triggers=("errors",),
    go_no_go_authority="ops",
)


def unit(
    name: str,
    status: UnitStatus,
    *,
    version: str = "v1",
    depends_on: tuple[str, ...] = (),
    pack: str = "p1",
) -> Unit:
    return Unit(
        name=name,
        version=version,
        status=status,
        depends_on=depends_on,
        acceptance_pack_version=pack,
    )


def release(
    state: ReleaseState,
    *,
    includes: tuple[UnitRef, ...] = (UnitRef("u", "v1"),),
    criteria: LaunchCriteria | None = CRITERIA,
    closure: ClosureRecord | None = None,
) -> Release:
    return Release(
        release_id="r1",
        archetype=ReleaseArchetype.OPERATIONAL,
        state=state,
        includes=includes,
        criteria=criteria,
        closure=closure,
    )


def closure(
    *, no_completion_claim: bool = True
) -> ClosureRecord:
    return ClosureRecord(
        trigger="operator",
        trigger_kind=ClosureTriggerKind.OPERATOR,
        roster_snapshot=(("u", "v1", "release-bound"),),
        no_completion_claim=no_completion_claim,
        observations=(),
    )


def accepted(snapshot: Snapshot, command: Command) -> Snapshot:
    decision = decide(snapshot, command)
    assert isinstance(decision, Accepted), decision
    return apply(snapshot, decision.events)


def refused_codes(snapshot: Snapshot, command: Command) -> set[str]:
    decision = decide(snapshot, command)
    assert isinstance(decision, Refused), decision
    return {violation.code for violation in decision.violations}


def prepared_snapshot() -> Snapshot:
    snapshot = Snapshot(units=(unit("u", UnitStatus.PUBLISHED),))
    return accepted(snapshot, PrepareRelease("r1", ("u",)))


def test_prepare_happy_path_freezes_refs_and_binds_published_units() -> None:
    snapshot = Snapshot(
        units=(
            unit("b", UnitStatus.PUBLISHED, version="v2"),
            unit("a", UnitStatus.PUBLISHED, depends_on=("b",)),
        )
    )
    after = accepted(snapshot, PrepareRelease("r1", ("a", "b")))
    found = after.release("r1")
    assert found is not None
    assert found.state is ReleaseState.PREPARED
    assert found.includes == (UnitRef("a", "v1"), UnitRef("b", "v2"))
    assert found.criteria is None and found.closure is None
    assert {candidate.status for candidate in after.units} == {
        UnitStatus.RELEASE_BOUND
    }


def test_prepare_does_not_rebind_already_release_bound_members() -> None:
    snapshot = Snapshot(units=(unit("u", UnitStatus.RELEASE_BOUND),))
    decision = decide(snapshot, PrepareRelease("r2", ("u",)))
    assert isinstance(decision, Accepted)
    assert not any(isinstance(event, UnitReleaseBound) for event in decision.events)


def test_prepare_refuses_identity_duplicate_and_unknown_unit() -> None:
    snapshot = Snapshot(units=(unit("u", UnitStatus.PUBLISHED),))
    assert refused_codes(snapshot, PrepareRelease("", ("u",))) == {
        "identifier.invalid"
    }
    prepared = accepted(snapshot, PrepareRelease("r1", ("u",)))
    assert refused_codes(prepared, PrepareRelease("r1", ("u",))) == {
        "release.exists"
    }
    assert refused_codes(snapshot, PrepareRelease("r1", ("ghost",))) == {
        "unit.unknown"
    }


def test_prepare_refuses_roster_that_is_not_dependency_closed() -> None:
    snapshot = Snapshot(
        units=(
            unit("dependency", UnitStatus.PUBLISHED),
            unit(
                "app",
                UnitStatus.PUBLISHED,
                depends_on=("dependency",),
            ),
        )
    )
    decision = decide(snapshot, PrepareRelease("r1", ("app",)))
    assert isinstance(decision, Refused)
    assert {v.code for v in decision.violations} == {
        "gate.release.roster-not-closed"
    }
    assert "dependency" in decision.violations[0].detail


@pytest.mark.parametrize(
    ("member", "reason"),
    (
        (unit("u", UnitStatus.IN_PROGRESS), "in-progress"),
        (unit("u", UnitStatus.BLOCKED), "blocked"),
        (unit("u", UnitStatus.PUBLISHED, pack=""), "empty acceptance pack"),
    ),
)
def test_prepare_refuses_each_ineligible_member_mode(
    member: Unit, reason: str
) -> None:
    decision = decide(
        Snapshot(units=(member,)), PrepareRelease("r1", ("u",))
    )
    assert isinstance(decision, Refused)
    assert {v.code for v in decision.violations} == {
        "gate.release.roster-ineligible"
    }
    assert "u" in decision.violations[0].detail
    assert reason in decision.violations[0].detail


def test_criteria_set_overwrite_and_freeze_on_deploy() -> None:
    snapshot = prepared_snapshot()
    snapshot = accepted(snapshot, SetReleaseCriteria("r1", CRITERIA))
    replacement = LaunchCriteria(("new",), "2h", (), "lead")
    snapshot = accepted(snapshot, SetReleaseCriteria("r1", replacement))
    found = snapshot.release("r1")
    assert found is not None and found.criteria == replacement
    snapshot = accepted(snapshot, StartDeploy("r1"))
    assert refused_codes(snapshot, SetReleaseCriteria("r1", CRITERIA)) == {
        "transition.illegal"
    }


@pytest.mark.parametrize(
    "criteria",
    (
        LaunchCriteria((), "1h", (), "ops"),
        LaunchCriteria(("healthy",), "1h", (), ""),
    ),
)
def test_incomplete_criteria_is_refused(criteria: LaunchCriteria) -> None:
    assert refused_codes(
        prepared_snapshot(), SetReleaseCriteria("r1", criteria)
    ) == {"release.criteria-incomplete"}


def test_i5_criteria_required_before_progression() -> None:
    assert refused_codes(prepared_snapshot(), StartDeploy("r1")) == {
        "gate.release.criteria-missing"
    }


def test_supersession_retain_policy_does_not_change_release_state() -> None:
    snapshot = accepted(prepared_snapshot(), SetReleaseCriteria("r1", CRITERIA))
    before = snapshot.release("r1")
    assert before is not None
    after = accepted(snapshot, Supersede("u"))
    found = after.release("r1")
    assert found is not None and found.state is before.state


def verifying_snapshot() -> Snapshot:
    snapshot = prepared_snapshot()
    snapshot = accepted(snapshot, SetReleaseCriteria("r1", CRITERIA))
    snapshot = accepted(snapshot, StartDeploy("r1"))
    return accepted(snapshot, StartVerification("r1"))


def test_completion_happy_path_rechecks_all_gates() -> None:
    snapshot = verifying_snapshot()
    decision = decide(snapshot, CompleteRelease("r1"))
    assert isinstance(decision, Accepted)
    event = decision.events[0]
    assert isinstance(event, ReleaseSucceeded)
    assert {check.obligation_id for check in event.gate.checks} == {
        mk2.GATE_RELEASE_COMPLETION_UNITS_RECHECK,
        mk2.GATE_RELEASE_COMPLETION_CRITERIA_RECORDED,
        mk2.GATE_RELEASE_COMPLETION_ROSTER_RESOLVES,
    }
    after = apply(snapshot, decision.events)
    found = after.release("r1")
    assert found is not None and found.state is ReleaseState.SUCCEEDED


def test_completion_refuses_blocked_member() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.BLOCKED),),
        releases=(release(ReleaseState.VERIFYING),),
    )
    assert refused_codes(snapshot, CompleteRelease("r1")) == {
        "gate.release.member-not-done"
    }


def test_completion_refuses_stale_roster_binding() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.RELEASE_BOUND, version="v2"),),
        releases=(release(ReleaseState.VERIFYING),),
    )
    assert refused_codes(snapshot, CompleteRelease("r1")) == {
        "gate.release.roster-stale"
    }


def test_completion_refuses_missing_criteria() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.RELEASE_BOUND),),
        releases=(release(ReleaseState.VERIFYING, criteria=None),),
    )
    assert refused_codes(snapshot, CompleteRelease("r1")) == {
        "gate.release.criteria-missing"
    }


def test_i3_inversion_protective_closure_never_blocked_by_contamination() -> None:
    refs = (UnitRef("blocked", "v1"), UnitRef("invalid", "v1"), UnitRef("ghost", "v1"))
    snapshot = Snapshot(
        units=(
            unit("blocked", UnitStatus.BLOCKED),
            unit("invalid", UnitStatus.INVALIDATED),
        ),
        releases=(
            Release(
                "r1",
                ReleaseArchetype.OPERATIONAL,
                ReleaseState.DEPLOYING,
                refs,
                CRITERIA,
                None,
            ),
        ),
    )
    for command in (
        RollBack("r1", "operator stop"),
        RetireRelease("r1", "operator stop"),
    ):
        decision = decide(snapshot, command)
        assert isinstance(decision, Accepted), decision
        event = decision.events[0]
        assert isinstance(event, ReleaseRolledBack | ReleaseRetired)
        event_closure = event.closure
        assert event_closure.roster_snapshot == (
            ("blocked", "v1", "blocked"),
            ("ghost", "v1", "missing"),
            ("invalid", "v1", "invalidated"),
        )
    assert refused_codes(snapshot, RollBack("r1", "")) == {
        "gate.release.closure-trigger-required"
    }


def deviation(deviation_id: str, unit_name: str = "u") -> Deviation:
    return Deviation(
        deviation_id,
        DeviationType.PACK_DEFECT,
        UnitRef(unit_name, "v1"),
        DeviationStatus.OPEN,
    )


def test_amendment_invalidates_release_and_second_amendment_is_noop() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.BLOCKED),),
        deviations=(deviation("d1"), deviation("d2")),
        releases=(release(ReleaseState.PREPARED, criteria=None),),
    )
    first = decide(snapshot, AmendDeviation("d1", "v2"))
    assert isinstance(first, Accepted)
    invalidations = [
        event for event in first.events if isinstance(event, ReleaseInvalidated)
    ]
    assert len(invalidations) == 1
    assert invalidations[0].closure is not None
    assert invalidations[0].closure.trigger_kind is ClosureTriggerKind.AMENDMENT
    amended = first.events[0]
    assert isinstance(amended, DeviationAmended)
    assert amended.release_impact_set == ("r1",)
    assert "u" in amended.impact_set
    after = apply(snapshot, first.events)
    after_release = after.release("r1")
    assert after_release is not None
    original_closure = after_release.closure
    second = decide(after, AmendDeviation("d2", "v2"))
    assert isinstance(second, Accepted)
    assert not any(isinstance(event, ReleaseInvalidated) for event in second.events)
    after_second = apply(after, second.events)
    after_second_release = after_second.release("r1")
    assert after_second_release is not None
    assert after_second_release.closure == original_closure


def test_amendment_preserves_operator_closure_on_rolled_back_release() -> None:
    original = closure()
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.BLOCKED),),
        deviations=(deviation("d1"),),
        releases=(release(ReleaseState.ROLLED_BACK, closure=original),),
    )
    decision = decide(snapshot, AmendDeviation("d1", "v2"))
    assert isinstance(decision, Accepted)
    invalidation = next(
        event for event in decision.events if isinstance(event, ReleaseInvalidated)
    )
    assert invalidation.closure is None
    amended = decision.events[0]
    assert isinstance(amended, DeviationAmended)
    assert amended.release_impact_set == ("r1",)
    assert "u" in amended.impact_set
    after = apply(snapshot, decision.events)
    found = after.release("r1")
    assert found is not None
    assert found.state is ReleaseState.INVALIDATED
    assert found.closure == original


RELEASE_COMMANDS: tuple[Command, ...] = (
    StartDeploy("r1"),
    StartVerification("r1"),
    CompleteRelease("r1"),
    RollBack("r1", "trigger"),
    MarkFailedUnrecovered("r1", "trigger"),
    RetireRelease("r1", "trigger"),
)


@pytest.mark.parametrize("state", list(ReleaseState))
@pytest.mark.parametrize("command", RELEASE_COMMANDS, ids=lambda c: type(c).__name__)
def test_release_state_command_matrix(state: ReleaseState, command: Command) -> None:
    existing_closure = closure() if state.value in {
        "rolled-back",
        "failed-unrecovered",
        "retired",
        "invalidated",
    } else None
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.RELEASE_BOUND),),
        releases=(release(state, closure=existing_closure),),
    )
    target = next(
        (
            transition.target
            for transition in mk2.MK2.machine(mk2.RELEASE_O_MACHINE).command_transitions
            if transition.source == state.value
            and transition.command == command.ir_command
        ),
        None,
    )
    decision = decide(snapshot, command)
    if target is None:
        assert isinstance(decision, Refused)
        assert decision.violations[0].code == "transition.illegal"
        return
    assert isinstance(decision, Accepted), decision
    after = apply(snapshot, decision.events)
    found = after.release("r1")
    assert found is not None and found.state.value == target


def test_release_standing_checks_pass_on_legal_snapshots() -> None:
    prepared = prepared_snapshot()
    for evaluator in (
        check_release_escape_property,
        check_release_rosters_resolve,
        check_release_criteria_before_progression,
        check_release_closure_records,
        check_release_bound_units_rostered,
    ):
        assert evaluator(prepared).verdict is Verdict.PASS

    progressed = accepted(prepared, SetReleaseCriteria("r1", CRITERIA))
    progressed = accepted(progressed, StartDeploy("r1"))
    protective = accepted(progressed, RollBack("r1", "operator"))
    for evaluator in (
        check_release_criteria_before_progression,
        check_release_closure_records,
    ):
        assert evaluator(protective).verdict is Verdict.PASS


def test_release_standing_checks_discriminate_drift() -> None:
    dangling = Snapshot(
        releases=(
            release(
                ReleaseState.PREPARED,
                includes=(UnitRef("ghost", "v1"),),
                criteria=None,
            ),
        )
    )
    assert check_release_rosters_resolve(dangling).verdict is Verdict.FAIL

    no_criteria = Snapshot(
        units=(unit("u", UnitStatus.RELEASE_BOUND),),
        releases=(release(ReleaseState.DEPLOYING, criteria=None),),
    )
    assert check_release_criteria_before_progression(no_criteria).verdict is Verdict.FAIL

    succeeded_with_closure = Snapshot(
        units=(unit("u", UnitStatus.RELEASE_BOUND),),
        releases=(release(ReleaseState.SUCCEEDED, closure=closure()),),
    )
    protective_without_closure = Snapshot(
        releases=(release(ReleaseState.RETIRED, closure=None),)
    )
    false_closure = Snapshot(
        releases=(
            release(
                ReleaseState.INVALIDATED,
                criteria=None,
                closure=closure(no_completion_claim=False),
            ),
        )
    )
    for snapshot in (
        succeeded_with_closure,
        protective_without_closure,
        false_closure,
    ):
        assert check_release_closure_records(snapshot).verdict is Verdict.FAIL

    unrostered = Snapshot(units=(unit("u", UnitStatus.RELEASE_BOUND),))
    assert check_release_bound_units_rostered(unrostered).verdict is Verdict.FAIL

    # The escape evaluator's FAIL branch is defensive and documented-unreachable
    # with the actual closed IR; test_mk2_ir.py proves the edge property directly.
    assert check_release_escape_property(Snapshot()).verdict is Verdict.PASS


def test_prepare_refuses_empty_roster() -> None:
    snapshot = Snapshot(
        units=(
            Unit(
                name="u",
                version="v1",
                status=UnitStatus.PUBLISHED,
                acceptance_pack_version="p",
            ),
        )
    )
    decision = decide(snapshot, PrepareRelease(release_id="r1", includes=()))
    assert isinstance(decision, Refused)
    assert decision.violations[0].code == "release.roster-empty"


def test_prepare_refuses_duplicate_roster_members() -> None:
    snapshot = Snapshot(
        units=(
            Unit(
                name="u",
                version="v1",
                status=UnitStatus.PUBLISHED,
                acceptance_pack_version="p",
            ),
        )
    )
    decision = decide(
        snapshot, PrepareRelease(release_id="r1", includes=("u", "u"))
    )
    assert isinstance(decision, Refused)
    assert decision.violations[0].code == "release.roster-duplicate"


def test_escape_property_detects_roster_gated_escapes() -> None:
    # The I3 regression the escape property exists to catch: a protective
    # escape gated on roster health traps a contaminated release. Edge
    # existence alone must not satisfy the property.
    from vouch_mk2.checks.standing import escape_property_violations
    from vouch_mk2.ir import mk2 as mk2_ir
    from vouch_mk2.ir.schema import StateMachine, Transition

    broken = StateMachine(
        name="release-operational",
        states=("prepared", "retired"),
        initial="prepared",
        command_transitions=(
            Transition(
                source="prepared",
                command="retire",
                target="retired",
                gates=(
                    mk2_ir.GATE_RELEASE_CLOSURE_RECORD_COMPLETE,
                    mk2_ir.GATE_RELEASE_COMPLETION_UNITS_RECHECK,
                ),
            ),
        ),
    )
    assert escape_property_violations(
        broken, frozenset({"prepared"}), frozenset({"retired"})
    ) == ("prepared",)
    healthy = StateMachine(
        name="release-operational",
        states=("prepared", "retired"),
        initial="prepared",
        command_transitions=(
            Transition(
                source="prepared",
                command="retire",
                target="retired",
                gates=(mk2_ir.GATE_RELEASE_CLOSURE_RECORD_COMPLETE,),
            ),
        ),
    )
    assert (
        escape_property_violations(
            healthy, frozenset({"prepared"}), frozenset({"retired"})
        )
        == ()
    )


def test_closure_roster_snapshot_correspondence_is_checked() -> None:
    # A fabricated closure record whose snapshot does not correspond to the
    # roster is drift; decide() always snapshots exactly the roster.
    from vouch_mk2.checks.standing import check_release_closure_records

    release = Release(
        release_id="r1",
        archetype=ReleaseArchetype.OPERATIONAL,
        state=ReleaseState.RETIRED,
        includes=(UnitRef(name="u", version="v1"),),
        criteria=None,
        closure=ClosureRecord(
            trigger="operator",
            trigger_kind=ClosureTriggerKind.OPERATOR,
            roster_snapshot=(("someone-else", "v1", "published"),),
            no_completion_claim=True,
            observations=(),
        ),
    )
    snapshot = Snapshot(
        units=(
            Unit(
                name="u",
                version="v1",
                status=UnitStatus.RELEASE_BOUND,
                acceptance_pack_version="p",
            ),
        ),
        releases=(release,),
    )
    found = check_release_closure_records(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "does not correspond" in found.detail
