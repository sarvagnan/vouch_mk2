"""Phase 0 exit criterion: the IR expresses the full 0.6 lifecycle as data.

Two independent sources are diffed against the transcription in
``vouch_mk2.ir.vouch_06``:

1. the vendored, digest-pinned copy of 0.6's machine-readable lifecycle tables
   (``verification-kit/policy/lifecycle-policy.json``, kit-5), and
2. the machine.md §14 terminal-state tables (claim shape + legal predecessors),
   re-transcribed here by hand so the transcription cannot supply its own
   discriminator (cross-cutting discipline 3).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from vouch_mk2.ir import ClaimShape, PolicyIr, StateMachine, validate
from vouch_mk2.ir.vouch_06 import (
    DEVIATION_MACHINE,
    RELEASE_O_MACHINE,
    RELEASE_Q_MACHINE,
    UNIT_MACHINE,
    VOUCH_06,
)

FIXTURE = Path(__file__).parent / "data" / "vouch06-lifecycle-policy.json"
FIXTURE_SHA256 = "7fc1fbf414dfb40ada21b8c002c758775c60aaf07f0e87bdb917666a9ae9842a"

EXPECTED_KEYS = {
    "version",
    "public_assurance_scope",
    "methodology_deltas",
    "unit_statuses",
    "deviation_types",
    "supersession_policies",
    "closure_trigger_kinds",
    "satisfied_dependency_statuses",
    "done_unit_statuses",
    "published_unit_statuses",
    "settled_unit_statuses",
    "marker_unit_statuses",
    "unit_stage_projection",
    "frozen_unit_statuses",
    "release_completion_claim_states",
    "release_protective_states",
    "release_negative_outcome_states",
    "release_active_states",
    "release_model_terminal_states",
    "supersession_no_op_release_states",
    "unit_set_status_transitions",
    "release_transitions",
}


@pytest.fixture(scope="module")
def policy() -> dict[str, Any]:
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256, (
        "vendored 0.6 lifecycle-policy.json changed; re-verify the transcription "
        "against the new tables before updating the pinned digest"
    )
    document = json.loads(raw)
    assert isinstance(document, dict)
    assert set(document.keys()) == EXPECTED_KEYS
    return document


def machine(name: str) -> StateMachine:
    return VOUCH_06.machine(name)


def command_graph(m: StateMachine, command: str) -> dict[str, list[str]]:
    return {
        source: sorted(
            t.target for t in m.command_transitions
            if t.source == source
            and (t.command == command or t.command.startswith(command + ":"))
        )
        for source in m.states
    }


def test_ir_is_structurally_valid() -> None:
    assert validate(VOUCH_06) == ()


def test_unit_states_match(policy: dict[str, Any]) -> None:
    assert list(machine(UNIT_MACHINE).states) == policy["unit_statuses"]


def test_unit_set_status_graph_matches(policy: dict[str, Any]) -> None:
    expected = {
        source: sorted(targets)
        for source, targets in policy["unit_set_status_transitions"].items()
    }
    assert command_graph(machine(UNIT_MACHINE), "set-status") == expected


def test_unit_freeze_is_the_only_other_unit_command() -> None:
    m = machine(UNIT_MACHINE)
    other = [
        t
        for t in m.command_transitions
        if not t.command.startswith("set-status:")
    ]
    assert [(t.source, t.command, t.target) for t in other] == [
        ("specified", "freeze", "acceptance-frozen")
    ]


def test_unit_invalidated_entered_only_by_adjudication() -> None:
    m = machine(UNIT_MACHINE)
    assert m.command_predecessors("invalidated") == ()
    assert [e.target for e in m.adjudication_entries] == ["invalidated"]


def test_release_transitions_match(policy: dict[str, Any]) -> None:
    tables: dict[str, dict[str, list[str]]] = policy["release_transitions"]
    for machine_name, archetype in (
        (RELEASE_O_MACHINE, "operational"),
        (RELEASE_Q_MACHINE, "qualified"),
    ):
        m = machine(machine_name)
        expected = {s: sorted(t) for s, t in tables[archetype].items()}
        assert list(m.states) == list(tables[archetype].keys()), machine_name
        assert command_graph(m, "advance") == expected, machine_name


def test_claim_shapes_match_release_classification(policy: dict[str, Any]) -> None:
    for machine_name, archetype in (
        (RELEASE_O_MACHINE, "operational"),
        (RELEASE_Q_MACHINE, "qualified"),
    ):
        by_shape: dict[ClaimShape, set[str]] = {shape: set() for shape in ClaimShape}
        for rule in VOUCH_06.claim_rules:
            if rule.machine == machine_name:
                by_shape[rule.shape].add(rule.state)
        assert by_shape[ClaimShape.COMPLETION_CLAIM] == set(
            policy["release_completion_claim_states"][archetype]
        ), machine_name
        assert by_shape[ClaimShape.PROTECTIVE_CLOSURE] == set(
            policy["release_protective_states"][archetype]
        ), machine_name
        assert by_shape[ClaimShape.NEGATIVE_EVALUATION_OUTCOME] == set(
            policy["release_negative_outcome_states"][archetype]
        ), machine_name


def test_active_states_are_exactly_the_claimless_states(policy: dict[str, Any]) -> None:
    for machine_name, archetype, set_name in (
        (RELEASE_O_MACHINE, "operational", "release.active.operational"),
        (RELEASE_Q_MACHINE, "qualified", "release.active.qualified"),
    ):
        m = machine(machine_name)
        claimless = {
            s for s in m.states if VOUCH_06.claim_rule(machine_name, s) is None
        }
        assert claimless == set(policy["release_active_states"][archetype])
        assert set(VOUCH_06.state_set(set_name).states) == claimless


def test_model_terminal_state_sets_match(policy: dict[str, Any]) -> None:
    for archetype, set_name in (
        ("operational", "release.model-terminal.operational"),
        ("qualified", "release.model-terminal.qualified"),
    ):
        assert set(VOUCH_06.state_set(set_name).states) == set(
            policy["release_model_terminal_states"][archetype]
        )


def test_supersession_no_op_sets_match(policy: dict[str, Any]) -> None:
    no_op = set(policy["supersession_no_op_release_states"])
    for machine_name, set_name in (
        (RELEASE_O_MACHINE, "release.supersession-no-op.operational"),
        (RELEASE_Q_MACHINE, "release.supersession-no-op.qualified"),
    ):
        m = machine(machine_name)
        assert set(VOUCH_06.state_set(set_name).states) == no_op & set(m.states)
        supersession = [
            e for e in m.adjudication_entries if e.trigger == "supersession"
        ]
        assert len(supersession) == 1
        assert set(supersession[0].excluded_sources) == no_op & set(m.states)


def test_unit_classification_sets_match(policy: dict[str, Any]) -> None:
    for set_name, key in (
        ("unit.satisfied-dependency", "satisfied_dependency_statuses"),
        ("unit.done", "done_unit_statuses"),
        ("unit.published", "published_unit_statuses"),
        ("unit.settled", "settled_unit_statuses"),
        ("unit.marker", "marker_unit_statuses"),
        ("unit.frozen", "frozen_unit_statuses"),
    ):
        assert list(VOUCH_06.state_set(set_name).states) == policy[key], set_name


def test_vocabularies_match(policy: dict[str, Any]) -> None:
    for vocab_name, key in (
        ("deviation-types", "deviation_types"),
        ("supersession-policies", "supersession_policies"),
        ("closure-trigger-kinds", "closure_trigger_kinds"),
    ):
        assert list(VOUCH_06.vocabulary(vocab_name).values) == policy[key], vocab_name


# machine.md §14 terminal-state tables, re-transcribed by hand: state →
# (claim shape, legal command predecessors). `invalidated` has no command
# predecessors — it is entered only by adjudication.
MACHINE_MD_O_TERMINAL = {
    "succeeded": (ClaimShape.COMPLETION_CLAIM, {"verifying"}),
    "rolled-back": (ClaimShape.PROTECTIVE_CLOSURE, {"deploying", "verifying"}),
    "failed-unrecovered": (ClaimShape.PROTECTIVE_CLOSURE, {"rolled-back"}),
    "retired": (
        ClaimShape.PROTECTIVE_CLOSURE,
        {"prepared", "deploying", "verifying", "succeeded"},
    ),
    "invalidated": (ClaimShape.PROTECTIVE_CLOSURE, set[str]()),
}

MACHINE_MD_Q_TERMINAL = {
    "succeeded": (ClaimShape.COMPLETION_CLAIM, {"verifying"}),
    "failed-unrecovered": (ClaimShape.NEGATIVE_EVALUATION_OUTCOME, {"verifying"}),
    "retired": (
        ClaimShape.PROTECTIVE_CLOSURE,
        {"prepared", "verifying", "succeeded", "failed-unrecovered"},
    ),
    "invalidated": (ClaimShape.PROTECTIVE_CLOSURE, set[str]()),
}


@pytest.mark.parametrize(
    ("machine_name", "table"),
    [
        (RELEASE_O_MACHINE, MACHINE_MD_O_TERMINAL),
        (RELEASE_Q_MACHINE, MACHINE_MD_Q_TERMINAL),
    ],
)
def test_machine_md_terminal_tables(
    machine_name: str, table: dict[str, tuple[ClaimShape, set[str]]]
) -> None:
    m = machine(machine_name)
    for state, (shape, predecessors) in table.items():
        rule = VOUCH_06.claim_rule(machine_name, state)
        assert rule is not None, (machine_name, state)
        assert rule.shape is shape, (machine_name, state)
        assert set(m.command_predecessors(state)) == predecessors, (machine_name, state)


def test_invalidated_semantics() -> None:
    # Releases: invalidated is fully terminal. Units: invalidated recovers, but
    # reaches published only through re-verifying (machine.md §11).
    for machine_name in (RELEASE_O_MACHINE, RELEASE_Q_MACHINE):
        assert machine(machine_name).command_targets("invalidated") == ()
    unit = machine(UNIT_MACHINE)
    assert "published" not in unit.command_targets("invalidated")
    assert "published" in unit.command_targets("re-verifying")


def test_every_state_reachable_from_initial() -> None:
    for m in VOUCH_06.machines:
        reachable = {m.initial}
        frontier = [m.initial]
        while frontier:
            current = frontier.pop()
            targets = list(m.command_targets(current)) + [
                e.target
                for e in m.adjudication_entries
                if current in e.sources and current not in e.excluded_sources
            ]
            for target in targets:
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        assert reachable == set(m.states), m.name


def test_command_terminal_states() -> None:
    assert machine(UNIT_MACHINE).command_terminal_states() == ("superseded",)
    assert machine(RELEASE_O_MACHINE).command_terminal_states() == (
        "failed-unrecovered",
        "retired",
        "invalidated",
    )
    assert machine(RELEASE_Q_MACHINE).command_terminal_states() == (
        "retired",
        "invalidated",
    )
    assert machine(DEVIATION_MACHINE).command_terminal_states() == ("closed",)


def test_conformance_matrix_row_count() -> None:
    # machine.md §13 has thirteen control rows; a divergence means the matrix
    # transcription drifted from the source table.
    assert len(VOUCH_06.tier_requirements) == 13


def test_transcription_is_a_policy_ir() -> None:
    assert isinstance(VOUCH_06, PolicyIr)
    assert VOUCH_06.identity.version == "0.6.2/kit-5"
