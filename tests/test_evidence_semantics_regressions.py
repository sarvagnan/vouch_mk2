"""Regression coverage for evidence-semantics@1 pack/3 amendments."""

from __future__ import annotations

import io
import json
import runpy
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.bundle import build_pack_inventory, pack_test_set_digest
from vouch_mk2.checks.tier import check_pack_bound
from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    EvidenceOutcome,
    EvidencePackSummary,
    RebindPack,
    RecordEvidence,
    Respecify,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.core.events import AcceptancePackRebound
from vouch_mk2.core.model import PackEntry, PackInventory, PackRebound
from vouch_mk2.ir.schema import Tier, Verdict
from vouch_mk2.ir import mk2
from vouch_mk2.store import (
    STATE_FORMAT_HIGH_WATER_FILE,
    ArtifactKind,
    DecodeError,
    Store,
    StoreError,
    decode_pack_inventory,
    digest_of,
    encode_evidence_event,
    encode_pack_inventory,
)
from vouch_mk2.store.upgrade import UPGRADERS
from vouch_mk2.verify import run_artifact_checks
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


ROOT = Path(__file__).resolve().parents[1]


def _pack3_helpers() -> dict[str, object]:
    return runpy.run_path(str(ROOT / "tests" / "test_evidence_semantics.py"))


def _captured_main(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _rebind_args(project: Path, root: str, *files: str, reason: str = "adapt") -> tuple[str, ...]:
    return (
        "rebind-pack",
        "--root",
        root,
        "--name",
        "u",
        "--project",
        str(project),
        *files,
        "--reason",
        reason,
    )


def test_pack_format_one_test_set_digest_is_exact_inventory_digest() -> None:
    inventory = PackInventory(
        entries=(PackEntry("tests/t.py", "a" * 64, 7),),
        pack_format=1,
    )
    data = encode_pack_inventory(inventory)
    assert data == (
        b'{"entries":[{"path":"tests/t.py","sha256":"'
        + b"a" * 64
        + b'","size":7}],"pack_format":1}\n'
    )
    assert pack_test_set_digest(inventory) == digest_of(
        ArtifactKind.ACCEPTANCE_PACK, data
    )


def test_pack_format_two_test_set_digest_ignores_spec_and_sorts_all_roles(
    tmp_path: Path,
) -> None:
    (tmp_path / "packs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "packs" / "z.md").write_text("first\n")
    (tmp_path / "tests" / "a.py").write_text("assert False\n")
    first = build_pack_inventory(
        str(tmp_path), ("packs/z.md",), ("tests/a.py",)
    )
    (tmp_path / "packs" / "z.md").write_text("amended prose\n")
    second = build_pack_inventory(
        str(tmp_path), ("packs/z.md",), ("tests/a.py",)
    )
    assert tuple(entry.path for entry in first.entries) == (
        "packs/z.md",
        "tests/a.py",
    )
    assert pack_test_set_digest(first) == pack_test_set_digest(second)
    assert encode_pack_inventory(first) != encode_pack_inventory(second)


def test_pack_format_two_decode_requires_a_known_role() -> None:
    data = (
        b'{"entries":[{"path":"tests/t.py","role":"runner","sha256":"'
        + b"a" * 64
        + b'","size":1}],"pack_format":2}\n'
    )
    with pytest.raises(DecodeError, match="expected 'test' or 'spec'"):
        decode_pack_inventory(data)


def test_bind_pack_with_pack_test_file_sets_the_snapshot_test_set_digest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "t.py").write_text("assert True\n")
    root = project / ".vouch"
    Store.init(str(root)).close()
    assert main(["specify", "--root", str(root), "--name", "u", "--version", "v1"]) == 0
    assert main([
        "freeze", "--root", str(root), "--name", "u", "--pack-version", "p1"
    ]) == 0
    assert main([
        "bind-pack", "--root", str(root), "--name", "u", "--project", str(project),
        "--pack-test-file", "tests/t.py",
    ]) == 0
    with Store.open(str(root), read_only=True) as store:
        unit = store.read_snapshot().unit("u")
        assert unit is not None
        inventory = decode_pack_inventory(
            store.get_object(ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest)
        )
    assert inventory.pack_format == 2
    assert inventory.entries[0].role == "test"
    assert unit.acceptance_test_set_digest == pack_test_set_digest(inventory)


def test_record_evidence_pure_decision_refuses_the_inventory_digest() -> None:
    unit = Unit(
        "u",
        "v1",
        UnitStatus.ACCEPTANCE_FROZEN,
        acceptance_pack_version="p1",
        acceptance_pack_digest="a" * 64,
        acceptance_test_set_digest="b" * 64,
    )
    event = EvidenceEvent(
        UnitRef("u", "v1"),
        1,
        "GENESIS",
        EvidenceKind.RED,
        "pytest",
        EvidenceOutcome.TESTS_FAILED,
        "2026-08-17T00:00:00Z",
        pack_digest="a" * 64,
    )
    decision = decide(Snapshot(units=(unit,)), RecordEvidence(event, "c" * 64))
    assert not isinstance(decision, Accepted)
    assert decision.violations[0].code == "evidence.pack-mismatch"


def test_superseded_digest_green_without_same_digest_red_keeps_evidence_chain_repairable(
    tmp_path: Path,
) -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    events: list[tuple[ArtifactKind, bytes]] = []
    parent = "GENESIS"
    for sequence, kind, pack_digest, evidence_format in (
        (1, EvidenceKind.RED, "", 1),
        (2, EvidenceKind.GREEN, digest_a, 2),
        (3, EvidenceKind.RED, digest_b, 2),
        (4, EvidenceKind.GREEN, digest_b, 2),
    ):
        event = EvidenceEvent(
            UnitRef("u", "v1"),
            sequence,
            parent,
            kind,
            "uv run pytest",
            EvidenceOutcome.TESTS_FAILED
            if kind is EvidenceKind.RED
            else EvidenceOutcome.TESTS_PASSED,
            f"t-{sequence}",
            pack_digest=pack_digest,
            evidence_format=evidence_format,
        )
        data = encode_evidence_event(event)
        parent = digest_of(ArtifactKind.EVIDENCE, data)
        events.append((ArtifactKind.EVIDENCE, data))
    snapshot = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.PUBLISHED,
                acceptance_pack_version="pack/b",
                acceptance_pack_digest=digest_b,
                acceptance_test_set_digest=digest_b,
                published_once=True,
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
                    EvidencePackSummary(digest_a, False, True),
                    EvidencePackSummary(digest_b, True, True),
                ),
            ),
        ),
    )
    with Store.init(str(tmp_path / "store")) as store:
        snapshot = commit_snapshot_fixture(store, snapshot, objects=tuple(events))
        result = next(
            check
            for check in run_artifact_checks(store, snapshot)
            if check.check_id == mk2.CHECK_EVIDENCE_CHAINS
        )
    # Append-only history must stay repairable: R4 leaves this chain-wide rule unchanged.
    assert result.verdict is Verdict.PASS, result.detail


