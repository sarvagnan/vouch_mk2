"""Acceptance pack for ``init-publication-closure@1`` (D-7 closure)."""

from __future__ import annotations

import inspect
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from vouch_mk2 import init as init_module
from vouch_mk2.init import InitError, InitStatus, init_project
from vouch_mk2.lock import build_lock, lock_digest
from vouch_mk2.store import Store


ROOT = Path(__file__).resolve().parents[1]
LOCK_BYTES = build_lock(str(ROOT / "src"))


def _plan(
    destination: Path, data: bytes = b"vouch bytes\n"
) -> init_module._PlannedFile:
    return init_module._PlannedFile(
        destination.name,
        str(destination),
        data,
        0o644,
        InitStatus.WRITTEN,
        True,
    )


def test_publication_race_never_overwrites_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "staged"
    staged.write_bytes(b"our staged bytes\n")
    destination = tmp_path / "final"
    winner = b"concurrent winner\n"
    plan = _plan(destination, staged.read_bytes())
    real_link = os.link
    real_rename = os.rename

    def race_link(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if Path(target) == destination and not destination.exists():
            destination.write_bytes(winner)
        real_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def race_rename(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if Path(target) == destination and not destination.exists():
            destination.write_bytes(winner)
        real_rename(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(init_module.os, "link", race_link)
    monkeypatch.setattr(init_module.os, "rename", race_rename)
    with pytest.raises(InitError):
        init_module._place_staged_file(str(staged), plan)
    assert destination.read_bytes() == winner


def test_store_publication_does_not_use_plain_rename() -> None:
    source = inspect.getsource(init_project)
    assert "os.rename(staged_store, store_path)" not in source


def test_rollback_does_not_unlink_a_concurrent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "staged"
    staged.write_bytes(b"our staged bytes\n")
    destination = tmp_path / "final"
    winner = b"replacement after publication\n"
    plan = _plan(destination, staged.read_bytes())

    real_stat = os.stat
    real_unlink = os.unlink
    replacement_injected = False

    def fail_after_publication(_directory: str) -> None:
        raise OSError("simulated directory fsync failure")

    def replace_after_identity_check(
        path: str, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        nonlocal replacement_injected
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if Path(path) == destination and not replacement_injected:
            real_unlink(destination)
            destination.write_bytes(winner)
            replacement_injected = True
        return result

    monkeypatch.setattr(init_module, "_fsync_directory", fail_after_publication)
    monkeypatch.setattr(init_module.os, "stat", replace_after_identity_check)
    with pytest.raises(InitError, match="cannot place"):
        init_module._place_staged_file(str(staged), plan)
    if replacement_injected:
        assert destination.read_bytes() == winner
    else:
        assert destination.read_bytes() == staged.read_bytes()


def test_post_publication_failure_is_exit_4_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project-incomplete"
    project.mkdir()
    supplied = tmp_path / "supplied.lock"
    supplied.write_bytes(LOCK_BYTES)
    real_place = init_module._place_staged_file
    placements = 0

    def fail_after_first_publication(
        staged: str, plan: init_module._PlannedFile
    ) -> object:
        nonlocal placements
        published = real_place(staged, plan)
        placements += 1
        if placements == 1:
            raise OSError("simulated failure after publication")
        return published

    monkeypatch.setattr(
        init_module, "_place_staged_file", fail_after_first_publication
    )
    from vouch_mk2.cli import main

    assert main(
        ["init", "--project", str(project), "--lock", str(supplied)]
    ) == 4
    captured = capsys.readouterr()
    output = (captured.out + captured.err).lower()
    assert "incomplete" in output and "retry" in output
    assert (project / "vouch.lock").read_bytes() == LOCK_BYTES
    assert not (project / ".vouch").exists()

    monkeypatch.setattr(init_module, "_place_staged_file", real_place)
    assert main(
        ["init", "--project", str(project), "--lock", str(supplied)]
    ) == 0
    assert (project / ".vouch/config.json").is_file()


def _crash_driver(tmp_path: Path, project: Path, mode: str, count: int = 0) -> Path:
    lock_path = tmp_path / f"{mode}-{count}.lock"
    lock_path.write_bytes(LOCK_BYTES)
    driver = tmp_path / f"{mode}-{count}.py"
    driver.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from vouch_mk2 import init as module\n"
        f"project = Path({str(project)!r})\n"
        f"lock = Path({str(lock_path)!r}).read_bytes()\n"
        f"mode = {mode!r}\n"
        f"limit = {count!r}\n"
        "if mode == 'file':\n"
        "    real = module._place_staged_file\n"
        "    seen = 0\n"
        "    def crash_after_file(staged, plan):\n"
        "        global seen\n"
        "        real(staged, plan)\n"
        "        seen += 1\n"
        "        if seen == limit:\n"
        "            os._exit(91)\n"
        "    module._place_staged_file = crash_after_file\n"
        "elif mode == 'store':\n"
        "    real_fsync_directory = module._fsync_directory\n"
        "    def crash_after_store(directory):\n"
        "        real_fsync_directory(directory)\n"
        "        if Path(directory) == project and (project / '.vouch').exists():\n"
        "            os._exit(92)\n"
        "    module._fsync_directory = crash_after_store\n"
        "module.init_project(str(project), lock)\n"
    )
    return driver


def _run_driver(driver: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(driver)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )


@pytest.mark.parametrize("placement_count", (1, 2))
def test_hard_death_after_each_file_placement_is_complete_and_retryable(
    tmp_path: Path, placement_count: int
) -> None:
    # guard: the review found missing crash coverage, not a demonstrated bad retry here.
    project = tmp_path / f"project-{placement_count}"
    project.mkdir()
    completed = _run_driver(_crash_driver(tmp_path, project, "file", placement_count))
    assert completed.returncode == 91, completed.stderr
    assert not (project / ".vouch").exists()
    if (project / "vouch.lock").exists():
        assert (project / "vouch.lock").read_bytes() == LOCK_BYTES
        assert stat.S_IMODE((project / "vouch.lock").stat().st_mode) == 0o755
    workflow = project / ".github/workflows/vouch.yml"
    if workflow.exists():
        assert workflow.read_text() == init_module.generated_workflow()

    init_project(str(project), LOCK_BYTES)
    assert (project / "vouch.lock").read_bytes() == LOCK_BYTES
    assert workflow.read_text() == init_module.generated_workflow()
    config = json.loads((project / ".vouch/config.json").read_text())
    assert config["lock_digest"] == lock_digest(LOCK_BYTES)


def test_hard_death_at_store_publication_never_exposes_an_invalid_install(
    tmp_path: Path,
) -> None:
    # guard: store publication must remain complete even when the process cannot return.
    project = tmp_path / "project-store"
    project.mkdir()
    completed = _run_driver(_crash_driver(tmp_path, project, "store"))
    assert completed.returncode == 92, completed.stderr
    assert (project / "vouch.lock").read_bytes() == LOCK_BYTES
    assert (project / ".github/workflows/vouch.yml").read_text() == init_module.generated_workflow()
    with Store.open(str(project / ".vouch")) as store:
        store.read_snapshot()
    config = json.loads((project / ".vouch/config.json").read_text())
    assert config["lock_digest"] == lock_digest(LOCK_BYTES)


def test_init_has_no_production_only_fault_hook() -> None:
    assert not hasattr(init_module, "_init_step")
    assert "_init_step(" not in Path(init_module.__file__).read_text()
    assert "on_step" not in inspect.signature(init_project).parameters


def test_successful_init_still_owns_only_the_closed_path_set(tmp_path: Path) -> None:
    # guard: D-7's closed ownership set must survive the publication changes.
    project = tmp_path / "project"
    project.mkdir()
    report = init_project(str(project), LOCK_BYTES)
    assert tuple(result.path for result in report) == (
        ".vouch",
        "vouch.lock",
        ".vouch/config.json",
        ".github/workflows/vouch.yml",
    )
