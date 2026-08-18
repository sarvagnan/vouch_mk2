"""Acceptance pack for self-hosted unit event-log@1 (D-16 + its dated correction).

Every test here FAILED against the pre-change tree (RED recorded before implementation) and must PASS
at completion -- except tests marked `# guard:`. The implementer may not edit this file; a finding that
this pack is wrong is a DEVIATION adjudicated by the owner.
"""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _store(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    code, out = _run("init", "--project", str(project))
    assert code == 0, out
    return project, str(project / ".vouch")


def _specified(tmp_path: Path) -> tuple[Path, str]:
    project, root = _store(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    return project, root


# ---- R1 event records ----


def test_event_records_are_content_addressed_with_the_declared_fields(tmp_path: Path) -> None:
    from vouch_mk2.store import ArtifactKind, Store

    _project, root = _specified(tmp_path)
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        head = snapshot.event_head
        assert head, "a specified unit must have produced an event record"
        raw = store.get_object(ArtifactKind.EVENT, head)
    document = json.loads(raw)
    for field in ("event", "payload", "recorded_at", "prev", "revision", "actor", "run_id"):
        assert field in document, f"event record is missing {field!r}: {sorted(document)}"
    assert document["prev"] == "GENESIS"
    assert document["revision"] == 1


# ---- R2 state format 4 ----


def test_state_format_4_upgrader_records_the_pre_log_gap(tmp_path: Path) -> None:
    from vouch_mk2.store import ArtifactKind, Store
    from vouch_mk2.store.codec import CURRENT_STATE_FORMAT

    assert CURRENT_STATE_FORMAT == 4
    _project, root = _specified(tmp_path)
    # Simulate a store written before the log existed: drop the head, keep the revision.
    state = Path(root) / "state.json"
    document = json.loads(state.read_text())
    revision = document["revision"]
    document["state_format"] = 3
    document.pop("event_head", None)
    state.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    code, out = _run("upgrade", "--root", root)
    assert code == 0, out
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        assert snapshot.event_head, "the upgrader must start the log"
        raw = store.get_object(ArtifactKind.EVENT, snapshot.event_head)
    record = json.loads(raw)
    blob = json.dumps(record)
    assert "pre-log" in blob and str(revision) in blob, (
        f"the upgrader must record the pre-log gap naming the revision range; got {blob}"
    )


# ---- R3 atomicity ----


def test_no_committed_snapshot_without_its_event_records(tmp_path: Path) -> None:
    from vouch_mk2.store import ArtifactKind, Store

    _project, root = _specified(tmp_path)
    for _ in range(3):
        _run("specify", "--root", root, "--name", f"n{_}", "--version", "v1")
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        digest = snapshot.event_head
        seen = 0
        while digest and digest != "GENESIS":
            raw = store.get_object(ArtifactKind.EVENT, str(digest))  # must resolve
            digest = str(json.loads(raw)["prev"])
            seen += 1
    assert seen >= 4, f"expected one record per folded event, walked {seen}"


# ---- R4 check.event-log ----


def test_check_event_log_replays_the_chain_and_reproduces_the_snapshot(tmp_path: Path) -> None:
    _project, root = _specified(tmp_path)
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--root", root)
    assert code in (0, 1), out
    rows = [line for line in out.splitlines() if "check.event-log" in line]
    assert rows, f"check.event-log is not registered: {out}"
    assert rows[0].startswith("PASS"), rows[0]


def test_check_event_log_fails_on_a_broken_link(tmp_path: Path) -> None:
    _project, root = _specified(tmp_path)
    state = Path(root) / "state.json"
    document = json.loads(state.read_text())
    document["event_head"] = "b" * 64
    state.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    code, out = _run("check", "--root", root)
    rows = [line for line in out.splitlines() if "check.event-log" in line]
    assert rows and rows[0].startswith("FAIL"), out


def test_check_event_log_fails_when_replay_diverges_from_the_committed_snapshot(
    tmp_path: Path,
) -> None:
    _project, root = _specified(tmp_path)
    state = Path(root) / "state.json"
    document = json.loads(state.read_text())
    # A unit the log never saw: replay cannot reproduce this snapshot.
    document["units"].append(
        {
            "name": "ghost",
            "version": "v1",
            "status": "specified",
            "depends_on": [],
            "acceptance_pack_version": "",
            "acceptance_pack_digest": "",
            "acceptance_test_set_digest": "",
            "pack_history": [],
            "published_once": False,
        }
    )
    state.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    code, out = _run("check", "--root", root)
    rows = [line for line in out.splitlines() if "check.event-log" in line]
    assert rows and rows[0].startswith("FAIL"), out
    assert "ghost" in out or "replay" in out.lower(), rows[0]


# ---- R5 actor and run id are claims ----


def test_actor_defaults_to_the_os_user_and_is_overridable(tmp_path: Path) -> None:
    from vouch_mk2.store import ArtifactKind, Store

    _project, root = _store(tmp_path)
    assert _run("specify", "--root", root, "--name", "a", "--version", "v1")[0] == 0
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        default_actor = json.loads(
            store.get_object(ArtifactKind.EVENT, snapshot.event_head)
        )["actor"]
    assert default_actor, "actor must default to something recorded"
    code, out = _run(
        "specify", "--root", root, "--name", "b", "--version", "v1", "--actor", "release-bot"
    )
    assert code == 0, out
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        actor = json.loads(store.get_object(ArtifactKind.EVENT, snapshot.event_head))["actor"]
    assert actor == "release-bot"


def test_run_id_is_captured_from_the_ci_environment_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vouch_mk2.store import ArtifactKind, Store

    _project, root = _store(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "1234567890")
    assert _run("specify", "--root", root, "--name", "a", "--version", "v1")[0] == 0
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        record = json.loads(store.get_object(ArtifactKind.EVENT, snapshot.event_head))
    assert record["run_id"] == "1234567890"


def test_claim_boundary_states_actor_and_run_id_are_claims() -> None:
    text = (ROOT / "docs" / "claim-boundary.md").read_text().lower()
    assert "actor" in text and "claim" in text
    assert "run id" in text or "run_id" in text
    assert "never" in text and "identity" in text


# ---- R6 the log is authoritative ----


def test_docs_state_the_log_is_authoritative_and_the_snapshot_derived() -> None:
    text = (
        (ROOT / "docs" / "claim-boundary.md").read_text()
        + (ROOT / "docs" / "quickstart.md").read_text()
    ).lower()
    assert "event log" in text
    assert "derived" in text or "cache" in text
    assert "replay" in text


# ---- R7 measured cost ----


def test_performance_doc_records_measured_event_log_cost() -> None:
    text = (ROOT / "docs" / "performance.md").read_text()
    lowered = text.lower()
    assert "event log" in lowered or "event-log" in lowered, (
        "performance.md must record the MEASURED event-log cost, not merely mention events"
    )
    # the measurement must be a real number next to the event-log statement
    window = [
        line for line in text.splitlines()
        if ("event log" in line.lower() or "event-log" in line.lower())
        and any(ch.isdigit() for ch in line)
    ]
    assert window, "no measured number recorded alongside the event-log cost"
