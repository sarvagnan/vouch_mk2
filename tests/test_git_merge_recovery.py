"""Frozen acceptance pack for self-hosted unit git-merge-recovery@1 (D-21).

Every test here failed against the pre-change tree. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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


def _git(
    project: Path,
    *argv: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *argv),
        check=False,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _must_git(project: Path, *argv: str) -> str:
    result = _git(project, *argv)
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _commit_store(project: Path, message: str) -> None:
    _must_git(project, "add", "--", ".vouch")
    _must_git(project, "commit", "-m", message)


def _change(project: Path, name: str, version: str) -> None:
    root = project / ".vouch"
    code, output = _run(
        "specify",
        "--root",
        str(root),
        "--name",
        name,
        "--version",
        version,
    )
    assert code == 0, output
    assert _run("generate", "--root", str(root))[0] == 0


def _repository(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _must_git(project, "init", "-b", "main")
    _must_git(project, "config", "user.name", "Vouch Test")
    _must_git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")

    Store.init(str(project / ".vouch")).close()
    _change(project, "shared", "v1")
    _must_git(project, "add", "--", ".gitignore")
    _commit_store(project, "shared vouch history")
    return project


def _conflicted_repository(
    tmp_path: Path,
    *,
    ours: tuple[str, str],
    theirs: tuple[str, str],
) -> Path:
    project = _repository(tmp_path)
    _must_git(project, "checkout", "-b", "incoming")
    _change(project, *theirs)
    _commit_store(project, "incoming vouch event")

    _must_git(project, "checkout", "main")
    _change(project, *ours)
    _commit_store(project, "current vouch event")

    merged = _git(project, "merge", "--no-edit", "incoming")
    assert merged.returncode != 0, merged.stdout
    assert _unmerged(project), merged.stdout
    return project


def _unmerged(project: Path) -> str:
    return _must_git(
        project,
        "diff",
        "--name-only",
        "--diff-filter=U",
        "--",
        ".vouch",
    )


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


def test_real_git_conflict_recovers_through_the_public_cli(tmp_path: Path) -> None:
    project = _conflicted_repository(
        tmp_path,
        ours=("mine", "v1"),
        theirs=("yours", "v1"),
    )
    root = project / ".vouch"
    index_before = _index(project)

    code, output = _run(
        "merge-git-stages",
        "--root",
        str(root),
        "--actor",
        "recovery-owner",
    )

    assert code == 0, output
    assert _units(root) == {"shared", "mine", "yours"}
    assert _index(project) == index_before, "the adapter must not stage its own result"
    assert _unmerged(project), "Git must still require the operator's explicit git add"
    assert "git add --" in output and ".vouch" in output

    map_before = (root / "map.json").read_bytes()
    context_before = (root / "vouch-context.md").read_bytes()
    json.loads(map_before)
    assert b"mine" in map_before and b"yours" in map_before
    assert b"mine" in context_before and b"yours" in context_before
    assert _run("generate", "--root", str(root))[0] == 0
    assert (root / "map.json").read_bytes() == map_before
    assert (root / "vouch-context.md").read_bytes() == context_before
    check_code, check_output = _run("check", "--root", str(root))
    assert check_code == 0, check_output
    event_rows = [line for line in check_output.splitlines() if "check.event-log" in line]
    assert event_rows and event_rows[0].startswith("PASS"), check_output


def test_semantic_conflict_preserves_the_worktree_and_index(tmp_path: Path) -> None:
    project = _conflicted_repository(
        tmp_path,
        ours=("clash", "v1"),
        theirs=("clash", "v2"),
    )
    root = project / ".vouch"
    tree_before = _tree(root)
    index_before = _index(project)

    code, output = _run("merge-git-stages", "--root", str(root))

    assert code == 2, output
    assert "merge.conflict" in output and "clash" in output
    assert _tree(root) == tree_before
    assert _index(project) == index_before


def test_clean_index_refuses_without_touching_the_store(tmp_path: Path) -> None:
    project = _repository(tmp_path)
    root = project / ".vouch"
    tree_before = _tree(root)
    index_before = _index(project)

    code, output = _run("merge-git-stages", "--root", str(root))

    assert code == 2, output
    assert "git.index-no-conflict" in output
    assert _tree(root) == tree_before
    assert _index(project) == index_before


def test_incomplete_index_stages_fail_closed(tmp_path: Path) -> None:
    project = _repository(tmp_path)
    root = project / ".vouch"

    _must_git(project, "checkout", "-b", "incoming")
    _must_git(project, "rm", "--", ".vouch/state.json")
    _must_git(project, "commit", "-m", "delete state")
    _must_git(project, "checkout", "main")
    _change(project, "mine", "v1")
    _commit_store(project, "modify state")
    merged = _git(project, "merge", "--no-edit", "incoming")
    assert merged.returncode != 0 and _unmerged(project), merged.stdout
    tree_before = _tree(root)
    index_before = _index(project)

    code, output = _run("merge-git-stages", "--root", str(root))

    assert code == 2, output
    assert "git.index-stages-incomplete" in output
    assert _tree(root) == tree_before
    assert _index(project) == index_before


def test_unsafe_git_mode_fails_closed(tmp_path: Path) -> None:
    project = _repository(tmp_path)
    root = project / ".vouch"
    unsafe = root / "unsafe-entry"
    unsafe.write_text("base\n")
    _commit_store(project, "tracked regular store entry")

    _must_git(project, "checkout", "-b", "incoming")
    unsafe.unlink()
    unsafe.symlink_to("state.json")
    _commit_store(project, "symlink store entry")

    _must_git(project, "checkout", "main")
    unsafe.write_text("current\n")
    _commit_store(project, "modify regular store entry")
    merged = _git(project, "merge", "--no-edit", "incoming")
    assert merged.returncode != 0 and _unmerged(project), merged.stdout
    tree_before = _tree(root)
    index_before = _index(project)

    code, output = _run("merge-git-stages", "--root", str(root))

    assert code == 2, output
    assert "git.index-mode-unsafe" in output
    assert _tree(root) == tree_before
    assert _index(project) == index_before


def test_symlinked_store_root_fails_closed(tmp_path: Path) -> None:
    project = _conflicted_repository(
        tmp_path,
        ours=("mine", "v1"),
        theirs=("yours", "v1"),
    )
    root = project / ".vouch"
    hidden = project / "hidden-vouch"
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "marker"
    marker.write_text("outside\n")
    root.rename(hidden)
    root.symlink_to(external, target_is_directory=True)
    hidden_before = _tree(hidden)
    index_before = _index(project)

    code, output = _run("merge-git-stages", "--root", str(root))

    assert code == 2, output
    assert "git.path-unsafe" in output
    assert marker.read_text() == "outside\n"
    assert tuple(external.iterdir()) == (marker,)
    assert _tree(hidden) == hidden_before
    assert _index(project) == index_before


def test_docs_cover_merge_and_rebase_recovery_without_overclaiming() -> None:
    quickstart = (ROOT / "docs" / "quickstart.md").read_text().lower()
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text().lower()
    commands = (ROOT / "docs" / "commands.md").read_text().lower()

    assert "merge-git-stages" in quickstart and "merge-git-stages" in commands
    assert "git add -- .vouch" in quickstart
    assert "git merge --continue" in quickstart
    assert "git rebase --continue" in quickstart
    assert "stage 2" in quickstart and "stage 3" in quickstart
    assert "rebase" in quickstart and "ours" in quickstart and "theirs" in quickstart
    assert "index" in boundary and "untrusted" in boundary
    assert "branch name" in boundary or "branch names" in boundary
    assert "does not" in boundary and "intent" in boundary