def test_respecify_clears_inventory_and_test_set_digests_together() -> None:
    snapshot = Snapshot(
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.BLOCKED,
                acceptance_pack_version="p1",
                acceptance_pack_digest="a" * 64,
                acceptance_test_set_digest="b" * 64,
            ),
        )
    )
    decision = decide(snapshot, Respecify("u"))
    assert isinstance(decision, Accepted)
    unit = apply(snapshot, decision.events).unit("u")
    assert unit is not None
    assert unit.acceptance_pack_digest == ""
    assert unit.acceptance_test_set_digest == ""


def test_rebind_event_records_old_new_test_set_and_reason_without_touching_chain() -> None:
    head = EvidenceHead(
        "u",
        "d" * 64,
        2,
        "d" * 64,
        unit_version="v1",
        pack_summaries=(EvidencePackSummary("b" * 64, True, True),),
    )
    snapshot = Snapshot(
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.PUBLISHED,
                acceptance_pack_version="p1",
                acceptance_pack_digest="a" * 64,
                acceptance_test_set_digest="b" * 64,
                published_once=True,
            ),
        ),
        evidence_heads=(head,),
    )
    decision = decide(
        snapshot,
        RebindPack(
            name="u",
            pack_digest="c" * 64,
            reason="adapt shared record field",
            test_set_digest="e" * 64,
            old_inventory=PackInventory(
                (PackEntry("tests/t.py", "a" * 64, 1),), pack_format=1
            ),
            new_inventory=PackInventory(
                (PackEntry("tests/t.py", "c" * 64, 1),), pack_format=1
            ),
        ),
    )
    assert isinstance(decision, Accepted)
    assert decision.events == (
        AcceptancePackRebound(
            "u",
            "a" * 64,
            "c" * 64,
            "b" * 64,
            "e" * 64,
            "adapt shared record field",
            2,
        ),
    )
    changed = apply(snapshot, decision.events)
    unit = changed.unit("u")
    assert unit is not None and unit.status is UnitStatus.PUBLISHED
    assert unit.acceptance_pack_version == "p1"
    assert changed.evidence_heads == snapshot.evidence_heads


