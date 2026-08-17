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
    EvidencePackSummary,
    RecordEvidence,
    Refused,
    SetAnchorMode,
    Snapshot,
    Tier,
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
    return Unit(
        name=name,
        version=version,
        status=UnitStatus.ACCEPTANCE_FROZEN,
        acceptance_pack_version="pack/1",
    )


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
        assert snapshot.evidence_head("a") == EvidenceHead(
            "a",
            a_refactor,
            3,
            "GENESIS",
            "v1",
            (EvidencePackSummary("", True, True),),
        )
        assert snapshot.evidence_head("b") == EvidenceHead(
            "b",
            b_red,
            1,
            "GENESIS",
            "v1",
            (EvidencePackSummary("", True, False),),
        )
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
        evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS", "v1"),),
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
    snapshot = Snapshot(units=(_unit("u"),), evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS", "v1"),))
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
        evidence_heads=(EvidenceHead("u", "a" * 64, 2, "b" * 64, "v1"),),
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
                evidence_heads=(EvidenceHead("u", missing, 1, "GENESIS", "v1"),),
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


def test_snapshot_ingress_rejects_head_for_missing_version(tmp_path: Path) -> None:
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
        with pytest.raises(StoreError, match="evidence.unit-unresolved"):
            store.commit(bumped)


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


def test_legacy_evidence_decodes_as_pre_d17_without_a_pack_digest() -> None:
    legacy = replace(
        _event("u", 1, "GENESIS", EvidenceKind.RED), evidence_format=1
    )
    data = encode_evidence_event(legacy)
    decoded = decode_evidence_event(data)
    assert decoded.evidence_format == 1
    assert decoded.pack_digest == ""
    assert encode_evidence_event(decoded) == data


def test_decide_refuses_evidence_for_a_digest_other_than_the_current_pack() -> None:
    unit = Unit(
        "u",
        "v1",
        UnitStatus.ACCEPTANCE_FROZEN,
        acceptance_pack_version="pack/1",
        acceptance_pack_digest="a" * 64,
    )
    event = replace(
        _event("u", 1, "GENESIS", EvidenceKind.RED),
        pack_digest="b" * 64,
    )
    decision = decide(Snapshot(units=(unit,)), RecordEvidence(event, "c" * 64))
    assert isinstance(decision, Refused)
    assert tuple(v.code for v in decision.violations) == ("evidence.pack-mismatch",)


def test_record_evidence_refuses_specified_unit_with_stale_pack_label() -> None:
    unit = Unit(
        "u",
        "v1",
        UnitStatus.SPECIFIED,
        acceptance_pack_version="stale-pack-label",
    )
    event = _event("u", 1, "GENESIS", EvidenceKind.RED)
    decision = decide(
        Snapshot(units=(unit,)),
        RecordEvidence(event, digest_of(ArtifactKind.EVIDENCE, encode_evidence_event(event))),
    )
    assert isinstance(decision, Refused)
    assert tuple(v.code for v in decision.violations) == ("evidence.no-pack",)


def test_pack_digest_a_to_b_to_a_retains_a_green_summary(tmp_path: Path) -> None:
    from vouch_mk2.core import Publish

    digest_a = "a" * 64
    digest_b = "b" * 64
    root = tmp_path / "digest-back"
    unit = Unit(
        "u",
        "v1",
        UnitStatus.ACCEPTANCE_FROZEN,
        acceptance_pack_version="pack/a",
        acceptance_pack_digest=digest_a,
    )
    with Store.init(str(root)) as store:
        store.commit(Snapshot(revision=1, tier=Tier.STANDARD, units=(unit,)))
        red_a = record_evidence(
            store,
            replace(_event("u", 1, "GENESIS", EvidenceKind.RED), pack_digest=digest_a),
        )
        record_evidence(
            store,
            replace(_event("u", 2, red_a, EvidenceKind.GREEN), pack_digest=digest_a),
        )
        snapshot = store.read_snapshot()
        store.commit(
            replace(
                snapshot,
                revision=snapshot.revision + 1,
                units=(
                    replace(
                        unit,
                        acceptance_pack_version="pack/b",
                        acceptance_pack_digest=digest_b,
                        acceptance_test_set_digest=digest_b,
                    ),
                ),
            )
        )
        head = store.read_snapshot().evidence_head("u")
        assert head is not None
        red_b = record_evidence(
            store,
            replace(
                _event("u", 3, head.head, EvidenceKind.RED),
                pack_digest=digest_b,
            ),
        )
        record_evidence(
            store,
            replace(_event("u", 4, red_b, EvidenceKind.GREEN), pack_digest=digest_b),
        )
        snapshot = store.read_snapshot()
        done = replace(
            snapshot,
            units=(
                replace(
                    unit,
                    status=UnitStatus.DONE_PENDING_ACCEPTANCE,
                    acceptance_pack_version="pack/a-again",
                ),
            ),
        )
    assert isinstance(decide(done, Publish("u")), Accepted)


def test_all_legacy_chain_rejects_forged_cached_summary(tmp_path: Path) -> None:
    from vouch_mk2.core import EvidencePackSummary

    legacy = replace(
        _event("u", 1, "GENESIS", EvidenceKind.RED), evidence_format=1
    )
    data = encode_evidence_event(legacy)
    digest = digest_of(ArtifactKind.EVIDENCE, data)
    snapshot = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="pack/1",
                acceptance_pack_digest="a" * 64,
            ),
        ),
        evidence_heads=(
            EvidenceHead(
                "u",
                digest,
                1,
                "GENESIS",
                "v1",
                (EvidencePackSummary("", True, True),),
            ),
        ),
    )
    with Store.init(str(tmp_path / "legacy-forged")) as store:
        store.commit(snapshot, objects=((ArtifactKind.EVIDENCE, data),))
        result = _result(store, snapshot, mk2.CHECK_EVIDENCE_CHAINS)
    assert result.verdict is Verdict.FAIL
    assert "u@v1" in result.detail and "summary" in result.detail


