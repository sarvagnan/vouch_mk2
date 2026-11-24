"""Frozen acceptance pack for owner-modes@1 (D-58, vouch_mk2-vz5).

Vouch sets the mode of every file and directory it creates, so the process umask cannot change
them: a command run under any umask leaves the same modes as under umask 022, those modes are the
intended ones (pinned per command below), and a umask that removes the owner's own bits no longer
makes the store unreadable. Vouch never changes the process umask. Every test here fails before the
change, except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main

ROOT = Path(__file__).resolve().parents[1]
UPGRADE_FIXTURES = ROOT / "tests" / "fixtures" / "merge-check-upgrade"
NORMAL = 0o022
OWNER_STRIPPING = (0o477, 0o377, 0o277)
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TEMP = re.compile(r"\.tmp$|\.txn$|^\.vouch-|^vouch-(junit|check-merge)-")

# The intended modes, as a 022 run gives them before this unit (D-58 keeps them).
_STORE = {
    ".vouch": "drwx------",
    ".vouch/.lock": "-rw-------",
    ".vouch/config.json": "-rw-------",
    ".vouch/map.json": "-rw-------",
    ".vouch/objects": "drwx------",
    ".vouch/objects/sha256": "drwx------",
    ".vouch/objects/sha256/<digest>": "-rw-------",
    ".vouch/state-format.json": "-rw-------",
    ".vouch/state.json": "-rw-------",
    ".vouch/vouch-context.md": "-rw-------",
}
_ADOPTION = {
    ".": "drwxr-xr-x",
    ".github": "drwxr-xr-x",
    ".github/workflows": "drwxr-xr-x",
    ".github/workflows/vouch.yml": "-rw-r--r--",
    "vouch.lock": "-rwxr-xr-x",
}
_HARNESS = {
    "packs": "drwxr-xr-x",
    "packs/p.effects.json": "-rw-r--r--",
    "tests": "drwxr-xr-x",
    "tests/test_p.py": "-rw-r--r--",
}
LIFECYCLE_MODES = {**_STORE, **_ADOPTION, **_HARNESS}
# merge-git-stages publishes the store into the working tree as Git checks files out.
GIT_STAGE_MODES = {
    **_ADOPTION,
    ".gitignore": "-rw-r--r--",
    ".vouch": "drwx------",
    ".vouch/.lock": "-rw-------",
    ".vouch/config.json": "-rw-r--r--",
    ".vouch/map.json": "-rw-r--r--",
    ".vouch/objects": "drwx------",
    ".vouch/objects/sha256": "drwx------",
    ".vouch/objects/sha256/<digest>": "-rw-r--r--",
    ".vouch/state-format.json": "-rw-r--r--",
    ".vouch/state.json": "-rw-r--r--",
    ".vouch/vouch-context.md": "-rw-r--r--",
}
LOCK_MODES = {".": "drwxr-xr-x", "a": "drwxr-xr-x", "a/vouch.lock": "-rwxr-xr-x"}


@pytest.fixture(autouse=True)
def _normal_harness_umask() -> Iterator[None]:
    """The harness (Git, fixtures) runs under 022, whatever the developer's umask."""
    previous = os.umask(NORMAL)
    try:
        yield
    finally:
        os.umask(previous)


@contextmanager
def _umask(mask: int) -> Iterator[None]:
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _run(mask: int, *argv: str) -> tuple[int, str, str]:
    """Run the CLI in process under MASK; the command must leave the umask as it found it."""
    out, err = io.StringIO(), io.StringIO()
    with _umask(mask):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 2
        seen = os.umask(mask)
    assert seen == mask, f"{argv[0]} left the process umask at {seen:#o}, not {mask:#o}"
    return code, out.getvalue(), err.getvalue()


def _must(*argv: str) -> str:
    code, out, err = _run(NORMAL, *argv)
    assert code == 0, (argv, out, err)
    return out


def _git(project: Path, *argv: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(project), "-c", "commit.gpgsign=false", *argv),
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _raise(error: OSError) -> None:
    raise error


def _entries(top: Path) -> Counter[tuple[str, str]]:
    """Count (path, mode) under TOP, TOP itself as ".", outside .git, digests normalised."""
    result: Counter[tuple[str, str]] = Counter()
    result[(".", stat.filemode(top.lstat().st_mode))] += 1
    for directory, subdirs, files in os.walk(top, onerror=_raise):
        subdirs[:] = [name for name in subdirs if name != ".git"]
        for name in (*subdirs, *files):
            path = Path(directory) / name
            relative = "/".join(
                _DIGEST.sub("<digest>", part) for part in path.relative_to(top).parts
            )
            result[(relative, stat.filemode(path.lstat().st_mode))] += 1
    return result


