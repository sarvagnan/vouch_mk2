"""Acceptance pack for self-hosted unit operator-docs@1 (bead bd4.3).

Every test here FAILED against the pre-change tree (RED recorded before implementation)
and must PASS at completion. The implementer may not edit this file.

Intent: mk2's operator documentation is EXECUTABLE. `docs/quickstart.md` walks a scratch
project from `init` through unit lifecycle, evidence, a deviation, and a release, and this
pack runs every one of its ``sh`` blocks, in order, against a freshly built lock — so the
document cannot drift from the tool. `docs/commands.md` is generated from the real
`--help` output and drift-bound.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "docs" / "quickstart.md"
COMMANDS = ROOT / "docs" / "commands.md"

_FENCE = re.compile(r"^```sh(?P<tag>[^\n]*)\n(?P<body>.*?)^```", re.M | re.S)


def _blocks() -> list[tuple[str, str]]:
    """(tag, body) for every ```sh block, in document order. tag is '' or 'refuses'."""
    return [(m.group("tag").strip(), m.group("body")) for m in _FENCE.finditer(QUICKSTART.read_text())]


@pytest.fixture(scope="module")
def lock(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from vouch_mk2.lock import build_lock

    path = tmp_path_factory.mktemp("lock") / "vouch.lock"
    path.write_bytes(build_lock(str(ROOT / "src")))
    return path


def _run_blocks(workdir: Path, lock: Path) -> list[tuple[str, int, str]]:
    """Run each block with `sh -e` in workdir; PATH has python3 -> sys.executable (pytest available)."""
    shim = workdir / ".bin"
    shim.mkdir()
    (shim / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (shim / "python3").chmod(0o755)
    (workdir / "vouch.lock").write_bytes(lock.read_bytes())
    env = {"PATH": f"{shim}:/usr/bin:/bin", "HOME": str(workdir), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""}
    results: list[tuple[str, int, str]] = []
    for tag, body in _blocks():
        proc = subprocess.run(["sh", "-e"], input=body, cwd=workdir, env=env, capture_output=True, text=True, check=False)
        results.append((tag, proc.returncode, proc.stdout + proc.stderr))
    return results


# ---- the document exists and is executable, block by block ----


def test_quickstart_has_a_full_lifecycle_worth_of_blocks() -> None:
    blocks = _blocks()
    assert len(blocks) >= 10
    text = QUICKSTART.read_text()
    for verb in ("init", "specify", "freeze", "start", "record-evidence", "publish", "open-deviation", "prepare-release", "complete", "check", "upgrade"):
        assert re.search(rf"vouch\.lock {verb}\b", text), f"quickstart never shows `{verb}`"


def test_every_quickstart_block_runs_in_order_with_the_expected_outcome(tmp_path: Path, lock: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    results = _run_blocks(project, lock)
    assert results, "no blocks ran"
    for index, (tag, code, output) in enumerate(results):
        if tag == "refuses":
            assert code == 2, f"block {index} tagged `refuses` exited {code}:\n{output}"
        else:
            assert code == 0, f"block {index} exited {code}:\n{output}"


def test_quickstart_ends_with_a_published_unit_a_succeeded_release_and_a_clean_check(tmp_path: Path, lock: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _run_blocks(project, lock)
    from vouch_mk2.core.model import ReleaseState, UnitStatus
    from vouch_mk2.store import Store

    with Store.open(str(project / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
    assert any(u.status is UnitStatus.PUBLISHED for u in snapshot.units)
    assert any(r.state is ReleaseState.SUCCEEDED for r in snapshot.releases)
    assert any(d.status.value == "closed" for d in snapshot.deviations), "the quickstart must walk one deviation to closure"
    assert snapshot.evidence_heads, "the quickstart must record real evidence"
    proc = subprocess.run([sys.executable, str(lock), "check", "--ci", "--root", str(project / ".vouch")], capture_output=True, text=True, env={"PATH": "", "PYTHONPATH": ""}, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_quickstart_shows_at_least_one_refusal_and_states_the_evidence_claim() -> None:
    assert any(tag == "refuses" for tag, _ in _blocks()), "show the reader what a refusal looks like"
    text = QUICKSTART.read_text()
    assert "exit status" in text, "state mk2's exact claim for record-evidence (claim-boundary.md)"
    assert "no invented state" in text or "--was-published" in text, "the upgrade section must mention the assertion rule"


def test_every_verb_the_quickstart_names_is_a_real_subcommand() -> None:
    from vouch_mk2.cli import main

    verbs = set(re.findall(r"vouch\.lock ([a-z][a-z-]*)", QUICKSTART.read_text()))
    assert verbs
    for verb in sorted(verbs):
        with pytest.raises(SystemExit) as raised:
            main([verb, "--help"])
        assert raised.value.code == 0, verb


# ---- the generated command reference is drift-bound ----


def test_commands_reference_is_generated_and_fresh() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_commands_doc.py"), "--check"], capture_output=True, text=True, cwd=ROOT, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = COMMANDS.read_text()
    from vouch_mk2.cli import LIFECYCLE_VERBS

    for verb in LIFECYCLE_VERBS:
        assert f"## {verb}" in text, verb
    for verb in ("check", "generate", "upgrade", "init", "build-lock", "record-evidence"):
        assert f"## {verb}" in text, verb


def test_readme_starts_with_the_quickstart() -> None:
    head = "\n".join((ROOT / "README.md").read_text().splitlines()[:12])
    assert "docs/quickstart.md" in head and "docs/commands.md" in head
