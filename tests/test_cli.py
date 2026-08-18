from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture

from vouch_mk2.cli import main
from vouch_mk2.core.model import (
    AnchorMode,
    CiPolicy,
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
from vouch_mk2.core import (
    Accepted,
    AdvanceAnchor,
    PreLogHistoryRecorded,
    SetAnchorMode,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.store import Store
from vouch_mk2.verify import record_evidence
from vouch_mk2.store.event_log import build_event_records, commit_events
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


def init_store(root: Path) -> None:
    store = Store.init(str(root))
    store.close()


def commit_drifted_snapshot(root: Path) -> None:
    # A blocking deviation bound to an in-progress unit: decodable, but
    # unreachable through decide() — the standing checks must catch it.
    with Store.open(str(root)) as store:
        commit_snapshot_fixture(
            store,
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
            ),
        )


def commit_evidence_snapshot(root: Path, mode: AnchorMode, *, anchor: bool) -> None:
    with Store.open(str(root)) as store:
        baseline = Snapshot(
            revision=1,
            ci_policy=CiPolicy.LEDGER,
            units=(
                Unit(
                    "u",
                    "v1",
                    UnitStatus.ACCEPTANCE_FROZEN,
                    acceptance_pack_version="p1",
                ),
            ),
        )
        checkpoint, objects, _records = build_event_records(
            Snapshot(),
            (
                PreLogHistoryRecorded(
                    "pre-log revisions 1..1: test fixture only",
                    baseline,
                ),
            ),
            actor="test",
            run_id="",
        )
        store.commit(checkpoint, objects=objects, expected_revision=0)
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
        commit_events(
            store,
            snapshot,
            mode_decision.events,
            actor="test",
            run_id="",
        )
        if anchor:
            snapshot = store.read_snapshot()
            anchor_decision = decide(snapshot, AdvanceAnchor("u"))
            assert isinstance(anchor_decision, Accepted)
            commit_events(
                store,
                snapshot,
                anchor_decision.events,
                actor="test",
                run_id="",
            )


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
        mk2.ORG_TIER_CONTROLS,
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
        mk2.CHECK_MAP_FRESHNESS,
        mk2.CHECK_CONTEXT_FRESHNESS,
        mk2.CHECK_PACK_FILES_MATCH,
        mk2.CHECK_EVENT_LOG,
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
        mk2.CHECK_MAP_FRESHNESS,
        mk2.CHECK_CONTEXT_FRESHNESS,
        mk2.CHECK_PACK_FILES_MATCH,
        mk2.CHECK_EVENT_LOG,
    } <= ids


def test_cli_projection_commands_and_generate_then_check(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)

    assert main(["generate", "--root", str(root)]) == 0
    generated = capsys.readouterr().out
    assert "map.json and vouch-context.md written (source state " in generated
    assert (root / "map.json").is_file()
    assert (root / "vouch-context.md").is_file()

    assert main(["check", "--ci", "--root", str(root)]) == 0
    assert mk2.CHECK_MAP_FRESHNESS in capsys.readouterr().out

    assert main(["next", "--root", str(root)]) == 0
    bootstrap = capsys.readouterr().out
    assert "specify" in bootstrap
    assert "<UNIT_NAME>" in bootstrap

    assert main(["status", "--root", str(root)]) == 0
    status = capsys.readouterr().out
    assert "unit_counts: \n" in status
    assert "next_action_count: 1\n" in status

    assert main(["emit-context", "--root", str(root)]) == 0
    written = Path(capsys.readouterr().out.strip())
    assert written == root / "vouch-context.md"
    assert written.is_file()


def test_cli_emit_context_uses_suggestion_sidecar(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    target = tmp_path / "AGENTS.md"
    init_store(root)
    target.write_text("human context\n", encoding="utf-8")
    assert main(
        ["emit-context", "--root", str(root), "--target", str(target)]
    ) == 0
    written = Path(capsys.readouterr().out.strip())
    assert written == Path(str(target) + ".vouch-suggested")
    assert target.read_text(encoding="utf-8") == "human context\n"
    assert written.is_file()


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
    assert "states: 264462" in output
    assert "transitions: 1381350" in output
    assert "universe: tier-v1" in output
    assert "states: 246" in output
    assert "transitions: 1640" in output
    assert "PASS  M1.no-direct-republication" in output
    assert "universe-bounded: label set exhausted for unit b: 2 labels" in output
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


def test_store_checked_out_without_runtime_lock_file_opens_and_checks(tmp_path: Path) -> None:
    # A store checked out from version control arrives WITHOUT .lock (it is
    # gitignored as a runtime primitive). Read verbs must not create it — this
    # is exactly the shape CI sees for the committed dogfood store.
    import shutil

    src = Path(__file__).resolve().parents[1] / ".vouch"
    dst = tmp_path / "checkout"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".lock"))
    assert not (dst / ".lock").exists()
    # A copied store has no project context; at tier standard the pack-files check needs it.
    assert main(["check", "--ci", "--root", str(dst), "--project", str(src.parent)]) == 0
    assert not (dst / ".lock").exists()
    # A planted symlink at .lock is still refused, not followed.
    (dst / ".lock").symlink_to(dst / "state.json")
    assert main(["check", "--ci", "--root", str(dst), "--project", str(src.parent)]) != 0


def test_dogfood_store_is_committed_checkable_and_honest(capsys: CaptureFixture[str]) -> None:
    # D-10: mk2's own committed store must pass check --ci with zero mechanical
    # NOT_CHECKED and every organizational row visibly NOT_CHECKED. Tier controls
    # deliberately render one row per control so none is hidden in a long detail.
    root = Path(__file__).resolve().parents[1] / ".vouch"
    assert (root / "state.json").exists(), "the dogfood store must be committed"
    assert main(["check", "--ci", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("composed: PASS")
    mechanical_not_checked = [
        line for line in out.splitlines()
        if line.startswith("NOT-CHECKED") and line.split()[1].startswith("check.")
    ]
    assert mechanical_not_checked == []
    org_rows = [line for line in out.splitlines() if line.startswith("NOT-CHECKED") and "org." in line]
    tier_rows = [line for line in org_rows if "org.tier-controls" in line]
    assert len(org_rows) == 5 + len(tier_rows)
    assert len(tier_rows) > 1