def _mode_table(entries: Counter[tuple[str, str]]) -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for path, mode in entries:
        table.setdefault(path, set()).add(mode)
    return table


def _expected(modes: dict[str, str]) -> dict[str, set[str]]:
    return {path: {mode} for path, mode in modes.items()}


def _temporaries(top: Path) -> list[str]:
    return sorted(
        path for path, _ in _entries(top) if _TEMP.search(path.rsplit("/", 1)[-1])
    )


# The project lifecycle: init through check, as the quickstart runs it.


def _project(parent: Path) -> Path:
    project = parent / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    (project / "tests").mkdir()
    (project / "packs").mkdir()
    (project / "tests" / "test_p.py").write_text("def test_p():\n    assert False\n")
    (project / "packs" / "p.effects.json").write_text('{"effects_format": 1, "effects": []}\n')
    return project


_LIFECYCLE: tuple[tuple[str, ...], ...] = (
    ("init", "--project", "."),
    ("specify", "--root", ".vouch", "--name", "p", "--version", "v1"),
    (
        "freeze", "--root", ".vouch", "--name", "p", "--pack-version", "p-pack/1",
        "--pack-file", "tests/test_p.py", "--pack-effects-file", "packs/p.effects.json",
    ),
    ("mark-ready", "--root", ".vouch", "--name", "p"),
    ("start", "--root", ".vouch", "--name", "p"),
    (
        "record-evidence", "--root", ".vouch", "--unit", "p@v1", "--kind", "red",
        "--actor", "t", "--", sys.executable, "-c", "raise SystemExit(1)",
    ),
    ("generate", "--root", ".vouch"),
    ("status", "--root", ".vouch"),
    ("check", "--root", ".vouch", "--project", "."),
)


def _lifecycle(parent: Path, mask: int, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[int]]:
    project = _project(parent)
    monkeypatch.chdir(project)
    codes = [_run(mask, *argv)[0] for argv in _LIFECYCLE]
    monkeypatch.chdir(ROOT)
    return project, codes


# R1


@pytest.mark.parametrize("mask", [*OWNER_STRIPPING, 0o077], ids=oct)
def test_the_lifecycle_leaves_the_intended_modes_under_any_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: int,
) -> None:
    normal, normal_codes = _lifecycle(tmp_path / "normal", NORMAL, monkeypatch)
    assert normal_codes[:-1] == [0] * (len(_LIFECYCLE) - 1), normal_codes
    masked, codes = _lifecycle(tmp_path / "masked", mask, monkeypatch)
    assert codes == normal_codes
    assert _entries(masked) == _entries(normal)
    assert _mode_table(_entries(masked)) == _expected(LIFECYCLE_MODES)
    assert _temporaries(masked) == []
    assert os.listdir(tmp_path / "masked") == ["project"], "init left its staging directory"


