"""Frozen acceptance pack for self-hosted unit anchor-unit-ref@1 (D-26).

Found by the activity_tracker_portal pilot (D-24), finding 4: a published unit was rebound and
given a fresh GREEN, then superseded by its successor, so main's store held two evidence heads
of one name without a run claim. ``advance-anchor`` and ``AnchorAdvanced`` named only the unit,
and both the decision and the fold resolved the CURRENT version's head, so CI claimed version 2
twice and version 1's head could never be claimed; ``check --ci`` stayed NOT-CHECKED and no
unit could reach acceptance. Every test here failed against the pre-change tree, except tests
marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    AdvanceAnchor,
    AnchorAdvanced,
    EvidenceHead,
    Snapshot,
    UnitRef,
    apply,
    decide,
)
from vouch_mk2.formal import HISTORY_V1, passed, run_formal
from vouch_mk2.init import generated_workflow
from vouch_mk2.ir import mk2
from vouch_mk2.projections.next_actions import next_actions
from vouch_mk2.store import (
    ArtifactKind,
    DecodeError,
    Store,
    decode_event_record,
    digest_of,
    encode_document,
)

ROOT = Path(__file__).resolve().parents[1]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]
OK = [sys.executable, "-c", "raise SystemExit(0)"]
RUN_ID = "424242"


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


def _records(root: str) -> list[dict[str, object]]:
    """Every event record of the chain, newest first, as the raw decoded JSON object."""
    records: list[dict[str, object]] = []
    with Store.open(root, read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest and digest != "GENESIS":
            record = json.loads(store.get_object(ArtifactKind.EVENT, digest))
            records.append(record)
            digest = str(record["prev"])
    return records


def _anchor_payloads(root: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for record in _records(root):
        if record["event"] == "AnchorAdvanced":
            payload = record["payload"]
            assert isinstance(payload, dict)
            payloads.append(payload)
    return payloads


def _spec_and_test(project: Path, body: str) -> None:
    (project / "packs").mkdir(exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    (project / "packs" / "u.spec.md").write_text("# u acceptance pack\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}')  # D-31: init requires effects
    (project / "tests" / "t.py").write_text(f"def test_x():\n    {body}\n")


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir()
    _must("init", "--project", str(project))
    root = str(project / ".vouch")
    _spec_and_test(project, "assert True")
    _must("set-tier", "--root", root, "--tier", "standard")
    _must("set-anchor-mode", "--root", root, "--mode", "ci")
    return project, root


def _evidence(root: str, unit: str, kind: str, argv: list[str]) -> None:
    _must("record-evidence", "--root", root, "--unit", unit, "--kind", kind, "--", *argv)


def _frozen_with_evidence(project: Path, root: str, version: str, pack: str) -> None:
    _must("specify", "--root", root, "--name", "u", "--version", version)
    _must(
        "freeze", "--root", root, "--name", "u", "--pack-version", pack,
        "--project", str(project), "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py", "--pack-effects-file", "packs/u.effects.json",
    )
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")
    _evidence(root, f"u@{version}", "red", FAIL)
    _evidence(root, f"u@{version}", "green", OK)


def _published(project: Path, root: str, version: str, pack: str) -> None:
    _frozen_with_evidence(project, root, version, pack)
    # The owner habit of method section 1.5: a local, unclaimed promotion.
    _must("advance-anchor", "--root", root, "--unit-name", "u")
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")


def _pilot(tmp_path: Path) -> tuple[Path, str]:
    """The pilot's main: a rebound, superseded u@v1 and its published successor u@v2.

    Both heads lack a run claim. u@v1's anchor lags its head (the rebind GREEN came after the
    local promotion); u@v2's anchor is current but unclaimed.
    """
    project, root = _project(tmp_path)
    _published(project, root, "v1", "u-pack/1")
    _spec_and_test(project, "assert 1 + 1 == 2  # adapted to a changed shared interface")
    _must(
        "rebind-pack", "--root", root, "--name", "u", "--project", str(project),
        "--pack-file", "packs/u.spec.md", "--pack-test-file", "tests/t.py",
        "--pack-effects-file", "packs/u.effects.json",
        "--reason", "adapt to a changed shared interface",
    )
    _evidence(root, "u@v1", "green", OK)
    _must("supersede", "--root", root, "--name", "u")
    _published(project, root, "v2", "u-pack/2")
    _must("generate", "--root", root)
    heads = _heads(root)
    assert set(heads) == {"u@v1", "u@v2"}
    assert heads["u@v1"].anchored != heads["u@v1"].head
    assert heads["u@v2"].anchored == heads["u@v2"].head
    assert all(not head.anchor_run_id for head in heads.values())
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


def _unclaimed_labels(workflow: str, project: Path) -> list[str]:
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
    """The loop passes each listed head's name AND version to advance-anchor."""
    job = _anchor_job(workflow)
    return any(
        "advance-anchor" in line and "--unit-name" in line and "--unit-version" in line
        for line in job.splitlines()
    )


