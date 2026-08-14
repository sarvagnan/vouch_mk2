from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import vouch_mk2.verify as verify_module
from vouch_mk2.core import Accepted, RecordFormalCertificate, Snapshot, apply, decide
from vouch_mk2.formal import CORE_V1, FormalRun, passed, run_formal, state_fingerprint
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
    assert formal_run.universe_name == CORE_V1.name == "core-v1"
    assert formal_run.state_count == 96_266
    assert formal_run.transition_count == 540_210
    assert len(set(formal_run.relation.states)) == formal_run.state_count
    assert len(set(formal_run.relation.transitions)) == formal_run.transition_count
    assert passed(formal_run)
    assert all(verdict.held for verdict in formal_run.invariants)
    assert all(verdict.discriminated for verdict in formal_run.witnesses)


def test_core_v1_has_no_truthful_terminal_states(formal_run: FormalRun) -> None:
    sources_with_real_exits = {
        source
        for source, _command, target in formal_run.relation.transitions
        if source != target
    }
    assert sources_with_real_exits == set(formal_run.relation.states)


def test_state_fingerprint_ignores_only_revision() -> None:
    baseline = Snapshot(revision=1)
    assert state_fingerprint(baseline) == state_fingerprint(replace(baseline, revision=9))
    assert state_fingerprint(baseline) != state_fingerprint(
        replace(baseline, formal_certificate_digest="f" * 64)
    )


def test_every_invariant_has_one_private_discriminating_witness(
    formal_run: FormalRun,
) -> None:
    assert {verdict.invariant_id for verdict in formal_run.invariants} == {
        invariant.invariant_id for invariant in INVARIANTS
    }
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
    assert encode_formal_relation(formal_run.relation) == encode_formal_relation(
        formal_run.relation
    )

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

        stale_data = encode_formal_certificate(
            replace(certificate, checker_version="stale-checker")
        )
        stale_digest = store.put_object(ArtifactKind.FORMAL_CERTIFICATE, stale_data)
        decision = decide(snapshot, RecordFormalCertificate(stale_digest))
        assert isinstance(decision, Accepted)
        store.commit(apply(snapshot, decision.events))
        stale_result = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )
        assert stale_result.verdict is Verdict.FAIL
        assert stale_result.detail == "checker_version mismatch"
