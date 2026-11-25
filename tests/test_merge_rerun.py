"""Frozen acceptance pack for self-hosted unit merge-rerun@1 (D-59).

The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _git(project: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *argv),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "GIT_EDITOR": "true"},
    )


def _must_git(project: Path, *argv: str) -> str:
    result = _git(project, *argv)
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _commit_store(project: Path, message: str) -> None:
    _must_git(project, "add", "--", ".vouch")
    _must_git(project, "commit", "-m", message)


def _change(project: Path, name: str) -> None:
    root = project / ".vouch"
    code, output = _run("specify", "--root", str(root), "--name", name, "--version", "v1")
    assert code == 0, output
    assert _run("generate", "--root", str(root))[0] == 0


def _conflicted_repository(tmp_path: Path, *, operation: str) -> Path:
    """A real Git conflict under .vouch: main adds unit "mine", incoming adds unit "yours".

    The project path contains a space, so the refusal's commands must quote the root.
    """
    project = tmp_path / "vouch project"
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

    if operation == "merge":
        result = _git(project, "merge", "--no-edit", "incoming")
    else:
        _must_git(project, "checkout", "incoming")
        result = _git(project, "rebase", "main")
    assert result.returncode != 0, result.stdout
    assert _unmerged(project), result.stdout
    return project


def _unmerged(project: Path) -> str:
    return _must_git(project, "diff", "--name-only", "--diff-filter=U", "--", ".vouch")


def _index(project: Path) -> str:
    return _must_git(project, "ls-files", "--stage", "--", ".vouch")


def _tree(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".lock":
            continue
        if path.is_symlink():
            result[relative] = b"SYMLINK\0" + os.readlink(path).encode()
        elif path.is_file():
            result[relative] = path.read_bytes()
    return result


def _units(root: Path) -> set[str]:
    with Store.open(str(root), read_only=True) as store:
        return {unit.name for unit in store.read_snapshot().units}


def _recover(root: Path, actor: str) -> tuple[int, str]:
    return _run("merge-git-stages", "--root", str(root), "--actor", actor)


def _assert_already_recovered(code: int, output: str, root: Path) -> None:
    assert code == 2, output
    assert "git.already-recovered" in output, output
    assert shlex.join(("git", "add", "--", str(root))) in output, output
    assert shlex.join(("git", "checkout", "--conflict=merge", "--", str(root))) in output, output


@pytest.mark.parametrize(
    ("operation", "second_actor"),
    (("merge", "second-owner"), ("merge", "recovery-owner"), ("rebase", "second-owner")),
    ids=("merge-other-actor", "merge-same-actor", "rebase"),
)
def test_a_rerun_before_staging_refuses_and_keeps_the_first_result(
    tmp_path: Path, operation: str, second_actor: str
) -> None:
    project = _conflicted_repository(tmp_path, operation=operation)
    root = project / ".vouch"
    index_before = _index(project)
    code, output = _recover(root, "recovery-owner")
    assert code == 0, output
    first_tree = _tree(root)

    code, output = _recover(root, second_actor)

    _assert_already_recovered(code, output, root)
    assert _tree(root) == first_tree
    assert _index(project) == index_before
    assert _units(root) == {"shared", "mine", "yours"}


def test_a_rerun_keeps_work_recorded_on_the_recovered_store(tmp_path: Path) -> None:
    project = _conflicted_repository(tmp_path, operation="merge")
    root = project / ".vouch"
    index_before = _index(project)
    code, output = _recover(root, "recovery-owner")
    assert code == 0, output
    _change(project, "later")
    later_tree = _tree(root)

    code, output = _recover(root, "recovery-owner")

    _assert_already_recovered(code, output, root)
    assert _tree(root) == later_tree
    assert _index(project) == index_before
    assert _units(root) == {"shared", "mine", "yours", "later"}


def test_a_restored_conflict_recovers_again(tmp_path: Path) -> None:
    project = _conflicted_repository(tmp_path, operation="merge")
    root = project / ".vouch"
    index_before = _index(project)
    code, output = _recover(root, "recovery-owner")
    assert code == 0, output
    _must_git(project, "checkout", "--conflict=merge", "--", ".vouch")

    code, output = _recover(root, "second-owner")

    assert code == 0, output
    assert _units(root) == {"shared", "mine", "yours"}
    assert _index(project) == index_before


@pytest.mark.parametrize("side", ("--ours", "--theirs"))
def test_a_working_store_that_took_one_side_is_replaced(tmp_path: Path, side: str) -> None:
    project = _conflicted_repository(tmp_path, operation="merge")
    root = project / ".vouch"
    index_before = _index(project)
    _must_git(project, "checkout", side, "--", ".vouch")
    assert len(_units(root)) == 2

    code, output = _recover(root, "recovery-owner")

    assert code == 0, output
    assert _units(root) == {"shared", "mine", "yours"}
    assert _index(project) == index_before


@pytest.mark.parametrize("side", ("--ours", "--theirs"))
def test_one_side_with_later_work_is_replaced(tmp_path: Path, side: str) -> None:
    """A working store past one stage head, but not incorporating the other, is not a recovery."""
    project = _conflicted_repository(tmp_path, operation="merge")
    root = project / ".vouch"
    index_before = _index(project)
    _must_git(project, "checkout", side, "--", ".vouch")
    _change(project, "extra")
    assert len(_units(root)) == 3

    code, output = _recover(root, "recovery-owner")

    assert code == 0, output
    assert _units(root) == {"shared", "mine", "yours"}
    assert _index(project) == index_before


def test_a_working_store_with_a_broken_chain_is_replaced(tmp_path: Path) -> None:
    """A readable snapshot whose history cannot be verified is not a recovery."""
    project = _conflicted_repository(tmp_path, operation="merge")
    root = project / ".vouch"
    index_before = _index(project)
    code, output = _recover(root, "recovery-owner")
    assert code == 0, output
    head = json.loads((root / "state.json").read_text())["event_head"]
    objects = root / "objects" / "sha256"
    previous = json.loads((objects / head).read_text())["prev"]
    (objects / previous).unlink()

    code, output = _recover(root, "second-owner")

    assert code == 0, output
    assert _units(root) == {"shared", "mine", "yours"}
    assert _index(project) == index_before


def _section(path: Path, start: str, end: str) -> str:
    text = path.read_text()
    begin = text.index(start)
    return re.sub(r"\s+", " ", text[begin : text.index(end, begin)])


def test_the_docs_state_the_refusal() -> None:
    quickstart = _section(
        ROOT / "docs" / "quickstart.md", "Recovery refuses if any of the following hold", "\n\n"
    )
    boundary = _section(
        ROOT / "docs" / "claim-boundary.md", "**Git conflict recovery (`merge-git-stages`):**", "\n\n"
    )

    assert (
        "The working store already incorporates both stages, as after an earlier divergent "
        "recovery (`git.already-recovered`): review and stage it, or run "
        "`git checkout --conflict=merge -- .vouch` to merge again, which discards any changes "
        "made since"
    ) in quickstart
    assert (
        "Recovery refuses with `git.already-recovered` and writes nothing when the working store "
        "is a valid store whose history incorporates both stage heads and whose head is neither, "
        "so a second run cannot replace an earlier divergent recovery. It cannot tell who wrote "
        "that store. A fast-forward or no-op recovery leaves a stage head in place and can run "
        "again."
    ) in boundary