def _ci_claim(root: str, label: str, monkeypatch: pytest.MonkeyPatch, run_id: str = RUN_ID) -> str:
    name, _, version = label.rpartition("@")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    try:
        return _must(
            "advance-anchor", "--root", root, "--unit-name", name, "--unit-version", version
        )
    finally:
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)


# ---- R1: the pilot's main can be anchored again ----


def test_ci_claims_every_unclaimed_head_of_a_name_including_the_superseded_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: the generated anchor step lists (name, version) per unclaimed head and claims each."""
    project, root = _pilot(tmp_path)
    code, output = _run("check", "--ci", "--root", root, "--project", str(project))
    assert code == 1 and "no workflow run claim" in output, output  # the pilot's blocker

    workflow = generated_workflow()
    labels = _unclaimed_labels(workflow, project)
    assert labels == ["u@v1", "u@v2"], labels
    assert _claims_each_pair(workflow), _anchor_job(workflow)
    for label in labels:
        _ci_claim(root, label, monkeypatch)

    heads = _heads(root)
    assert heads["u@v1"].anchored == heads["u@v1"].head
    assert heads["u@v2"].anchored == heads["u@v2"].head
    assert {head.anchor_run_id for head in heads.values()} == {RUN_ID}
    _must("generate", "--root", root)
    code, output = _run("check", "--ci", "--root", root, "--project", str(project))
    assert code == 0, output
    # A second run finds nothing left to claim, as the CI step relies on.
    assert _unclaimed_labels(workflow, project) == []


# ---- R2: an exact reference claims only that version's head ----


def test_an_exact_reference_claims_only_that_versions_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: --unit-version v1 promotes and claims u@v1; u@v2 is untouched; the event names u@v1."""
    _project_path, root = _pilot(tmp_path)
    before = _heads(root)

    output = _ci_claim(root, "u@v1", monkeypatch, run_id="777")

    assert "u@v1" in output, output
    after = _heads(root)
    assert after["u@v1"].anchored == after["u@v1"].head
    assert after["u@v1"].anchor_run_id == "777"
    assert after["u@v2"] == before["u@v2"]
    assert _anchor_payloads(root)[0] == {"unit_name": "u", "unit_version": "v1"}


# ---- R3: a bare name keeps its meaning and the record is exact ----


