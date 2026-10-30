"""Additional anchor-reference regressions, independent of the frozen pack."""

from __future__ import annotations

import io
import json
import re
import shlex
import shutil
import sys
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    AdvanceAnchor,
    AnchorAdvanced,
    EvidenceHead,
    EventRecord,
    Refused,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.init import generated_workflow
from vouch_mk2.store import (
    ArtifactKind,
    DecodeError,
    Store,
    StoreError,
    codec,
    decode_event_record,
    digest_of,
    encode_document,
    encode_event_record,
)
from vouch_mk2.store.event_log import build_event_records

ROOT = Path(__file__).resolve().parents[1]


def _two_heads() -> Snapshot:
    return Snapshot(
        units=(
            Unit("u", "v1", UnitStatus.SUPERSEDED),
            Unit("u", "v2", UnitStatus.PUBLISHED),
        ),
        evidence_heads=(
            EvidenceHead("u", "a" * 64, 1, "GENESIS", "v1"),
            EvidenceHead("u", "b" * 64, 1, "GENESIS", "v2"),
        ),
    )


def _record(event: str, payload: dict[str, object]) -> bytes:
    return encode_document({
        "actor": "regression",
        "event": event,
        "pack_digest": "",
        "payload": payload,
        "prev": "GENESIS",
        "recorded_at": "2026-10-30T00:00:00Z",
        "revision": 1,
        "run_id": "run",
    })


def test_legacy_shaped_event_fold_resolves_current_name_with_two_heads() -> None:
    before = _two_heads()
    event = decode_event_record(_record("AnchorAdvanced", {"unit_name": "u"})).folded_event
    assert event == AnchorAdvanced("u")
    after = apply(before, (event,), event_run_ids=("legacy-run",))
    assert after.evidence_heads[0] == before.evidence_heads[0]
    assert after.evidence_heads[1] == replace(
        before.evidence_heads[1], anchored="b" * 64, anchor_run_id="legacy-run"
    )


def test_exact_anchor_refusals_name_the_reference() -> None:
    before = _two_heads()
    missing = decide(before, AdvanceAnchor("u", unit_version="v9"))
    assert isinstance(missing, Refused)
    assert missing.violations[0].code == "evidence.no-chain"
    assert "u@v9" in missing.violations[0].detail
    anchored = apply(before, (AnchorAdvanced("u", unit_version="v1"),))
    current = decide(anchored, AdvanceAnchor("u", unit_version="v1"))
    assert isinstance(current, Refused)
    assert current.violations[0].code == "evidence.anchor-current"
    assert "u@v1" in current.violations[0].detail


def test_exact_fold_never_falls_back_to_current_head() -> None:
    with pytest.raises(LookupError, match="u@v9"):
        apply(_two_heads(), (AnchorAdvanced("u", unit_version="v9"),))


def test_exact_advance_can_claim_a_name_with_only_a_superseded_version() -> None:
    before = _two_heads()
    before = replace(before, units=before.units[:1], evidence_heads=before.evidence_heads[:1])
    assert isinstance(decide(before, AdvanceAnchor("u")), Refused)
    command = AdvanceAnchor("u", "run", unit_version="v1")
    result = decide(before, command)
    assert isinstance(result, Accepted)
    assert result.events == (AnchorAdvanced("u", unit_version="v1"),)
    after = apply(before, result.events, event_run_ids=(command.run_id,))
    head = after.evidence_head_ref(UnitRef("u", "v1"))
    assert head == replace(before.evidence_heads[0], anchored="a" * 64, anchor_run_id="run")


@pytest.mark.parametrize("version", [None, 1, False, [], {}])
def test_anchor_decoder_rejects_non_string_versions(version: object) -> None:
    with pytest.raises(DecodeError, match="unit_version: expected string"):
        decode_event_record(_record("AnchorAdvanced", {"unit_name": "u", "unit_version": version}))


@pytest.mark.parametrize("payload", [
    {"unit_version": "v1"},
    {"unit_name": "u", "target": "a" * 64},
    {"unit_name": "u", "unit_version": "v1", "target": "a" * 64},
])
def test_anchor_decoder_still_requires_name_and_rejects_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DecodeError):
        decode_event_record(_record("AnchorAdvanced", payload))


