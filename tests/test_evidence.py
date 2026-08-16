from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.core import (
    Accepted,
    AdvanceAnchor,
    AnchorMode,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    EvidenceOutcome,
    RecordEvidence,
    Refused,
    SetAnchorMode,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    StoreError,
    decode_evidence_event,
    decode_snapshot,
    digest_of,
    encode_evidence_event,
    encode_snapshot,
)
from vouch_mk2.verify import record_evidence, run_artifact_checks


def _unit(name: str, version: str = "v1") -> Unit:
    return Unit(name=name, version=version, status=UnitStatus.SPECIFIED)


def _event(
    name: str,
    sequence: int,
    parent: str,
    kind: EvidenceKind,
    *,
    version: str = "v1",
    outcome: EvidenceOutcome | None = None,
) -> EvidenceEvent:
    expected = (
        EvidenceOutcome.TESTS_FAILED
        if kind is EvidenceKind.RED
        else EvidenceOutcome.TESTS_PASSED
    )
    return EvidenceEvent(
        unit=UnitRef(name, version),
        sequence=sequence,
        parent=parent,
        kind=kind,
        command_line="uv run pytest",
        outcome=expected if outcome is None else outcome,
        recorded_at=f"t-{sequence}",
    )


def _init(root: Path, units: tuple[Unit, ...] = (_unit("u"),)) -> None:
    with Store.init(str(root)) as store:
        store.commit(Snapshot(revision=1, units=units))


def _result(store: Store, snapshot: Snapshot, check_id: str):
    return next(
        result
        for result in run_artifact_checks(store, snapshot)
        if result.check_id == check_id
    )


def _commit_decision(store: Store, command: AdvanceAnchor | SetAnchorMode) -> None:
    snapshot = store.read_snapshot()
    decision = decide(snapshot, command)
    assert isinstance(decision, Accepted)
    store.commit(apply(snapshot, decision.events))


def test_record_evidence_happy_chain_and_independent_second_unit(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root, (_unit("a"), _unit("b")))
    with Store.open(str(root)) as store:
        a_red = record_evidence(store, _event("a", 1, "GENESIS", EvidenceKind.RED))
        a_green = record_evidence(store, _event("a", 2, a_red, EvidenceKind.GREEN))
        a_refactor = record_evidence(
            store, _event("a", 3, a_green, EvidenceKind.REFACTOR)
        )
        b_red = record_evidence(store, _event("b", 1, "GENESIS", EvidenceKind.RED))
        snapshot = store.read_snapshot()
        assert snapshot.evidence_head("a") == EvidenceHead("a", a_refactor, 3, "GENESIS")
        assert snapshot.evidence_head("b") == EvidenceHead("b", b_red, 1, "GENESIS")
        assert tuple(head.unit_name for head in snapshot.evidence_heads) == ("a", "b")
        assert decode_evidence_event(store.get_object(ArtifactKind.EVIDENCE, a_green)).kind is EvidenceKind.GREEN


@pytest.mark.parametrize(
    ("event", "digest", "code"),
    (
        (_event("u", 1, "f" * 64, EvidenceKind.RED), "a" * 64, "evidence.chain-mismatch"),
        (_event("u", 2, "GENESIS", EvidenceKind.RED), "a" * 64, "evidence.chain-mismatch"),
        (_event("u", 1, "GENESIS", EvidenceKind.RED, version="old"), "a" * 64, "evidence.binding-stale"),
        (_event("u", 1, "GENESIS", EvidenceKind.RED, outcome=EvidenceOutcome.TESTS_PASSED), "a" * 64, "evidence.kind-outcome-mismatch"),
        (_event("u", 1, "GENESIS", EvidenceKind.GREEN, outcome=EvidenceOutcome.TESTS_FAILED), "a" * 64, "evidence.kind-outcome-mismatch"),
        (_event("u", 1, "GENESIS", EvidenceKind.RED), "BAD", "evidence.digest-invalid"),
    ),
)
def test_record_evidence_named_refusals(
    event: EvidenceEvent, digest: str, code: str
) -> None:
    decision = decide(Snapshot(units=(_unit("u"),)), RecordEvidence(event, digest))
    assert isinstance(decision, Refused)
    assert decision.violations[0].code == code


