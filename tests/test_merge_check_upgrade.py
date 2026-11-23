"""Frozen acceptance pack for merge-check-upgrade@1 (D-53, vouch_mk2-bd4.4.25).

`check-merge` upgrades a private copy of a BASE or HEAD store that is at an older state format
than the running Vouch, then replays and compares as before. The fixtures are stores written by
the code of their time (`tests/fixtures/merge-check-upgrade/`, each naming its source commit).
Every test here fails before the change, except tests marked ``# guard:``. The implementer may
not edit this file or the fixtures.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store.codec import CURRENT_STATE_FORMAT

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "merge-check-upgrade"
CLAIM_BOUNDARY = ROOT / "docs" / "claim-boundary.md"

Json = dict[str, object]


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _must(*argv: str) -> str:
    code, out, err = _run(*argv)
    assert code == 0, (argv, out, err)
    return out


def _fixture(state_format: int) -> Json:
    document: object = json.loads((FIXTURES / f"format-{state_format}-store.json").read_text())
    assert isinstance(document, dict)
    return cast(Json, document)


def _store(state_format: int, target: Path) -> Path:
    """Write the fixture store for this format into TARGET."""
    files = cast(dict[str, str], _fixture(state_format)["files"])
    for relative, encoded in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    return target


def _copy(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def _upgraded(source: Path, target: Path) -> Path:
    _copy(source, target)
    _must("upgrade", "--root", str(target), "--actor", "t")
    return target


def _green_b(root: Path) -> Path:
    _must(
        "record-evidence", "--root", str(root), "--unit", "b@v1", "--kind", "green",
        "--actor", "t", "--", sys.executable, "-c", "pass",
    )
    return root


def _check(base: Path, head: Path, merged: Path) -> tuple[int, str, str]:
    return _run(
        "check-merge", "--base", str(base), "--head", str(head), "--merged", str(merged)
    )


def _lines(text: str) -> list[str]:
    lines = text.splitlines()
    assert lines, "no output"
    return lines


def _upgrade_line(label: str, state_format: int) -> str:
    return (
        f"  upgraded a private copy of {label} from format {state_format} "
        f"to {CURRENT_STATE_FORMAT}"
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# R1


@pytest.mark.parametrize("state_format", [4, 5, 6])
def test_an_upgrade_pull_request_passes(tmp_path: Path, state_format: int) -> None:
    base = _store(state_format, tmp_path / "base")
    head = _upgraded(base, tmp_path / "head")
    merged = _copy(head, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    assert code == 0, (out, err)
    lines = _lines(out)
    assert lines[0] == "PASS check-merge: replay matches MERGED (no-op)", out
    assert _upgrade_line("BASE", state_format) in lines, out
    assert not any("of HEAD" in line for line in lines), out


# R2


def test_an_upgrade_with_new_evidence_passes(tmp_path: Path) -> None:
    base = _store(5, tmp_path / "base")
    head = _green_b(_upgraded(base, tmp_path / "head"))
    merged = _copy(head, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    assert code == 0, (out, err)
    lines = _lines(out)
    assert lines[0] == "PASS check-merge: replay matches MERGED (fast-forward)", out
    assert _upgrade_line("BASE", 5) in lines, out


# R3


def test_an_older_head_is_upgraded_too(tmp_path: Path) -> None:
    head = _store(5, tmp_path / "head")
    base = _green_b(_upgraded(head, tmp_path / "base"))
    merged = _copy(base, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    assert code == 0, (out, err)
    lines = _lines(out)
    assert lines[0] == "PASS check-merge: replay matches MERGED (no-op)", out
    assert _upgrade_line("HEAD", 5) in lines, out
    assert not any("of BASE" in line for line in lines), out


def test_both_older_inputs_are_upgraded_in_order(tmp_path: Path) -> None:
    base = _store(5, tmp_path / "base")
    head = _store(6, tmp_path / "head")
    merged = _upgraded(base, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    lines = _lines(out)
    assert _upgrade_line("BASE", 5) in lines, (out, err)
    assert _upgrade_line("HEAD", 6) in lines, (out, err)
    assert lines.index(_upgrade_line("BASE", 5)) < lines.index(_upgrade_line("HEAD", 6))
    assert lines[0].startswith(("PASS check-merge", "FAIL check-merge")), out
    assert code in (0, 1)


# R4


def test_a_merged_store_missing_the_heads_work_still_fails(tmp_path: Path) -> None:
    base = _store(5, tmp_path / "base")
    head = _green_b(_upgraded(base, tmp_path / "head"))
    merged = _upgraded(base, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    assert code == 1, (out, err)
    lines = _lines(out)
    assert lines[0] == "FAIL check-merge: replay differs from MERGED", out
    assert _upgrade_line("BASE", 5) in lines, out
    assert any("b@v1" in line for line in lines), out


def test_a_hand_edited_upgraded_head_still_fails(tmp_path: Path) -> None:
    base = _store(5, tmp_path / "base")
    head = _upgraded(base, tmp_path / "head")
    state = head / "state.json"
    document = cast(Json, json.loads(state.read_text()))
    for unit in cast(list[Json], document["units"]):
        if unit["name"] == "a":
            unit["status"] = "in-progress"
    state.write_text(json.dumps(document))
    merged = _copy(head, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    assert code == 1, (out, err)
    assert _lines(out)[0].startswith("FAIL check-merge"), out
    assert "merge.head-history-invalid" in out, out
    assert _upgrade_line("BASE", 5) in _lines(out), out


# R5


def test_inputs_are_never_written_and_no_temporary_directory_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _store(5, tmp_path / "base")
    head = _green_b(_upgraded(base, tmp_path / "head"))
    merged = _copy(head, tmp_path / "merged")
    short = _upgraded(base, tmp_path / "short")
    roots = (base, head, merged, short)
    for root in roots:
        # A leftover temporary file that a writable open's cleanup would remove.
        (root / ".state-interrupted.tmp").write_bytes(b"partial")
    before = [_tree(root) for root in roots]
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))

    assert _check(base, head, merged)[0] == 0
    assert _check(base, head, short)[0] == 1
    assert _check(head, base, merged)[0] in (0, 1)

    assert [_tree(root) for root in roots] == before
    assert list(scratch.iterdir()) == []


# R6


def test_an_older_merged_store_still_fails_and_is_named(tmp_path: Path) -> None:
    base = _store(5, tmp_path / "base")
    head = _upgraded(base, tmp_path / "head")
    merged = _store(5, tmp_path / "merged")
    code, out, err = _check(base, head, merged)
    assert code == 1, (out, err)
    first = _lines(out)[0]
    assert first.startswith("FAIL check-merge"), out
    assert "MERGED" in first and "upgrade" in first, out


# R7


@pytest.mark.parametrize("side", ["BASE", "HEAD"])
def test_a_store_from_before_the_event_log_fails_with_its_reason(
    tmp_path: Path, side: str,
) -> None:
    old = _store(3, tmp_path / "old")
    new = _upgraded(old, tmp_path / "new")
    merged = _copy(new, tmp_path / "merged")
    base, head = (old, new) if side == "BASE" else (new, old)
    code, out, err = _check(base, head, merged)
    assert code == 1, (out, err)
    first = _lines(out)[0]
    assert first.startswith("FAIL check-merge"), out
    assert side in first and "before the event log" in first, out


# R8


def test_a_store_newer_than_vouch_still_fails(tmp_path: Path) -> None:  # guard: as today
    base = _store(5, tmp_path / "base")
    head = _upgraded(base, tmp_path / "head")
    merged = _copy(head, tmp_path / "merged")
    for root in (base,):
        state = root / "state.json"
        document = cast(Json, json.loads(state.read_text()))
        document["state_format"] = CURRENT_STATE_FORMAT + 1
        state.write_text(json.dumps(document))
    code, out, err = _check(base, head, merged)
    assert code == 1, (out, err)
    assert _lines(out)[0].startswith("FAIL check-merge"), out
    assert "newer" in out, out


def test_the_fixtures_are_the_formats_they_name() -> None:  # guard: fixture integrity
    for state_format in (3, 4, 5, 6):
        fixture = _fixture(state_format)
        assert fixture["state_format"] == state_format
        files = cast(dict[str, str], fixture["files"])
        state = json.loads(base64.b64decode(files["state.json"]))
        assert state.get("state_format", 0) == state_format
        assert isinstance(fixture["source_commit"], str) and len(fixture["source_commit"]) == 40


# R9


def test_claim_boundary_states_the_upgrade() -> None:
    text = " ".join(CLAIM_BOUNDARY.read_text().split())
    for sentence in (
        "When BASE or HEAD is at an older state format than the running Vouch, `check-merge` "
        "upgrades a private copy of it first, as `upgrade` would, so a pull request that "
        "upgrades the store is checked too.",
        "MERGED is never upgraded, and a store older than format 4, from before the event log, "
        "fails the check.",
    ):
        assert sentence in text, sentence
