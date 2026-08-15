from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import zipfile

import pytest
from io import BytesIO
from pathlib import Path

from pytest import CaptureFixture

from vouch_mk2.cli import main
from vouch_mk2.lock import SHEBANG, build_lock, lock_digest
from vouch_mk2.store import ArtifactKind, Store, StoreError, digest_of


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"


def test_lock_build_is_byte_reproducible_and_package_only() -> None:
    first = build_lock(str(SOURCE_ROOT))
    second = build_lock(str(SOURCE_ROOT))
    assert first == second
    assert first.startswith(SHEBANG)

    with zipfile.ZipFile(BytesIO(first)) as archive:
        assert archive.comment == b""
        names = archive.namelist()
        assert names == sorted(names)
        assert "__main__.py" in names
        assert "vouch_mk2/cli.py" in names
        assert all(name == "__main__.py" or name.startswith("vouch_mk2/") for name in names)
        # Every shipped package file is bound (data files would appear here);
        # host droppings never do.
        assert not any(name.rsplit("/", 1)[-1].startswith(".") for name in names)
        assert not any(name.endswith(".pyc") for name in names)
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.external_attr == 0o100644 << 16


def test_lock_runs_check_and_self_check_without_repo_on_path(tmp_path: Path) -> None:
    lock_path = tmp_path / "vouch.lock"
    lock_bytes = build_lock(str(SOURCE_ROOT))
    lock_path.write_bytes(lock_bytes)
    lock_path.chmod(0o755)
    scratch = tmp_path / "scratch"
    Store.init(str(scratch)).close()
    environment = {"PATH": os.environ["PATH"], "PYTHONPATH": ""}

    check = subprocess.run(
        [sys.executable, str(lock_path), "check", "--root", str(scratch)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert check.returncode == 0, check.stderr
    assert check.stdout.rstrip().endswith("composed: PASS")

    self_check = subprocess.run(
        [sys.executable, str(lock_path), "self-check"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert self_check.returncode == 0, self_check.stderr
    assert self_check.stdout.strip() == digest_of(ArtifactKind.LOCK, lock_bytes)


def test_cli_build_lock_is_atomic_executable_and_reports_digest(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    output = tmp_path / "vouch.lock"
    assert main(["build-lock", "--output", str(output)]) == 0
    payload = output.read_bytes()
    assert capsys.readouterr().out == f"vouch.lock {digest_of(ArtifactKind.LOCK, payload)}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o755
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_self_check_refuses_source_installation(capsys: CaptureFixture[str]) -> None:
    assert main(["self-check"]) == 1
    assert capsys.readouterr().out == "not running from a lock\n"



def test_running_lock_path_prefers_the_executing_archive_over_argv0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # self-check must attest the archive the code actually runs from; argv[0]
    # is caller-controlled and can name a decoy.
    import importlib
    import sys

    real = tmp_path / "real.lock"
    real.write_bytes(build_lock(str(SOURCE_ROOT)))
    decoy = tmp_path / "decoy.lock"
    decoy.write_bytes(build_lock(str(SOURCE_ROOT)) + b"\n# decoy\n")
    monkeypatch.setattr(sys, "argv", [str(decoy)])
    # Simulate the executing module living inside real.lock.
    import vouch_mk2.lock as lock_module

    monkeypatch.setattr(
        lock_module, "__file__", str(real / "vouch_mk2" / "lock.py")
    )
    assert lock_module.running_lock_path() == str(real)
    importlib.reload(lock_module)


def test_self_check_expect_pins_the_digest(tmp_path: Path) -> None:
    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(SOURCE_ROOT)))
    lock.chmod(0o755)
    digest = lock_digest(lock.read_bytes())
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": ""}
    ok = subprocess.run(
        [sys.executable, str(lock), "self-check", "--expect", digest],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert ok.returncode == 0, ok.stderr
    bad = subprocess.run(
        [sys.executable, str(lock), "self-check", "--expect", "0" * 64],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert bad.returncode == 1 and "mismatch" in bad.stderr
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"lock_digest": digest}))
    via_config = subprocess.run(
        [sys.executable, str(lock), "self-check", "--expect", f"@{config}"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert via_config.returncode == 0, via_config.stderr


def test_lock_binds_shipped_data_files_and_refuses_symlinked_packages(
    tmp_path: Path,
) -> None:
    import shutil

    copy = tmp_path / "src"
    shutil.copytree(SOURCE_ROOT / "vouch_mk2", copy / "vouch_mk2",
                    ignore=shutil.ignore_patterns("__pycache__", ".*"))
    baseline = lock_digest(build_lock(str(copy)))
    (copy / "vouch_mk2" / "py.typed").write_bytes(b"")
    assert lock_digest(build_lock(str(copy))) != baseline, "data files must be bound"
    (copy / "vouch_mk2" / ".DS_Store").write_bytes(b"junk")
    with_dropping = lock_digest(build_lock(str(copy)))
    (copy / "vouch_mk2" / ".DS_Store").unlink()
    assert lock_digest(build_lock(str(copy))) == with_dropping, "dotfiles are not bound"
    os.symlink(tmp_path, copy / "vouch_mk2" / "extra")
    with pytest.raises(StoreError, match="symlinked package directory refused"):
        build_lock(str(copy))