def test_parent_and_sequence_mismatch_after_existing_head() -> None:
    snapshot = Snapshot(
        units=(_unit("u"),),
        evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS"),),
    )
    for event in (
        _event("u", 2, "b" * 64, EvidenceKind.GREEN),
        _event("u", 3, "a" * 64, EvidenceKind.GREEN),
    ):
        decision = decide(snapshot, RecordEvidence(event, "c" * 64))
        assert isinstance(decision, Refused)
        assert decision.violations[0].code == "evidence.chain-mismatch"
        assert "expected" in decision.violations[0].detail


def test_anchor_promotion_and_mode_round_trip() -> None:
    snapshot = Snapshot(units=(_unit("u"),), evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS"),))
    advanced = decide(snapshot, AdvanceAnchor("u"))
    assert isinstance(advanced, Accepted)
    after = apply(snapshot, advanced.events)
    after_head = after.evidence_head("u")
    assert after_head is not None
    assert after_head.anchored == "a" * 64
    current = decide(after, AdvanceAnchor("u"))
    assert isinstance(current, Refused)
    assert current.violations[0].code == "evidence.anchor-current"
    absent = decide(Snapshot(), AdvanceAnchor("u"))
    assert isinstance(absent, Refused)
    assert absent.violations[0].code == "evidence.no-chain"
    same = decide(after, SetAnchorMode(AnchorMode.NONE))
    assert isinstance(same, Accepted)
    assert apply(after, same.events).anchor_mode is AnchorMode.NONE
    ci = decide(after, SetAnchorMode(AnchorMode.CI))
    assert isinstance(ci, Accepted)
    ci_snapshot = apply(after, ci.events)
    none = decide(ci_snapshot, SetAnchorMode(AnchorMode.NONE))
    assert isinstance(none, Accepted)
    assert apply(ci_snapshot, none.events).anchor_mode is AnchorMode.NONE


def test_anchor_monotonicity_by_construction(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root, (_unit("a"), _unit("b")))
    with Store.open(str(root)) as store:
        parents = {"a": "GENESIS", "b": "GENESIS"}
        sequences = {"a": 0, "b": 0}
        for name, action, kind in (
            ("b", "record", EvidenceKind.RED),
            ("a", "record", EvidenceKind.RED),
            ("a", "advance", EvidenceKind.RED),
            ("b", "advance", EvidenceKind.RED),
            ("a", "record", EvidenceKind.GREEN),
            ("b", "record", EvidenceKind.GREEN),
            ("a", "record", EvidenceKind.REFACTOR),
            ("a", "advance", EvidenceKind.RED),
        ):
            if action == "record":
                sequences[name] += 1
                parents[name] = record_evidence(
                    store,
                    _event(name, sequences[name], parents[name], kind),
                )
            else:
                _commit_decision(store, AdvanceAnchor(name))
            snapshot = store.read_snapshot()
            for head in snapshot.evidence_heads:
                ancestors: set[str] = set()
                digest = head.head
                while digest != "GENESIS":
                    ancestors.add(digest)
                    digest = decode_evidence_event(
                        store.get_object(ArtifactKind.EVIDENCE, digest)
                    ).parent
                assert head.anchored == "GENESIS" or head.anchored in ancestors


def test_snapshot_codec_round_trip_with_phase5_fields() -> None:
    snapshot = Snapshot(
        revision=3,
        units=(_unit("u"),),
        evidence_heads=(EvidenceHead("u", "a" * 64, 2, "b" * 64),),
        anchor_mode=AnchorMode.CI,
    )
    assert decode_snapshot(encode_snapshot(snapshot)) == snapshot


def test_artifact_checks_pass_for_complete_anchored_chain(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root)
    with Store.open(str(root)) as store:
        red = record_evidence(store, _event("u", 1, "GENESIS", EvidenceKind.RED))
        record_evidence(store, _event("u", 2, red, EvidenceKind.GREEN))
        _commit_decision(store, SetAnchorMode(AnchorMode.CI))
        _commit_decision(store, AdvanceAnchor("u"))
        results = run_artifact_checks(store, store.read_snapshot())
        by_id = {result.check_id: result for result in results}
        assert by_id[mk2.CHECK_MAP_FRESHNESS].verdict is Verdict.NOT_APPLICABLE
        assert all(
            result.verdict is Verdict.PASS
            for result in results
            if result.check_id
            not in {
                mk2.CHECK_MAP_FRESHNESS,
                mk2.CHECK_PACK_BOUND,
                mk2.CHECK_PACK_FILES_MATCH,
            }
        )


def test_corrupt_evidence_and_absent_head_fail(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root)
    with Store.open(str(root)) as store:
        digest = record_evidence(store, _event("u", 1, "GENESIS", EvidenceKind.RED))
    (root / "objects" / "sha256" / digest).write_bytes(b"corrupt")
    with Store.open(str(root)) as store:
        found = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_CHAINS)
        assert found.verdict is Verdict.FAIL and "u" in found.detail

    missing = "f" * 64
    absent_root = tmp_path / "absent"
    with Store.init(str(absent_root)) as store:
        store.commit(
            Snapshot(
                revision=1,
                units=(_unit("u"),),
                evidence_heads=(EvidenceHead("u", missing, 1, "GENESIS"),),
            )
        )
        found = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_CHAINS)
        assert found.verdict is Verdict.FAIL and "u" in found.detail


