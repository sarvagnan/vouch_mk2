"""Acceptance pack for self-hosted unit init-recovery@1 (D-19; external review #4).

`init_project`'s docstring promises "every precondition ... so a refusal never leaves a
half-initialized project", but the preflight cannot see every failure: with `.github/workflows`
non-writable the store, config and lock are created first, the workflow write fails, and the
retry refuses with "store already exists" — an unrecoverable half-created project. Separately a
lock/workflow collision writes `.vouch-suggested` files and still exits 0, leaving config bound
to a lock that is not the one installed.

Every test here FAILED against the pre-change tree — except tests marked `# guard:`.
"""

from __future__ import annotations

import io
import json
import os
import stat
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.lock import build_lock

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


@pytest.fixture(scope="module")
def lock_bytes() -> bytes:
    return build_lock(str(ROOT / "src"))


def _project(tmp_path: Path, lock_bytes: bytes) -> tuple[Path, Path]:
    project = tmp_path / "proj"
    project.mkdir()
    lock = project / "vouch.lock"
    lock.write_bytes(lock_bytes)
    return project, lock


# ---- a failed init leaves nothing behind, and the retry works ----


def test_a_failed_init_leaves_no_partial_project_and_retry_succeeds(tmp_path: Path, lock_bytes: bytes) -> None:
    project, lock = _project(tmp_path, lock_bytes)
    workflows = project / ".github" / "workflows"
    workflows.mkdir(parents=True)
    os.chmod(workflows, stat.S_IRUSR | stat.S_IXUSR)  # readable, not writable
    try:
        code, output = _run("init", "--project", str(project), "--lock", str(lock))
        assert code == 2, output
        assert not (project / ".vouch").exists(), "a refused init must leave no store behind"
        assert not (project / ".vouch" / "config.json").exists()
    finally:
        os.chmod(workflows, stat.S_IRWXU)
    code, output = _run("init", "--project", str(project), "--lock", str(lock))
    assert code == 0, output
    assert (project / ".vouch" / "state.json").exists()
    assert (project / ".github" / "workflows" / "vouch.yml").exists()


def test_a_successful_init_always_leaves_self_check_passing(tmp_path: Path, lock_bytes: bytes) -> None:
    # guard: holds when there is no collision; it must keep holding after the collision fix.
    project, lock = _project(tmp_path, lock_bytes)
    code, output = _run("init", "--project", str(project), "--lock", str(lock))
    assert code == 0, output
    config = json.loads((project / ".vouch" / "config.json").read_text())
    from vouch_mk2.lock import lock_digest

    assert config["lock_digest"] == lock_digest((project / "vouch.lock").read_bytes()), (
        "config must be bound to the lock actually installed in the project"
    )


# ---- a collision is not success ----


def test_a_lock_collision_does_not_report_success(tmp_path: Path, lock_bytes: bytes) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "vouch.lock").write_bytes(b"an older, different lock")
    source = tmp_path / "new.lock"
    source.write_bytes(lock_bytes)
    code, output = _run("init", "--project", str(project), "--lock", str(source))
    assert code == 3, output  # distinct from 0 (done) and 2 (refused)
    assert "suggested" in output.lower()
    assert "vouch.lock" in output
    # It must say what to do next, and must not bind config to a lock that is not installed.
    assert "review" in output.lower() or "replace" in output.lower()
    if (project / ".vouch" / "config.json").exists():
        config = json.loads((project / ".vouch" / "config.json").read_text())
        from vouch_mk2.lock import lock_digest

        assert config["lock_digest"] == lock_digest((project / "vouch.lock").read_bytes()), (
            "config must never be bound to the suggested lock while a different one is installed"
        )


def test_an_identical_lock_is_not_a_collision(tmp_path: Path, lock_bytes: bytes) -> None:
    # guard: re-running init with the same lock bytes is not a conflict.
    project, lock = _project(tmp_path, lock_bytes)
    code, output = _run("init", "--project", str(project), "--lock", str(lock))
    assert code == 0, output


# ---- the store is still refused when it genuinely exists ----


def test_an_existing_store_is_still_refused(tmp_path: Path, lock_bytes: bytes) -> None:
    # guard: init never re-initializes a real store.
    project, lock = _project(tmp_path, lock_bytes)
    assert _run("init", "--project", str(project), "--lock", str(lock))[0] == 0
    code, output = _run("init", "--project", str(project), "--lock", str(lock))
    assert code == 2 and "already exists" in output.lower()


def test_init_exit_codes_are_documented() -> None:
    import re

    text = (ROOT / "docs" / "commands.md").read_text() + (ROOT / "docs" / "quickstart.md").read_text()
    # `init`'s three outcomes must be documented where an adopter reads them: 0 initialised,
    # 2 refused (nothing written), 3 suggestions written and action required.
    section = re.search(r"##\s*init\b(.*?)(?=\n##\s|\Z)", text, re.S)
    assert section, "docs/commands.md must document init"
    body = section.group(1)
    assert re.search(r"\b3\b", body) and "suggest" in body.lower(), body[:400]
    assert re.search(r"\b2\b", body), body[:400]
