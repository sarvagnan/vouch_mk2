"""The mk2 IR is valid, conforms to the Phase 0-validated 0.6 lifecycle, and every
gate obligation it declares has exactly one registered evaluator."""

from __future__ import annotations

from vouch_mk2.core import (
    AnchorMode,
    ClosureTriggerKind,
    DeviationStatus,
    DeviationType,
    EvidenceKind,
    EvidenceOutcome,
    ReleaseState,
    Tier,
    UnitStatus,
)
from vouch_mk2.core.decide import GATE_EVALUATORS, RELEASE_GATE_EVALUATORS
from vouch_mk2.ir import validate
from vouch_mk2.ir.mk2 import DEVIATION_MACHINE, MK2, RELEASE_O_MACHINE, UNIT_MACHINE
from vouch_mk2.ir.schema import ObligationScope, StateMachine
from vouch_mk2.ir.vouch_06 import VOUCH_06
from vouch_mk2.ir.vouch_06 import DEVIATION_MACHINE as V06_DEVIATION
from vouch_mk2.ir.vouch_06 import RELEASE_O_MACHINE as V06_RELEASE_O
from vouch_mk2.ir.vouch_06 import UNIT_MACHINE as V06_UNIT


def edge_pairs(machine: StateMachine) -> set[tuple[str, str]]:
    return {(t.source, t.target) for t in machine.command_transitions}


def test_mk2_ir_is_structurally_valid() -> None:
    assert validate(MK2) == ()


def test_unit_states_match_06() -> None:
    assert MK2.machine(UNIT_MACHINE).states == VOUCH_06.machine(V06_UNIT).states


def test_unit_edges_match_06_except_recorded_divergences() -> None:
    # Phase 4 supplies the release-bound entry. mk2 adds exactly two recorded
    # edges (DECISIONS.md D-3): the
    # blocked self-loop making deviation accumulation table-visible, and the
    # invalidated->specified respecify escape for never-frozen units caught in
    # an amendment impact closure.
    mk2_pairs = edge_pairs(MK2.machine(UNIT_MACHINE))
    v06_pairs = edge_pairs(VOUCH_06.machine(V06_UNIT))
    assert v06_pairs - mk2_pairs == set()
    assert mk2_pairs - v06_pairs == {
        ("blocked", "blocked"),
        ("invalidated", "specified"),
    }


def test_deviation_machine_matches_06() -> None:
    assert edge_pairs(MK2.machine(DEVIATION_MACHINE)) == edge_pairs(
        VOUCH_06.machine(V06_DEVIATION)
    )
    assert MK2.machine(DEVIATION_MACHINE).states == VOUCH_06.machine(
        V06_DEVIATION
    ).states


def test_operational_release_edges_match_06() -> None:
    # mk2 adds prepared self-loops over 0.6: configuration is table-visible in
    # prepared (like the blocked open-deviation self-loop) instead of
    # hand-coded legality in decide().
    mk2_pairs = edge_pairs(MK2.machine(RELEASE_O_MACHINE))
    assert mk2_pairs - {("prepared", "prepared")} == edge_pairs(
        VOUCH_06.machine(V06_RELEASE_O)
    )
    assert {
        transition.command
        for transition in MK2.machine(RELEASE_O_MACHINE).command_transitions
        if transition.source == transition.target == "prepared"
    } == {"set-criteria", "bind-bundle"}


def test_operational_release_claim_rules_mirror_06_shapes() -> None:
    states = ("succeeded", "rolled-back", "failed-unrecovered", "retired", "invalidated")
    for state in states:
        mk2_rule = MK2.claim_rule(RELEASE_O_MACHINE, state)
        v06_rule = VOUCH_06.claim_rule(V06_RELEASE_O, state)
        assert mk2_rule is not None and v06_rule is not None
        assert mk2_rule.shape is v06_rule.shape
        assert len(mk2_rule.obligations) == len(v06_rule.obligations) == 1


def test_every_command_has_one_meaning_per_machine() -> None:
    # A command label maps each source state to at most one target — decide()'s
    # (status, command) lookup is unambiguous.
    seen: set[tuple[str, str]] = set()
    for machine in MK2.machines:
        seen = set()
        for transition in machine.command_transitions:
            key = (transition.source, transition.command)
            assert key not in seen, (machine.name, key)
            seen.add(key)


