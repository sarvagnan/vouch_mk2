"""Owner regressions for owner-modes@1 (D-58), outside the frozen pack."""

from __future__ import annotations

import io
import os
import stat
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_init_walks_through_a_search_only_existing_directory(tmp_path: Path) -> None:
    """Codex review on PR #53: the descriptor walk must not need read access to traverse."""
    project = tmp_path / "project"
    workflows = project / ".github" / "workflows"
    workflows.mkdir(parents=True)
    github = project / ".github"
    github.chmod(0o111)
    try:
        code, out, err = _run("init", "--project", str(project))
        assert code == 0, (out, err)
        assert stat.S_IMODE(github.stat().st_mode) == 0o111
        assert (workflows / "vouch.yml").is_file()
    finally:
        github.chmod(0o755)
