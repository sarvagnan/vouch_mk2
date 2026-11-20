"""Frozen acceptance pack for self-hosted unit merge-redundant-clear@1 (D-37).

Found by the merge-open-in-ci-replay@1 review (``vouch_mk2-bd4.4.3``): shared history holds an
open-in-CI exception, ours clears it, and theirs clears it and marks it again. Replay refused
theirs' clear with ``ci-policy.exception-missing`` because ``decide()`` refuses a clear of an
absent exception, so two branches that each closed the same exception could not merge. Every
test here failed against the pre-change tree, except tests marked ``# guard:``. The
implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot
from vouch_mk2.store import Store, decode_event_record
from vouch_mk2.store.digests import ArtifactKind

ROOT = Path(__file__).resolve().parents[1]
SKIPPED_TEXT = "skipped 1 redundant open-in-CI clear(s)"


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


def _snapshot(project: Path) -> Snapshot:
    with Store.open(_store(project), read_only=True) as store:
        return store.read_snapshot()


def _exceptions(project: Path) -> dict[str, str]:
    return {item.unit_name: item.reason for item in _snapshot(project).ci_exceptions}


def _event_kinds(project: Path) -> list[str]:
    """Every event kind on the chain, oldest first."""
    kinds: list[str] = []
    with Store.open(_store(project), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest and digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            kinds.append(type(record.folded_event).__name__)
            digest = record.prev
    return kinds[::-1]


def _clear(project: Path) -> None:
    _must("clear-open-in-ci", "--root", _store(project), "--name", "u")


def _mark(project: Path, reason: str) -> None:
    _must("mark-open-in-ci", "--root", _store(project), "--name", "u", "--reason", reason)


def _ours_clear(project: Path) -> None:
    """Ours diverges first (so its clear is a different record from theirs'), then clears."""
    _must("specify", "--root", _store(project), "--name", "ours-only", "--version", "1")
    _clear(project)


def _clear_and_remark(project: Path) -> None:
    _clear(project)
    _mark(project, "re-marked on theirs")


def _shared(project: Path) -> None:
    """Shared history: u@1 frozen against a one-file pack and marked open in CI."""
    project.mkdir()
    (project / "packs").mkdir()
    (project / "packs" / "u.md").write_text("# u synthetic pack\n")
    Store.init(_store(project)).close()
    _must("specify", "--root", _store(project), "--name", "u", "--version", "1")
    _must(
        "freeze", "--root", _store(project), "--name", "u", "--pack-version", "u-pack/1",
        "--project", str(project), "--pack-file", "packs/u.md",
    )
    _mark(project, "shared reason")


def _directories(
    tmp_path: Path, theirs_events: Callable[[Path], None], ours_events: Callable[[Path], None]
) -> tuple[Path, Path]:
    ours = tmp_path / "ours"
    _shared(ours)
    theirs = tmp_path / "theirs"
    shutil.copytree(ours, theirs)
    theirs_events(theirs)
    ours_events(ours)
    return ours, theirs


def test_directory_merge_skips_a_clear_ours_already_made_and_keeps_the_remark(
    tmp_path: Path,
) -> None:
    """R1, amended by D-45 (2026-11-21): a clear against THEIRS' clear and re-mark ends with
    different values (absent against present), so the merge now refuses and OURS is unchanged."""
    ours, theirs = _directories(tmp_path, _clear_and_remark, _ours_clear)
    before = _event_kinds(ours)
    code, output = _run("merge", "--root", _store(ours), "--other", _store(theirs))
    assert code == 2, output
    assert "merge.conflict" in output, output
    assert _exceptions(ours) == {}
    assert _event_kinds(ours) == before


def test_json_merge_reports_the_skipped_clear(tmp_path: Path) -> None:
    """R2, amended by D-45 (2026-11-21): the same merge with --json reports one merge.conflict.
    R3's JSON-free skip report is unchanged."""
    ours, theirs = _directories(tmp_path, _clear_and_remark, _ours_clear)
    code, output = _run("merge", "--root", _store(ours), "--other", _store(theirs), "--json")
    assert code == 2, output
    document = json.loads(output)
    assert document["accepted"] is False
    assert [item["code"] for item in document["violations"]] == ["merge.conflict"]


def test_a_clear_on_both_sides_merges_with_no_exception(tmp_path: Path) -> None:
    """R3."""
    ours, theirs = _directories(tmp_path, _clear, _ours_clear)
    code, output = _run("merge", "--root", _store(ours), "--other", _store(theirs))
    assert code == 0, output
    assert SKIPPED_TEXT in output, output
    assert _exceptions(ours) == {}
    assert _event_kinds(ours).count("OpenInCiCleared") == 1


def test_git_stage_recovery_skips_the_redundant_clear(tmp_path: Path) -> None:
    """R4, amended by D-45 (2026-11-21)."""
    project = tmp_path / "project"
    _shared(project)
    _must_git(project, "init", "-b", "main")
    _must_git(project, "config", "user.name", "Vouch Test")
    _must_git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    _must("generate", "--root", _store(project))
    _must_git(project, "add", "--", ".gitignore", ".vouch", "packs")
    _must_git(project, "commit", "-m", "shared")
    _must_git(project, "checkout", "-b", "incoming")
    _clear_and_remark(project)
    _must("generate", "--root", _store(project))
    _must_git(project, "add", "--", ".vouch")
    _must_git(project, "commit", "-m", "incoming")
    _must_git(project, "checkout", "main")
    _ours_clear(project)
    _must("generate", "--root", _store(project))
    _must_git(project, "add", "--", ".vouch")
    _must_git(project, "commit", "-m", "current")
    assert _git(project, "merge", "--no-edit", "incoming").returncode != 0
    stages = _must_git(project, "ls-files", "--stage")
    store = {
        str(path.relative_to(project)): path.read_bytes()
        for path in sorted((project / ".vouch").rglob("*"))
        if path.is_file()
    }
    code, output = _run("merge-git-stages", "--root", _store(project))
    # Amended by D-45 (2026-11-21): the clear against THEIRS' clear and re-mark now refuses and
    # leaves the index and the (still conflicted) working store unchanged.
    assert code == 2, output
    assert "merge.conflict" in output, output
    assert _must_git(project, "ls-files", "--stage") == stages
    assert {
        str(path.relative_to(project)): path.read_bytes()
        for path in sorted((project / ".vouch").rglob("*"))
        if path.is_file()
    } == store


def test_a_clear_aimed_at_another_unit_version_is_still_refused(tmp_path: Path) -> None:
    """R5. # guard:"""

    def ours_events(project: Path) -> None:
        _ours_clear(project)
        _must(
            "open-deviation", "--root", _store(project), "--deviation-id", "DEV-1",
            "--deviation-type", "pack-defect", "--unit", "u@1",
        )
        _must("supersede", "--root", _store(project), "--name", "u")
        _must("specify", "--root", _store(project), "--name", "u", "--version", "2")

    ours, theirs = _directories(tmp_path, _clear, ours_events)
    before = _snapshot(ours)
    code, output = _run("merge", "--root", _store(ours), "--other", _store(theirs))
    assert code == 2, output
    assert "merge.conflict" in output and "u@1" in output, output
    assert _exceptions(ours) == {}
    assert _snapshot(ours) == before


def test_a_direct_clear_of_an_absent_exception_is_still_refused(tmp_path: Path) -> None:
    """R6. # guard:"""
    project = tmp_path / "ours"
    _shared(project)
    _clear(project)
    before = _snapshot(project)
    code, output = _run("clear-open-in-ci", "--root", _store(project), "--name", "u")
    assert code == 2, output
    assert "ci-policy.exception-missing" in output, output
    assert _snapshot(project) == before


def test_quickstart_states_that_a_redundant_clear_is_skipped() -> None:
    """R7."""
    text = " ".join((ROOT / "docs" / "quickstart.md").read_text().split())
    assert (
        "An incoming clear of an open-in-CI exception that OURS has already cleared is skipped"
        in text
    )
