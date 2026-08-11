"""The mk2 IR is valid, conforms to the Phase 0-validated 0.6 lifecycle, and every
gate obligation it declares has exactly one registered evaluator."""

from __future__ import annotations

from vouch_mk2.core import DeviationStatus, DeviationType, UnitStatus
from vouch_mk2.core.decide import GATE_EVALUATORS
from vouch_mk2.ir import validate
from vouch_mk2.ir.mk2 import DEVIATION_MACHINE, MK2, UNIT_MACHINE
from vouch_mk2.ir.schema import StateMachine
from vouch_mk2.ir.vouch_06 import VOUCH_06
from vouch_mk2.ir.vouch_06 import DEVIATION_MACHINE as V06_DEVIATION
from vouch_mk2.ir.vouch_06 import UNIT_MACHINE as V06_UNIT


def edge_pairs(machine: StateMachine) -> set[tuple[str, str]]:
    return {(t.source, t.target) for t in machine.command_transitions}


def test_mk2_ir_is_structurally_valid() -> None:
    assert validate(MK2) == ()


def test_unit_states_match_06() -> None:
    assert MK2.machine(UNIT_MACHINE).states == VOUCH_06.machine(V06_UNIT).states


def test_unit_edges_match_06_except_recorded_divergences() -> None:
    # Phase 1 defers the release-bound *entry* edge (Phase 4 introduces release
    # commands). mk2 adds exactly two recorded edges (DECISIONS.md D-3): the
    # blocked self-loop making deviation accumulation table-visible, and the
    # invalidated->specified respecify escape for never-frozen units caught in
    # an amendment impact closure.
    mk2_pairs = edge_pairs(MK2.machine(UNIT_MACHINE))
    v06_pairs = edge_pairs(VOUCH_06.machine(V06_UNIT))
    assert v06_pairs - mk2_pairs == {("published", "release-bound")}
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


def test_every_unit_command_has_one_meaning() -> None:
    # A command label maps each source state to at most one target — decide()'s
    # (status, command) lookup is unambiguous.
    seen: set[tuple[str, str]] = set()
    for transition in MK2.machine(UNIT_MACHINE).command_transitions:
        key = (transition.source, transition.command)
        assert key not in seen, key
        seen.add(key)


def test_gate_registry_is_complete_both_ways() -> None:
    declared = {o.obligation_id for o in MK2.obligations}
    bound = {
        gate
        for machine in MK2.machines
        for transition in machine.command_transitions
        for gate in transition.gates
    }
    # Every gate bound to a transition is a declared obligation (schema validation
    # also enforces this) and has an evaluator; every declared obligation and every
    # registered evaluator is actually used. No orphans in any direction.
    assert bound == declared
    assert bound == set(GATE_EVALUATORS.keys())


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
