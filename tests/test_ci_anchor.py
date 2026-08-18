"""Acceptance pack for self-hosted unit ci-anchor@1 (D-18 B5 option a).

Every test here FAILED against the pre-change tree (RED recorded before implementation) and must PASS
at completion -- except tests marked `# guard:`. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main

ROOT = Path(__file__).resolve().parents[1]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]
OK = [sys.executable, "-c", "raise SystemExit(0)"]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    code, out = _run("init", "--project", str(project))
    assert code == 0, out
    return project, str(project / ".vouch")


def _workflow(project: Path) -> str:
    return (project / ".github" / "workflows" / "vouch.yml").read_text()


def _unit_with_evidence(project: Path, root: str, name: str = "u") -> None:
    (project / "tests").mkdir(exist_ok=True)
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    assert _run("set-anchor-mode", "--root", root, "--mode", "ci")[0] == 0
    assert _run("specify", "--root", root, "--name", name, "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/1",
                "--project", str(project), "--pack-test-file", "tests/t.py")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", name)[0] == 0
    assert _run("start", "--root", root, "--name", name)[0] == 0
    assert _run("record-evidence", "--root", root, "--unit", f"{name}@v1", "--kind", "red",
                "--", *FAIL)[0] == 0


# ---- R1 ----


def test_workflow_anchors_after_checks_and_commits_with_skip_ci(tmp_path: Path) -> None:
    project, _root = _project(tmp_path)
    workflow = _workflow(project)
    assert "advance-anchor" in workflow, workflow
    assert "[skip ci]" in workflow, workflow
    check_at = workflow.index("check --ci")
    anchor_at = workflow.index("advance-anchor")
    assert anchor_at > check_at, "anchoring must come after the check step"


# ---- R2 ----


def test_workflow_does_not_anchor_on_fork_pull_requests(tmp_path: Path) -> None:
    project, _root = _project(tmp_path)
    workflow = _workflow(project)
    assert "if:" in workflow, workflow
    lowered = workflow.lower()
    assert "head.repo" in lowered or "fork" in lowered, (
        "the fork guard must be visible in the workflow, not only described in prose"
    )


# ---- R3 ----


def _anchor_record(root: str) -> dict[str, object]:
    from vouch_mk2.store import ArtifactKind, Store

    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        digest = snapshot.event_head
        while digest and digest != "GENESIS":
            record = json.loads(store.get_object(ArtifactKind.EVENT, str(digest)))
            if "anchor" in str(record["event"]).lower():
                return record
            digest = str(record["prev"])
    raise AssertionError("no anchor event found in the chain")


def test_advance_anchor_records_the_run_id_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, root = _project(tmp_path)
    _unit_with_evidence(project, root)
    monkeypatch.setenv("GITHUB_RUN_ID", "424242")
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _anchor_record(root)["run_id"] == "424242"


def test_local_anchor_records_no_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, root = _project(tmp_path)
    _unit_with_evidence(project, root)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _anchor_record(root)["run_id"] == ""


# ---- R4 ----


def test_anchor_run_claim_is_displayed_when_present_and_absent_when_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, root = _project(tmp_path)
    _unit_with_evidence(project, root)
    monkeypatch.setenv("GITHUB_RUN_ID", "987654")
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("status", "--root", root)
    assert code == 0, out
    context = (Path(root) / "vouch-context.md").read_text()
    combined = out + context
    assert "987654" in combined and "advanced by workflow run" in combined, combined[:800]

    project2, root2 = _project(tmp_path / "second")
    _unit_with_evidence(project2, root2)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert _run("advance-anchor", "--root", root2, "--unit-name", "u")[0] == 0
    assert _run("generate", "--root", root2)[0] == 0
    code, out2 = _run("status", "--root", root2)
    context2 = (Path(root2) / "vouch-context.md").read_text()
    assert "advanced by workflow run" not in (out2 + context2)


# ---- R5 ----


def test_ci_anchor_mode_does_not_claim_ci_for_locally_anchored_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, root = _project(tmp_path)
    _unit_with_evidence(project, root)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--root", root)
    rows = [line for line in out.splitlines() if "evidence-anchors" in line]
    assert rows, out
    assert rows[0].startswith("NOT-CHECKED"), (
        f"at anchor mode ci a locally advanced anchor must not read as CI-observed: {rows[0]}"
    )
    assert "u" in rows[0]


# ---- R6 ----


def test_docs_state_the_run_id_is_a_claim() -> None:
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text().lower()
    quickstart = (ROOT / "docs" / "quickstart.md").read_text().lower()
    assert "run" in boundary and "claim" in boundary
    assert "url" in boundary
    assert "skip ci" in quickstart or "bot commit" in quickstart
    assert "fork" in quickstart
