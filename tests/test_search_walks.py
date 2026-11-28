"""Frozen acceptance pack for self-hosted unit search-walks@1 (D-63, vouch_mk2-867).

`refresh-workflow`, `check.workflow-current` and `build-lock --output` walk to their target one
directory descriptor at a time. A directory the walk only passes through needs search permission,
not read permission; the directory a file is written into must still be readable, because the
write syncs it. Each test puts existing directories at mode 0111 (search only) or 0311 (write and
search) and restores 0755 afterwards. Expected bytes come from the same command run where nothing
is restricted. Every test here fails before the change, except tests marked ``# guard:``. The
implementer may not edit this file.
"""

from __future__ import annotations

import io
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main

ROW = "check.workflow-current"
WORKFLOW = Path(".github") / "workflows" / "vouch.yml"
STALE = b"# a stale workflow\n"

pytestmark = [
    pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions"),
    pytest.mark.skipif(
        not (hasattr(os, "O_SEARCH") or hasattr(os, "O_PATH")),
        reason="the platform has no search-only directory open",
    ),
]


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


@contextmanager
def _modes(changes: dict[Path, int]) -> Iterator[None]:
    """Set each directory's mode for the block, then restore 0755 so tmp_path can be removed."""
    try:
        for path, mode in changes.items():
            path.chmod(mode)
        for path, mode in changes.items():
            assert stat.S_IMODE(path.stat().st_mode) == mode, path
        yield
        for path, mode in changes.items():
            assert stat.S_IMODE(path.stat().st_mode) == mode, path
    finally:
        for path in changes:
            path.chmod(0o755)


def _adopted(tmp_path: Path, name: str) -> Path:
    project = tmp_path / name
    project.mkdir()
    _must("init", "--project", str(project))
    return project


def _expected_workflow(tmp_path: Path) -> bytes:
    """The workflow `init` writes in a fresh project with nothing restricted."""
    return (_adopted(tmp_path, "reference") / WORKFLOW).read_bytes()


def _row(out: str) -> tuple[str, str]:
    rows = [line for line in out.splitlines() if ROW in line.split()]
    assert len(rows) == 1, out
    return rows[0].split()[0], rows[0]


def _check(project: Path) -> tuple[int, str]:
    code, out, _ = _run("check", "--ci", "--root", str(project / ".vouch"), "--project", str(project))
    return code, out


def _restricted(project: Path, names: tuple[str, ...], mode: int) -> dict[Path, int]:
    paths = {"project": project, ".github": project / ".github",
             "workflows": project / ".github" / "workflows"}
    return {paths[name]: mode for name in names}


# R1 — refresh-workflow passes through search-only directories


@pytest.mark.parametrize("names", [("project",), (".github",), ("project", ".github")])
def test_refresh_workflow_passes_through_search_only_directories(
    tmp_path: Path, names: tuple[str, ...],
) -> None:
    expected = _expected_workflow(tmp_path)
    project = _adopted(tmp_path, "project")
    (project / WORKFLOW).write_bytes(STALE)

    with _modes(_restricted(project, names, 0o111)):
        code, out, err = _run("refresh-workflow", "--project", str(project))
        assert code == 0, (out, err)

    assert (project / WORKFLOW).read_bytes() == expected


def test_refresh_workflow_creates_workflows_inside_a_write_and_search_only_github(
    tmp_path: Path,
) -> None:
    expected = _expected_workflow(tmp_path)
    project = _adopted(tmp_path, "project")
    workflows = project / ".github" / "workflows"
    (project / WORKFLOW).unlink()
    workflows.rmdir()

    with _modes({project / ".github": 0o311}):
        code, out, err = _run("refresh-workflow", "--project", str(project))
        assert code == 0, (out, err)

    assert stat.S_IMODE(workflows.stat().st_mode) == 0o755
    assert (project / WORKFLOW).read_bytes() == expected


# R2 — check.workflow-current reads through search-only directories


@pytest.mark.parametrize(
    "names",
    [("project",), (".github",), ("workflows",), ("project", ".github", "workflows")],
)
def test_check_reads_the_workflow_through_search_only_directories(
    tmp_path: Path, names: tuple[str, ...],
) -> None:
    project = _adopted(tmp_path, "project")

    with _modes(_restricted(project, names, 0o111)):
        code, out = _check(project)

    verdict, line = _row(out)
    assert verdict == "PASS", line
    assert code == 0, out


