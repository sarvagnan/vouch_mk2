"""Acceptance tests for formal-memory@1 (D-42, settles vouch_mk2-7dr).

The formal certificate hashes each explored relation without building its encoded bytes, the
explorer keeps one string object per state, and the verification layer hashes a relation object at
most once per process. Every count, verdict and digest is unchanged.
"""

from __future__ import annotations

import gc
import tracemalloc
from collections.abc import Iterator
from typing import Self, SupportsIndex, overload, override

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

# The digest of that whole recorded certificate.
_RECORDED_CERTIFICATE_DIGEST = "7b3919fd45fb169c16863f2ae5164287a7e3393df5a5f3db912a9cc357fd689c"

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


class _Tripwire[T](tuple[T, ...]):
    """A tuple that fails when it is read after being armed."""

    armed: bool = False

    @override
    def __iter__(self) -> Iterator[T]:
        if self.armed:
            raise AssertionError("the relation was read again")
        return super().__iter__()

    @overload
    def __getitem__(self, index: SupportsIndex) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    @override
    def __getitem__(self, index: SupportsIndex | slice) -> T | tuple[T, ...]:
        if self.armed:
            raise AssertionError("the relation was read again")
        return super().__getitem__(index)


class _Released(tuple[str, ...]):
    """A tuple that records when it is freed."""

    log: list[str]

    def __new__(cls, items: tuple[str, ...], log: list[str]) -> Self:
        instance = super().__new__(cls, items)
        instance.log = log
        return instance

    def __del__(self) -> None:
        self.log.append("released")


def test_a_relation_already_hashed_is_not_read_again() -> None:
    tier = explore(TIER_V1)
    states = _Tripwire[str](tier.states)
    transitions = _Tripwire[tuple[str, str, str]](tier.transitions)
    relation = Relation(
        states=states,
        transitions=transitions,
        initial=tier.initial,
        representatives=(),
    )
    first = verify_module.formal_relation_digest(relation)
    assert first == _RECORDED_RELATION_DIGESTS["tier-v1"]
    states.armed = True
    transitions.armed = True
    assert verify_module.formal_relation_digest(relation) == first
    run = FormalRun(
        universes=(_universe_run("tier-v1", relation),),
        witnesses=(),
        tables=emit_tables(),
    )
    certificate, _data = build_certificate(run)
    assert certificate.universes == (
        ("tier-v1", first, len(tier.states), len(tier.transitions)),
    )


def test_distinct_relations_keep_their_own_digests() -> None:
    tier = explore(TIER_V1)
    effects = explore(EFFECTS_V1)
    for relation in (tier, effects, tier, effects):
        assert verify_module.formal_relation_digest(relation) == _legacy_digest(relation)
    # Each synthetic relation is dropped before the next is built, so a new relation may reuse
    # an earlier one's object id. Every one must still get its own digest.
    seen: set[str] = set()
    for rows in range(1, 31):
        relation = _synthetic_relation(_AWKWARD, rows)
        digest = verify_module.formal_relation_digest(relation)
        assert digest == _legacy_digest(relation)
        seen.add(digest)
        del relation
        gc.collect()
    assert len(seen) == 30
    # The memo is bounded: once eight newer relations have been hashed, the oldest is released.
    released: list[str] = []
    oldest = Relation(
        states=_Released(_AWKWARD, released),
        transitions=(),
        initial=_AWKWARD[0],
        representatives=(),
    )
    assert verify_module.formal_relation_digest(oldest) == _legacy_digest(oldest)
    del oldest
    newer = [_synthetic_relation(_AWKWARD, rows) for rows in range(31, 39)]
    for relation in newer:
        assert verify_module.formal_relation_digest(relation) == _legacy_digest(relation)
    gc.collect()
    assert released == ["released"]


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


def test_the_formal_run_certifies_the_recorded_relation_digests() -> None:
    run = run_formal()
    certificate, data = build_certificate(run)
    assert {row[0]: row[1] for row in certificate.universes} == _RECORDED_RELATION_DIGESTS
    assert digest_of(ArtifactKind.FORMAL_CERTIFICATE, data) == _RECORDED_CERTIFICATE_DIGEST