def test_gate_registry_is_complete_both_ways() -> None:
    declared_transition_gates = {
        o.obligation_id
        for o in MK2.obligations
        if o.scope is ObligationScope.TRANSITION_GATE
    }
    bound = {
        gate
        for machine in MK2.machines
        for transition in machine.command_transitions
        for gate in transition.gates
    }
    # Every gate bound to a transition is a declared obligation (schema validation
    # also enforces this) and has an evaluator; every transition-gate obligation
    # and every registered evaluator is actually used. No gate orphans in either
    # direction; standing and organizational obligations are intentionally unbound.
    assert bound == declared_transition_gates
    unit_gates = {gate for gate in bound if not gate.startswith("gate.release.")}
    release_gates = bound - unit_gates
    assert unit_gates == set(GATE_EVALUATORS.keys())
    assert release_gates == set(RELEASE_GATE_EVALUATORS.keys())


def test_every_active_release_state_has_protective_escape() -> None:
    # This is the CI-time guarantee behind the defensive standing evaluator;
    # with the real closed IR its FAIL branch is intentionally unreachable.
    machine = MK2.machine(RELEASE_O_MACHINE)
    active = set(MK2.state_set("release.active.operational").states)
    protective = set(MK2.state_set("release.protective.operational").states)
    for state in active:
        assert any(
            transition.source == state and transition.target in protective
            for transition in machine.command_transitions
        )


def test_satisfied_dependency_set_matches_06() -> None:
    assert (
        MK2.state_set("unit.satisfied-dependency").states
        == VOUCH_06.state_set("unit.satisfied-dependency").states
    )


def test_deviation_types_match_06() -> None:
    assert (
        MK2.vocabulary("deviation-types").values
        == VOUCH_06.vocabulary("deviation-types").values
    )


def test_core_enums_conform_to_ir() -> None:
    # The core enums are the typed face of the IR's closed sets; a member added
    # to one side without the other must fail here, not silently drop matrix
    # rows or become unrepresentable in commands.
    assert {s.value for s in UnitStatus} == set(MK2.machine(UNIT_MACHINE).states)
    assert {s.value for s in DeviationStatus} == set(
        MK2.machine(DEVIATION_MACHINE).states
    )
    assert {t.value for t in DeviationType} == set(
        MK2.vocabulary("deviation-types").values
    )
    assert {s.value for s in ReleaseState} == set(
        MK2.machine(RELEASE_O_MACHINE).states
    )
    assert {kind.value for kind in ClosureTriggerKind} == set(
        MK2.vocabulary("closure-trigger-kinds").values
    )
    assert {kind.value for kind in EvidenceKind} == set(
        MK2.vocabulary("evidence-kinds").values
    )
    assert {outcome.value for outcome in EvidenceOutcome} == set(
        MK2.vocabulary("evidence-outcomes").values
    )
    assert {mode.value for mode in AnchorMode} == set(
        MK2.vocabulary("anchor-modes").values
    )
    assert {tier.value for tier in Tier} == set(MK2.vocabulary("tiers").values)


def test_artifact_obligations_are_exactly_eight_and_never_gates() -> None:
    artifact_ids = {
        obligation.obligation_id
        for obligation in MK2.obligations
        if obligation.scope is ObligationScope.ARTIFACT_CHECK
    }
    assert artifact_ids == {
        "check.evidence-chains",
        "check.evidence-anchors",
        "check.bundle-inventories",
        "check.formal-certificate",
        "check.map-freshness",
        "check.context-freshness",
        "check.pack-files-match",
        "check.event-log",
    }
    bound = {
        gate
        for machine in MK2.machines
        for transition in machine.command_transitions
        for gate in transition.gates
    }
    assert artifact_ids.isdisjoint(bound)


def test_scope_misuse_is_a_named_violation() -> None:
    # A standing, artifact, or organizational obligation bound as a transition gate
    # must be refused by validate() — this is the rule that keeps the check
    # scopes mechanically separate.
    from vouch_mk2.ir.schema import (
        IrIdentity,
        Obligation,
        PolicyIr,
        Transition,
    )

    for scope in (
        ObligationScope.STANDING_CHECK,
        ObligationScope.ARTIFACT_CHECK,
        ObligationScope.ORGANIZATIONAL,
    ):
        broken = PolicyIr(
            identity=IrIdentity(name="broken", version="test", source="test"),
            machines=(
                StateMachine(
                    name="m",
                    states=("a", "b"),
                    initial="a",
                    command_transitions=(
                        Transition(
                            source="a",
                            command="go",
                            target="b",
                            gates=("check.non-gate",),
                        ),
                    ),
                ),
            ),
            obligations=(
                Obligation(
                    obligation_id="check.non-gate",
                    scope=scope,
                    description="misused",
                ),
            ),
        )
        codes = {violation.code for violation in validate(broken)}
        assert "obligation.scope-misuse" in codes