def test_cached_pack_digest_set_must_match_the_nonlegacy_chain(tmp_path: Path) -> None:
    from vouch_mk2.core import EvidencePackSummary

    event = replace(
        _event("u", 1, "GENESIS", EvidenceKind.RED), pack_digest="a" * 64
    )
    data = encode_evidence_event(event)
    digest = digest_of(ArtifactKind.EVIDENCE, data)
    snapshot = Snapshot(
        revision=1,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="pack/a",
                acceptance_pack_digest="a" * 64,
            ),
        ),
        evidence_heads=(
            EvidenceHead(
                "u",
                digest,
                1,
                "GENESIS",
                "v1",
                (EvidencePackSummary("b" * 64, True, False),),
            ),
        ),
    )
    with Store.init(str(tmp_path / "wrong-digest")) as store:
        store.commit(snapshot, objects=((ArtifactKind.EVIDENCE, data),))
        result = _result(store, snapshot, mk2.CHECK_EVIDENCE_CHAINS)
    assert result.verdict is Verdict.FAIL
    assert "u@v1" in result.detail and "summary" in result.detail


def test_chain_check_validates_every_cached_pack_summary(tmp_path: Path) -> None:
    from vouch_mk2.core import EvidencePackSummary

    digest_a = "a" * 64
    digest_b = "b" * 64
    events: list[tuple[EvidenceEvent, bytes, str]] = []
    parent = "GENESIS"
    for sequence, (pack_digest, kind) in enumerate(
        (
            (digest_a, EvidenceKind.RED),
            (digest_a, EvidenceKind.GREEN),
            (digest_b, EvidenceKind.RED),
            (digest_b, EvidenceKind.GREEN),
        ),
        start=1,
    ):
        event = replace(
            _event("u", sequence, parent, kind), pack_digest=pack_digest
        )
        data = encode_evidence_event(event)
        parent = digest_of(ArtifactKind.EVIDENCE, data)
        events.append((event, data, parent))
    snapshot = Snapshot(
        revision=1,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="pack/b",
                acceptance_pack_digest=digest_b,
            ),
        ),
        evidence_heads=(
            EvidenceHead(
                "u",
                parent,
                4,
                "GENESIS",
                "v1",
                (
                    EvidencePackSummary(digest_a, False, False),
                    EvidencePackSummary(digest_b, True, True),
                ),
            ),
        ),
    )
    objects = tuple((ArtifactKind.EVIDENCE, data) for _event_value, data, _ in events)
    with Store.init(str(tmp_path / "all-summaries")) as store:
        store.commit(snapshot, objects=objects)
        result = _result(store, snapshot, mk2.CHECK_EVIDENCE_CHAINS)
    assert result.verdict is Verdict.FAIL
    assert "u@v1" in result.detail and "summary" in result.detail


