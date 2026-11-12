from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

from pytest import CaptureFixture

from vouch_mk2.checks import CheckReport, CheckResult
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
    BindPack,
    EffectsRequired,
    FreezeAcceptance,
    PackEntry,
    PackInventory,
    RebindPack,
    Refused,
    SpecifyUnit,
    Tier,
    AdvanceAnchor,
    PreLogHistoryRecorded,
    SetAnchorMode,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.projections import system_map
from vouch_mk2.store import (
    Store, decode_snapshot, decode_system_map, encode_snapshot, encode_system_map,
)
from vouch_mk2.verify import run_artifact_checks
from vouch_mk2.verify import pending_anchor_is_only_unchecked, record_evidence
from vouch_mk2.store.event_log import build_event_records, commit_events
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


def init_store(root: Path) -> None:
    store = Store.init(str(root))
    store.close()


def test_pending_anchor_tolerance_rejects_another_not_checked_row() -> None:
    report = CheckReport(
        revision=1,
        mechanical=(
            CheckResult(
                mk2.CHECK_EVIDENCE_ANCHORS,
                Verdict.NOT_CHECKED,
                "unit 'u' anchor has no workflow run claim",
            ),
            CheckResult(
                mk2.CHECK_EVENT_LOG,
                Verdict.NOT_CHECKED,
                "another mechanical row was not checked",
            ),
        ),
        organizational=(),
    )
    assert not pending_anchor_is_only_unchecked(report)


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
            anchor_decision = decide(snapshot, AdvanceAnchor("u", "fixture-run"))
            assert isinstance(anchor_decision, Accepted)
            commit_events(
                store,
                snapshot,
                anchor_decision.events,
                actor="test",
                run_id="fixture-run",
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
        mk2.ORG_EFFECTS_TRUTH,
    }


def test_cli_bad_root_returns_typed_transport_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    bad_root = tmp_path / "nonexistent"
    assert main(["check", "--root", str(bad_root)]) == 2
    captured = capsys.readouterr()
    assert captured.err


def test_cli_artifact_rows_appear_in_text_and_json(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _regression_effect_declarations(tmp_path / "effects-regressions")
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
    assert "universe: effects-v1" in output
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


def _assert_clean_or_pending_ci_anchor(exit_code: int, output: str) -> None:
    mechanical_fail = [
        line
        for line in output.splitlines()
        if line.startswith("FAIL") and line.split()[1].startswith("check.")
    ]
    assert mechanical_fail == [], "the dogfood store must refuse every mechanical FAIL row"

    mechanical_not_checked = [
        line
        for line in output.splitlines()
        if line.startswith("NOT-CHECKED") and line.split()[1].startswith("check.")
    ]
    if mechanical_not_checked:
        assert len(mechanical_not_checked) == 1
        pending = mechanical_not_checked[0]
        assert pending.split()[1] == mk2.CHECK_EVIDENCE_ANCHORS
        assert "no workflow run claim" in pending
        assert (exit_code, output.rstrip().splitlines()[-1]) == (
            1,
            "composed: NOT-CHECKED",
        )
    else:
        assert (exit_code, output.rstrip().splitlines()[-1]) == (0, "composed: PASS")


def test_store_checked_out_without_runtime_lock_file_opens_and_checks(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    # A store checked out from version control arrives WITHOUT .lock (it is
    # gitignored as a runtime primitive). Read verbs must not create it — this
    # is exactly the shape CI sees for the committed dogfood store.
    import shutil

    src = Path(__file__).resolve().parents[1] / ".vouch"
    dst = tmp_path / "checkout"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".lock"))
    assert not (dst / ".lock").exists()
    # A copied store has no project context; at tier standard the pack-files check needs it.
    exit_code = main(["check", "--ci", "--root", str(dst), "--project", str(src.parent)])
    _assert_clean_or_pending_ci_anchor(exit_code, capsys.readouterr().out)
    assert not (dst / ".lock").exists()
    # A planted symlink at .lock is still refused, not followed.
    (dst / ".lock").symlink_to(dst / "state.json")
    assert main(["check", "--ci", "--root", str(dst), "--project", str(src.parent)]) != 0


def test_dogfood_store_is_committed_checkable_and_honest(capsys: CaptureFixture[str]) -> None:
    # D-10: mk2's own committed store must be clean, or clean except for the one
    # pending CI anchor, and every organizational row must be visibly NOT_CHECKED.
    # Tier controls deliberately render one row per control so none is hidden in
    # a long detail.
    root = Path(__file__).resolve().parents[1] / ".vouch"
    assert (root / "state.json").exists(), "the dogfood store must be committed"
    exit_code = main(["check", "--ci", "--root", str(root)])
    out = capsys.readouterr().out
    _assert_clean_or_pending_ci_anchor(exit_code, out)
    org_rows = [line for line in out.splitlines() if line.startswith("NOT-CHECKED") and "org." in line]
    tier_rows = [line for line in org_rows if "org.tier-controls" in line]
    assert len(org_rows) == 6 + len(tier_rows)
    assert len(tier_rows) > 1


def _effects_run(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        code = main(list(args))
    return code, out.getvalue()


def _effects_must(*args: str) -> None:
    code, text = _effects_run(*args)
    assert code == 0, text


def _effects_project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    root = str(project / ".vouch")
    with Store.init(root):
        pass
    (project / "test.py").write_text("assert True\n")
    (project / "effects.json").write_text('{"effects_format":1,"effects":[]}\n')
    _effects_must("specify", "--root", root, "--name", "u", "--version", "1")
    return project, root


def _effects_tree(root: str) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in Path(root).rglob("*") if path.is_file()
    }