def test_a_bare_name_still_means_the_current_version_and_is_recorded_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: without --unit-version the current version is claimed, and the event carries it."""
    _project_path, root = _pilot(tmp_path)
    before = _heads(root)
    snapshot = _snapshot(root)

    decision = decide(snapshot, AdvanceAnchor(unit_name="u", run_id="888"))
    assert isinstance(decision, Accepted)
    assert decision.events == (AnchorAdvanced(unit_name="u", unit_version="v2"),)

    monkeypatch.setenv("GITHUB_RUN_ID", "888")
    _must("advance-anchor", "--root", root, "--unit-name", "u")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    after = _heads(root)
    assert after["u@v2"].anchor_run_id == "888"
    assert after["u@v1"] == before["u@v1"]
    assert _anchor_payloads(root)[0] == {"unit_name": "u", "unit_version": "v2"}


def test_an_unknown_reference_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """R3: a version with no evidence chain is refused as evidence.no-chain, naming the ref."""
    _project_path, root = _pilot(tmp_path)
    state = Path(root) / "state.json"
    before = state.read_bytes()

    code, output = _run(
        "advance-anchor", "--root", root, "--unit-name", "u", "--unit-version", "v9"
    )

    assert code == 2, output
    assert "evidence.no-chain" in output and "u@v9" in output, output
    assert state.read_bytes() == before


# ---- R4: stores keep replaying; legacy records keep their recorded meaning ----


def _append_legacy_anchor_record(root: str, run_id: str) -> bytes:
    """Commit an AnchorAdvanced record in the pre-unit shape: payload holds only unit_name."""
    with Store.open(root) as store:
        with store.write_locked():
            snapshot = store.read_snapshot()
            document = {
                "actor": "legacy",
                "event": "AnchorAdvanced",
                "pack_digest": "",
                "payload": {"unit_name": "u"},
                "prev": snapshot.event_head,
                "recorded_at": "2026-08-20T10:34:26+00:00",
                "revision": snapshot.revision + 1,
                "run_id": run_id,
            }
            data = encode_document(document)
            digest = digest_of(ArtifactKind.EVENT, data)
            folded = apply(
                snapshot,
                (AnchorAdvanced(unit_name="u"),),
                event_digests=(digest,),
                event_run_ids=(run_id,),
            )
            store.commit(
                folded,
                objects=((ArtifactKind.EVENT, data),),
                expected_revision=snapshot.revision,
            )
    return data


def _check_rows(output: str, check_id: str) -> list[str]:
    return [line for line in output.splitlines() if check_id in line]


def test_a_legacy_anchor_record_replays_as_the_version_current_when_it_was_recorded(
    tmp_path: Path,
) -> None:
    """R4: a record without unit_version decodes, and replay resolves it by name at that point."""
    project, root = _project(tmp_path)
    _frozen_with_evidence(project, root, "v1", "u-pack/1")
    data = _append_legacy_anchor_record(root, run_id="7")

    decoded = decode_event_record(data).folded_event
    assert decoded == AnchorAdvanced(unit_name="u", unit_version="")

    # History moves on: v1 is superseded by v2, which has its own unclaimed chain.
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")
    _must("supersede", "--root", root, "--name", "u")
    _spec_and_test(project, "assert 2 + 2 == 4")
    _frozen_with_evidence(project, root, "v2", "u-pack/2")
    _must("generate", "--root", root)

    code, output = _run("check", "--root", root)
    rows = _check_rows(output, "check.event-log")
    assert rows and rows[0].startswith("PASS"), output
    heads = _heads(root)
    assert heads["u@v1"].anchored == heads["u@v1"].head
    assert heads["u@v1"].anchor_run_id == "7"
    assert heads["u@v2"].anchored == "GENESIS"
    assert heads["u@v2"].anchor_run_id == ""
    assert code == 1  # only u@v2's pending anchor keeps the report from composing


def test_mk2_own_store_with_legacy_anchor_records_still_replays() -> None:
    # guard: mk2's own log holds AnchorAdvanced records written before this unit.
    code, output = _run("check", "--root", str(ROOT / ".vouch"))
    rows = _check_rows(output, "check.event-log")
    assert rows and rows[0].startswith("PASS"), output
    assert code in (0, 1), output  # a pending CI anchor may keep the report from composing


# ---- R5: the decoder is exact about the new field ----


def _record_bytes(payload: dict[str, object]) -> bytes:
    return encode_document(
        {
            "actor": "x",
            "event": "AnchorAdvanced",
            "pack_digest": "",
            "payload": payload,
            "prev": "GENESIS",
            "recorded_at": "2026-10-30T00:00:00+00:00",
            "revision": 1,
            "run_id": "",
        }
    )


def test_an_exact_version_decodes_and_an_explicit_empty_version_is_refused() -> None:
    """R5: {unit_name, unit_version} decodes to the exact event; unit_version "" is refused."""
    exact = decode_event_record(_record_bytes({"unit_name": "u", "unit_version": "v1"}))
    assert exact.folded_event == AnchorAdvanced(unit_name="u", unit_version="v1")
    with pytest.raises(DecodeError):
        decode_event_record(_record_bytes({"unit_name": "u", "unit_version": ""}))


# ---- R6: semantic merge replays the exact reference ----


def test_a_directory_merge_replays_an_exact_reference_claim(tmp_path: Path) -> None:
    """R6: theirs claimed u@v1 by reference; after merge --other, ours' u@v1 is anchored."""
    project, root = _pilot(tmp_path)
    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    theirs_root = str(theirs / ".vouch")
    _must("advance-anchor", "--root", theirs_root, "--unit-name", "u", "--unit-version", "v1")
    _evidence(root, "u@v2", "refactor", OK)  # ours diverges on the current version
    before = _heads(root)

    code, output = _run("merge", "--root", root, "--other", theirs_root)

    assert code == 0, output
    after = _heads(root)
    assert after["u@v1"].anchored == after["u@v1"].head == before["u@v1"].head
    assert after["u@v2"].head == before["u@v2"].head
    assert after["u@v2"].anchored == before["u@v2"].anchored
    assert {"unit_name": "u", "unit_version": "v1"} in _anchor_payloads(root)


