"""Owner regressions for merge-check-upgrade@1 (D-53), from Astra's implementation review."""

from __future__ import annotations

import base64
import io
import json
import shutil
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "merge-check-upgrade"


def _cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _store(state_format: int, target: Path) -> Path:
    document = json.loads((FIXTURES / f"format-{state_format}-store.json").read_text())
    for relative, encoded in cast(dict[str, str], document["files"]).items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    return target


def _upgraded(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    code, out, err = _cli("upgrade", "--root", str(target), "--actor", "t")
    assert code == 0, (out, err)
    return target


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.mkdir()
    try:
        return (directory / "caseprobe").exists()
    finally:
        shutil.rmtree(probe)


def test_a_temporary_directory_spelled_as_an_input_is_refused_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem")
    base = _store(5, tmp_path / "base")
    head = _upgraded(base, tmp_path / "head")
    merged = Path(shutil.copytree(head, tmp_path / "Merged"))
    before = _tree(merged)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "merged"))
    code, out, err = _cli(
        "check-merge", "--base", str(base), "--head", str(head), "--merged", str(merged)
    )
    assert code == 1, (out, err)
    assert out.startswith("FAIL check-merge: temporary directory"), out
    assert _tree(merged) == before
