from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from pytest import CaptureFixture, MonkeyPatch

from vouch_mk2.cli import main
from vouch_mk2.core.model import Deviation, DeviationStatus, DeviationType, Snapshot, Unit, UnitRef, UnitStatus
from vouch_mk2.hook import HookError, hook_message, resolve_root
from vouch_mk2.store import Store
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


def _store(path: Path) -> None:
    Store.init(str(path)).close()


def _drift(path: Path) -> None:
    with Store.open(str(path)) as store:
        commit_snapshot_fixture(
            store,
            Snapshot(
                revision=1,
                units=(Unit("u", "v1", UnitStatus.IN_PROGRESS, acceptance_pack_version="p1"),),
                deviations=(
                    Deviation(
                        "d",
                        DeviationType.PACK_DEFECT,
                        UnitRef("u", "v1"),
                        DeviationStatus.OPEN,
                    ),
                ),
            ),
        )


def test_resolve_root_from_nested_nonexistent_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "src/deep"
    nested.mkdir(parents=True)
    root = project / ".vouch"
    _store(root)
    assert resolve_root(str(nested / "future.py")) == str(root)


def test_resolve_root_unrelated_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "unrelated/file.py"
    target.parent.mkdir()
    assert resolve_root(str(target)) is None


def test_resolve_root_never_calls_getcwd(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = project / ".vouch"
    _store(root)

    def forbidden() -> str:
        raise AssertionError("cwd must not be consulted")

    monkeypatch.setattr(os, "getcwd", forbidden)
    assert resolve_root(str(project / "future.py")) == str(root)


def test_resolve_root_refuses_symlinked_store(tmp_path: Path) -> None:
    real = tmp_path / "real-store"
    _store(real)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".vouch").symlink_to(real, target_is_directory=True)
    with pytest.raises(HookError, match="symlinked .vouch"):
        resolve_root(str(project / "file.py"))


@pytest.mark.parametrize(("drift", "verdict"), ((False, "pass"), (True, "fail")))
def test_hook_pass_and_fail_are_warn_only(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    drift: bool,
    verdict: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = project / ".vouch"
    _store(root)
    if drift:
        _drift(root)
    assert main(["hook", "--edit-target", str(project / "file.py")]) == 0
    output = capsys.readouterr().out
    assert f"vouch: {verdict}" in output
    assert "mechanical FAIL rows" in output
    assert "tier=lightweight" in output
    assert f"check --root {root}" in output


def test_hook_no_store_and_error_both_exit_zero(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    target = tmp_path / "file.py"
    assert main(["hook", "--edit-target", str(target)]) == 0
    assert "no store above" in capsys.readouterr().out

    project = tmp_path / "project"
    project.mkdir()
    (project / ".vouch").symlink_to(tmp_path / "missing", target_is_directory=True)
    assert main(["hook", "--edit-target", str(project / "file.py")]) == 0
    assert "vouch: hook error:" in capsys.readouterr().out


def test_hook_reads_claude_stdin_payload(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _store(project / ".vouch")
    payload = f'{{"tool_input":{{"file_path":"{project / "file.py"}"}}}}'
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main(["hook"]) == 0
    assert "vouch: pass" in capsys.readouterr().out


def test_hook_missing_or_malformed_stdin_is_warn_only(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["hook"]) == 0
    assert capsys.readouterr().out == "vouch: no edit target; skipping\n"

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["hook"]) == 0
    assert "vouch: hook error:" in capsys.readouterr().out


def test_resolve_root_stops_at_repository_boundary(tmp_path: Path) -> None:
    # A planted ancestor store above an unrelated repo must never be picked up:
    # the walk stops at the first `.git` boundary crossed without a store.
    planted = tmp_path / ".vouch"
    Store.init(str(planted)).close()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".git").mkdir()
    assert resolve_root(str(repo / "src" / "app.py")) is None
    # But a store INSIDE the repo is found from a nested target.
    inner = repo / ".vouch"
    Store.init(str(inner)).close()
    assert resolve_root(str(repo / "src" / "deep" / "app.py")) == str(inner)


def test_hook_does_not_block_on_held_store_lock(tmp_path: Path) -> None:
    import fcntl

    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    Store.init(str(root / ".vouch")).close()
    holder = open(root / ".vouch" / ".lock", "r+b")
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        message = hook_message(str(root / "src" / "x.py"))
        assert message.startswith("vouch: ")
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_hook_does_not_sweep_the_store(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    Store.init(str(root / ".vouch")).close()
    orphan = root / ".vouch" / ".state-victim.tmp"
    orphan.write_bytes(b"orphan")
    hook_message(str(root / "src" / "x.py"))
    assert orphan.exists()