# ---- R7: mk2's own workflow claims each head too ----


def test_mk2_own_anchor_step_lists_and_claims_each_unclaimed_head(tmp_path: Path) -> None:
    """R7: .github/workflows/ci.yml emits NAME@VERSION per unclaimed head and passes both."""
    project, _root = _pilot(tmp_path)
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "anchor_run_id" in _anchor_job(workflow)  # still only unclaimed heads
    assert _unclaimed_labels(workflow, project) == ["u@v1", "u@v2"]
    assert _claims_each_pair(workflow), _anchor_job(workflow)


# ---- R8: next actions name the exact reference ----


def test_next_actions_propose_an_exact_reference_advance_for_a_lagging_superseded_head(
    tmp_path: Path,
) -> None:
    """R8: the lagging u@v1 head gets an advance-anchor action whose argv names its version."""
    _project_path, root = _pilot(tmp_path)

    evidence_actions = [
        action for action in next_actions(_snapshot(root)) if action.subject_kind == "evidence"
    ]
    assert [(action.subject_id, action.command) for action in evidence_actions] == [
        ("u@v1", mk2.CMD_ADVANCE_ANCHOR)
    ]

    code, output = _run("next", "--json", "--root", root)
    assert code == 0, output
    actions = json.loads(output)["actions"]
    argv = next(
        list(action["argv"]) for action in actions if action["subject_kind"] == "evidence"
    )
    assert argv[1] == "advance-anchor"
    assert argv[argv.index("--unit-name") + 1] == "u"
    assert argv[argv.index("--unit-version") + 1] == "v1"


# ---- R9: the formal relation witnesses the superseded claim without shrinking ----


def test_history_v1_witnesses_claiming_a_superseded_versions_head_without_shrinking() -> None:
    """R9: history-v1 seeds an unanchored u@v1 head and offers the exact-reference advance."""
    seeded = HISTORY_V1.initial.evidence_head_ref(UnitRef(name="u", version="v1"))
    assert seeded is not None and seeded.anchored != seeded.head
    assert HISTORY_V1.initial.unit_ref(UnitRef(name="u", version="v1")) is not None
    assert AdvanceAnchor(unit_name="u", unit_version="v1") in HISTORY_V1.commands

    run = run_formal()
    assert passed(run)
    history = run.universe("history-v1")
    assert all(verdict.held for verdict in history.invariants)
    assert any(
        (head := snapshot.evidence_head_ref(UnitRef(name="u", version="v1"))) is not None
        and head.anchored == head.head
        for _fingerprint, snapshot in history.relation.representatives
    )
    # Coverage gauge: the seeded head can only double the relation; nothing else may move.
    assert 31_416 < history.state_count <= 62_832
    assert 94_280 < history.transition_count
    core = run.universe("core-v1")
    assert (core.state_count, core.transition_count) == (264_462, 1_381_350)
    tier = run.universe("tier-v1")
    assert (tier.state_count, tier.transition_count) == (246, 1_640)


# ---- R10: the operator docs say so ----


def test_docs_describe_claiming_a_superseded_versions_head() -> None:
    """R10: the quickstart and the generated command reference show --unit-version."""
    quickstart = (ROOT / "docs" / "quickstart.md").read_text()
    assert "--unit-version" in quickstart
    commands = (ROOT / "docs" / "commands.md").read_text()
    section = commands.split("## advance-anchor", 1)[1].split("\n## ", 1)[0]
    assert "--unit-version" in section