@pytest.mark.parametrize("first_kind", (EvidenceKind.GREEN, EvidenceKind.REFACTOR))
def test_green_requires_prior_red_not_refactor(
    tmp_path: Path, first_kind: EvidenceKind
) -> None:
    root = tmp_path / first_kind.value
    _init(root)
    with Store.open(str(root)) as store:
        first = record_evidence(store, _event("u", 1, "GENESIS", first_kind))
        if first_kind is EvidenceKind.REFACTOR:
            record_evidence(store, _event("u", 2, first, EvidenceKind.GREEN))
        found = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_CHAINS)
        assert found.verdict is Verdict.FAIL
        assert "u" in found.detail and "v1" in found.detail


def test_anchor_applicability_stagnation_ancestor_and_dangling(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root)
    with Store.open(str(root)) as store:
        red = record_evidence(store, _event("u", 1, "GENESIS", EvidenceKind.RED))
        green = record_evidence(store, _event("u", 2, red, EvidenceKind.GREEN))
        none = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_ANCHORS)
        assert none.verdict is Verdict.NOT_APPLICABLE
        assert "anchor-mode-is: ci" in none.detail
        _commit_decision(store, SetAnchorMode(AnchorMode.CI))
        stagnant = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_ANCHORS)
        assert stagnant.verdict is Verdict.NOT_CHECKED and "u" in stagnant.detail
        _commit_decision(store, AdvanceAnchor("u"))
        record_evidence(store, _event("u", 3, green, EvidenceKind.REFACTOR))
        ancestor = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_ANCHORS)
        assert ancestor.verdict is Verdict.PASS

        snapshot = store.read_snapshot()
        head = snapshot.evidence_head("u")
        assert head is not None
        store.commit(
            replace(
                snapshot,
                revision=snapshot.revision + 1,
                evidence_heads=(replace(head, anchored="d" * 64),),
            )
        )
        dangling = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_ANCHORS)
        assert dangling.verdict is Verdict.FAIL