def test_other_event_defaults_remain_required_on_the_wire() -> None:
    with pytest.raises(DecodeError, match="payload.test_set_digest: missing required field"):
        decode_event_record(_record("AcceptancePackBound", {"name": "u", "pack_digest": "a" * 64}))


@pytest.mark.parametrize("own_workflow", [False, True])
def test_ci_anchor_loop_splits_labels_and_passes_exact_argv(own_workflow: bool) -> None:
    workflow = (
        (ROOT / ".github/workflows/ci.yml").read_text()
        if own_workflow else generated_workflow()
    )
    match = re.search(r"while IFS= read -r label; do\n(.*?)\n\s*done <", workflow, re.DOTALL)
    assert match is not None
    body = match.group(1)
    assert 'unit_name="${label%@*}"' in body
    assert 'unit_version="${label##*@}"' in body
    # Stub the Python command and execute the actual loop body, preserving
    # shell quoting and flag order without touching a store or running a lock.
    result = subprocess.run(
        ["bash", "-c", 'set -eu; python() { printf "%s\\n" "$@"; }; label="u@v1"; ' + body],
        text=True, capture_output=True, check=False,
        env={"RUNNER_TEMP": "/tmp"},
    )
    assert result.returncode == 0, result.stderr
    argv = result.stdout.splitlines()
    assert argv[1:] == [
        "advance-anchor", "--root", ".vouch", "--unit-name=u", "--unit-version=v1",
    ]



def _anchor_record(event: AnchorAdvanced) -> EventRecord:
    return EventRecord(
        folded_event=event,
        recorded_at="2026-10-30T00:00:00Z",
        prev="GENESIS",
        revision=1,
        actor="regression",
        run_id="run",
        pack_digest="",
    )


def _unexpected_serialization(document: object) -> bytes:
    raise AssertionError("unresolved anchor reached byte serialization")


def test_encoder_refuses_unresolved_anchor_before_producing_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codec, "_canonical_json", _unexpected_serialization)
    with pytest.raises(
        StoreError, match="an AnchorAdvanced record must name its resolved unit_version"
    ):
        encode_event_record(_anchor_record(AnchorAdvanced("u")))


def test_record_builder_refuses_unresolved_anchor_before_producing_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codec, "_canonical_json", _unexpected_serialization)
    before = _two_heads()
    with pytest.raises(
        StoreError, match="an AnchorAdvanced record must name its resolved unit_version"
    ):
        build_event_records(before, (AnchorAdvanced("u"),), actor="regression", run_id="run")
    assert before == _two_heads()


def test_resolved_anchor_record_round_trips_exactly() -> None:
    record = _anchor_record(AnchorAdvanced("u", unit_version="v1"))
    assert decode_event_record(encode_event_record(record)) == record