def test_high_pack_bound_accepts_rebound_green_without_a_red() -> None:
    unit = Unit(
        "u",
        "v1",
        UnitStatus.PUBLISHED,
        acceptance_pack_version="p1",
        acceptance_pack_digest="a" * 64,
        acceptance_test_set_digest="b" * 64,
        pack_binding="rebound",
        rebound_history=(
            PackRebound(
                "c" * 64,
                "a" * 64,
                "c" * 64,
                "b" * 64,
                "adapt",
                0,
            ),
        ),
        published_once=True,
    )
    before = check_pack_bound(Snapshot(units=(unit,), tier=Tier.HIGH))
    assert before.verdict is Verdict.FAIL
    head = EvidenceHead(
        "u",
        "d" * 64,
        1,
        "d" * 64,
        unit_version="v1",
        pack_summaries=(EvidencePackSummary("b" * 64, False, True),),
    )
    after = check_pack_bound(
        Snapshot(units=(unit,), evidence_heads=(head,), tier=Tier.HIGH)
    )
    assert after.verdict is Verdict.PASS
    assert "rebound after publication: u@v1" in after.detail


def test_upgrade_two_to_three_copies_inventory_digest_to_test_set_digest() -> None:
    document: dict[str, object] = {
        "state_format": 2,
        "units": [
            {
                "name": "u",
                "version": "v1",
                "status": "published",
                "acceptance_pack_digest": "a" * 64,
            }
        ],
        "evidence_heads": [],
    }
    upgraded = UPGRADERS[2](document)
    units = upgraded["units"]
    assert isinstance(units, list)
    unit = units[0]
    assert isinstance(unit, dict)
    assert unit["acceptance_test_set_digest"] == "a" * 64