def test_anchor_fail_precedes_stagnant_not_checked(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root, (_unit("a"), _unit("b")))
    with Store.open(str(root)) as store:
        record_evidence(store, _event("a", 1, "GENESIS", EvidenceKind.RED))
        record_evidence(store, _event("b", 1, "GENESIS", EvidenceKind.RED))
        _commit_decision(store, SetAnchorMode(AnchorMode.CI))
        snapshot = store.read_snapshot()
        heads = tuple(
            replace(head, anchored="d" * 64) if head.unit_name == "b" else head
            for head in snapshot.evidence_heads
        )
        store.commit(
            replace(
                snapshot,
                revision=snapshot.revision + 1,
                evidence_heads=heads,
            )
        )
        found = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_ANCHORS)
        assert found.verdict is Verdict.FAIL
        assert "a" in found.detail and "b" in found.detail


def test_fifo_planted_over_object_is_refused_not_hung(tmp_path: Path) -> None:
    import os

    from vouch_mk2.store import IntegrityError

    root = tmp_path / "store"
    _init(root)
    with Store.open(str(root)) as store:
        digest = record_evidence(
            store, _event("u", 1, "GENESIS", EvidenceKind.RED)
        )
    object_path = root / "objects" / "sha256" / digest
    os.unlink(object_path)
    os.mkfifo(object_path)
    with Store.open(str(root)) as store:
        with pytest.raises(IntegrityError, match="expected regular file"):
            store.get_object(ArtifactKind.EVIDENCE, digest)
        # The artifact checks surface it as a FAIL, not a hang or a traceback.
        found = _result(store, store.read_snapshot(), mk2.CHECK_EVIDENCE_CHAINS)
        assert found.verdict is Verdict.FAIL


def test_ghost_evidence_head_is_refused_at_store_ingress(tmp_path: Path) -> None:
    # The fabricated-ledger exploit: a head for a unit absent from the
    # snapshot must never produce a clean report.
    root = tmp_path / "store"
    _init(root)
    with Store.open(str(root)) as store:
        digest = record_evidence(
            store, _event("u", 1, "GENESIS", EvidenceKind.RED)
        )
        snapshot = store.read_snapshot()
        ghost = Snapshot(
            revision=snapshot.revision + 1,
            units=(),
            evidence_heads=snapshot.evidence_heads,
        )
        with pytest.raises(StoreError, match="evidence head"):
            store.commit(ghost)
        assert store.read_snapshot() == snapshot
        del digest


def test_stale_version_chain_events_fail_chains_check(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init(root)
    with Store.open(str(root)) as store:
        record_evidence(store, _event("u", 1, "GENESIS", EvidenceKind.RED))
        snapshot = store.read_snapshot()
        bumped = Snapshot(
            revision=snapshot.revision + 1,
            units=(_unit("u", version="v2"),),
            evidence_heads=snapshot.evidence_heads,
        )
        store.commit(bumped)
        found = _result(store, bumped, mk2.CHECK_EVIDENCE_CHAINS)
        assert found.verdict is Verdict.FAIL
        assert "unit is at 'v2'" in found.detail


def test_anchor_advanced_event_is_promotion_only_by_type() -> None:
    # D-4: the event carries no target; apply derives it from the head. An
    # arbitrary-rotation event is unrepresentable, not merely refused.
    from vouch_mk2.core import AnchorAdvanced

    assert not hasattr(AnchorAdvanced(unit_name="u"), "anchored")


def test_non_canonical_evidence_bytes_are_refused() -> None:
    import json as json_module

    from vouch_mk2.store import DecodeError, decode_evidence_event

    canonical = encode_evidence_event(_event("u", 1, "GENESIS", EvidenceKind.RED))
    document = json_module.loads(canonical)
    reordered = json_module.dumps(document, indent=2).encode() + b"\n"
    assert reordered != canonical
    with pytest.raises(DecodeError, match="non-canonical"):
        decode_evidence_event(reordered)
    assert decode_evidence_event(canonical) is not None
