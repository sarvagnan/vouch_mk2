"""Frozen acceptance pack for workflow-refresh@1 (D-55, vouch_mk2-bd4.4.26).

`check` compares an adopted project's generated `.github/workflows/vouch.yml` with the workflow
the running Vouch generates (`check.workflow-current`, a FAIL at every tier on any difference),
and `refresh-workflow` rewrites it. The oracle for the expected bytes is the file `init` wrote in
a fresh project, so it is independent of the new code. Every test here fails before the change,
except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import os
import stat
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
ROW = "check.workflow-current"
WORKFLOW = Path(".github") / "workflows" / "vouch.yml"


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


def _adopted(tmp_path: Path, tier: str = "lightweight", name: str = "proj") -> tuple[Path, bytes]:
    """A project `init` adopted; returns it and the workflow bytes `init` wrote."""
    project = tmp_path / name
    project.mkdir(parents=True)
    extra = () if tier == "lightweight" else ("--test-job", "unit-tests")
    _must("init", "--project", str(project), "--tier", tier, *extra)
    return project, (project / WORKFLOW).read_bytes()


def _check(
    project: Path, *, with_project: bool = True, root: Path | None = None,
) -> tuple[int, str]:
    store = project / ".vouch" if root is None else root
    argv = ["check", "--ci", "--root", str(store)]
    if with_project:
        argv += ["--project", str(project)]
    code, out, _ = _run(*argv)
    return code, out


def _row(out: str) -> tuple[str, str]:
    rows = [line for line in out.splitlines() if ROW in line.split()]
    assert len(rows) == 1, out
    verdict = rows[0].split()[0]
    return verdict, rows[0]


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink() and path.name != ".lock"
    }


# R1


@pytest.mark.parametrize("tier", ["lightweight", "standard", "high"])
def test_a_freshly_adopted_project_passes(tmp_path: Path, tier: str) -> None:
    project, _ = _adopted(tmp_path, tier)
    _, out = _check(project)
    assert _row(out)[0] == "PASS"


# R2


@pytest.mark.parametrize("tier", ["lightweight", "standard", "high"])
@pytest.mark.parametrize("change", ["appended", "edited", "emptied", "crlf"])
def test_a_changed_workflow_fails_at_every_tier(
    tmp_path: Path, tier: str, change: str,
) -> None:
    project, original = _adopted(tmp_path, tier)
    path = project / WORKFLOW
    if change == "appended":
        path.write_bytes(original + b"# local note\n")
    elif change == "edited":
        path.write_bytes(original.replace(b"python vouch.lock", b"python3 vouch.lock", 1))
        assert path.read_bytes() != original
    elif change == "emptied":
        path.write_bytes(b"")
    else:
        assert b"\r\n" not in original
        path.write_bytes(original.replace(b"\n", b"\r\n"))
    code, out = _check(project)
    verdict, line = _row(out)
    assert verdict == "FAIL", line
    assert "refresh-workflow" in line, line
    assert code == 1, out


# R3


@pytest.mark.parametrize(
    "case", ["missing", "symlink", "directory", "workflows-symlink", "github-symlink"],
)
def test_a_missing_or_irregular_workflow_fails(tmp_path: Path, case: str) -> None:
    project, original = _adopted(tmp_path)
    path = project / WORKFLOW
    path.unlink()
    elsewhere = tmp_path / "elsewhere"
    if case == "symlink":
        target = project / "elsewhere.yml"
        target.write_bytes(original)
        os.symlink(target, path)
    elif case == "directory":
        path.mkdir()
    elif case == "workflows-symlink":
        (elsewhere).mkdir()
        (elsewhere / "vouch.yml").write_bytes(original)
        (project / ".github" / "workflows").rmdir()
        os.symlink(elsewhere, project / ".github" / "workflows")
    elif case == "github-symlink":
        (elsewhere / "workflows").mkdir(parents=True)
        (elsewhere / "workflows" / "vouch.yml").write_bytes(original)
        (project / ".github" / "workflows").rmdir()
        (project / ".github").rmdir()
        os.symlink(elsewhere, project / ".github")
    code, out = _check(project)
    verdict, line = _row(out)
    assert verdict == "FAIL", line
    assert "refresh-workflow" in line, line
    assert code == 1, out


# R4


def test_without_a_project_root_the_row_is_not_applicable(tmp_path: Path) -> None:
    project, original = _adopted(tmp_path)
    (project / WORKFLOW).write_bytes(original + b"# local note\n")
    _, out = _check(project, with_project=False)
    assert _row(out)[0] == "NOT-APPLICABLE"


def test_a_bare_store_checked_alone_is_not_applicable_and_passes(tmp_path: Path) -> None:
    store = tmp_path / "bare"
    Store.init(str(store)).close()
    code, out, err = _run("check", "--ci", "--root", str(store))
    assert _row(out)[0] == "NOT-APPLICABLE"
    assert code == 0, (out, err)


def test_the_store_decides_applicability_not_the_project(tmp_path: Path) -> None:
    project, original = _adopted(tmp_path)
    (project / WORKFLOW).write_bytes(original + b"# local note\n")
    bare = tmp_path / "bare"
    Store.init(str(bare)).close()
    _, out = _check(project, root=bare)
    assert _row(out)[0] == "NOT-APPLICABLE"
    other = tmp_path / "other"
    other.mkdir()
    _, out = _check(other, root=project / ".vouch")
    verdict, line = _row(out)
    assert verdict == "FAIL", line


def test_a_store_that_init_did_not_write_is_not_applicable(tmp_path: Path) -> None:
    project = tmp_path / "plain"
    project.mkdir()
    Store.init(str(project / ".vouch")).close()
    (project / ".github" / "workflows").mkdir(parents=True)
    (project / WORKFLOW).write_text("name: not generated\n")
    _, out = _check(project)
    assert _row(out)[0] == "NOT-APPLICABLE"


def test_mk2s_own_store_is_not_applicable() -> None:
    _, out = _check(ROOT)
    assert _row(out)[0] == "NOT-APPLICABLE"


# R5


@pytest.mark.parametrize("start", ["edited", "missing", "hard-linked", "no-directories"])
def test_refresh_restores_the_generated_workflow(tmp_path: Path, start: str) -> None:
    project, original = _adopted(tmp_path)
    path = project / WORKFLOW
    sentinel = tmp_path / "sentinel.yml"
    if start == "edited":
        path.write_bytes(original + b"# local note\n")
    elif start == "missing":
        path.unlink()
    elif start == "hard-linked":
        path.unlink()
        sentinel.write_bytes(b"# outside the project\n")
        os.link(sentinel, path)
    else:
        path.unlink()
        (project / ".github" / "workflows").rmdir()
        (project / ".github").rmdir()
    if start != "no-directories":
        (project / ".github" / "workflows" / "ci.yml").write_text("name: project ci\n")
    store_before = _tree(project / ".vouch")
    _must("refresh-workflow", "--project", str(project))
    assert path.read_bytes() == original
    assert not path.is_symlink()
    if start == "hard-linked":
        assert sentinel.read_bytes() == b"# outside the project\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    if start != "no-directories":
        assert (project / ".github" / "workflows" / "ci.yml").read_text() == "name: project ci\n"
    assert _tree(project / ".vouch") == store_before
    leftovers = sorted(
        item.name for item in (project / ".github" / "workflows").iterdir()
        if item.name not in {"vouch.yml", "ci.yml"}
    )
    assert leftovers == []
    _, out = _check(project)
    assert _row(out)[0] == "PASS"


def test_refresh_of_a_current_workflow_changes_nothing(tmp_path: Path) -> None:
    project, original = _adopted(tmp_path)
    before = _tree(project)
    _must("refresh-workflow", "--project", str(project))
    assert _tree(project) == before
    assert (project / WORKFLOW).read_bytes() == original


# R6


def test_refresh_refuses_a_project_init_did_not_adopt(tmp_path: Path) -> None:
    project = tmp_path / "plain"
    project.mkdir()
    code, out, err = _run("refresh-workflow", "--project", str(project))
    assert code == 2, (out, err)
    assert "config.json" in err, err
    assert sorted(item.name for item in project.iterdir()) == []


@pytest.mark.parametrize("case", ["file", "file-identical", "workflows-dir", "github-dir"])
def test_refresh_refuses_a_symlinked_path(tmp_path: Path, case: str) -> None:
    project, original = _adopted(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if case in {"file", "file-identical"}:
        target = elsewhere / "vouch.yml"
        target.write_bytes(original if case == "file-identical" else b"# not generated\n")
        (project / WORKFLOW).unlink()
        os.symlink(target, project / WORKFLOW)
    elif case == "workflows-dir":
        (elsewhere / "vouch.yml").write_bytes(b"# not generated\n")
        workflows = project / ".github" / "workflows"
        (workflows / "vouch.yml").unlink()
        workflows.rmdir()
        os.symlink(elsewhere, workflows)
    else:
        (elsewhere / "workflows").mkdir()
        (elsewhere / "workflows" / "vouch.yml").write_bytes(b"# not generated\n")
        github = project / ".github"
        (github / "workflows" / "vouch.yml").unlink()
        (github / "workflows").rmdir()
        github.rmdir()
        os.symlink(elsewhere, github)
    outside_before = _tree(elsewhere)
    project_before = _tree(project)
    code, out, err = _run("refresh-workflow", "--project", str(project))
    assert code == 2, (out, err)
    assert "symlink" in err, err
    assert _tree(elsewhere) == outside_before
    assert _tree(project) == project_before
    if case != "file-identical":
        assert original not in _tree(elsewhere).values()


# R7


def test_docs_state_the_row_and_the_re_pin_step() -> None:
    quickstart = " ".join((ROOT / "docs" / "quickstart.md").read_text().split())
    assert "refresh-workflow" in quickstart
    assert (
        "After you re-pin `vouch.lock`, run `python3 vouch.lock refresh-workflow --project .`"
    ) in quickstart
    boundary = " ".join((ROOT / "docs" / "claim-boundary.md").read_text().split())
    assert (
        "For a store `init` wrote, checked with `--project`, `check.workflow-current` fails when "
        "`.github/workflows/vouch.yml` differs from the workflow the running Vouch generates."
    ) in boundary
    commands = (ROOT / "docs" / "commands.md").read_text()
    assert "refresh-workflow" in commands
