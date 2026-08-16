from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main
from vouch_mk2.mcp import serve
from vouch_mk2.store import Store
from vouch_mk2.store.upgrade import UPGRADERS


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
    assert json.loads((root / "state.json").read_bytes())["state_format"] == 1


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
