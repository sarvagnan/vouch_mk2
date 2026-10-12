"""Frozen acceptance pack for self-hosted unit merge-open-in-ci-replay@1 (D-25).

Found by the activity_tracker_portal pilot (D-24): a divergent semantic merge could not
replay a strict-CI exception, because ``OpenInCiMarked`` records the exact ``NAME@VERSION``
label while the replayed command resolves a bare unit name. Every test here failed against
the pre-change tree. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot
from vouch_mk2.store import Store


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _must(*argv: str) -> str:
    code, output = _run(*argv)
    assert code == 0, (argv, output)
    return output


def _git(project: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *argv),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _must_git(project: Path, *argv: str) -> str:
    result = _git(project, *argv)
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _store(project: Path) -> str:
    return str(project / ".vouch")


def _generate(project: Path) -> None:
    _must("generate", "--root", _store(project))


def _commit(project: Path, message: str) -> None:
    _must_git(project, "add", "--", ".vouch", "packs")
    _must_git(project, "commit", "-m", message)


def _frozen(project: Path, name: str, version: str) -> None:
    """Specify and freeze NAME@VERSION against a one-file synthetic pack."""
    pack = project / "packs" / f"{name}-{version}.md"
    pack.parent.mkdir(exist_ok=True)
    pack.write_text(f"# {name}@{version} synthetic pack\n")
    _must("specify", "--root", _store(project), "--name", name, "--version", version)
    _must(
        "freeze",
        "--root",
        _store(project),
        "--name",
        name,
        "--pack-version",
        f"{name}-pack/{version}",
        "--project",
        str(project),
        "--pack-file",
        f"packs/{name}-{version}.md",
    )


def _open(project: Path, name: str, reason: str) -> None:
    _must("mark-open-in-ci", "--root", _store(project), "--name", name, "--reason", reason)


def _repository(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _must_git(project, "init", "-b", "main")
    _must_git(project, "config", "user.name", "Vouch Test")
    _must_git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    (project / "packs").mkdir()
    (project / "packs" / ".keep").write_text("")
    Store.init(str(project / ".vouch")).close()
    _must("specify", "--root", _store(project), "--name", "shared", "--version", "v1")
    _generate(project)
    _must_git(project, "add", "--", ".gitignore")
    _commit(project, "shared vouch history")
    return project


def _snapshot(project: Path) -> Snapshot:
    with Store.open(_store(project), read_only=True) as store:
        return store.read_snapshot()


def _units(project: Path) -> dict[str, str]:
    return {f"{unit.name}@{unit.version}": unit.status.value for unit in _snapshot(project).units}


def _exceptions(project: Path) -> dict[str, str]:
    return {item.unit_name: item.reason for item in _snapshot(project).ci_exceptions}


def _diverged(
    tmp_path: Path,
    incoming: Callable[[Path], None],
    current: Callable[[Path], None],
) -> Path:
    """Build two branches from shared history, then start the conflicted git merge."""
    project = _repository(tmp_path)
    _must_git(project, "checkout", "-b", "incoming")
    incoming(project)
    _generate(project)
    _commit(project, "incoming vouch events")
    _must_git(project, "checkout", "main")
    current(project)
    _generate(project)
    _commit(project, "current vouch events")
    merged = _git(project, "merge", "--no-edit", "incoming")
    assert merged.returncode != 0, merged.stdout
    return project


def _incoming_open(project: Path) -> None:
    _frozen(project, "incoming-unit", "1")
    _open(project, "incoming-unit", "incoming reason")


def _current_open(project: Path) -> None:
    _frozen(project, "current-unit", "1")
    _open(project, "current-unit", "current reason")


def test_git_stage_recovery_replays_an_incoming_open_in_ci_exception(tmp_path: Path) -> None:
    """R1: merge-git-stages recovers both branches' frozen units and both exceptions."""
    project = _diverged(tmp_path, _incoming_open, _current_open)

    code, output = _run("merge-git-stages", "--root", _store(project))

    assert code == 0, output
    assert _units(project) == {
        "shared@v1": "specified",
        "current-unit@1": "acceptance-frozen",
        "incoming-unit@1": "acceptance-frozen",
    }
    assert _exceptions(project) == {
        "current-unit@1": "current reason",
        "incoming-unit@1": "incoming reason",
    }
    _generate(project)
    code, output = _run("check", "--ci", "--root", _store(project), "--project", str(project))
    assert code == 0, output


def test_directory_merge_replays_an_incoming_open_in_ci_exception(tmp_path: Path) -> None:
    """R2: merge --other replays the same exception between two store directories."""
    project = _repository(tmp_path)
    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    _incoming_open(theirs)
    _current_open(project)

    code, output = _run("merge", "--root", _store(project), "--other", _store(theirs))

    assert code == 0, output
    assert _exceptions(project) == {
        "current-unit@1": "current reason",
        "incoming-unit@1": "incoming reason",
    }
    assert _units(project)["incoming-unit@1"] == "acceptance-frozen"


def test_a_cleared_incoming_exception_replays_as_cleared(tmp_path: Path) -> None:
    """R3: an incoming mark-then-clear leaves no exception for that unit; ours remains."""

    def incoming(project: Path) -> None:
        _incoming_open(project)
        _must("clear-open-in-ci", "--root", _store(project), "--name", "incoming-unit")

    project = _diverged(tmp_path, incoming, _current_open)

    code, output = _run("merge-git-stages", "--root", _store(project))

    assert code == 0, output
    assert _exceptions(project) == {"current-unit@1": "current reason"}
    assert _units(project)["incoming-unit@1"] == "acceptance-frozen"


def test_an_exception_never_moves_to_another_unit_version(tmp_path: Path) -> None:
    """R4: replay refuses when the recorded NAME@VERSION no longer names the current unit."""
    project = _repository(tmp_path)
    _frozen(project, "moving", "1")
    _generate(project)
    _commit(project, "shared moving@1")

    def incoming(project: Path) -> None:
        _open(project, "moving", "exception for version 1")

    def current(project: Path) -> None:
        _must(
            "open-deviation",
            "--root",
            _store(project),
            "--deviation-id",
            "move-on",
            "--deviation-type",
            "pack-defect",
            "--unit",
            "moving@1",
        )
        _must("supersede", "--root", _store(project), "--name", "moving")
        _frozen(project, "moving", "2")

    _must_git(project, "checkout", "-b", "incoming")
    incoming(project)
    _generate(project)
    _commit(project, "incoming exception")
    _must_git(project, "checkout", "main")
    current(project)
    _generate(project)
    _commit(project, "current successor")
    assert _git(project, "merge", "--no-edit", "incoming").returncode != 0
    before = (project / ".vouch" / "state.json").read_bytes()

    code, output = _run("merge-git-stages", "--root", _store(project))

    assert code != 0, output
    assert "merge.conflict" in output
    # The refusal names the recorded exception and the version it would have moved to.
    assert "moving@1" in output
    assert "moving@2" in output
    assert "unit.unknown" not in output
    assert (project / ".vouch" / "state.json").read_bytes() == before
