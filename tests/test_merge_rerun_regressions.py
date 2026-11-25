"""Owner regressions for merge-rerun@1 (D-59), outside the frozen pack."""

from __future__ import annotations

import fcntl
import io
import os
import subprocess
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.store import Store


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _must_git(project: Path, *argv: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(project), *argv),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _change(project: Path, name: str) -> None:
    root = project / ".vouch"
    code, output = _run("specify", "--root", str(root), "--name", name, "--version", "v1")
    assert code == 0, output
    assert _run("generate", "--root", str(root))[0] == 0


def _commit_store(project: Path, message: str) -> None:
    _must_git(project, "add", "--", ".vouch")
    _must_git(project, "commit", "-m", message)


def _conflicted_repository(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _must_git(project, "init", "-b", "main")
    _must_git(project, "config", "user.name", "Vouch Test")
    _must_git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    Store.init(str(project / ".vouch")).close()
    _change(project, "shared")
    _must_git(project, "add", "--", ".gitignore")
    _commit_store(project, "shared vouch history")
    _must_git(project, "checkout", "-b", "incoming")
    _change(project, "yours")
    _commit_store(project, "incoming vouch event")
    _must_git(project, "checkout", "main")
    _change(project, "mine")
    _commit_store(project, "current vouch event")
    merged = subprocess.run(
        ("git", "-C", str(project), "merge", "--no-edit", "incoming"),
        check=False,
        capture_output=True,
    )
    assert merged.returncode != 0
    return project


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != ".lock"
    }


def test_a_root_moved_while_a_rerun_waits_for_the_lock_is_not_overwritten(tmp_path: Path) -> None:
    """Astra review: an unreadable root name must not count as "not recovered" for a moved root."""
    project = _conflicted_repository(tmp_path)
    root = project / ".vouch"
    code, output = _run("merge-git-stages", "--root", str(root), "--actor", "first")
    assert code == 0, output
    _change(project, "later")
    index_before = _must_git(project, "ls-files", "--stage", "--", ".vouch")
    recovered = _tree(root)

    lock_fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    result: dict[str, tuple[int, str]] = {}
    worker = threading.Thread(
        target=lambda: result.update(
            rerun=_run("merge-git-stages", "--root", str(root), "--actor", "second")
        )
    )
    moved = project / "moved-vouch"
    try:
        worker.start()
        deadline = time.monotonic() + 60
        while not any(project.glob(".vouch-git-merge-*")):
            assert worker.is_alive() and time.monotonic() < deadline, result
            time.sleep(0.01)
        root.rename(moved)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        worker.join(timeout=120)

    code, output = result["rerun"]
    assert code == 2, output
    assert "git.concurrent-update" in output, output
    assert not root.exists()
    assert _tree(moved) == recovered
    assert _must_git(project, "ls-files", "--stage", "--", ".vouch") == index_before
