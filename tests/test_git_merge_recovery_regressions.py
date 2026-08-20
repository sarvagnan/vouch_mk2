"""Non-pack regressions for Git conflict recovery's operator-facing seam."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store import Store


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert result.returncode == 0, result.stdout
    return result.stdout


def _change(root: Path, name: str) -> None:
    assert main(
        [
            "specify",
            "--root",
            str(root),
            "--name",
            name,
            "--version",
            "v1",
        ]
    ) == 0
    assert main(["generate", "--root", str(root)]) == 0


def _commit_store(project: Path, message: str) -> None:
    _git(project, "add", "--", ".vouch")
    _git(project, "commit", "-m", message)


def _conflicted_store(tmp_path: Path) -> Path:
    project = tmp_path / "project with spaces; $(not-run)"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Vouch Test")
    _git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    root = project / ".vouch"
    Store.init(str(root)).close()
    _change(root, "shared")
    _git(project, "add", "--", ".gitignore")
    _commit_store(project, "shared")

    _git(project, "checkout", "-b", "incoming")
    _change(root, "theirs")
    _commit_store(project, "theirs")
    _git(project, "checkout", "main")
    _change(root, "ours")
    _commit_store(project, "ours")
    merge = subprocess.run(
        ("git", "-C", str(project), "merge", "--no-edit", "incoming"),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert merge.returncode != 0, merge.stdout
    return root


def _write_git_blob(project: Path, payload: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(project), "hash-object", "-w", "--stdin"),
        check=False,
        text=True,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert result.returncode == 0, result.stdout
    return result.stdout.strip()


def test_printed_git_add_command_round_trips_a_shell_sensitive_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _conflicted_store(tmp_path)

    assert main(["merge-git-stages", "--root", str(root)]) == 0

    output = capsys.readouterr().out
    next_step = next(
        line.partition("then run: ")[2]
        for line in output.splitlines()
        if "then run: " in line
    )
    assert shlex.split(next_step) == ["git", "add", "--", str(root)]


def test_replace_refs_cannot_substitute_bytes_for_an_indexed_stage(
    tmp_path: Path,
) -> None:
    root = _conflicted_store(tmp_path)
    project = root.parent
    state_rows = _git(project, "ls-files", "--stage", "--", ".vouch/state.json")
    ours_object = next(
        row.split()[1] for row in state_rows.splitlines() if row.split()[2] == "2"
    )
    replacement_object = _write_git_blob(project, "not a Vouch state document\n")
    _git(project, "replace", ours_object, replacement_object)

    assert main(["merge-git-stages", "--root", str(root)]) == 0

    with Store.open(str(root), read_only=True) as store:
        assert {unit.name for unit in store.read_snapshot().units} == {
            "shared",
            "ours",
            "theirs",
        }
