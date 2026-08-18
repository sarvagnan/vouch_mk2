from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from pytest import CaptureFixture

from vouch_mk2.cli import main
from vouch_mk2.init import InitError, InitStatus, init_project
from vouch_mk2.lock import build_lock
from vouch_mk2.store import ArtifactKind, digest_of


ROOT = Path(__file__).resolve().parents[1]
LOCK_BYTES = build_lock(str(ROOT / "src"))


def _project(tmp_path: Path, name: str = "project") -> Path:
    project = tmp_path / name
    project.mkdir()
    return project


def _all_relative_paths(project: Path) -> set[str]:
    return {
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
    }


def test_init_fresh_project_writes_only_closed_set_and_valid_config(tmp_path: Path) -> None:
    project = _project(tmp_path)
    report = init_project(str(project), LOCK_BYTES)
    assert tuple(result.path for result in report) == (
        ".vouch",
        "vouch.lock",
        ".vouch/config.json",
        ".github/workflows/vouch.yml",
    )
    assert all(result.status is InitStatus.WRITTEN for result in report)
    assert _all_relative_paths(project) == {
        ".github",
        ".github/workflows",
        ".github/workflows/vouch.yml",
        ".vouch",
        ".vouch/.lock",
        ".vouch/config.json",
        ".vouch/objects",
        ".vouch/objects/sha256",
        ".vouch/state.json",
        ".vouch/state-format.json",
        "vouch.lock",
    }
    config = json.loads((project / ".vouch/config.json").read_bytes())
    assert config == {
        "config_format": 1,
        "lock_digest": digest_of(ArtifactKind.LOCK, LOCK_BYTES),
        "store": ".vouch",
    }
    assert "check --ci" in (project / ".github/workflows/vouch.yml").read_text()
    assert stat.S_IMODE((project / "vouch.lock").stat().st_mode) == 0o755


def test_second_init_refuses_existing_store(tmp_path: Path) -> None:
    project = _project(tmp_path)
    init_project(str(project), LOCK_BYTES)
    lock_before = (project / "vouch.lock").read_bytes()
    with pytest.raises(InitError, match="refusing re-initialization"):
        init_project(str(project), b"different")
    assert (project / "vouch.lock").read_bytes() == lock_before


def test_init_differing_workflow_writes_sidecar_without_touching_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    workflow = project / ".github/workflows/vouch.yml"
    workflow.parent.mkdir(parents=True)
    original = b"human workflow\n"
    workflow.write_bytes(original)
    report = init_project(str(project), LOCK_BYTES)
    result = next(item for item in report if item.path == ".github/workflows/vouch.yml")
    assert result.status is InitStatus.SUGGESTED
    assert workflow.read_bytes() == original
    suggested = Path(str(workflow) + ".vouch-suggested")
    assert b"check --ci" in suggested.read_bytes()


def test_init_equal_workflow_is_unchanged(tmp_path: Path) -> None:
    template = _project(tmp_path, "template")
    init_project(str(template), LOCK_BYTES)
    expected = (template / ".github/workflows/vouch.yml").read_bytes()

    project = _project(tmp_path)
    workflow = project / ".github/workflows/vouch.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(expected)
    report = init_project(str(project), LOCK_BYTES)
    result = next(item for item in report if item.path == ".github/workflows/vouch.yml")
    assert result.status is InitStatus.UNCHANGED
    assert workflow.read_bytes() == expected
    assert not Path(str(workflow) + ".vouch-suggested").exists()


def test_init_refuses_symlinked_lock_before_writing_store(tmp_path: Path) -> None:
    project = _project(tmp_path)
    referent = tmp_path / "referent"
    referent.write_bytes(b"human\n")
    (project / "vouch.lock").symlink_to(referent)
    with pytest.raises(InitError, match="symlinked init target refused"):
        init_project(str(project), LOCK_BYTES)
    assert referent.read_bytes() == b"human\n"
    assert not (project / ".vouch").exists()


def test_cli_init_with_supplied_lock_prints_four_report_lines(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    supplied = tmp_path / "supplied.lock"
    supplied.write_bytes(LOCK_BYTES)
    assert main(["init", "--project", str(project), "--lock", str(supplied)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "written .vouch",
        "written vouch.lock",
        "written .vouch/config.json",
        "written .github/workflows/vouch.yml",
        "Vouch requires Python 3.14+",
    ]


def test_init_run_from_lock_copies_the_running_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "source.lock"
    lock_path.write_bytes(LOCK_BYTES)
    project = _project(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(lock_path), "init", "--project", str(project)],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": ""},
    )
    assert completed.returncode == 0, completed.stderr
    assert (project / "vouch.lock").read_bytes() == LOCK_BYTES


def test_init_refuses_symlinked_github_components_and_writes_nothing(
    tmp_path: Path,
) -> None:
    # A cloned repo shipping `.github -> /elsewhere` must not make init write
    # outside the project root while reporting an in-root write.
    import os

    lock = LOCK_BYTES
    for link_target_name, link_path_parts in (
        ("victim", (".github",)),
        ("victim2", (".github", "workflows")),
    ):
        project = tmp_path / f"proj-{link_target_name}"
        project.mkdir()
        victim = tmp_path / link_target_name
        victim.mkdir()
        link = project.joinpath(*link_path_parts)
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(victim, link)
        before = sorted(str(p) for p in project.rglob("*")) + sorted(
            str(p) for p in victim.rglob("*")
        )
        with pytest.raises(InitError, match="symlinked path component refused"):
            init_project(str(project), lock)
        after = sorted(str(p) for p in project.rglob("*")) + sorted(
            str(p) for p in victim.rglob("*")
        )
        assert after == before, "refusal must precede every write"
        assert not (project / ".vouch").exists(), "no half-initialized store"


def test_init_dangling_github_symlink_is_typed_and_leaves_no_store(tmp_path: Path) -> None:
    import os

    project = tmp_path / "proj"
    project.mkdir()
    os.symlink(tmp_path / "does-not-exist", project / ".github")
    with pytest.raises(InitError):
        init_project(str(project), LOCK_BYTES)
    assert not (project / ".vouch").exists()
    # A retry after removing the bad link must succeed — no permanent wedge.
    os.unlink(project / ".github")
    results = init_project(str(project), LOCK_BYTES)
    assert {r.path for r in results} == {
        ".vouch", "vouch.lock", ".vouch/config.json", ".github/workflows/vouch.yml"
    }


def test_generated_workflow_pins_the_lock_digest() -> None:
    from vouch_mk2.init import _WORKFLOW

    assert b"self-check --expect @.vouch/config.json" in _WORKFLOW