def test_rebind_refuses_removed_bound_paths(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    (project / "tests" / "replacement.py").write_text("def test_ok():\n    pass\n")
    code, out = helpers["_run"](  # type: ignore[operator]
        *_rebind_args(
            project,
            root,
            "--pack-file",
            "packs/p.spec.md",
            "--pack-test-file",
            "tests/replacement.py",
        )
    )
    assert code == 2
    assert "rebind.paths-removed" in out and "tests/t.py" in out


def test_rebind_refuses_test_to_spec_demotion(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    (project / "tests" / "replacement.py").write_text("def test_ok():\n    pass\n")
    code, out = helpers["_run"](  # type: ignore[operator]
        *_rebind_args(
            project,
            root,
            "--pack-file",
            "packs/p.spec.md",
            "--pack-file",
            "tests/t.py",
            "--pack-test-file",
            "tests/replacement.py",
        )
    )
    assert code == 2
    assert "rebind.test-demoted" in out and "tests/t.py" in out


def test_rebind_treats_every_format_one_path_as_executable(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_project"](tmp_path)  # type: ignore[operator]
    helpers["_frozen_unit"](project, root)  # type: ignore[operator]
    assert helpers["_evidence"](root, "u@v1", "red", helpers["FAIL"])[0] == 0  # type: ignore[operator]
    assert helpers["_evidence"](root, "u@v1", "green", helpers["OK"])[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("mark-done", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("publish", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    (project / "tests" / "replacement.py").write_text("def test_ok():\n    pass\n")
    code, out = helpers["_run"](  # type: ignore[operator]
        *_rebind_args(
            project,
            root,
            "--pack-file",
            "tests/t.py",
            "--pack-test-file",
            "tests/replacement.py",
        )
    )
    assert code == 2
    assert "rebind.test-demoted" in out and "tests/t.py" in out


def test_rebind_allows_changed_format_one_inventory_without_narrowing(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_project"](tmp_path)  # type: ignore[operator]
    helpers["_frozen_unit"](project, root)  # type: ignore[operator]
    assert helpers["_evidence"](root, "u@v1", "red", helpers["FAIL"])[0] == 0  # type: ignore[operator]
    assert helpers["_evidence"](root, "u@v1", "green", helpers["OK"])[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("mark-done", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("publish", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    code, out = helpers["_run"](  # type: ignore[operator]
        *_rebind_args(project, root, "--pack-file", "tests/t.py")
    )
    assert code == 0, out


def test_rebind_allows_spec_to_test_promotion(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    (project / "packs" / "p.spec.md").write_text("promoted executable check\n")
    code, out = helpers["_run"](  # type: ignore[operator]
        *_rebind_args(
            project,
            root,
            "--pack-test-file",
            "packs/p.spec.md",
            "--pack-test-file",
            "tests/t.py",
        )
    )
    assert code == 0, out


def test_rebind_refuses_an_unchanged_inventory_digest(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    code, out = helpers["_rebind"](project, root, "same files")  # type: ignore[operator]
    assert code == 2
    assert "rebind.no-change" in out


def test_rebind_refuses_a_published_label_only_unit(tmp_path: Path) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_project"](tmp_path, tier="lightweight")  # type: ignore[operator]
    assert helpers["_run"]("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("freeze", "--root", root, "--name", "u", "--pack-version", "p1")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("mark-ready", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("start", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("mark-done", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("publish", "--root", root, "--name", "u")[0] == 0  # type: ignore[operator]
    (project / "tests" / "t.py").write_text("def test_x():\n    pass\n")
    code, out = helpers["_run"](  # type: ignore[operator]
        *_rebind_args(project, root, "--pack-test-file", "tests/t.py")
    )
    assert code == 2
    assert "rebind.no-pack" in out


def test_rebound_history_is_durable_and_visible_in_every_reader_surface(
    tmp_path: Path,
) -> None:
    from vouch_mk2.mcp import _call_tool

    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    reason = "adapt exact shared interface field"
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    assert helpers["_rebind"](project, root, reason)[0] == 0  # type: ignore[operator]
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("u")
        assert unit is not None
        history = unit.rebound_history
        assert len(history) == 1
        rebound = history[0]
        assert rebound.old_inventory_digest != rebound.new_inventory_digest
        assert rebound.old_test_set_digest != rebound.new_test_set_digest
        assert rebound.reason == reason
        assert rebound.evidence_head_sequence == 2
    assert helpers["_run"]("generate", "--root", root)[0] == 0  # type: ignore[operator]
    status_code, status = helpers["_run"]("status", "--root", root)[0:2]  # type: ignore[index,operator]
    assert status_code == 0
    for value in (
        "rebound_pack_units: u@v1",
        rebound.old_inventory_digest,
        rebound.new_inventory_digest,
        rebound.old_test_set_digest,
        rebound.new_test_set_digest,
        reason,
        "evidence_head_sequence=2",
    ):
        assert value in status
    mcp_status = _call_tool(root, "status", {})["status"]
    assert isinstance(mcp_status, dict)
    assert mcp_status["rebound_pack_units"] == ["u@v1"]
    assert reason in json.dumps(mcp_status)
    mapped = _call_tool(root, "map", {})["map"]
    assert reason in json.dumps(mapped)
    context = Path(root, "vouch-context.md").read_text()
    assert reason in context and rebound.old_inventory_digest in context
    _, check = helpers["_run"]("check", "--root", root, "--project", str(project))  # type: ignore[operator]
    assert "check.pack-bound" in check and reason in check


def test_reused_test_set_digest_needs_green_after_the_latest_rebound(
    tmp_path: Path,
) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    original = (project / "tests" / "t.py").read_text()
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True  # B\n")
    assert helpers["_rebind"](project, root, "A to B")[0] == 0  # type: ignore[operator]
    assert helpers["_evidence"](root, "u@v1", "green", helpers["OK"])[0] == 0  # type: ignore[operator]
    (project / "tests" / "t.py").write_text(original)
    assert helpers["_rebind"](project, root, "B back to A")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("generate", "--root", root)[0] == 0  # type: ignore[operator]
    code, out = helpers["_run"]("check", "--root", root, "--project", str(project))  # type: ignore[operator]
    assert code == 1
    assert "check.evidence-chains" in out and "fresh GREEN" in out
    assert helpers["_evidence"](root, "u@v1", "green", helpers["OK"])[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("generate", "--root", root)[0] == 0  # type: ignore[operator]
    code, out = helpers["_run"]("check", "--root", root, "--project", str(project))  # type: ignore[operator]
    assert not any(
        "check.evidence-chains" in line and "FAIL" in line
        for line in out.splitlines()
    )


def test_lightweight_rebound_still_requires_fresh_green_and_is_named(
    tmp_path: Path,
) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path, "lightweight")  # type: ignore[operator]
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    assert helpers["_rebind"](project, root, "lightweight adaptation")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("generate", "--root", root)[0] == 0  # type: ignore[operator]
    code, out = helpers["_run"]("check", "--root", root, "--project", str(project))  # type: ignore[operator]
    assert code == 1
    assert any(
        "check.evidence-chains" in line and "FAIL" in line and "fresh GREEN" in line
        for line in out.splitlines()
    )
    assert any(
        "check.pack-bound" in line
        and "NOT-APPLICABLE" in line
        and "rebound after publication: u@v1" in line
        for line in out.splitlines()
    )


def test_repeated_rebounds_keep_each_adaptation_green_legal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helpers = _pack3_helpers()
    project, root = helpers["_published_with_roles"](tmp_path)  # type: ignore[operator]
    for marker in ("B", "C"):
        (project / "tests" / "t.py").write_text(
            f"def test_x():\n    assert True  # {marker}\n"
        )
        assert helpers["_rebind"](project, root, f"adapt {marker}")[0] == 0  # type: ignore[operator]
        assert helpers["_evidence"](root, "u@v1", "green", helpers["OK"])[0] == 0  # type: ignore[operator]
    monkeypatch.setenv("GITHUB_RUN_ID", "fixture-run")
    assert helpers["_run"]("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0  # type: ignore[operator]
    assert helpers["_run"]("generate", "--root", root)[0] == 0  # type: ignore[operator]
    code, out = helpers["_run"]("check", "--root", root, "--project", str(project))  # type: ignore[operator]
    assert code == 0, out


def test_pack_format_two_encode_refuses_zero_test_entries() -> None:
    inventory = PackInventory(
        entries=(PackEntry("packs/spec.md", "a" * 64, 1, role="spec"),),
        pack_format=2,
    )
    with pytest.raises(StoreError, match="at least one.*test"):
        encode_pack_inventory(inventory)


def test_pack_format_two_decode_refuses_zero_test_entries() -> None:
    data = (
        b'{"entries":[{"path":"packs/spec.md","role":"spec","sha256":"'
        + b"a" * 64
        + b'","size":1}],"pack_format":2}\n'
    )
    with pytest.raises(DecodeError, match="at least one.*test"):
        decode_pack_inventory(data)


def test_upgrade_repairs_pre_extension_format_three_fixture(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    (root / STATE_FORMAT_HIGH_WATER_FILE).unlink()
    fixture = ROOT / "tests" / "fixtures" / "state-format" / "format3-pre-test-set-digest.json"
    Path(root, "state.json").write_bytes(fixture.read_bytes())
    code, out = _captured_main("upgrade", "--root", str(root))
    assert code == 0, out
    with Store.open(str(root), read_only=True) as store:
        snapshot = store.read_snapshot()
    bound = snapshot.unit("bound")
    unbound = snapshot.unit("unbound")
    assert bound is not None and bound.acceptance_test_set_digest == "a" * 64
    assert unbound is not None and unbound.acceptance_test_set_digest == ""
    assert bound.rebound_history == () and unbound.rebound_history == ()


def test_upgrade_rebuilds_stale_head_summaries_but_refuses_inconsistent_evidence_objects(
    tmp_path: Path,
) -> None:
    pack_digest = "a" * 64
    legacy_red = EvidenceEvent(
        UnitRef("u", "v1"),
        1,
        "GENESIS",
        EvidenceKind.RED,
        "uv run pytest",
        EvidenceOutcome.TESTS_FAILED,
        "t-1",
        evidence_format=1,
    )
    legacy_data = encode_evidence_event(legacy_red)
    legacy_digest = digest_of(ArtifactKind.EVIDENCE, legacy_data)
    superseded_green = EvidenceEvent(
        UnitRef("u", "v1"),
        2,
        legacy_digest,
        EvidenceKind.GREEN,
        "uv run pytest",
        EvidenceOutcome.TESTS_PASSED,
        "t-2",
        pack_digest=pack_digest,
    )
    green_data = encode_evidence_event(superseded_green)
    green_digest = digest_of(ArtifactKind.EVIDENCE, green_data)
    stale = Snapshot(
        revision=1,
        units=(Unit("u", "v1", UnitStatus.ACCEPTANCE_FROZEN),),
        evidence_heads=(
            EvidenceHead(
                "u",
                green_digest,
                2,
                "GENESIS",
                "v1",
                (EvidencePackSummary(pack_digest, False, False),),
            ),
        ),
    )
    stale_root = tmp_path / "stale-summary"
    with Store.init(str(stale_root)) as store:
        stale = commit_snapshot_fixture(
            store,
            stale,
            objects=(
                (ArtifactKind.EVIDENCE, legacy_data),
                (ArtifactKind.EVIDENCE, green_data),
            ),
        )
        before = next(
            row
            for row in run_artifact_checks(store, stale)
            if row.check_id == mk2.CHECK_EVIDENCE_CHAINS
        )
    assert before.verdict is Verdict.FAIL
    assert "summary does not match its immutable chain" in before.detail

    code, out = _captured_main("upgrade", "--root", str(stale_root))
    assert code == 0, out
    assert "rebuilt 1 evidence-head summaries" in out
    with Store.open(str(stale_root), read_only=True) as store:
        repaired = store.read_snapshot()
        after = next(
            row
            for row in run_artifact_checks(store, repaired)
            if row.check_id == mk2.CHECK_EVIDENCE_CHAINS
        )
    repaired_head = repaired.evidence_head("u")
    assert repaired_head is not None
    assert repaired_head.pack_summaries == (
        EvidencePackSummary(pack_digest, False, True),
    )
    assert after.verdict is Verdict.PASS, after.detail

    incoherent = EvidenceEvent(
        UnitRef("u", "v1"),
        1,
        "GENESIS",
        EvidenceKind.GREEN,
        "uv run pytest",
        EvidenceOutcome.TESTS_FAILED,
        "t-1",
        pack_digest=pack_digest,
    )
    incoherent_data = encode_evidence_event(incoherent)
    incoherent_digest = digest_of(ArtifactKind.EVIDENCE, incoherent_data)
    inconsistent = Snapshot(
        revision=1,
        units=(Unit("u", "v1", UnitStatus.ACCEPTANCE_FROZEN),),
        evidence_heads=(
            EvidenceHead(
                "u",
                incoherent_digest,
                1,
                "GENESIS",
                "v1",
                (EvidencePackSummary(pack_digest, False, True),),
            ),
        ),
    )
    inconsistent_root = tmp_path / "inconsistent-object"
    with Store.init(str(inconsistent_root)) as store:
        inconsistent = commit_snapshot_fixture(
            store,
            inconsistent,
            objects=((ArtifactKind.EVIDENCE, incoherent_data),),
        )
    state_before = (inconsistent_root / "state.json").read_bytes()
    code, out = _captured_main("upgrade", "--root", str(inconsistent_root))
    assert code == 2
    assert "incoherent evidence object" in out
    assert (inconsistent_root / "state.json").read_bytes() == state_before


def test_claim_boundary_calls_rebound_reason_an_unverified_claim() -> None:
    text = (ROOT / "docs" / "claim-boundary.md").read_text()
    assert "records the stated reason as a claim" in text
    assert "does not verify the reason's truth" in text
    assert "does not verify" in text and "adequacy of the new test content" in text
    assert "interface-only" not in text
