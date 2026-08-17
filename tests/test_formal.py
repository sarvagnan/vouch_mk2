from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import vouch_mk2.verify as verify_module
from vouch_mk2.core import (
    Accepted,
    RecordFormalCertificate,
    Snapshot,
    Tier,
    Unit,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.formal import (
    CORE_V1,
    HISTORY_V1,
    TIER_V1,
    FormalRun,
    passed,
    run_formal,
    state_fingerprint,
)
from vouch_mk2.formal.explore import Relation
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.witnesses import (
    WITNESSES,
    WitnessBuildError,
    validate_witness_registry,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    DecodeError,
    Store,
    decode_formal_certificate,
    encode_formal_certificate,
    encode_formal_relation,
    encode_formal_tables,
)
from vouch_mk2.verify import (
    build_certificate,
    record_formal_certificate,
    run_artifact_checks,
)


@pytest.fixture(scope="module")
def formal_run() -> FormalRun:
    return run_formal()


def test_core_v1_closes_exhaustively_with_stable_counts(
    formal_run: FormalRun,
) -> None:
    core = formal_run.universe(CORE_V1.name)
    assert core.universe_name == CORE_V1.name == "core-v1"
    assert core.state_count == 264_462
    assert core.transition_count == 1_381_350
    assert len(set(core.relation.states)) == core.state_count
    assert len(set(core.relation.transitions)) == core.transition_count
    assert passed(formal_run)
    assert all(verdict.held for verdict in core.invariants)
    assert all(verdict.discriminated for verdict in formal_run.witnesses)


def test_history_v1_closes_exhaustively_with_stable_counts(
    formal_run: FormalRun,
) -> None:
    history = formal_run.universe(HISTORY_V1.name)
    assert history.universe_name == HISTORY_V1.name == "history-v1"
    assert history.state_count == 31_416
    assert history.transition_count == 94_280
    assert len(set(history.relation.states)) == history.state_count
    assert len(set(history.relation.transitions)) == history.transition_count
    assert all(verdict.held for verdict in history.invariants)
    _regression_tier_v1_covers_legal_lowering_and_committed_refusals(formal_run)


def _assert_respecify_refreeze_republish_reachable(
    formal_run: FormalRun, universe_name: str, unit_name: str
) -> None:
    relation = formal_run.universe(universe_name).relation
    units = tuple(
        unit
        for _fingerprint, snapshot in relation.representatives
        if (unit := snapshot.unit(unit_name)) is not None
    )
    assert any(
        unit.pack_history
        and unit.status is UnitStatus.ACCEPTANCE_FROZEN
        for unit in units
    )
    assert any(
        unit.pack_history and unit.status is UnitStatus.PUBLISHED
        for unit in units
    )


def test_core_v1_keeps_the_respecify_refreeze_loop_reachable(
    formal_run: FormalRun,
) -> None:
    _assert_respecify_refreeze_republish_reachable(
        formal_run, CORE_V1.name, "b"
    )


def test_history_v1_keeps_the_respecify_refreeze_loop_reachable(
    formal_run: FormalRun,
) -> None:
    _assert_respecify_refreeze_republish_reachable(
        formal_run, HISTORY_V1.name, "u"
    )


def test_m6_reports_bounded_label_exhaustion_and_w6_still_discriminates(
    formal_run: FormalRun,
) -> None:
    core_m6 = next(
        verdict
        for verdict in formal_run.universe(CORE_V1.name).invariants
        if verdict.invariant_id == "M6.no-nonterminal-deadlock"
    )
    assert core_m6.held
    assert core_m6.detail.startswith("excluded ")
    assert "universe-bounded: label set exhausted for unit b: 2 labels" in (
        core_m6.detail
    )
    exhausted = Snapshot(
        units=(
            Unit(
                "b",
                "v1",
                UnitStatus.SPECIFIED,
                pack_history=("p-b", "p-b2"),
            ),
        )
    )
    exhausted_fingerprint = state_fingerprint(exhausted)
    bounded_relation = Relation(
        states=(exhausted_fingerprint,),
        transitions=(),
        initial=exhausted_fingerprint,
        representatives=((exhausted_fingerprint, exhausted),),
        offered_freeze_labels=(("b", ("p-b", "p-b2")),),
    )
    m6 = next(
        invariant
        for invariant in INVARIANTS
        if invariant.invariant_id == "M6.no-nonterminal-deadlock"
    )
    assert not m6.check(bounded_relation)
    assert m6.detail is not None
    assert m6.detail(bounded_relation) == (
        "excluded 1 universe-bounded state; universe-bounded: label set "
        "exhausted for unit b: 2 labels (1 state)"
    )
    fresh_label_relation = replace(
        bounded_relation,
        offered_freeze_labels=(("b", ("p-b", "p-b2", "p-b3")),),
    )
    assert m6.check(fresh_label_relation)
    w6 = next(
        verdict
        for verdict in formal_run.witnesses
        if verdict.witness_id == "W6.deadlock"
    )
    assert w6.discriminated


def _regression_tier_v1_covers_legal_lowering_and_committed_refusals(
    formal_run: FormalRun,
) -> None:
    from vouch_mk2.core import Refused, SetTier, Unit, UnitStatus

    tier = formal_run.universe(TIER_V1.name)
    assert tier.universe_name == TIER_V1.name == "tier-v1"
    assert tier.state_count == 66
    assert tier.transition_count == 230
    representatives = dict(tier.relation.representatives)
    lowering = [
        (representatives[source], representatives[target])
        for source, command, target in tier.relation.transitions
        if command == mk2.CMD_SET_TIER
        and representatives[source].tier is Tier.HIGH
        and representatives[target].tier is Tier.LIGHTWEIGHT
    ]
    assert any(
        len(before.units) == 1
        and before.units[0].status is UnitStatus.SPECIFIED
        for before, _after in lowering
    )
    assert not any(
        any(unit.status is not UnitStatus.SPECIFIED for unit in before.units)
        for before, _after in lowering
    )
    committed = Snapshot(
        tier=Tier.HIGH,
        units=(
            Unit(
                "tier-unit",
                "v1",
                UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="pack-v1",
            ),
        ),
    )
    for target in (Tier.STANDARD, Tier.LIGHTWEIGHT):
        decision = decide(committed, SetTier(target))
        assert isinstance(decision, Refused)
        assert decision.violations[0].code == "tier.lowering-refused"


def test_m10_m11_hold_in_their_certified_universes_and_witnesses_discriminate(
    formal_run: FormalRun,
) -> None:
    for universe in formal_run.universes:
        if universe.universe_name == TIER_V1.name:
            continue
        m10 = next(
            verdict
            for verdict in universe.invariants
            if verdict.invariant_id == "M10.one-active-record-per-name"
        )
        assert m10.held
        assert not m10.violations
    w10 = next(
        verdict
        for verdict in formal_run.witnesses
        if verdict.witness_id == "W10.second-active-record"
    )
    assert w10.invariant_id == "M10.one-active-record-per-name"
    assert w10.discriminated
    for universe in formal_run.universes:
        m11 = next(
            verdict
            for verdict in universe.invariants
            if verdict.invariant_id == "M11.tier-never-lowers-after-commitment"
        )
        assert m11.held
        assert not m11.violations
    w11 = next(
        verdict
        for verdict in formal_run.witnesses
        if verdict.witness_id == "W11.tier-lowering"
    )
    assert w11.invariant_id == "M11.tier-never-lowers-after-commitment"
    assert w11.discriminated


def test_core_v1_has_no_truthful_terminal_states(formal_run: FormalRun) -> None:
    relation = formal_run.universe(CORE_V1.name).relation
    sources_with_real_exits = {
        source
        for source, _command, target in relation.transitions
        if source != target
    }
    assert sources_with_real_exits == set(relation.states)


def test_state_fingerprint_ignores_only_revision() -> None:
    baseline = Snapshot(revision=1)
    assert state_fingerprint(baseline) == state_fingerprint(replace(baseline, revision=9))
    assert state_fingerprint(baseline) != state_fingerprint(
        replace(baseline, formal_certificate_digest="f" * 64)
    )
    assert state_fingerprint(baseline) != state_fingerprint(
        replace(baseline, tier=Tier.HIGH)
    )


def test_every_invariant_has_one_private_discriminating_witness(
    formal_run: FormalRun,
) -> None:
    for universe in formal_run.universes:
        expected = (
            {"M11.tier-never-lowers-after-commitment"}
            if universe.universe_name == TIER_V1.name
            else {invariant.invariant_id for invariant in INVARIANTS}
        )
        assert {verdict.invariant_id for verdict in universe.invariants} == expected
    assert {verdict.invariant_id for verdict in formal_run.witnesses} == {
        invariant.invariant_id for invariant in INVARIANTS
    }
    assert len(formal_run.witnesses) == len(WITNESSES) == len(INVARIANTS)


def test_witness_registry_rejects_missing_and_borrowed_mutations() -> None:
    with pytest.raises(WitnessBuildError, match="must not be empty"):
        validate_witness_registry((), ())

    with pytest.raises(WitnessBuildError, match="has 0 witnesses"):
        validate_witness_registry(WITNESSES[:-1])

    borrowed = WITNESSES[:1] + (
        replace(WITNESSES[1], mutate=WITNESSES[0].mutate),
    ) + WITNESSES[2:]
    with pytest.raises(WitnessBuildError, match="borrows a mutation"):
        validate_witness_registry(borrowed)


def test_formal_artifact_encodings_are_stable_and_certificate_is_strict(
    formal_run: FormalRun,
) -> None:
    certificate, data = build_certificate(formal_run)
    assert decode_formal_certificate(data) == certificate
    assert encode_formal_certificate(certificate) == data
    assert encode_formal_tables(formal_run.tables) == encode_formal_tables(
        formal_run.tables
    )
    for universe in formal_run.universes:
        assert encode_formal_relation(
            universe.relation
        ) == encode_formal_relation(universe.relation)
    assert {row[0] for row in certificate.universes} == {
        CORE_V1.name,
        HISTORY_V1.name,
        TIER_V1.name,
    }
    assert {
        row[0]: row[2:] for row in certificate.universes
    } == {
        universe.universe_name: (
            universe.state_count,
            universe.transition_count,
        )
        for universe in formal_run.universes
    }

    document = json.loads(data)
    assert isinstance(document, dict)
    document["surprise"] = True
    noncanonical = json.dumps(document, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(DecodeError, match="surprise"):
        decode_formal_certificate(noncanonical)


def test_recorded_certificate_recomputes_and_stale_certificate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    formal_run: FormalRun,
) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        digest = record_formal_certificate(store, formal_run)
        snapshot = store.read_snapshot()
        assert snapshot.formal_certificate_digest == digest
        certificate = decode_formal_certificate(
            store.get_object(ArtifactKind.FORMAL_CERTIFICATE, digest)
        )
        assert certificate == build_certificate(formal_run)[0]

        monkeypatch.setattr(verify_module, "run_formal", lambda: formal_run)
        passed_result = next(
            result
            for result in run_artifact_checks(store, snapshot)
            if result.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )
        assert passed_result.verdict is Verdict.PASS

        tampered_universes = tuple(
            (
                name,
                ("0" * 64 if name == HISTORY_V1.name else relation_digest),
                state_count,
                transition_count,
            )
            for name, relation_digest, state_count, transition_count in (
                certificate.universes
            )
        )
        tampered_data = encode_formal_certificate(
            replace(certificate, universes=tampered_universes)
        )
        with store.write_locked():
            tampered_digest = store.put_object(
                ArtifactKind.FORMAL_CERTIFICATE, tampered_data
            )
        decision = decide(snapshot, RecordFormalCertificate(tampered_digest))
        assert isinstance(decision, Accepted)
        tampered_snapshot = apply(snapshot, decision.events)
        store.commit(tampered_snapshot)
        tampered_result = next(
            result
            for result in run_artifact_checks(store, tampered_snapshot)
            if result.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )
        assert tampered_result.verdict is Verdict.FAIL
        assert tampered_result.detail.startswith("formal universe relation mismatch: [")
        assert "disagree with recomputation" in tampered_result.detail

        stale_data = encode_formal_certificate(
            replace(certificate, checker_version="stale-checker")
        )
        with store.write_locked():
            stale_digest = store.put_object(
                ArtifactKind.FORMAL_CERTIFICATE, stale_data
            )
        decision = decide(tampered_snapshot, RecordFormalCertificate(stale_digest))
        assert isinstance(decision, Accepted)
        store.commit(apply(tampered_snapshot, decision.events))
        stale_result = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )
        assert stale_result.verdict is Verdict.FAIL
        assert stale_result.detail == "checker_version mismatch"
