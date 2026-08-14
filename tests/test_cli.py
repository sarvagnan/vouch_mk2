from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture

from vouch_mk2.cli import main
from vouch_mk2.core.model import (
    AnchorMode,
    Deviation,
    DeviationStatus,
    DeviationType,
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.core import Accepted, AdvanceAnchor, SetAnchorMode, apply, decide
from vouch_mk2.ir import mk2
from vouch_mk2.store import Store
from vouch_mk2.verify import record_evidence


def init_store(root: Path) -> None:
    store = Store.init(str(root))
    store.close()


def commit_drifted_snapshot(root: Path) -> None:
    # A blocking deviation bound to an in-progress unit: decodable, but
    # unreachable through decide() — the standing checks must catch it.
    with Store.open(str(root)) as store:
        store.commit(
            Snapshot(
                revision=1,
                units=(
                    Unit(
                        name="u",
                        version="v1",
                        status=UnitStatus.IN_PROGRESS,
                        acceptance_pack_version="p1",
                    ),
                ),
                deviations=(
                    Deviation(
                        deviation_id="d",
                        deviation_type=DeviationType.PACK_DEFECT,
                        unit=UnitRef(name="u", version="v1"),
                        status=DeviationStatus.OPEN,
                    ),
                ),
            )
        )


def commit_evidence_snapshot(root: Path, mode: AnchorMode, *, anchor: bool) -> None:
    with Store.open(str(root)) as store:
        store.commit(
            Snapshot(
                revision=1,
                units=(Unit("u", "v1", UnitStatus.SPECIFIED),),
            )
        )
        red_event = EvidenceEvent(
            UnitRef("u", "v1"),
            1,
            "GENESIS",
            EvidenceKind.RED,
            "pytest",
            EvidenceOutcome.TESTS_FAILED,
            "t1",
        )
        red = record_evidence(store, red_event)
        record_evidence(
            store,
            EvidenceEvent(
                UnitRef("u", "v1"),
                2,
                red,
                EvidenceKind.GREEN,
                "pytest",
                EvidenceOutcome.TESTS_PASSED,
                "t2",
            ),
        )
        snapshot = store.read_snapshot()
        mode_decision = decide(snapshot, SetAnchorMode(mode))
        assert isinstance(mode_decision, Accepted)
        store.commit(apply(snapshot, mode_decision.events))
        if anchor:
            snapshot = store.read_snapshot()
            anchor_decision = decide(snapshot, AdvanceAnchor("u"))
            assert isinstance(anchor_decision, Accepted)
            store.commit(apply(snapshot, anchor_decision.events))


def test_cli_check_on_fresh_store_passes(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    assert main(["check", "--ci", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "organizational (never mechanically provable)" in output
    assert output.rstrip().endswith("composed: PASS")


def test_cli_check_on_drifted_store_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_drifted_snapshot(root)
    assert main(["check", "--ci", "--root", str(root)]) == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert mk2.CHECK_BLOCKED_STATUS_COHERENT in output
    assert output.rstrip().endswith("composed: FAIL")


def test_cli_json_matches_text_composition_and_lists_organizational_ids(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_drifted_snapshot(root)

    assert main(["check", "--ci", "--root", str(root)]) == 1
    text_output = capsys.readouterr().out
    text_composed = text_output.rstrip().splitlines()[-1].split(": ", 1)[1].lower()

    assert main(["check", "--ci", "--json", "--root", str(root)]) == 1
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert isinstance(payload, dict)
    assert payload["composed"] == text_composed
    organizational = payload["organizational"]
    assert isinstance(organizational, list)
    organizational_ids = {
        row["check_id"] for row in organizational if isinstance(row, dict)
    }
    assert organizational_ids == {
        mk2.ORG_ACCEPTANCE_OWNERSHIP,
        mk2.ORG_INTENT_VALIDATION,
        mk2.ORG_INDEPENDENCE,
        mk2.ORG_CAUSAL_RED_HISTORY,
        mk2.ORG_PROTECTIVE_CLOSURE_TRUTHFULNESS,
    }


def test_cli_bad_root_returns_typed_transport_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    bad_root = tmp_path / "nonexistent"
    assert main(["check", "--root", str(bad_root)]) == 2
    captured = capsys.readouterr()
    assert captured.err


def test_cli_artifact_rows_appear_in_text_and_json(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_evidence_snapshot(root, AnchorMode.CI, anchor=True)
    assert main(["check", "--ci", "--root", str(root)]) == 0
    text = capsys.readouterr().out
    for check_id in (
        mk2.CHECK_EVIDENCE_CHAINS,
        mk2.CHECK_EVIDENCE_ANCHORS,
        mk2.CHECK_BUNDLE_INVENTORIES,
        mk2.CHECK_FORMAL_CERTIFICATE,
    ):
        assert check_id in text
    assert main(["check", "--ci", "--json", "--root", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = payload["mechanical"]
    ids = {row["check_id"] for row in rows}
    assert {
        mk2.CHECK_EVIDENCE_CHAINS,
        mk2.CHECK_EVIDENCE_ANCHORS,
        mk2.CHECK_BUNDLE_INVENTORIES,
        mk2.CHECK_FORMAL_CERTIFICATE,
    } <= ids


def test_cli_stagnant_ci_anchor_is_not_checked_and_exits_one(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_evidence_snapshot(root, AnchorMode.CI, anchor=False)
    assert main(["check", "--ci", "--root", str(root)]) == 1
    output = capsys.readouterr().out
    assert "NOT-CHECKED" in output
    assert mk2.CHECK_EVIDENCE_ANCHORS in output


def test_cli_none_anchor_is_not_applicable_but_overall_passes(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_evidence_snapshot(root, AnchorMode.NONE, anchor=False)
    assert main(["check", "--ci", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "NOT-APPLICABLE" in output
    assert mk2.CHECK_EVIDENCE_ANCHORS in output
    assert output.rstrip().endswith("composed: PASS")


def test_cli_formal_reports_closed_relation_and_discriminators(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    init_store(root)
    assert main(["formal", "--record", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "states: 96266" in output
    assert "transitions: 540210" in output
    assert "PASS  M1.no-direct-republication" in output
    assert "PASS  M9.anchor-monotonic" in output
    assert "DISCRIMINATED     W1.direct-republication" in output
    assert "DISCRIMINATED     W9.anchor-rotation" in output
    assert "formal certificate: " in output
    with Store.open(str(root)) as store:
        assert len(store.read_snapshot().formal_certificate_digest) == 64


def test_cli_formal_record_requires_explicit_root(
    capsys: CaptureFixture[str],
) -> None:
    assert main(["formal", "--record"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires --root" in captured.err
