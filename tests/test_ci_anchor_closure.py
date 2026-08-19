"""Acceptance pack for self-hosted unit ci-anchor-closure@1 (D-18 B5 follow-up).

Batch A implemented CI anchoring for adopters (the fragment `init` writes) and made mk2's own
store depend on it, but never wired the anchor job into mk2's OWN ci.yml — so the store's honest
`check.evidence-anchors` NOT_CHECKED row had no path to resolution, and the no-network
walkthrough (a plan §7 DoD probe) began exiting 1 with nobody recording that change.

Every test here FAILED against the pre-change tree — except tests marked `# guard:`. The
implementer may not edit this file; a finding that this pack is wrong is a DEVIATION.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
WALKTHROUGH = ROOT / "scripts" / "walkthrough.py"


# ---- the anchor claim is real: it resolves only with a run identity ----


def _unit_through_green(root: str, project: Path, name: str = "core") -> None:
    (project / "tests").mkdir(parents=True, exist_ok=True)
    (project / "tests" / "t.py").write_text("def test_x():\n    assert False\n")
    assert main(["set-tier", "--root", root, "--tier", "standard"]) == 0
    assert main(["set-anchor-mode", "--root", root, "--mode", "ci"]) == 0
    assert main(["specify", "--root", root, "--name", name, "--version", "v1"]) == 0
    assert main(["freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/1",
                 "--project", str(project), "--pack-test-file", "tests/t.py"]) == 0
    assert main(["mark-ready", "--root", root, "--name", name]) == 0
    assert main(["start", "--root", root, "--name", name]) == 0
    assert main(["record-evidence", "--root", root, "--unit", f"{name}@v1", "--kind", "red", "--",
                 sys.executable, "-c", "raise SystemExit(1)"]) == 0
    assert main(["record-evidence", "--root", root, "--unit", f"{name}@v1", "--kind", "green", "--",
                 sys.executable, "-c", "raise SystemExit(0)"]) == 0


def test_a_locally_advanced_anchor_is_not_checked_and_a_ci_run_resolves_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # guard: batch A built this; pin it, because everything below depends on it being real.
    project = tmp_path / "proj"
    project.mkdir()
    root = str(project / ".vouch")
    Store.init(root).close()
    _unit_through_green(root, project)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert main(["advance-anchor", "--root", root, "--unit-name", "core"]) == 0
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    def anchors() -> tuple[Verdict, str]:
        with Store.open(root, read_only=True) as store:
            row = next(r for r in run_artifact_checks(store, store.read_snapshot()) if r.check_id == mk2.CHECK_EVIDENCE_ANCHORS)
        return row.verdict, row.detail

    verdict, detail = anchors()
    assert verdict is Verdict.NOT_CHECKED and "run claim" in detail
    # A real CI run advances the same head with its run identity; nothing else changes.
    monkeypatch.setenv("GITHUB_RUN_ID", "123456789")
    assert main(["record-evidence", "--root", root, "--unit", "core@v1", "--kind", "refactor", "--",
                 sys.executable, "-c", "raise SystemExit(0)"]) == 0
    assert main(["advance-anchor", "--root", root, "--unit-name", "core"]) == 0
    verdict, detail = anchors()
    assert verdict is Verdict.PASS, detail
    with Store.open(root, read_only=True) as store:
        head = store.read_snapshot().evidence_head("core")
    assert head is not None and head.anchor_run_id == "123456789"


# ---- mk2's own CI closes the loop it opened ----


def test_mk2_ci_advances_anchors_before_asserting_its_own_store_is_clean() -> None:
    text = CI.read_text()
    assert "advance-anchor" in text, "mk2's own CI must advance anchors it observed"
    assert "contents: write" in text
    assert "[skip ci]" not in text
    assert "github.event_name == 'push'" in text
    assert "github.event.repository.default_branch" in text
    anchor_at = text.index("advance-anchor")
    dogfood_at = text.index("check --ci --root .vouch")
    assert anchor_at < dogfood_at, "anchors must be advanced before the dogfood step asserts no mechanical NOT-CHECKED"


def test_the_suite_can_pass_before_ci_has_anchored(tmp_path: Path) -> None:
    """pack/3 (DEV-cac-03): `pytest` runs BEFORE the anchor step, so any test that reads mk2's
    own store must tolerate a pending anchor — otherwise CI goes red at the test gate and never
    reaches the job that resolves it.

    This asserts the TOLERANCE PROPERTY, not mk2's live store state. The earlier version
    asserted the committed store had no FAIL row at the moment this test ran, which made it a
    trap: any transient store condition broke it, and repairing it required a `rebind-pack`
    whose own pending row was such a condition. The store's health is asserted by the dogfood
    tests themselves; this test's job is that those tests tolerate the right row and no other.
    """
    text = (ROOT / "tests" / "test_cli.py").read_text()
    assert "no workflow run claim" in text, "the dogfood tests must name the one row they tolerate"
    # The tolerance must be exact: a FAIL is never acceptable, and only the anchor row may be
    # a tolerated NOT-CHECKED.
    assert "mechanical_fail == []" in text or "must refuse every mechanical FAIL row" in text
    assert "len(mechanical_not_checked) == 1" in text or "exactly one" in text.lower()


def test_mk2_ci_still_refuses_any_mechanical_not_checked_row() -> None:
    # guard: the strict dogfood assertion must survive this unit — it is the D-10 promise.
    text = CI.read_text()
    assert "! grep -E '^NOT-CHECKED +check\\.'" in text or '! grep -E "^NOT-CHECKED +check\\."' in text


# ---- the walkthrough proves both states, and says which ----


def test_walkthrough_demonstrates_the_pending_anchor_and_then_a_simulated_ci_run() -> None:
    text = WALKTHROUGH.read_text()
    assert "GITHUB_RUN_ID" in text, "the walkthrough must simulate a CI run rather than assert the mechanism works"
    assert "composed: NOT-CHECKED" in text, "it must first show the honest pending state"
    assert "composed: PASS" in text
    assert "no workflow run claim" in text


@pytest.mark.slow
def test_walkthrough_from_the_lock_ends_clean_again(tmp_path: Path) -> None:
    from vouch_mk2.lock import build_lock

    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    result = subprocess.run([sys.executable, str(WALKTHROUGH), "--lock", str(lock)],
                            capture_output=True, text=True, cwd=str(ROOT), check=False,
                            env={**os.environ, "PYTHONPATH": ""})
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "anchor pending" in result.stdout and "composed PASS" in result.stdout


# ---- the constraint is recorded where the decision that created it lives ----


def test_d18_records_that_ci_mode_cannot_compose_pass_before_a_real_run() -> None:
    text = (ROOT / "DECISIONS.md").read_text()
    section = text[text.index("## D-18"):]
    assert "cannot compose" in section.lower() or "no project composes PASS" in section
    assert "run" in section.lower()


def test_docs_state_the_constraint_where_an_operator_meets_it() -> None:
    quickstart = (ROOT / "docs" / "quickstart.md").read_text()
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text()
    # The operator meets this the first time `check` returns 1 on a store they believe is finished.
    for text, where in ((quickstart, "quickstart"), (boundary, "claim-boundary")):
        assert re.search(r"until .{0,80}(workflow run|CI run)", text, re.I | re.S), where
    assert "anchor mode `ci`" in quickstart or "anchor mode ci" in quickstart
