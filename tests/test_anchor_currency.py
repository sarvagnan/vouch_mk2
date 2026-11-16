"""Frozen acceptance pack for self-hosted unit anchor-currency@1 (D-34).

Found by the activity_tracker_portal pilot (D-24), finding 2: once CI claimed a unit's anchor,
evidence recorded after it was never anchored, because both anchor loops advanced only heads
with no run claim, and ``check.evidence-anchors`` still passed because it asked only whether the
anchor carried a run claim, not whether it had reached the head. The check's messages also named
a head by its unit name alone (``vouch_mk2-bd4.4.10``). Every test here failed against the
pre-change tree, except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import EvidenceHead, Snapshot
from vouch_mk2.init import generated_workflow
from vouch_mk2.ir import mk2
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]
OK = [sys.executable, "-c", "raise SystemExit(0)"]
FIRST_RUN = "424242"
SECOND_RUN = "515151"
LAG = "anchor lags its head"

# Every CI run-id variable Vouch recognises (store/event_log.py). Tests set one only where a
# requirement names a run claim.
CI_RUN_VARIABLES = (
    "GITHUB_RUN_ID",
    "CI_PIPELINE_ID",
    "CIRCLE_WORKFLOW_ID",
    "BUILDKITE_BUILD_ID",
    "BUILD_BUILDID",
    "JENKINS_BUILD_ID",
    "BUILD_ID",
    "TRAVIS_BUILD_ID",
    "BITBUCKET_PIPELINE_UUID",
    "CODEBUILD_BUILD_ID",
    "DRONE_BUILD_NUMBER",
    "TEAMCITY_BUILD_ID",
)


@pytest.fixture(autouse=True)
def _no_ci_run(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in CI_RUN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield


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


def _snapshot(root: str) -> Snapshot:
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot()


def _heads(root: str) -> dict[str, EvidenceHead]:
    return {head.ref().label(): head for head in _snapshot(root).evidence_heads}


def _spec_and_test(project: Path) -> None:
    (project / "packs").mkdir(exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    (project / "packs" / "u.spec.md").write_text("# u acceptance pack\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}')
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir()
    _must("init", "--project", str(project))
    root = str(project / ".vouch")
    _spec_and_test(project)
    _must("set-tier", "--root", root, "--tier", "standard")
    _must("set-anchor-mode", "--root", root, "--mode", "ci")
    return project, root


def _evidence(root: str, unit: str, kind: str, argv: list[str]) -> None:
    _must("record-evidence", "--root", root, "--unit", unit, "--kind", kind, "--", *argv)


def _frozen_with_evidence(project: Path, root: str, name: str, version: str) -> None:
    _must("specify", "--root", root, "--name", name, "--version", version)
    _must(
        "freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/{version}",
        "--project", str(project), "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py", "--pack-effects-file", "packs/u.effects.json",
    )
    _must("mark-ready", "--root", root, "--name", name)
    _must("start", "--root", root, "--name", name)
    _evidence(root, f"{name}@{version}", "red", FAIL)
    _evidence(root, f"{name}@{version}", "green", OK)


def _ci_claim(root: str, label: str, monkeypatch: pytest.MonkeyPatch, run_id: str) -> str:
    name, _, version = label.rpartition("@")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    try:
        return _must(
            "advance-anchor", "--root", root, "--unit-name", name, "--unit-version", version
        )
    finally:
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)


def _check(project: Path, root: str, *extra: str) -> tuple[int, str]:
    _must("generate", "--root", root)
    return _run("check", "--ci", "--root", root, "--project", str(project), *extra)


def _anchor_row(output: str) -> str:
    rows = [
        line
        for line in output.splitlines()
        if line.split()[1:2] == [mk2.CHECK_EVIDENCE_ANCHORS]
    ]
    assert len(rows) == 1, output
    return rows[0]


def _claimed_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """u@v1 published, its anchor claimed by CI run FIRST_RUN at its head; check --ci passes."""
    project, root = _project(tmp_path)
    _frozen_with_evidence(project, root, "u", "v1")
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")
    _ci_claim(root, "u@v1", monkeypatch, FIRST_RUN)
    head = _heads(root)["u@v1"]
    assert head.anchored == head.head and head.anchor_run_id == FIRST_RUN
    code, output = _check(project, root)
    assert code == 0, output
    return project, root


def _lagging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """The pilot's shape: late evidence after the claimed anchor leaves it behind its head."""
    project, root = _claimed_published(tmp_path, monkeypatch)
    _evidence(root, "u@v1", "green", OK)
    head = _heads(root)["u@v1"]
    assert head.anchored != head.head and head.anchor_run_id == FIRST_RUN
    return project, root