@pytest.mark.parametrize("tier", (Tier.STANDARD, Tier.HIGH))
def test_mixed_legacy_chain_is_reported_for_nonpublished_green_unit(
    tmp_path: Path, tier: Tier
) -> None:
    from vouch_mk2.core import EvidencePackSummary

    pack_digest = "a" * 64
    rows: list[tuple[bytes, str]] = []
    parent = "GENESIS"
    for sequence, (evidence_format, kind) in enumerate(
        (
            (1, EvidenceKind.RED),
            (1, EvidenceKind.GREEN),
            (2, EvidenceKind.RED),
            (2, EvidenceKind.GREEN),
        ),
        start=1,
    ):
        event = replace(
            _event("u", sequence, parent, kind),
            evidence_format=evidence_format,
            pack_digest=pack_digest if evidence_format == 2 else "",
        )
        data = encode_evidence_event(event)
        parent = digest_of(ArtifactKind.EVIDENCE, data)
        rows.append((data, parent))
    snapshot = Snapshot(
        revision=1,
        tier=tier,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="pack/a",
                acceptance_pack_digest=pack_digest,
            ),
        ),
        evidence_heads=(
            EvidenceHead(
                "u",
                parent,
                4,
                "GENESIS",
                "v1",
                (EvidencePackSummary(pack_digest, True, True),),
            ),
        ),
    )
    with Store.init(str(tmp_path / tier.value)) as store:
        store.commit(
            snapshot,
            objects=tuple((ArtifactKind.EVIDENCE, data) for data, _ in rows),
        )
        result = _result(store, snapshot, mk2.CHECK_EVIDENCE_CHAINS)
    assert result.verdict is Verdict.PASS
    assert "pre-D-17 evidence" in result.detail and "u@v1" in result.detail


def test_pre_d17_notice_survives_a_current_pack_failure(tmp_path: Path) -> None:
    from vouch_mk2.core import EvidencePackSummary

    pack_digest = "a" * 64
    legacy = replace(
        _event("u", 1, "GENESIS", EvidenceKind.RED), evidence_format=1
    )
    legacy_data = encode_evidence_event(legacy)
    legacy_digest = digest_of(ArtifactKind.EVIDENCE, legacy_data)
    current = replace(
        _event("u", 2, legacy_digest, EvidenceKind.RED), pack_digest=pack_digest
    )
    current_data = encode_evidence_event(current)
    current_digest = digest_of(ArtifactKind.EVIDENCE, current_data)
    snapshot = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.PUBLISHED,
                acceptance_pack_version="pack/a",
                published_once=True,
                acceptance_pack_digest=pack_digest,
            ),
        ),
        evidence_heads=(
            EvidenceHead(
                "u",
                current_digest,
                2,
                "GENESIS",
                "v1",
                (EvidencePackSummary(pack_digest, True, False),),
            ),
        ),
    )
    with Store.init(str(tmp_path / "legacy-and-failure")) as store:
        store.commit(
            snapshot,
            objects=(
                (ArtifactKind.EVIDENCE, legacy_data),
                (ArtifactKind.EVIDENCE, current_data),
            ),
        )
        result = _result(store, snapshot, mk2.CHECK_EVIDENCE_CHAINS)
    assert result.verdict is Verdict.FAIL
    assert "pre-D-17 evidence" in result.detail
    assert "latest evidence for the current pack is not GREEN" in result.detail


def test_pre_d17_chain_is_reported_without_failing_a_published_unit(
    tmp_path: Path,
) -> None:
    red_event = replace(
        _event("u", 1, "GENESIS", EvidenceKind.RED), evidence_format=1
    )
    red_data = encode_evidence_event(red_event)
    red_digest = digest_of(ArtifactKind.EVIDENCE, red_data)
    green_event = replace(
        _event("u", 2, red_digest, EvidenceKind.GREEN), evidence_format=1
    )
    green_data = encode_evidence_event(green_event)
    green_digest = digest_of(ArtifactKind.EVIDENCE, green_data)
    snapshot = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.PUBLISHED,
                acceptance_pack_version="pack/1",
                published_once=True,
                acceptance_pack_digest="a" * 64,
            ),
        ),
        evidence_heads=(EvidenceHead("u", green_digest, 2, "GENESIS", "v1"),),
    )
    with Store.init(str(tmp_path / "legacy")) as store:
        store.commit(
            snapshot,
            objects=(
                (ArtifactKind.EVIDENCE, red_data),
                (ArtifactKind.EVIDENCE, green_data),
            ),
        )
        result = _result(store, snapshot, mk2.CHECK_EVIDENCE_CHAINS)
    assert result.verdict is Verdict.PASS
    assert "pre-D-17 evidence" in result.detail and "u@v1" in result.detail
