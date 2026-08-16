"""Acceptance pack for self-hosted unit cli-completeness@1 (beads bd4.5 + bd4.7).

Every test here FAILED against the pre-change tree (RED recorded before implementation)
and must PASS at completion. The implementer may not edit this file.

Intent: three gaps the docs review surfaced. (1) `bundle-inventory` — build a bundle inventory
from a tree and place it in the store under the lock, so `bind-bundle` is usable without
Python. (2) `next` prints COMPLETE invocations: every required argument the reflected verb
needs appears, bound where the projection knows the value, as a `<--flag VALUE>` placeholder
where it does not. (3) `record-evidence --help` shows the mandatory `-- <argv...>` tail.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot, Unit, UnitStatus
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _store(tmp_path: Path) -> str:
    root = str(tmp_path / "s")
    Store.init(root).close()
    return root


# ---- bundle-inventory verb ----


def test_bundle_inventory_builds_places_and_prints_the_digest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _store(tmp_path)
    tree = tmp_path / "dist"
    tree.mkdir()
    (tree / "app.bin").write_bytes(b"binary")
    (tree / "notes.txt").write_text("hello")
    assert main(["bundle-inventory", "--root", root, "--tree", str(tree), "--exclude", "*.txt"]) == 0
    digest = capsys.readouterr().out.strip().split()[-1]
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    from vouch_mk2.store import ArtifactKind, decode_bundle_inventory

    with Store.open(root, read_only=True) as store:
        inventory = decode_bundle_inventory(store.get_object(ArtifactKind.BUNDLE_INVENTORY, digest))
    assert [e.path for e in inventory.entries] == ["app.bin"]
    assert inventory.exclusions == ("*.txt",)


def test_bundle_inventory_json_and_then_bind_bundle_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _store(tmp_path)
    tree = tmp_path / "dist"
    tree.mkdir()
    (tree / "app.bin").write_bytes(b"binary")
    for argv in (["specify", "--name", "u", "--version", "1"], ["freeze", "--name", "u", "--pack-version", "p"], ["mark-ready", "--name", "u"],
                 ["start", "--name", "u"], ["mark-done", "--name", "u"], ["publish", "--name", "u"],
                 ["prepare-release", "--release-id", "r1", "--includes", "u"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0, argv
    capsys.readouterr()
    assert main(["bundle-inventory", "--root", root, "--tree", str(tree), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["accepted"] is True and re.fullmatch(r"[0-9a-f]{64}", document["inventory_digest"])
    assert main(["bind-bundle", "--root", root, "--release-id", "r1", "--inventory-digest", document["inventory_digest"]]) == 0
    assert main(["check", "--ci", "--root", root]) == 0
    assert "check.bundle-inventories" in capsys.readouterr().out


def test_bundle_inventory_refuses_a_missing_or_symlinked_tree_and_writes_nothing(tmp_path: Path) -> None:
    root = _store(tmp_path)
    objects_before = sorted(p.name for p in (Path(root) / "objects" / "sha256").iterdir())
    assert main(["bundle-inventory", "--root", root, "--tree", str(tmp_path / "nope")]) == 2
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert main(["bundle-inventory", "--root", root, "--tree", str(link)]) == 2
    assert sorted(p.name for p in (Path(root) / "objects" / "sha256").iterdir()) == objects_before


def test_bundle_inventory_is_read_only_safe() -> None:
    # guard: passes today and must keep passing — the verb is not a lifecycle command (not reflected) but must be in the lock.
    from vouch_mk2.cli import LIFECYCLE_VERBS

    assert "bundle-inventory" not in LIFECYCLE_VERBS


# ---- next prints complete invocations ----


def test_next_invocations_include_every_required_argument_as_value_or_placeholder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _store(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    capsys.readouterr()
    assert main(["next", "--root", root]) == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("vouch-mk2 freeze")]
    assert lines, "expected a freeze next-action for a specified unit"
    line = lines[0].split("  #")[0]
    assert "--pack-version <PACK_VERSION>" in line
    argv = shlex.split(line)
    assert argv[:2] == ["vouch-mk2", "freeze"] and "--root" in argv and "--name" in argv


def test_next_placeholders_cover_all_reflected_required_fields() -> None:
    from vouch_mk2.cli import invocation_for
    from vouch_mk2.projections import NextAction

    action = NextAction(subject_kind="release", subject_id="r1", command="set-criteria", reason="x")
    line = invocation_for(action, "/tmp/r")
    for flag in ("--launch-criteria", "--verification-window", "--rollback-trigger", "--go-no-go-authority"):
        assert f"{flag} <" in line, flag
    action = NextAction(subject_kind="unit", subject_id="core", command="mark-done", reason="x")
    assert "<" not in invocation_for(action, "/tmp/r")  # fully bound: no placeholders


def test_next_json_carries_structured_invocations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _store(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    capsys.readouterr()
    assert main(["next", "--root", root, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    action = next(a for a in document["actions"] if a["command"] == "freeze")
    assert action["argv"][:2] == ["vouch-mk2", "freeze"]
    assert action["placeholders"] == ["--pack-version"]


# ---- record-evidence help ----


def test_record_evidence_help_shows_the_argv_tail(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["record-evidence", "--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert "-- <argv" in out or "-- COMMAND" in out
    assert "exit status" in out  # the claim boundary, right where the operator reads it


def test_commands_reference_reflects_the_new_help_and_verb() -> None:
    text = (ROOT / "docs" / "commands.md").read_text()
    assert "## bundle-inventory" in text
    assert "-- <argv" in text or "-- COMMAND" in text


def test_quickstart_bundle_parenthetical_now_uses_the_verb() -> None:
    text = (ROOT / "docs" / "quickstart.md").read_text()
    assert "bundle-inventory" in text and "requires the Python API" not in text
    assert "its help does not show that tail yet" not in text