def _jobs(text: str) -> dict[str, str]:
    """Split a workflow into top-level job blocks (as in the generated-ci pack)."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n(?=  [a-z][\w-]*:\n)", body)
    jobs: dict[str, str] = {}
    for part in parts:
        match = re.match(r"\s*([\w-]+):", part)
        if match is not None:
            jobs[match.group(1)] = part
    return jobs


def _anchor_job(workflow: str) -> str:
    return next(body for body in _jobs(workflow).values() if "advance-anchor" in body)


def _listed_heads(workflow: str, project: Path) -> list[str]:
    """Run the anchor step's head-listing Python exactly as CI does, from the project root."""
    job = _anchor_job(workflow)
    snippets = re.findall(r"<<'PY'[^\n]*\n(.*?)\n\s*PY\n", job, re.DOTALL)
    snippet = next(text for text in snippets if "evidence_heads" in text)
    result = subprocess.run(
        [sys.executable, "-I", "-"],
        input=textwrap.dedent(snippet),
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def _claims_each_pair(workflow: str) -> bool:
    job = _anchor_job(workflow)
    return any(
        "advance-anchor" in line and "--unit-name" in line and "--unit-version" in line
        for line in job.splitlines()
    )


def _mk2_workflow() -> str:
    return (ROOT / ".github" / "workflows" / "ci.yml").read_text()


# ---- R1: a claimed anchor behind its head is not checked ----


def test_a_claimed_anchor_behind_its_head_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: late evidence after a CI-claimed anchor makes the row NOT-CHECKED, naming u@v1."""
    project, root = _lagging(tmp_path, monkeypatch)

    code, output = _check(project, root)

    assert code == 1, output
    row = _anchor_row(output)
    assert row.split()[0] == "NOT-CHECKED", row
    assert f"'u@v1' {LAG}" in row, row
    assert "composed: NOT-CHECKED" in output, output


def test_check_json_reports_the_lag_as_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: the structured report carries the same verdict and detail."""
    project, root = _lagging(tmp_path, monkeypatch)
    _must("generate", "--root", root)

    code, output = _run("check", "--ci", "--json", "--root", root, "--project", str(project))

    assert code == 1, output
    report = json.loads(output)
    rows = [row for row in report["mechanical"] if row["check_id"] == mk2.CHECK_EVIDENCE_ANCHORS]
    assert len(rows) == 1, rows
    assert rows[0]["verdict"] == "not-checked", rows
    assert f"'u@v1' {LAG}" in rows[0]["detail"], rows


# ---- R2: every row names the head's exact reference ----


def test_rows_name_each_head_by_its_exact_reference(tmp_path: Path) -> None:
    """R2: two heads of one name read as two references, and a GENESIS head names its version."""
    project, root = _project(tmp_path)
    _frozen_with_evidence(project, root, "u", "v1")
    _must("advance-anchor", "--root", root, "--unit-name", "u", "--unit-version", "v1")
    _must(
        "open-deviation", "--root", root, "--deviation-id", "move-on",
        "--deviation-type", "pack-defect", "--unit", "u@v1",
    )
    _must("supersede", "--root", root, "--name", "u")
    _frozen_with_evidence(project, root, "u", "v2")
    _must("advance-anchor", "--root", root, "--unit-name", "u", "--unit-version", "v2")
    _frozen_with_evidence(project, root, "w", "1")  # never advanced: still at GENESIS
    heads = _heads(root)
    assert set(heads) == {"u@v1", "u@v2", "w@1"}
    assert heads["w@1"].anchored == "GENESIS"

    _code, output = _check(project, root)

    row = _anchor_row(output)
    assert "'u@v1' anchor has no workflow run claim" in row, row
    assert "'u@v2' anchor has no workflow run claim" in row, row
    assert "'w@1' remains at GENESIS" in row, row
    assert "unit 'u' " not in row and "unit 'w' " not in row, row


# ---- R3: a lagging anchor is a pending anchor ----


def test_pending_anchor_ok_tolerates_a_lagging_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3 (# guard:): --pending-anchor-ok still exits 0 when the lag is the only exception."""
    project, root = _lagging(tmp_path, monkeypatch)

    code, output = _check(project, root, "--pending-anchor-ok")

    assert code == 0, output


# ---- R4: the generated workflow advances a lagging claimed head ----


def test_generated_anchor_step_lists_and_claims_a_lagging_claimed_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: init's workflow lists u@v1, the claim reaches the head, and check --ci passes."""
    project, root = _lagging(tmp_path, monkeypatch)
    workflow = generated_workflow()

    labels = _listed_heads(workflow, project)

    assert labels == ["u@v1"], labels
    assert _claims_each_pair(workflow), _anchor_job(workflow)
    for label in labels:
        _ci_claim(root, label, monkeypatch, SECOND_RUN)
    head = _heads(root)["u@v1"]
    assert head.anchored == head.head and head.anchor_run_id == SECOND_RUN
    code, output = _check(project, root)
    assert code == 0, output
    assert _listed_heads(workflow, project) == []


# ---- R5: mk2's own workflow does the same ----


def test_mk2_own_anchor_step_lists_a_lagging_claimed_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5: .github/workflows/ci.yml lists the lagging claimed head and passes name and version."""
    project, _root = _lagging(tmp_path, monkeypatch)
    workflow = _mk2_workflow()

    assert _listed_heads(workflow, project) == ["u@v1"]
    assert _claims_each_pair(workflow), _anchor_job(workflow)


# ---- R6: a current claimed head is not listed again ----


def test_a_current_claimed_head_is_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6 (# guard:): neither loop re-claims an anchor already at its head under a run claim."""
    project, _root = _claimed_published(tmp_path, monkeypatch)

    assert _listed_heads(generated_workflow(), project) == []
    assert _listed_heads(_mk2_workflow(), project) == []


def test_an_unclaimed_current_head_is_still_listed(tmp_path: Path) -> None:
    """R6 (# guard:): a locally advanced, unclaimed head is listed by both loops, as before."""
    project, root = _project(tmp_path)
    _frozen_with_evidence(project, root, "u", "v1")
    _must("advance-anchor", "--root", root, "--unit-name", "u", "--unit-version", "v1")
    head = _heads(root)["u@v1"]
    assert head.anchored == head.head and not head.anchor_run_id

    assert _listed_heads(generated_workflow(), project) == ["u@v1"]
    assert _listed_heads(_mk2_workflow(), project) == ["u@v1"]


# ---- R7: next actions propose the advance ----


def test_next_actions_propose_advancing_a_lagging_claimed_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7 (# guard:): `next` offers advance-anchor for u@v1 with its exact version."""
    _project_path, root = _lagging(tmp_path, monkeypatch)

    code, output = _run("next", "--json", "--root", root)

    assert code == 0, output
    argvs = [
        list(action["argv"])
        for action in json.loads(output)["actions"]
        if action["subject_kind"] == "evidence"
    ]
    assert len(argvs) == 1, argvs
    argv = argvs[0]
    assert argv[1] == "advance-anchor"
    assert argv[argv.index("--unit-name") + 1] == "u"
    assert argv[argv.index("--unit-version") + 1] == "v1"


# ---- R8: the documents state it ----


def _section(path: Path, heading: str) -> str:
    text = path.read_text()
    start = text.index(heading)
    level = heading.split(" ", 1)[0]
    rest = text[start + len(heading):]
    match = re.search(rf"\n{re.escape(level)} ", rest)
    body = rest if match is None else rest[: match.start()]
    return " ".join(body.split())


def test_docs_state_that_a_lagging_anchor_is_not_checked_until_ci_advances_it() -> None:
    """R8: quickstart section 6 and claim-boundary's CI anchoring section say so."""
    sections = (
        _section(ROOT / "docs" / "quickstart.md", "## 6. Assurance tier and anchoring"),
        _section(ROOT / "docs" / "claim-boundary.md", "## CI anchoring"),
    )
    for text in sections:
        assert "evidence recorded after a CI-claimed anchor" in text, text
        assert LAG in text, text
        assert "until the next CI run advances it" in text, text
