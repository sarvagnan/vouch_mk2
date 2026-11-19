"""Acceptance tests for formal-memory@1 (D-42, settles vouch_mk2-7dr).

The formal certificate hashes each explored relation without building its encoded bytes, the
explorer keeps one string object per state, and a formal run carries each relation's digest so a
certificate build does not hash a relation again. Every count, verdict and digest is unchanged.
"""

from __future__ import annotations

import gc
import tracemalloc
from dataclasses import replace

import pytest

import vouch_mk2.store.codec as codec_module
import vouch_mk2.verify as verify_module
from vouch_mk2.formal.explore import Relation, emit_tables, explore
from vouch_mk2.formal.run import FormalRun, UniverseRun, run_formal
from vouch_mk2.formal.universe import EFFECTS_V1, HISTORY_V1, TIER_V1
from vouch_mk2.store.codec import encode_formal_relation
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.verify import build_certificate

# The relation digests in the formal certificate recorded in mk2's own store (checker
# formal-checker-v9). They must not move.
_RECORDED_RELATION_DIGESTS = {
    "core-v1": "6f6ff0105ee23133eb0a0a8f254703da6dd8d32d98162d9c28c1e44f306beb60",
    "effects-v1": "af0736c6eaa83131b11ba402e3fbb89e69d935905f82eaa300867a0acff962df",
    "history-v1": "eff8e3a3ec66ad0a4b543bff6a80bdb48cd65416f7076e7ad0137eda46929c39",
    "tier-v1": "5822a72ba21a762f5b1531949bdd1d814509bfaeeaaf0066805b37d4d9ad422e",
}

_AWKWARD = (
    'quote " backslash \\ slash /',
    "controls \n \t \r \x00 \x01 \x1f \x7f",
    "non-ascii é ü 中文 \U0001f600    ",
    "",
)


def _legacy_digest(relation: Relation) -> str:
    return digest_of(ArtifactKind.FORMAL_RELATION, encode_formal_relation(relation))


def _synthetic_relation(states: tuple[str, ...], rows: int) -> Relation:
    commands = ("cmd-a", "cmd-b", "cmd-c")
    count = len(states)
    transitions = tuple(
        sorted(
            {
                (
                    states[i % count],
                    commands[(i // count) % 3],
                    states[(i % count + i // count + 1) % count],
                )
                for i in range(rows)
            }
        )
    )
    return Relation(
        states=tuple(sorted(states)),
        transitions=transitions,
        initial=states[0],
        representatives=(),
    )


def _universe_run(name: str, relation: Relation) -> UniverseRun:
    return UniverseRun(
        universe_name=name,
        state_count=len(relation.states),
        transition_count=len(relation.transitions),
        invariants=(),
        relation=relation,
    )


def _boom(*_args: object, **_kwargs: object) -> bytes:
    raise AssertionError("the encoded relation was built")


def test_streamed_relation_digest_matches_the_encoded_relation() -> None:
    relations = [
        explore(TIER_V1),
        explore(EFFECTS_V1),
        _synthetic_relation(_AWKWARD, 12),
        _synthetic_relation(("only",), 0),
        Relation(states=("s",), transitions=(), initial="s", representatives=()),
    ]
    for relation in relations:
        assert verify_module.formal_relation_digest(relation) == _legacy_digest(relation)


def test_streamed_relation_digest_does_not_build_the_encoded_relation() -> None:
    states = tuple(f"state-{i:05d}-" + "x" * 800 for i in range(4000))
    relation = _synthetic_relation(states, 80_000)
    gc.collect()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        digest = verify_module.formal_relation_digest(relation)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(digest) == 64
    assert len(relation.transitions) == 80_000
    assert peak < 8 * 1024 * 1024


def test_certificate_hashes_relations_without_encoding_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tier = explore(TIER_V1)
    effects = explore(EFFECTS_V1)
    expected = {"tier-v1": _legacy_digest(tier), "effects-v1": _legacy_digest(effects)}
    monkeypatch.setattr(codec_module, "encode_formal_relation", _boom)
    monkeypatch.setattr(verify_module, "encode_formal_relation", _boom, raising=False)
    run = FormalRun(
        universes=(_universe_run("tier-v1", tier), _universe_run("effects-v1", effects)),
        witnesses=(),
        tables=emit_tables(),
    )
    certificate, _data = build_certificate(run)
    assert {row[0]: row[1] for row in certificate.universes} == expected


def test_certificate_uses_the_digest_a_run_carries() -> None:
    tier = explore(TIER_V1)
    carried = "a" * 64
    run = FormalRun(
        universes=(replace(_universe_run("tier-v1", tier), relation_digest=carried),),
        witnesses=(),
        tables=emit_tables(),
    )
    certificate, _data = build_certificate(run)
    assert certificate.universes == (
        ("tier-v1", carried, len(tier.states), len(tier.transitions)),
    )


def test_a_universe_run_without_a_carried_digest_still_certifies() -> None:
    tier = explore(TIER_V1)
    run = FormalRun(
        universes=(_universe_run("tier-v1", tier),),
        witnesses=(),
        tables=emit_tables(),
    )
    assert _universe_run("tier-v1", tier).relation_digest == ""
    certificate, _data = build_certificate(run)
    assert certificate.universes[0][1] == _RECORDED_RELATION_DIGESTS["tier-v1"]


def test_explored_transitions_share_each_state_string() -> None:
    for universe in (TIER_V1, EFFECTS_V1, HISTORY_V1):
        relation = explore(universe)
        canonical = {state: state for state in relation.states}
        assert len(canonical) == len(relation.states)
        assert relation.initial is canonical[relation.initial]
        for source, _command, target in relation.transitions:
            assert source is canonical[source]
            assert target is canonical[target]
        for state, _snapshot in relation.representatives:
            assert state is canonical[state]


def test_formal_run_carries_the_recorded_relation_digests() -> None:
    run = run_formal()
    assert {
        universe.universe_name: universe.relation_digest for universe in run.universes
    } == _RECORDED_RELATION_DIGESTS
    certificate, _data = build_certificate(run)
    assert {row[0]: row[1] for row in certificate.universes} == _RECORDED_RELATION_DIGESTS
