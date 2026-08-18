from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    AdvanceAnchor,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    EvidenceOutcome,
    EvidenceRecorded,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.projections import system_map
from vouch_mk2.store import (
    DecodeError,
    decode_system_map,
    encode_snapshot,
    encode_system_map,
)
from vouch_mk2.store.upgrade import upgrade_document


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _unit_with_local_anchor(project: Path, name: str) -> None:
    root = str(project / ".vouch")
    test_file = project / "tests" / f"test_{name}.py"
    test_file.parent.mkdir(exist_ok=True)
    test_file.write_text("def test_ok():\n    assert True\n")
    assert _run("specify", "--root", root, "--name", name, "--version", "v1")[0] == 0
    assert _run(
        "freeze",
        "--root",
        root,
        "--name",
        name,
        "--pack-version",
        f"{name}-pack/1",
        "--project",
        str(project),
        "--pack-test-file",
        str(test_file.relative_to(project)),
    )[0] == 0
    assert _run("record-evidence", "--root", root, "--unit", f"{name}@v1", "--kind", "red", "--", sys.executable, "-c", "raise SystemExit(1)")[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", name)[0] == 0


def test_ci_reobserves_a_locally_current_anchor_and_replay_preserves_claim() -> None:
    digest = "a" * 64
    snapshot = Snapshot(
        units=(Unit("u", "v1", UnitStatus.IN_PROGRESS),),
        evidence_heads=(EvidenceHead("u", digest, 1, digest, "v1"),),
    )
    decision = decide(snapshot, AdvanceAnchor("u", run_id="424242"))
    assert isinstance(decision, Accepted)
    replayed = apply(snapshot, decision.events, event_run_ids=("424242",))
    head = replayed.evidence_head("u")
    assert head is not None and head.anchor_run_id == "424242"


def test_anchor_run_claim_survives_later_evidence_until_the_next_advance() -> None:
    old_digest = "a" * 64
    new_digest = "b" * 64
    snapshot = Snapshot(
        units=(Unit("u", "v1", UnitStatus.IN_PROGRESS),),
        evidence_heads=(
            EvidenceHead(
                "u",
                old_digest,
                1,
                old_digest,
                "v1",
                anchor_run_id="41",
            ),
        ),
    )
    evidence = EvidenceEvent(
        unit=UnitRef("u", "v1"),
        sequence=2,
        parent=old_digest,
        kind=EvidenceKind.REFACTOR,
        command_line="pytest",
        outcome=EvidenceOutcome.TESTS_PASSED,
        recorded_at="2026-08-18T00:00:00+00:00",
    )
    changed = apply(snapshot, (EvidenceRecorded(evidence, new_digest),))
    head = changed.evidence_head("u")
    assert head is not None
    assert head.anchored == old_digest and head.anchor_run_id == "41"


def test_ci_anchor_check_names_every_locally_advanced_unit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert _run("init", "--project", str(project))[0] == 0
    root = str(project / ".vouch")
    assert _run("set-anchor-mode", "--root", root, "--mode", "ci")[0] == 0
    _unit_with_local_anchor(project, "alpha")
    _unit_with_local_anchor(project, "beta")
    code, output = _run("check", "--root", root)
    assert code == 1
    row = next(line for line in output.splitlines() if "evidence-anchors" in line)
    assert row.startswith("NOT-CHECKED")
    assert "alpha" in row and "beta" in row


def test_format4_upgrade_adds_no_invented_anchor_run_claim() -> None:
    digest = "a" * 64
    snapshot = Snapshot(
        units=(Unit("u", "v1", UnitStatus.IN_PROGRESS),),
        evidence_heads=(EvidenceHead("u", digest, 1, digest, "v1"),),
    )
    document = json.loads(encode_snapshot(snapshot))
    document["state_format"] = 4
    for head in document["evidence_heads"]:
        del head["anchor_run_id"]
    upgraded, steps = upgrade_document(document)
    assert steps == [(4, 5)]
    heads = upgraded["evidence_heads"]
    assert isinstance(heads, list) and isinstance(heads[0], dict)
    assert heads[0]["anchor_run_id"] == ""


def test_generated_workflow_uses_a_real_repository_identity_guard(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert _run("init", "--project", str(project))[0] == 0
    workflow = (project / ".github" / "workflows" / "vouch.yml").read_text()
    guard = "github.event.pull_request.head.repo.full_name == github.event.pull_request.base.repo.full_name"
    assert f"if: github.event_name != 'pull_request' || {guard}" in workflow
    assert workflow.index("python vouch.lock formal") < workflow.index("advance-anchor")
    assert "git add -- .vouch/" in workflow
    assert "git push origin" in workflow and "[skip ci]" in workflow


def test_system_map_refuses_an_anchor_claim_that_disagrees_with_its_head() -> None:
    digest = "a" * 64
    snapshot = Snapshot(
        units=(Unit("u", "v1", UnitStatus.IN_PROGRESS),),
        evidence_heads=(
            EvidenceHead(
                "u",
                digest,
                1,
                digest,
                "v1",
                anchor_run_id="42",
            ),
        ),
    )
    document = json.loads(encode_system_map(system_map(snapshot), "b" * 64))
    document["map"]["status_facts"]["anchor_run_claims"] = [["u@v1", "99"]]
    tampered = (
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    with pytest.raises(DecodeError, match="anchor_run_claims"):
        decode_system_map(tampered)