def test_a_permissive_umask_widens_nothing(  # guard: modes were never wider than intended
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    normal, normal_codes = _lifecycle(tmp_path / "normal", NORMAL, monkeypatch)
    open_, codes = _lifecycle(tmp_path / "open", 0o000, monkeypatch)
    assert codes == normal_codes
    assert _entries(open_) == _entries(normal)
    assert _mode_table(_entries(open_)) == _expected(LIFECYCLE_MODES)


# R2


def _diverged(parent: Path) -> tuple[Path, Path]:
    project = _project(parent)
    ours = project / ".vouch"
    _must("init", "--project", str(project))
    _must("specify", "--root", str(ours), "--name", "shared", "--version", "v1")
    theirs = parent / "theirs"
    shutil.copytree(ours, theirs)
    _must("specify", "--root", str(ours), "--name", "mine", "--version", "v1")
    _must("specify", "--root", str(theirs), "--name", "yours", "--version", "v1")
    return ours, theirs


def test_a_store_merge_leaves_the_intended_modes_under_an_owner_stripping_umask(
    tmp_path: Path,
) -> None:
    normal_ours, normal_theirs = _diverged(tmp_path / "normal")
    masked_ours, masked_theirs = _diverged(tmp_path / "masked")
    normal = _run(NORMAL, "merge", "--root", str(normal_ours), "--other", str(normal_theirs))
    masked = _run(0o477, "merge", "--root", str(masked_ours), "--other", str(masked_theirs))
    assert normal[0] == 0, normal
    assert masked[0] == normal[0], masked
    assert _entries(masked_ours) == _entries(normal_ours)
    store_relative = {
        "." if path == ".vouch" else path.removeprefix(".vouch/"): mode
        for path, mode in _STORE.items()
        if path not in (".vouch/map.json", ".vouch/vouch-context.md")  # merge does not generate
    }
    assert _mode_table(_entries(masked_ours)) == _expected(store_relative)
    assert _temporaries(masked_ours) == []
    assert _run(NORMAL, "check", "--root", str(masked_ours))[0] == 0


# R3


def _conflicted(parent: Path) -> Path:
    project = parent / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    _git(project, "config", "user.name", "Vouch Test")
    _git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    root = str(project / ".vouch")
    _must("init", "--project", str(project))
    _must("specify", "--root", root, "--name", "shared", "--version", "v1")
    _must("generate", "--root", root)
    _git(project, "add", "--", ".gitignore", ".vouch", ".github")
    _git(project, "commit", "-q", "-m", "shared")
    _git(project, "checkout", "-q", "-b", "incoming")
    _must("specify", "--root", root, "--name", "yours", "--version", "v1")
    _must("generate", "--root", root)
    _git(project, "add", "--", ".vouch")
    _git(project, "commit", "-q", "-m", "incoming")
    _git(project, "checkout", "-q", "main")
    _must("specify", "--root", root, "--name", "mine", "--version", "v1")
    _must("generate", "--root", root)
    _git(project, "add", "--", ".vouch")
    _git(project, "commit", "-q", "-m", "current")
    merged = subprocess.run(
        ("git", "-C", str(project), "-c", "commit.gpgsign=false", "merge", "--no-edit", "incoming"),
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert merged.returncode != 0, merged.stdout
    return project


@pytest.mark.parametrize("mask", [0o477, 0o077], ids=oct)
def test_merge_git_stages_leaves_the_intended_modes_under_any_umask(
    tmp_path: Path, mask: int,
) -> None:
    normal = _conflicted(tmp_path / "normal")
    masked = _conflicted(tmp_path / "masked")
    # A fresh lock: merge-git-stages creates it.
    (normal / ".vouch" / ".lock").unlink()
    (masked / ".vouch" / ".lock").unlink()
    normal_result = _run(NORMAL, "merge-git-stages", "--root", str(normal / ".vouch"))
    masked_result = _run(mask, "merge-git-stages", "--root", str(masked / ".vouch"))
    assert normal_result[0] == 0, normal_result
    assert masked_result[0] == 0, masked_result
    assert _entries(masked) == _entries(normal)
    assert _mode_table(_entries(masked)) == _expected(GIT_STAGE_MODES)
    assert _temporaries(masked) == []
    assert os.listdir(tmp_path / "masked") == ["project"]


def test_merge_git_stages_keeps_an_existing_locks_mode(  # guard: an existing lock is not re-moded
    tmp_path: Path,
) -> None:
    project = _conflicted(tmp_path)
    lock = project / ".vouch" / ".lock"
    lock.chmod(0o640)
    result = _run(0o077, "merge-git-stages", "--root", str(project / ".vouch"))
    assert result[0] == 0, result
    assert stat.S_IMODE(lock.stat().st_mode) == 0o640


# R4


def _fixture_store(target: Path) -> Path:
    document = cast(
        dict[str, object],
        json.loads((UPGRADE_FIXTURES / "format-5-store.json").read_text()),
    )
    for relative, encoded in cast(dict[str, str], document["files"]).items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    return target


def test_check_merge_upgrades_its_private_copy_under_an_owner_stripping_umask(
    tmp_path: Path,
) -> None:
    base = _fixture_store(tmp_path / "base")
    head = tmp_path / "head"
    shutil.copytree(base, head)
    _must("upgrade", "--root", str(head), "--actor", "t")
    merged = tmp_path / "merged"
    shutil.copytree(head, merged)
    inputs = (base, head, merged)
    before = [_entries(path) for path in inputs]
    argv = ("check-merge", "--base", str(base), "--head", str(head), "--merged", str(merged))
    normal = _run(NORMAL, *argv)
    assert normal[0] == 0, normal
    assert "upgraded a private copy of BASE" in normal[1], normal
    assert [_entries(path) for path in inputs] == before
    masked = _run(0o477, *argv)
    assert masked[:2] == normal[:2], masked
    assert [_entries(path) for path in inputs] == before
    assert sorted(os.listdir(tmp_path)) == ["base", "head", "merged"]


# R5


@pytest.mark.parametrize("mask", [0o477, 0o077], ids=oct)
def test_build_lock_creates_its_directories_with_the_intended_modes(
    tmp_path: Path, mask: int,
) -> None:
    normal_out = tmp_path / "normal"
    masked_out = tmp_path / "masked"
    normal = _run(NORMAL, "build-lock", "--output", str(normal_out / "a" / "vouch.lock"))
    masked = _run(mask, "build-lock", "--output", str(masked_out / "a" / "vouch.lock"))
    assert normal[0] == 0, normal
    assert masked[0] == 0, masked
    assert masked[1] == normal[1]
    assert _entries(masked_out) == _entries(normal_out)
    assert _mode_table(_entries(masked_out)) == _expected(LOCK_MODES)


def test_build_lock_keeps_an_existing_directorys_mode(  # guard: existing parents are not re-moded
    tmp_path: Path,
) -> None:
    parent = tmp_path / "existing"
    parent.mkdir()
    parent.chmod(0o750)
    result = _run(0o077, "build-lock", "--output", str(parent / "vouch.lock"))
    assert result[0] == 0, result
    assert stat.S_IMODE(parent.stat().st_mode) == 0o750


@pytest.mark.parametrize("mask", [0o477, 0o077], ids=oct)
def test_refresh_workflow_creates_its_directories_with_the_intended_modes(
    tmp_path: Path, mask: int,
) -> None:
    projects: dict[int, Path] = {}
    for current in (NORMAL, mask):
        project = tmp_path / f"p{current:o}"
        project.mkdir()
        _must("init", "--project", str(project))
        shutil.rmtree(project / ".github")
        projects[current] = project
    normal = _run(NORMAL, "refresh-workflow", "--project", str(projects[NORMAL]))
    masked = _run(mask, "refresh-workflow", "--project", str(projects[mask]))
    assert normal[0] == 0, normal
    assert masked[0] == 0, masked
    assert _entries(projects[mask]) == _entries(projects[NORMAL])
    table = _mode_table(_entries(projects[mask]))
    assert {path: modes for path, modes in table.items() if path.startswith(".github")} == {
        path: modes for path, modes in _expected(_ADOPTION).items() if path.startswith(".github")
    }


# R6


_ENTRY = """
import os, sys
from vouch_mk2.cli import main
try:
    code = main(sys.argv[1:])
finally:
    mask = os.umask(0)
    open(os.environ["VOUCH_TEST_UMASK_AFTER"], "w").write(oct(mask))
sys.exit(code)
"""


def test_the_operators_umask_reaches_the_evidence_command_and_survives(  # guard: never changed
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    for argv in _LIFECYCLE[:5]:
        assert _run(NORMAL, *argv)[0] == 0, argv
    monkeypatch.chdir(ROOT)
    seen = tmp_path / "child-umask.txt"
    after = tmp_path / "after-umask.txt"
    child = (
        "import os, pathlib, sys; mask = os.umask(0); "
        f"pathlib.Path({str(seen)!r}).write_text(oct(mask)); sys.exit(1)"
    )
    # A fresh interpreter, with the umask set before Vouch is imported.
    result = subprocess.run(
        [
            sys.executable, "-c", _ENTRY, "record-evidence", "--root", ".vouch",
            "--unit", "p@v1", "--kind", "red", "--actor", "t", "--",
            sys.executable, "-c", child,
        ],
        cwd=project, umask=0o027, capture_output=True, text=True, check=False,
        env={**os.environ, "VOUCH_TEST_UMASK_AFTER": str(after)},
    )
    assert result.returncode == 0, result
    assert seen.read_text() == "0o27", "the evidence command must inherit the operator's umask"
    assert after.read_text() == "0o27", "Vouch must leave the process umask as it found it"


# R7


def test_the_claim_boundary_states_the_modes() -> None:
    text = " ".join((ROOT / "docs" / "claim-boundary.md").read_text().split())
    assert (
        "Vouch sets the mode of each file and directory it creates, so a command leaves the same "
        "modes under any umask. A file or directory that already exists keeps its mode, unless "
        "the command replaces it."
    ) in text
