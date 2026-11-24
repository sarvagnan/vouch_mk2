"""Frozen acceptance pack for release-identity@1 (D-56, vouch_mk2-b6g.14.1).

Vouch carries an MIT licence (at the repository root, in the package, and so in every lock) and
reports its version: `vouch-mk2 --version` prints it, and a lock also prints its own digest, the
one `self-check` prints. Every test here fails before the change, except tests marked
``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tomllib
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import vouch_mk2
from vouch_mk2.cli import main

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
MIT_GRANT = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy of this "
    "software and associated documentation files (the \"Software\"), to deal in the Software "
    "without restriction"
)


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return cast(dict[str, object], tomllib.load(handle)["project"])


def _built_lock(tmp_path: Path) -> tuple[Path, str]:
    lock = tmp_path / "vouch.lock"
    code, out, err = _run("build-lock", "--output", str(lock))
    assert code == 0, (out, err)
    return lock, out.split()[-1]


# R1


def test_the_repository_carries_the_mit_licence() -> None:
    text = (ROOT / "LICENSE").read_text()
    assert text.splitlines()[0] == "MIT License"
    assert "Copyright (c) 2026 sarvagnan" in text
    assert MIT_GRANT in " ".join(text.split())
    assert (ROOT / "src" / "vouch_mk2" / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_the_package_metadata_declares_the_licence() -> None:
    project = _pyproject()
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]


# R2


def test_the_version_is_0_1_0_everywhere() -> None:  # guard: the decided version
    assert _pyproject()["version"] == VERSION
    assert vouch_mk2.__version__ == VERSION


def test_the_cli_prints_its_version() -> None:
    code, out, err = _run("--version")
    assert code == 0, (out, err)
    assert out == f"vouch-mk2 {VERSION}\n"
    assert err == ""


# R3


def test_a_lock_prints_its_version_and_digest(tmp_path: Path) -> None:
    lock, built_digest = _built_lock(tmp_path)
    version = subprocess.run(
        [sys.executable, str(lock), "--version"],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )
    assert version.returncode == 0, version
    assert version.stdout == f"vouch-mk2 {VERSION} lock {built_digest}\n"
    self_check = subprocess.run(
        [sys.executable, str(lock), "self-check"],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )
    assert self_check.stdout.strip() == built_digest


def test_a_lock_carries_the_licence(tmp_path: Path) -> None:
    lock, _ = _built_lock(tmp_path)
    with zipfile.ZipFile(lock) as archive:
        assert archive.read("vouch_mk2/LICENSE") == (ROOT / "LICENSE").read_bytes()


# R4


def test_the_readme_states_the_licence_and_the_version_flag() -> None:
    text = " ".join((ROOT / "README.md").read_text().split())
    assert "Vouch is released under the MIT License (see `LICENSE`)." in text
    assert (
        "`python3 vouch.lock --version` prints the version and the lock's digest."
    ) in text