@pytest.mark.parametrize("own_workflow", [False, True])
@pytest.mark.parametrize("label, expected", [("u@-v1", ("u", "-v1")), ("-x@v1", ("-x", "v1"))])
def test_ci_anchor_loop_parses_leading_dash_identifiers(
    own_workflow: bool, label: str, expected: tuple[str, str],
) -> None:
    workflow = (
        (ROOT / ".github/workflows/ci.yml").read_text()
        if own_workflow else generated_workflow()
    )
    match = re.search(r"while IFS= read -r label; do\n(.*?)\n\s*done <", workflow, re.DOTALL)
    assert match is not None
    parser_script = (
        "import json, sys; from vouch_mk2.cli import _parser; "
        "args = _parser().parse_args(sys.argv[1:]); "
        "print(json.dumps([args.unit_name, args.unit_version]))"
    )
    # Replace just the Python executable: discard the lock path and let the
    # real CLI parser consume the exact shell-expanded arguments from CI.
    stub = (
        'set -eu; python() { shift; '
        + shlex.quote(sys.executable) + " -c " + shlex.quote(parser_script)
        + ' "$@"; }; label=' + shlex.quote(label) + "; "
    )
    result = subprocess.run(
        ["bash", "-c", stub + match.group(1)],
        text=True, capture_output=True, check=False,
        env={"RUNNER_TEMP": "/tmp"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == list(expected)


def _cli(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def test_divergent_merge_resolves_legacy_anchor_to_ours_current_version(tmp_path: Path) -> None:
    project = tmp_path / "ours"
    project.mkdir()
    root = str(project / ".vouch")
    ok = [sys.executable, "-c", "raise SystemExit(0)"]
    fail = [sys.executable, "-c", "raise SystemExit(1)"]

    def must(*argv: str) -> None:
        code, output = _cli(*argv)
        assert code == 0, output

    def heads(store_root: str) -> tuple[Snapshot, dict[str, EvidenceHead]]:
        with Store.open(store_root, read_only=True) as store:
            snapshot = store.read_snapshot()
        return snapshot, {head.ref().label(): head for head in snapshot.evidence_heads}

    must("init", "--project", str(project))
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    (project / "packs/u.spec.md").write_text("# Unit u\n")
    (project / "tests/t.py").write_text("def test_u():\n    assert True\n")
    must("set-tier", "--root", root, "--tier", "standard")
    must("set-anchor-mode", "--root", root, "--mode", "ci")
    for version in ("v1", "v2"):
        must("specify", "--root", root, "--name", "u", "--version", version)
        must(
            "freeze", "--root", root, "--name", "u", "--pack-version", f"u-pack/{version}",
            "--project", str(project), "--pack-file", "packs/u.spec.md",
            "--pack-test-file", "tests/t.py",
        )
        must("mark-ready", "--root", root, "--name", "u")
        must("start", "--root", root, "--name", "u")
        for kind, command in (("red", fail), ("green", ok)):
            must("record-evidence", "--root", root, "--unit", f"u@{version}", "--kind", kind, "--", *command)
        if version == "v1":
            must("advance-anchor", "--root", root, "--unit-name", "u")
            must("mark-done", "--root", root, "--name", "u")
            must("publish", "--root", root, "--name", "u")
            must("supersede", "--root", root, "--name", "u")

    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    theirs_root = str(theirs / ".vouch")
    # Commit the same pre-upgrade wire shape as frozen R4's helper, bypassing
    # the new encoder while retaining the historical record and CI claim.
    with Store.open(theirs_root) as store:
        with store.write_locked():
            snapshot = store.read_snapshot()
            document = {
                "actor": "legacy", "event": "AnchorAdvanced", "pack_digest": "",
                "payload": {"unit_name": "u"}, "prev": snapshot.event_head,
                "recorded_at": "2026-08-20T10:34:26+00:00",
                "revision": snapshot.revision + 1, "run_id": "legacy-ci-17",
            }
            data = encode_document(document)
            digest = digest_of(ArtifactKind.EVENT, data)
            folded = apply(
                snapshot, (AnchorAdvanced("u"),),
                event_digests=(digest,), event_run_ids=("legacy-ci-17",),
            )
            store.commit(folded, objects=((ArtifactKind.EVENT, data),), expected_revision=snapshot.revision)
    source = decode_event_record(data)
    assert source.folded_event == AnchorAdvanced("u")
    assert source.run_id == "legacy-ci-17"
    assert json.loads(data)["payload"] == {"unit_name": "u"}
    must("record-evidence", "--root", root, "--unit", "u@v2", "--kind", "refactor", "--", *ok)
    before_snapshot, before = heads(root)
    assert before["u@v2"].anchored != before["u@v2"].head

    code, output = _cli("merge", "--root", root, "--other", theirs_root)

    assert code == 0, output
    assert "replayed 1 source record(s)" in output
    merged, after = heads(root)
    assert after["u@v2"].anchored == after["u@v2"].head == before["u@v2"].head
    assert after["u@v1"] == before["u@v1"]
    with Store.open(root, read_only=True) as store:
        merge = decode_event_record(store.get_object(ArtifactKind.EVENT, merged.event_head))
        record = store.get_object(ArtifactKind.EVENT, merge.prev)
    assert type(merge.folded_event).__name__ == "MergeRecorded"
    document = json.loads(record)
    assert document["event"] == "AnchorAdvanced"
    assert document["payload"] == {"unit_name": "u", "unit_version": "v2"}
    assert document["prev"] == before_snapshot.event_head