def _regression_effects_requirement_round_trips_in_state_and_both_map_layers() -> None:
    legacy = Snapshot(revision=1)
    required = replace(legacy, effects_required=True)
    for snapshot in (legacy, required):
        assert decode_snapshot(encode_snapshot(snapshot)) == snapshot
        projection = system_map(snapshot)
        data = encode_system_map(projection, "1" * 64)
        assert decode_system_map(data)[0] == projection
        assert projection.effects_required is snapshot.effects_required
        assert projection.status_facts.effects_required is snapshot.effects_required
        assert data.count(b'"effects_required":true') == (2 if snapshot.effects_required else 0)
    assert b'"effects_required"' not in encode_snapshot(legacy)


def _regression_format_three_file_checks_bind_raw_effects_bytes_without_parsing_them(
    tmp_path: Path, tests: bool,
) -> None:
    project, root = _effects_project(tmp_path)
    _effects_must("freeze", "--root", root, "--name", "u", "--pack-version", "p",
          "--project", str(project), "--pack-test-file" if tests else "--pack-file",
          "test.py", "--pack-effects-file", "effects.json")
    with Store.open(root) as store:
        snapshot = replace(store.read_snapshot(), tier=Tier.STANDARD)

        def result() -> Verdict:
            return next(
                row.verdict for row in run_artifact_checks(store, snapshot, project=str(project))
                if row.check_id == mk2.CHECK_PACK_FILES_MATCH
            )

        assert result() is Verdict.PASS
        # Equivalent declarations with different whitespace still change bound bytes.
        (project / "effects.json").write_text('{"effects_format": 1, "effects": []}\n')
        assert result() is Verdict.FAIL
        (project / "effects.json").write_text("invalid JSON\n")
        assert result() is Verdict.FAIL
        (project / "effects.json").unlink()
        assert result() is Verdict.FAIL


def _regression_rebind_effect_change_refusal_precedes_removed_paths() -> None:
    old = PackInventory((PackEntry("old.py", "1" * 64, 1),))
    new = PackInventory((PackEntry("new.py", "2" * 64, 1),), pack_format=3, effects=())
    snapshot = Snapshot(units=(Unit(
        "u", "1", UnitStatus.PUBLISHED, acceptance_pack_version="p",
        acceptance_pack_digest="1" * 64, acceptance_test_set_digest="1" * 64,
        published_once=True,
    ),))
    decision = decide(snapshot, RebindPack(
        "u", "2" * 64, "adapt interface", old_inventory=old, new_inventory=new,
    ))
    assert isinstance(decision, Refused)
    assert tuple(v.code for v in decision.violations) == ("rebind.effects-changed",)


def _regression_rebind_invalid_effects_arguments_leave_every_store_file_unchanged(tmp_path: Path) -> None:
    project, root = _effects_project(tmp_path)
    before = _effects_tree(root)
    (project / "bad.json").write_text('{"effects_format":1}')
    for args in (
        ("--pack-effects-file", "effects.json"),
        ("--pack-test-file", "test.py", "--pack-effects-file", "bad.json"),
    ):
        code, text = _effects_run("rebind-pack", "--root", root, "--name", "u",
                          "--reason", "adapt interface", "--project", str(project), *args)
        assert code == 2, text
        assert _effects_tree(root) == before


def _regression_merge_replays_the_requirement_and_recovers_a_late_bound_declaration(tmp_path: Path) -> None:
    project, root = _effects_project(tmp_path)
    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    their_root = str(theirs / ".vouch")
    _effects_must("require-effects", "--root", their_root)
    _effects_must("freeze", "--root", their_root, "--name", "u", "--pack-version", "p")
    _effects_must("bind-pack", "--root", their_root, "--name", "u", "--project", str(theirs),
          "--pack-test-file", "test.py", "--pack-effects-file", "effects.json")
    _effects_must("specify", "--root", root, "--name", "other", "--version", "1")
    _effects_must("merge", "--root", root, "--other", their_root)
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        assert snapshot.effects_required
        unit = snapshot.unit("u")
        assert unit is not None and unit.acceptance_pack_digest
    code, text = _effects_run("show-effects", "--root", root, "--json")
    assert code == 0
    assert json.loads(text)["units"][1]["effects"] == []


def _regression_replayed_requirement_survives_subsequent_events(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True)
    root = str(tmp_path / "store")
    with Store.init(root) as store, store.write_locked():
        initial = store.read_snapshot()
        snapshot = commit_events(store, initial, (EffectsRequired(),), actor="test", run_id="")
        decision = decide(snapshot, SpecifyUnit("u", "1"))
        assert isinstance(decision, Accepted)
        snapshot = commit_events(store, snapshot, decision.events, actor="test", run_id="")
        decision = decide(snapshot, FreezeAcceptance("u", "p"))
        assert isinstance(decision, Accepted)
        snapshot = commit_events(store, snapshot, decision.events, actor="test", run_id="")
        assert store.read_snapshot().effects_required
        assert isinstance(decide(snapshot, BindPack("u", "1" * 64)), Refused)


def _regression_effect_declarations(tmp_path: Path) -> None:
    _regression_effects_requirement_round_trips_in_state_and_both_map_layers()
    for tests in (False, True):
        _regression_format_three_file_checks_bind_raw_effects_bytes_without_parsing_them(
            tmp_path / f"files-{tests}", tests,
        )
    _regression_rebind_effect_change_refusal_precedes_removed_paths()
    _regression_rebind_invalid_effects_arguments_leave_every_store_file_unchanged(tmp_path / "rebind")
    _regression_merge_replays_the_requirement_and_recovers_a_late_bound_declaration(tmp_path / "merge")
    _regression_replayed_requirement_survives_subsequent_events(tmp_path / "replay")
