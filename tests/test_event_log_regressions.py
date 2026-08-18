"""Regression coverage beyond the frozen event-log@1 acceptance pack."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.cli import _LIFECYCLE_COMMANDS, _parser, main
from vouch_mk2.bundle import bind_bundle, bind_pack, build_inventory, build_pack_inventory
from vouch_mk2.core import (
    Accepted,
    DeviationType,
    OpenDeviation,
    Snapshot,
    SpecifyUnit,
    UnitRef,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    DecodeError,
    Store,
    StoreError,
    decode_event_record,
    digest_of,
)
from vouch_mk2.store.event_log import build_event_records, ci_run_id, commit_events
from vouch_mk2.verify import run_artifact_checks
from vouch_mk2.projections import agent_context, system_map


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def test_event_digest_domain_never_aliases_other_artifact_kinds() -> None:
    payload = b"identical canonical bytes\n"
    event_digest = digest_of(ArtifactKind.EVENT, payload)
    assert all(
        digest_of(kind, payload) != event_digest
        for kind in ArtifactKind
        if kind is not ArtifactKind.EVENT
    )


def test_multi_event_command_records_consecutive_revisions_and_replays(
    tmp_path: Path,
) -> None:
    root = str(tmp_path / "store")
    with Store.init(root) as store:
        with store.write_locked():
            empty = store.read_snapshot()
            specified = decide(empty, SpecifyUnit("u", "v1"))
            assert isinstance(specified, Accepted)
            snapshot = commit_events(
                store, empty, specified.events, actor="tester", run_id="run-1"
            )
            opened = decide(
                snapshot,
                OpenDeviation("d", DeviationType.PLAN_GAP, UnitRef("u", "v1")),
            )
            assert isinstance(opened, Accepted) and len(opened.events) == 2
            snapshot = commit_events(
                store, snapshot, opened.events, actor="tester", run_id="run-1"
            )
        revisions: list[int] = []
        digest = snapshot.event_head
        while digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            revisions.append(record.revision)
            digest = record.prev
        assert revisions == [3, 2, 1]
        result = next(
            row
            for row in run_artifact_checks(store, snapshot)
            if row.check_id == mk2.CHECK_EVENT_LOG
        )
    assert result.verdict is Verdict.PASS


def test_event_record_decoder_refuses_unknown_envelope_fields(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert _run("init", "--project", str(project))[0] == 0
    root = str(project / ".vouch")
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        document = json.loads(store.get_object(ArtifactKind.EVENT, snapshot.event_head))
    document["verified_actor"] = True
    forged = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(DecodeError, match="unknown field"):
        decode_event_record(forged)


@pytest.mark.parametrize(
    ("variable", "value"),
    (
        ("GITHUB_RUN_ID", "gh-1"),
        ("CI_PIPELINE_ID", "gitlab-2"),
        ("CIRCLE_WORKFLOW_ID", "circle-3"),
        ("BUILDKITE_BUILD_ID", "buildkite-4"),
        ("BUILD_BUILDID", "azure-5"),
    ),
)
def test_ci_provider_run_id_claims_are_captured(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    for name in (
        "GITHUB_RUN_ID",
        "CI_PIPELINE_ID",
        "CIRCLE_WORKFLOW_ID",
        "BUILDKITE_BUILD_ID",
        "BUILD_BUILDID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, value)
    assert ci_run_id() == value


def test_event_commit_crash_before_state_swap_never_publishes_a_dangling_head(
    tmp_path: Path,
) -> None:
    root = str(tmp_path / "store")
    Store.init(root).close()
    decision = decide(Snapshot(), SpecifyUnit("u", "v1"))
    assert isinstance(decision, Accepted)

    def fail_after_object(step: str) -> None:
        if step == "objects-dir-fsynced":
            raise RuntimeError("synthetic crash")

    with Store.open(root, on_step=fail_after_object) as store:
        with pytest.raises(RuntimeError, match="synthetic crash"):
            commit_events(
                store,
                store.read_snapshot(),
                decision.events,
                actor="tester",
                run_id="",
            )
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot() == Snapshot()


def test_store_commit_refuses_an_unresolved_event_head(tmp_path: Path) -> None:
    root = str(tmp_path / "store")
    with Store.init(root) as store, pytest.raises(StoreError, match="event_head"):
        store.commit(Snapshot(revision=1, event_head="a" * 64))


def test_event_head_is_present_in_pure_map_and_context_projections() -> None:
    snapshot = Snapshot(event_head="a" * 64)
    assert system_map(snapshot).event_head == "a" * 64
    assert "Event log head `" + "a" * 64 + "`." in agent_context(snapshot)


def test_every_reflected_lifecycle_verb_exposes_the_actor_claim() -> None:
    parser = _parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    for verb in _LIFECYCLE_COMMANDS:
        assert "--actor" in subparsers.choices[verb]._option_string_actions, verb


def test_empty_actor_is_recorded_as_the_callers_literal_claim(tmp_path: Path) -> None:
    root = str(tmp_path / "store")
    Store.init(root).close()
    assert _run(
        "specify",
        "--root",
        root,
        "--name",
        "u",
        "--version",
        "v1",
        "--actor",
        "",
    )[0] == 0
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        record = decode_event_record(
            store.get_object(ArtifactKind.EVENT, snapshot.event_head)
        )
    assert record.actor == ""


def test_upgrade_refuses_a_format_downgrade_that_retains_event_head(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert _run("init", "--project", str(project))[0] == 0
    root = project / ".vouch"
    assert _run("specify", "--root", str(root), "--name", "u", "--version", "v1")[0] == 0
    state = root / "state.json"
    document = json.loads(state.read_text())
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
            "rebound_history": [],
            "published_once": False,
        }
    )
    document["state_format"] = 3
    state.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    code, output = _run("upgrade", "--root", str(root))

    assert code == 2
    assert "event_head" in output
    assert "git history" in output
    assert json.loads(state.read_text())["state_format"] == 3


def test_upgrade_refuses_a_format_downgrade_when_event_objects_remain(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert _run("init", "--project", str(project))[0] == 0
    root = project / ".vouch"
    assert _run("specify", "--root", str(root), "--name", "u", "--version", "v1")[0] == 0
    state = root / "state.json"
    document = json.loads(state.read_text())
    prior_head = str(document.pop("event_head"))
    document["state_format"] = 3
    state.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    code, output = _run("upgrade", "--root", str(root))

    assert code == 2
    assert "event object" in output
    assert prior_head in output
    assert "git history" in output
    assert json.loads(state.read_text())["state_format"] == 3


def test_store_commit_refuses_revision_advance_without_supplied_event_records(
    tmp_path: Path,
) -> None:
    root = str(tmp_path / "store")
    with Store.init(root) as store:
        decision = decide(Snapshot(), SpecifyUnit("u", "v1"))
        assert isinstance(decision, Accepted)
        current = commit_events(
            store,
            Snapshot(),
            decision.events,
            actor="tester",
            run_id="",
        )
        with pytest.raises(StoreError, match=r"revision 2.*event record"):
            store.commit(
                replace(current, revision=2),
                expected_revision=current.revision,
            )
        assert store.read_snapshot() == current


def test_store_commit_allows_unchanged_revision_reencode_without_event_records(
    tmp_path: Path,
) -> None:
    root = str(tmp_path / "store")
    with Store.init(root) as store:
        store.commit(Snapshot(), expected_revision=0)
        assert store.read_snapshot() == Snapshot()


def test_interrupted_event_commit_is_readable_cleaned_and_retryable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        first = decide(Snapshot(), SpecifyUnit("first", "v1"))
        assert isinstance(first, Accepted)
        current = commit_events(
            store,
            Snapshot(),
            first.events,
            actor="tester",
            run_id="",
            recorded_at="2026-08-18T00:00:00+00:00",
        )

    second = decide(current, SpecifyUnit("second", "v1"))
    assert isinstance(second, Accepted)
    _candidate, objects, _records = build_event_records(
        current,
        second.events,
        actor="tester",
        run_id="",
        recorded_at="2026-08-18T00:00:01+00:00",
    )
    orphan_digest = digest_of(ArtifactKind.EVENT, objects[0][1])

    def fail_before_state(step: str) -> None:
        if step == "objects-dir-fsynced":
            raise RuntimeError("synthetic crash")

    with Store.open(str(root), on_step=fail_before_state) as store:
        with pytest.raises(RuntimeError, match="synthetic crash"):
            commit_events(
                store,
                current,
                second.events,
                actor="tester",
                run_id="",
                recorded_at="2026-08-18T00:00:01+00:00",
            )

    orphan_path = root / "objects" / "sha256" / orphan_digest
    assert orphan_path.exists()
    with Store.open(str(root), read_only=True) as store:
        assert store.read_snapshot() == current
        result = next(
            row
            for row in run_artifact_checks(store, current)
            if row.check_id == mk2.CHECK_EVENT_LOG
        )
        assert result.verdict is Verdict.PASS

    with Store.open(str(root)) as store:
        assert not orphan_path.exists()
        committed = commit_events(
            store,
            current,
            second.events,
            actor="tester",
            run_id="",
            recorded_at="2026-08-18T00:00:01+00:00",
        )
        result = next(
            row
            for row in run_artifact_checks(store, committed)
            if row.check_id == mk2.CHECK_EVENT_LOG
        )
        assert result.verdict is Verdict.PASS


def test_public_pack_and_bundle_binders_capture_present_ci_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "run-42")
    project = tmp_path / "project"
    project.mkdir()
    pack_file = project / "pack.spec"
    pack_file.write_text("acceptance\n")
    assert _run("init", "--project", str(project))[0] == 0
    root = project / ".vouch"
    lifecycle = (
        ("specify", "--name", "u", "--version", "v1"),
        ("freeze", "--name", "u", "--pack-version", "pack/1"),
    )
    for arguments in lifecycle:
        code, output = _run(arguments[0], "--root", str(root), *arguments[1:])
        assert code == 0, output

    pack_inventory = build_pack_inventory(str(project), ("pack.spec",))
    with Store.open(str(root)) as store:
        bind_pack(store, "u", pack_inventory, actor="pack-caller")
        pack_record = decode_event_record(
            store.get_object(ArtifactKind.EVENT, store.read_snapshot().event_head)
        )
    assert pack_record.actor == "pack-caller"
    assert pack_record.run_id == "run-42"

    for arguments in (
        ("mark-ready", "--name", "u"),
        ("start", "--name", "u"),
        ("mark-done", "--name", "u"),
        ("publish", "--name", "u"),
        ("prepare-release", "--release-id", "r1", "--includes", "u"),
    ):
        code, output = _run(arguments[0], "--root", str(root), *arguments[1:])
        assert code == 0, output

    bundle_tree = tmp_path / "bundle"
    bundle_tree.mkdir()
    (bundle_tree / "artifact.txt").write_text("artifact\n")
    bundle_inventory = build_inventory(str(bundle_tree), ())
    with Store.open(str(root)) as store:
        bind_bundle(store, "r1", bundle_inventory, actor="bundle-caller")
        bundle_record = decode_event_record(
            store.get_object(ArtifactKind.EVENT, store.read_snapshot().event_head)
        )
    assert bundle_record.actor == "bundle-caller"
    assert bundle_record.run_id == "run-42"


def test_claim_boundary_denies_unanchored_real_world_event_order() -> None:
    text = (Path(__file__).resolve().parents[1] / "docs" / "claim-boundary.md").read_text().lower()
    assert "replay proves the snapshot is derivable from the recorded chain" in text
    assert "does not prove that the recorded order is the order in which events really occurred" in text


def test_operator_docs_name_the_unverifiable_pre_log_checkpoint() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in ("docs/claim-boundary.md", "docs/quickstart.md"):
        text = (root / relative).read_text().lower()
        assert "pre-log checkpoint" in text, relative
        assert "attested rather than verified" in text, relative
        assert "replay proves nothing about pre-log history" in text, relative
