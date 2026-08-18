from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

import vouch_mk2.verify as verify_module
from vouch_mk2.checks import run_checks
from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot, UnitStatus
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.mcp import serve
from vouch_mk2.store import Store, StoreError, encode_snapshot
from vouch_mk2.store.upgrade import UPGRADERS, UpgradeRefused, upgrade_document
from vouch_mk2.verify import run_artifact_checks
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


def _make_stale_store(root: Path) -> bytes:
    Store.init(str(root)).close()
    state = root / "state.json"
    document = json.loads(state.read_bytes())
    del document["state_format"]
    legacy = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
        + b"\n"
    )
    state.write_bytes(legacy)
    return legacy


def test_mcp_stale_store_error_names_upgrade(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _make_stale_store(root)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {}},
        }
    )
    response = cast(dict[str, object], json.loads(next(serve(str(root), [request]))))
    result = cast(dict[str, object], response["result"])
    structured = cast(dict[str, object], result["structuredContent"])
    assert result["isError"] is True
    assert "upgrade" in cast(str, structured["error"])


def test_upgrade_waits_for_the_writer_lock(tmp_path: Path) -> None:
    root = tmp_path / "store"
    legacy = _make_stale_store(root)
    holder = open(root / ".lock", "r+b")
    fcntl.flock(holder, fcntl.LOCK_EX)
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from vouch_mk2.cli import main; "
            "raise SystemExit(main(['upgrade', '--root', sys.argv[1]]))",
            str(root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert process.poll() is None
        assert (root / "state.json").read_bytes() == legacy
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert "0 -> 1" in stdout
    assert json.loads((root / "state.json").read_bytes())["state_format"] == 4


def test_format2_head_upgrade_binds_the_active_ref_without_inventing_results() -> None:
    fixture = Path("tests/fixtures/state-format/format2.golden.json")
    original = json.loads(fixture.read_bytes())
    upgraded, steps = upgrade_document(original)
    assert steps == [(2, 3), (3, 4)]
    heads = cast(list[dict[str, object]], upgraded["evidence_heads"])
    by_name = {cast(str, head["unit_name"]): head for head in heads}
    assert by_name["core"]["unit_version"] == "v1"
    assert by_name["ui"]["unit_version"] == "v1"
    assert all(head["pack_summaries"] == [] for head in heads)


def test_format2_upgrade_refuses_ambiguous_superseded_evidence_binding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ambiguous-format2-store"
    Store.init(str(root)).close()
    document = json.loads(
        Path("tests/fixtures/state-format/format2.golden.json").read_bytes()
    )
    for unit in cast(list[dict[str, object]], document["units"]):
        if unit["name"] == "core":
            unit["status"] = UnitStatus.SUPERSEDED.value
    (root / "state.json").write_bytes(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    assert main(["upgrade", "--root", str(root)]) == 2
    error = capsys.readouterr().err
    assert str(root) in error
    assert "core" in error
    assert "core@v0" in error and "core@v1" in error
    assert "restore" in error and "active" in error


def test_format2_upgrade_derives_a_single_superseded_candidate() -> None:
    original = json.loads(
        Path("tests/fixtures/state-format/format2.golden.json").read_bytes()
    )
    units = cast(list[dict[str, object]], original["units"])
    original["units"] = [
        unit
        for unit in units
        if not (unit["name"] == "core" and unit["version"] == "v0")
    ]
    for unit in cast(list[dict[str, object]], original["units"]):
        if unit["name"] == "core":
            unit["status"] = UnitStatus.SUPERSEDED.value
    upgraded, steps = upgrade_document(original)
    assert steps == [(2, 3), (3, 4)]
    core = next(
        head
        for head in cast(list[dict[str, object]], upgraded["evidence_heads"])
        if head["unit_name"] == "core"
    )
    assert core["unit_version"] == "v1"
    assert core["pack_summaries"] == []


def test_upgraded_format2_legacy_chain_has_an_honest_empty_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from vouch_mk2.core import (
        EvidenceEvent,
        EvidenceHead,
        EvidenceKind,
        EvidenceOutcome,
        Tier,
        Unit,
        UnitRef,
    )
    from vouch_mk2.store import (
        ArtifactKind,
        digest_of,
        encode_evidence_event,
    )

    root = tmp_path / "honest-format2"
    legacy = EvidenceEvent(
        UnitRef("u", "v1"),
        1,
        "GENESIS",
        EvidenceKind.RED,
        "pytest",
        EvidenceOutcome.TESTS_FAILED,
        "t1",
        evidence_format=1,
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
        evidence_heads=(EvidenceHead("u", digest, 1, "GENESIS", "v1"),),
    )
    with Store.init(str(root)) as store:
        snapshot = commit_snapshot_fixture(
            store,
            snapshot,
            objects=((ArtifactKind.EVIDENCE, data),),
        )
    document = json.loads((root / "state.json").read_bytes())
    document["state_format"] = 2
    for head in cast(list[dict[str, object]], document["evidence_heads"]):
        for field in (
            "unit_version",
            "pack_summaries",
            "current_pack_digest",
            "current_pack_red_seen",
            "current_pack_green",
        ):
            head.pop(field, None)
    (root / "state.json").write_bytes(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    assert main(["upgrade", "--root", str(root)]) == 0
    capsys.readouterr()
    with Store.open(str(root), read_only=True) as store:
        upgraded = store.read_snapshot()
        result = next(
            row
            for row in run_artifact_checks(store, upgraded)
            if row.check_id == mk2.CHECK_EVIDENCE_CHAINS
        )
    head = upgraded.evidence_head("u")
    assert head is not None and head.pack_summaries == ()
    assert result.verdict is Verdict.PASS
    assert "summary does not match" not in result.detail


def test_upgrade_catches_upgrader_exception_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    legacy = _make_stale_store(root)

    def explode(_document: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("synthetic upgrader failure")

    monkeypatch.setitem(UPGRADERS, 0, explode)
    assert main(["upgrade", "--root", str(root)]) == 2
    assert "upgrade" in capsys.readouterr().err
    assert (root / "state.json").read_bytes() == legacy
    assert not list(root.glob("*.tmp"))


def test_upgrade_strictly_gates_an_invalid_current_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    state = root / "state.json"
    document = json.loads(state.read_bytes())
    del document["tier"]
    invalid = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
        + b"\n"
    )
    state.write_bytes(invalid)
    assert main(["upgrade", "--root", str(root)]) == 2
    assert "upgrade" in capsys.readouterr().err
    assert state.read_bytes() == invalid


def test_upgrade_dry_run_creates_no_runtime_or_generated_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    legacy = _make_stale_store(root)
    (root / ".lock").unlink()
    entries_before = set(root.iterdir())
    assert main(["upgrade", "--root", str(root), "--dry-run"]) == 0
    assert "0 -> 1" in capsys.readouterr().out
    assert set(root.iterdir()) == entries_before
    assert (root / "state.json").read_bytes() == legacy


def test_pack3_assertions_reject_unknown_status_and_cli_conflicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    _make_stale_store(root)
    state = root / "state.json"
    document = json.loads(state.read_bytes())
    document["units"] = [
        {
            "acceptance_pack_version": "p",
            "depends_on": [],
            "name": "maybe",
            "status": "mystery",
            "version": "1",
        }
    ]
    with pytest.raises(UpgradeRefused) as raised:
        upgrade_document(document)
    assert "maybe@1" in str(raised.value)
    assert "mystery" in str(raised.value)

    document["units"][0]["status"] = "blocked"
    state.write_bytes(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    assert main(
        ["upgrade", "--root", str(root), "--was-published", "maybe@1"]
    ) == 0
    assert "assert was-published maybe@1" in capsys.readouterr().out
    with Store.open(str(root), read_only=True) as store:
        unit = store.read_snapshot().unit("maybe")
    assert unit is not None and unit.published_once is True

    with pytest.raises(SystemExit) as conflict:
        main(
            [
                "upgrade",
                "--root",
                str(root),
                "--was-published",
                "maybe@1",
                "--never-published",
                "maybe@1",
            ]
        )
    assert conflict.value.code == 2
    assert "conflicting" in capsys.readouterr().err


@pytest.mark.parametrize("replacement_kind", ("undecodable", "wrong-revision"))
def test_upgrade_replacement_validates_bytes_and_preserves_original(
    tmp_path: Path,
    replacement_kind: str,
) -> None:
    root = tmp_path / replacement_kind
    with Store.init(str(root)) as store:
        original = (root / "state.json").read_bytes()
        replacement = (
            b"not-json\n"
            if replacement_kind == "undecodable"
            else encode_snapshot(Snapshot(revision=9))
        )
        with store.write_locked():
            with pytest.raises(StoreError):
                store.replace_state_for_upgrade(replacement)
        assert (root / "state.json").read_bytes() == original


def test_projection_failure_reports_committed_upgrade_and_retry_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    _make_stale_store(root)
    real_emit_system_map = verify_module.emit_system_map

    def fail_map(_store: Store) -> bytes:
        raise RuntimeError("synthetic map failure")

    monkeypatch.setattr(verify_module, "emit_system_map", fail_map)
    assert main(["upgrade", "--root", str(root)]) == 2
    error = capsys.readouterr().err
    assert (
        "state upgraded to format 4; projection regeneration failed: "
        "synthetic map failure — run 'vouch-mk2 generate'"
    ) in error
    assert json.loads((root / "state.json").read_bytes())["state_format"] == 4

    monkeypatch.setattr(verify_module, "emit_system_map", real_emit_system_map)
    assert main(["upgrade", "--root", str(root)]) == 0
    assert (
        "already at format 4; projections regenerated"
        in capsys.readouterr().out
    )
    with Store.open(str(root), read_only=True) as store:
        rows = {
            result.check_id: result.verdict
            for result in run_artifact_checks(store, store.read_snapshot())
        }
    assert rows[mk2.CHECK_MAP_FRESHNESS] is Verdict.PASS
    assert rows[mk2.CHECK_CONTEXT_FRESHNESS] is Verdict.PASS


def test_already_current_upgrade_repairs_stale_and_missing_projections(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        store.write_generated("map.json", b"stale")
    assert not (root / "vouch-context.md").exists()

    assert main(["upgrade", "--root", str(root)]) == 0
    assert (
        "already at format 4; projections regenerated"
        in capsys.readouterr().out
    )
    with Store.open(str(root), read_only=True) as store:
        rows = {
            result.check_id: result.verdict
            for result in run_artifact_checks(store, store.read_snapshot())
        }
    assert rows[mk2.CHECK_MAP_FRESHNESS] is Verdict.PASS
    assert rows[mk2.CHECK_CONTEXT_FRESHNESS] is Verdict.PASS


def test_record_evidence_preflights_legacy_store_before_child_side_effect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    _make_stale_store(root)
    marker = tmp_path / "child-ran"
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@1",
            "--kind",
            "green",
            "--",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
        ]
    ) == 2
    assert not marker.exists()
    assert "upgrade" in capsys.readouterr().err


def test_upgrade_dry_run_labels_every_line_and_confirms_no_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    original = _make_stale_store(root)
    assert main(["upgrade", "--root", str(root), "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines
    assert all(line.startswith("dry-run: ") for line in lines)
    assert any("0 -> 1" in line for line in lines)
    assert lines[-1] == "dry-run: no changes written"
    assert (root / "state.json").read_bytes() == original


def test_generated_write_oserror_is_typed_and_hides_temp_name(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    (root / "map.json").mkdir()
    with Store.open(str(root)) as store:
        with pytest.raises(StoreError) as raised:
            store.write_generated("map.json", b"map")
    assert ".generated-" not in str(raised.value)
    assert not tuple(root.glob(".generated-*.tmp"))


def test_absent_map_freshness_is_not_applicable(tmp_path: Path) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        snapshot = store.read_snapshot()
        artifact_results = run_artifact_checks(store, snapshot)
        result = next(
            row
            for row in artifact_results
            if row.check_id == mk2.CHECK_MAP_FRESHNESS
        )
    assert result.verdict is Verdict.NOT_APPLICABLE
    assert "absent" in result.detail
    report = run_checks(snapshot, artifact_results=artifact_results)
    composed_result = next(
        row
        for row in report.mechanical
        if row.check_id == mk2.CHECK_MAP_FRESHNESS
    )
    assert composed_result.verdict is Verdict.NOT_APPLICABLE
    assert report.composed() is Verdict.PASS


def test_state_format_docs_distinguish_legacy_and_upgrade_lock_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    boundary = (root / "docs" / "claim-boundary.md").read_text()
    import vouch_mk2.store.codec as codec

    format_sentence = (
        "Format 1 and later declare `state_format`; absence denotes legacy format 0."
    )
    assert format_sentence in readme
    assert format_sentence in boundary
    assert "Format 1 and later declare ``state_format``" in (codec.__doc__ or "")
    assert "upgrade holds its outer writer lock through regeneration" in " ".join(
        boundary.split()
    )