def test_check_compares_a_stale_workflow_through_search_only_directories(
    tmp_path: Path,
) -> None:
    project = _adopted(tmp_path, "project")
    (project / WORKFLOW).write_bytes(STALE)
    _, unrestricted = _check(project)
    expected_verdict, expected_line = _row(unrestricted)
    assert expected_verdict == "FAIL", expected_line

    with _modes(_restricted(project, ("project", ".github"), 0o111)):
        code, out = _check(project)
        verdict, line = _row(out)
        assert line == expected_line, (line, expected_line)
        assert "Permission denied" not in line, line
        assert code == 1, out

        code, out, err = _run("refresh-workflow", "--project", str(project))
        assert code == 0, (out, err)
        code, out = _check(project)
        assert _row(out)[0] == "PASS", out
        assert code == 0, out


def test_check_still_reports_a_workflows_directory_it_cannot_search(tmp_path: Path) -> None:
    # guard: a directory without search permission is reported in the row before and after.
    project = _adopted(tmp_path, "project")

    with _modes({project / ".github" / "workflows": 0o000}):
        code, out = _check(project)

    verdict, line = _row(out)
    assert verdict == "FAIL", line
    assert "Permission denied" in line, line
    assert code == 1, out


# R3 — build-lock --output passes through search-only directories


def _reference_lock(tmp_path: Path) -> tuple[bytes, str]:
    reference = tmp_path / "reference" / "vouch.lock"
    out = _must("build-lock", "--output", str(reference))
    return reference.read_bytes(), out.split()[1]


@pytest.mark.parametrize("existing", [True, False], ids=["existing-parent", "new-parent"])
def test_build_lock_writes_through_a_search_only_ancestor(
    tmp_path: Path, existing: bool,
) -> None:
    expected, digest = _reference_lock(tmp_path)
    ancestor = tmp_path / "ancestor"
    parent = ancestor / "out"
    if existing:
        parent.mkdir(parents=True)
        parent.chmod(0o700)
    else:
        ancestor.mkdir()
    mode = 0o111 if existing else 0o311
    target = parent / "vouch.lock"

    with _modes({ancestor: mode}):
        code, out, err = _run("build-lock", "--output", str(target))
        assert code == 0, (out, err)
        assert out.split()[1] == digest, out

    assert target.read_bytes() == expected
    assert stat.S_IMODE(parent.stat().st_mode) == (0o700 if existing else 0o755)


def test_build_lock_follows_a_symlink_to_a_search_only_directory(tmp_path: Path) -> None:
    expected, _ = _reference_lock(tmp_path)
    ancestor = tmp_path / "ancestor"
    (ancestor / "out").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(ancestor, target_is_directory=True)

    with _modes({ancestor: 0o111}):
        code, out, err = _run("build-lock", "--output", str(link / "out" / "vouch.lock"))
        assert code == 0, (out, err)

    assert (ancestor / "out" / "vouch.lock").read_bytes() == expected


# R4 — the directory written into must still be readable


@pytest.mark.parametrize("through_symlink", [False, True], ids=["direct", "symlink"])
def test_build_lock_still_needs_to_read_the_directory_it_writes_into(
    tmp_path: Path, through_symlink: bool,
) -> None:
    # guard: the lock's parent, named directly or through a symlink, is opened for reading
    # before and after the change.
    parent = tmp_path / "out"
    parent.mkdir()
    named = parent
    if through_symlink:
        named = tmp_path / "link"
        named.symlink_to(parent, target_is_directory=True)

    with _modes({parent: 0o311}):
        code, out, err = _run("build-lock", "--output", str(named / "vouch.lock"))
        assert code == 2, (out, err)
        assert "Permission denied" in err, err

    assert list(parent.iterdir()) == []


def test_refresh_workflow_still_needs_to_read_the_workflows_directory(tmp_path: Path) -> None:
    # guard: .github/workflows is opened for reading before and after the change.
    project = _adopted(tmp_path, "project")
    (project / WORKFLOW).write_bytes(STALE)

    with _modes({project / ".github" / "workflows": 0o311}):
        code, out, err = _run("refresh-workflow", "--project", str(project))
        assert code == 2, (out, err)
        assert "Permission denied" in err, err

    assert sorted(path.name for path in (project / ".github" / "workflows").iterdir()) == [
        "vouch.yml"
    ]
    assert (project / WORKFLOW).read_bytes() == STALE
