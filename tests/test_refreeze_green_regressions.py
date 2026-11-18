"""D-36 upgrade, replay and repeated-freeze regressions beyond the frozen pack."""

from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.checks import run_checks
from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted, BeginReverify, BindPack, FreezeAcceptance, MarkDone, MarkReady,
    Publish, Refused, Respecify, StartWork, apply, decide,
)
from vouch_mk2.core.events import EvidenceRecorded, EventRecord
from vouch_mk2.core.invariants import current_pack_evidence_is_green
from vouch_mk2.core.model import (
    EvidenceEvent, EvidenceKind, EvidenceOutcome, Snapshot, Unit, UnitStatus,
)
from vouch_mk2.formal.explore import state_fingerprint
from vouch_mk2.projections.next_actions import next_actions
from vouch_mk2.store import (
    DecodeError, Store, decode_event_record, decode_snapshot, encode_document,
    encode_event_record, encode_snapshot,
)
from vouch_mk2.store.upgrade import UPGRADERS, upgrade_document


FIXTURES = Path(__file__).parent / "fixtures" / "state-format"
OLD_PACK = "c" * 64
NEW_PACK = "e" * 64


def _cli(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _cli_must(*argv: str) -> None:
    code, output = _cli(*argv)
    assert code == 0, (argv, output)


def _cli_snapshot(root: str) -> Snapshot:
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot()


def _cli_publish_offered(root: str) -> bool:
    code, output = _cli("next", "--root", root, "--json")
    assert code == 0, output
    return any(action["command"] == "publish" for action in json.loads(output)["actions"])


def _cli_gap_green_then_bind(tmp_path: Path, *, pending: bool) -> str:
    project = tmp_path / "proj"
    project.mkdir()
    _cli_must("init", "--project", str(project))
    root = str(project / ".vouch")
    _cli_must("set-tier", "--root", root, "--tier", "standard")
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    spec = project / "packs/u.spec.md"
    spec.write_text("# Original pack\n")
    (project / "tests/t.py").write_text("def test_x():\n    assert True\n")
    (project / "packs/u.effects.json").write_text('{"effects_format": 1, "effects": []}')
    files = (
        "--project", str(project), "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py", "--pack-effects-file", "packs/u.effects.json",
    )

    def finish() -> None:
        for command in ("mark-ready", "start", "mark-done"):
            _cli_must(command, "--root", root, "--name", "u")

    def amend(number: int) -> None:
        deviation = f"DEV-{number}"
        _cli_must(
            "open-deviation", "--root", root, "--deviation-id", deviation,
            "--deviation-type", "pack-defect", "--unit", "u@v1",
        )
        _cli_must(
            "adjudicate-amend", "--root", root, "--deviation-id", deviation,
            "--amendment-version", f"u-pack/{number + 1}",
        )
        _cli_must("close-deviation", "--root", root, "--deviation-id", deviation)
        _cli_must("respecify", "--root", root, "--name", "u")

    _cli_must("specify", "--root", root, "--name", "u", "--version", "v1")
    _cli_must("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1", *files)
    for kind, exit_code in (("red", 1), ("green", 0)):
        _cli_must(
            "record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind,
            "--", sys.executable, "-c", f"raise SystemExit({exit_code})",
        )
    finish()
    if pending:
        amend(1)
        spec.write_text("# Changed pack\n")
        _cli_must("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2", *files)
        finish()
    amend(2 if pending else 1)
    _cli_must(
        "freeze", "--root", root, "--name", "u", "--pack-version",
        "u-pack/3" if pending else "u-pack/2",
    )
    _cli_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green",
        "--", sys.executable, "-c", "raise SystemExit(0)",
    )
    unit = _cli_snapshot(root).unit("u")
    assert unit is not None
    assert unit.acceptance_test_set_digest == ""
    assert unit.refreeze_green_required == pending
    _cli_must("bind-pack", "--root", root, "--name", "u", *files)
    finish()
    return root


def test_cli_gap_green_cannot_clear_a_pending_requirement_before_identical_binding(
    tmp_path: Path,
) -> None:
    root = _cli_gap_green_then_bind(tmp_path, pending=True)
    before = _cli_snapshot(root)
    assert not _cli_publish_offered(root)
    code, output = _cli("publish", "--root", root, "--name", "u")
    assert code == 2, output
    assert re.findall(r"\bgate\.[a-z.-]+", output) == ["gate.dod.current-pack-green"]
    assert "after the latest freeze" in output
    assert "after the bind-pack that bound it" in output
    assert _cli_snapshot(root) == before
    _cli_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green",
        "--", sys.executable, "-c", "raise SystemExit(0)",
    )
    assert _cli_publish_offered(root)
    _cli_must("publish", "--root", root, "--name", "u")


def test_cli_gap_green_keeps_the_earlier_green_when_unchanged_binding_needs_no_new_green(
    tmp_path: Path,
) -> None:
    root = _cli_gap_green_then_bind(tmp_path, pending=False)
    assert _cli_publish_offered(root)
    _cli_must("publish", "--root", root, "--name", "u")


def _upgraded_green() -> Snapshot:
    document = json.loads((FIXTURES / "format5-refreeze.json").read_bytes())
    upgraded, steps = upgrade_document(document)
    assert steps == [(5, 6), (6, 7)]
    return decode_snapshot(encode_document(upgraded))


def _unit(snapshot: Snapshot) -> Unit:
    unit = snapshot.unit("core")
    assert unit is not None
    return unit


def _refreeze(snapshot: Snapshot, digest: str, version: str = "pack/next") -> Snapshot:
    unit = _unit(snapshot)
    snapshot = replace(snapshot, units=(replace(unit, status=UnitStatus.INVALIDATED),))
    respecify = decide(snapshot, Respecify("core"))
    assert isinstance(respecify, Accepted), respecify
    snapshot = apply(snapshot, respecify.events)
    freeze = decide(snapshot, FreezeAcceptance("core", version, digest, OLD_PACK if digest else ""))
    assert isinstance(freeze, Accepted), freeze
    return apply(snapshot, freeze.events)


def _record(snapshot: Snapshot, kind: EvidenceKind, digest: str = OLD_PACK) -> Snapshot:
    unit = _unit(snapshot)
    head = snapshot.evidence_head_ref(unit.ref())
    assert head is not None
    event = EvidenceEvent(
        unit.ref(), head.head_sequence + 1, head.head, kind, "pytest",
        EvidenceOutcome.TESTS_FAILED if kind is EvidenceKind.RED else EvidenceOutcome.TESTS_PASSED,
        "2026-11-17T00:00:00+00:00", pack_digest=digest,
    )
    return apply(snapshot, (EvidenceRecorded(event, "f" * 64),))


def test_format5_upgrade_preserves_the_old_green_without_inventing_a_previous_freeze() -> None:
    original = json.loads((FIXTURES / "format5-refreeze.json").read_bytes())
    before = encode_document(original)
    upgraded = UPGRADERS[5](original)
    assert encode_document(original) == before
    assert upgraded["state_format"] == 6
    snapshot = decode_snapshot(encode_document(UPGRADERS[6](upgraded)))
    assert snapshot.revision == original["revision"]
    unit = _unit(snapshot)
    assert unit.previous_acceptance_pack_digest == ""
    assert not unit.refreeze_green_required
    assert current_pack_evidence_is_green(snapshot, unit)
    refrozen = _refreeze(snapshot, NEW_PACK)
    assert not current_pack_evidence_is_green(refrozen, _unit(refrozen))


def test_upgraded_format5_matches_the_current_golden() -> None:
    original = json.loads((FIXTURES / "format5.golden.json").read_bytes())
    upgraded, steps = upgrade_document(original)
    assert steps == [(5, 6), (6, 7)]
    golden = (FIXTURES / "format7.golden.json").read_bytes()
    assert encode_snapshot(decode_snapshot(encode_document(upgraded))) == golden
    assert encode_snapshot(decode_snapshot(golden)) == golden


@pytest.mark.parametrize("field", ["previous_acceptance_pack_digest", "refreeze_green_required"])
def test_current_format_requires_both_refreeze_fields(field: str) -> None:
    document = json.loads(encode_snapshot(_upgraded_green()))
    del document["units"][0][field]
    with pytest.raises(DecodeError, match=field):
        decode_snapshot(encode_document(document))


@pytest.mark.parametrize(
    ("field", "value"),
    [("previous_acceptance_pack_digest", "bad"), ("refreeze_green_required", 1)],
)
def test_current_format_strictly_validates_the_refreeze_fields(field: str, value: object) -> None:
    document = json.loads(encode_snapshot(_upgraded_green()))
    document["units"][0][field] = value
    with pytest.raises(DecodeError, match=field):
        decode_snapshot(encode_document(document))


def test_an_identical_second_refreeze_cannot_clear_an_outstanding_green_requirement() -> None:
    first = _refreeze(_upgraded_green(), NEW_PACK)
    second = _refreeze(first, NEW_PACK, "pack/again")
    assert _unit(second).refreeze_green_required
    assert not current_pack_evidence_is_green(second, _unit(second))
    green = _record(second, EvidenceKind.GREEN)
    assert current_pack_evidence_is_green(green, _unit(green))
    same = _refreeze(green, NEW_PACK, "pack/third")
    assert current_pack_evidence_is_green(same, _unit(same))


def test_a_label_only_refreeze_does_not_invent_a_changed_bound_inventory() -> None:
    snapshot = _refreeze(_upgraded_green(), "")
    assert not _unit(snapshot).refreeze_green_required
    assert _unit(snapshot).previous_acceptance_pack_digest == OLD_PACK


def test_an_empty_digest_summary_cannot_satisfy_current_pack_evidence() -> None:
    snapshot = _refreeze(_upgraded_green(), "")
    snapshot = _record(snapshot, EvidenceKind.RED, "")
    snapshot = _record(snapshot, EvidenceKind.GREEN, "")
    head = snapshot.evidence_head_ref(_unit(snapshot).ref())
    assert head is not None
    summary = next(item for item in head.pack_summaries if item.pack_digest == "")
    assert summary.red_seen and summary.green
    assert not current_pack_evidence_is_green(snapshot, _unit(snapshot))


@pytest.mark.parametrize("cycles", [1, 3])
@pytest.mark.parametrize("digest", [OLD_PACK, NEW_PACK])
def test_late_binding_compares_the_inventory_retained_across_label_only_cycles(
    cycles: int, digest: str,
) -> None:
    snapshot = _upgraded_green()
    for cycle in range(cycles):
        snapshot = _refreeze(snapshot, "", f"pack/label-{cycle}")
        assert _unit(snapshot).previous_acceptance_pack_digest == OLD_PACK
        assert not _unit(snapshot).refreeze_green_required
        # Check that persisting an intermediate label-only freeze keeps the baseline.
        snapshot = decode_snapshot(encode_snapshot(snapshot))
    binding = decide(snapshot, BindPack("core", digest, OLD_PACK))
    assert isinstance(binding, Accepted), binding
    snapshot = apply(snapshot, binding.events)
    assert _unit(snapshot).previous_acceptance_pack_digest == ""
    assert _unit(snapshot).refreeze_green_required == (digest != OLD_PACK)
    for command in (MarkReady("core"), StartWork("core"), MarkDone("core")):
        decision = decide(snapshot, command)
        assert isinstance(decision, Accepted), decision
        snapshot = apply(snapshot, decision.events)
    publication = decide(snapshot, Publish("core"))
    offered = any(action.command == "publish" for action in next_actions(snapshot))
    if digest == OLD_PACK:
        assert isinstance(publication, Accepted), publication
        assert offered
    else:
        assert isinstance(publication, Refused), publication
        assert [v.code for v in publication.violations] == ["gate.dod.current-pack-green"]
        assert not offered
        green = _record(snapshot, EvidenceKind.GREEN)
        assert isinstance(decide(green, Publish("core")), Accepted)
        assert any(action.command == "publish" for action in next_actions(green))


def test_late_binding_consumes_the_baseline_and_the_next_refreeze_uses_the_new_inventory() -> None:
    snapshot = _refreeze(_upgraded_green(), "")
    binding = decide(snapshot, BindPack("core", NEW_PACK, OLD_PACK))
    assert isinstance(binding, Accepted)
    snapshot = _record(apply(snapshot, binding.events), EvidenceKind.GREEN)
    assert not _unit(snapshot).refreeze_green_required
    # Returning to the original bytes is another change, compared with NEW_PACK.
    snapshot = _refreeze(snapshot, "", "pack/return")
    assert _unit(snapshot).previous_acceptance_pack_digest == NEW_PACK
    binding = decide(snapshot, BindPack("core", OLD_PACK, OLD_PACK))
    assert isinstance(binding, Accepted)
    snapshot = apply(snapshot, binding.events)
    assert _unit(snapshot).previous_acceptance_pack_digest == ""
    assert _unit(snapshot).refreeze_green_required


def test_identical_late_binding_cannot_clear_a_pending_requirement_even_when_reverifying() -> None:
    snapshot = _refreeze(_upgraded_green(), NEW_PACK)
    for cycle in range(3):
        snapshot = _refreeze(snapshot, "", f"pack/pending-{cycle}")
        assert _unit(snapshot).previous_acceptance_pack_digest == NEW_PACK
        assert _unit(snapshot).refreeze_green_required
    binding = decide(snapshot, BindPack("core", NEW_PACK, OLD_PACK))
    assert isinstance(binding, Accepted)
    snapshot = apply(snapshot, binding.events)
    snapshot = replace(snapshot, units=(replace(_unit(snapshot), status=UnitStatus.INVALIDATED),))
    reverify = decide(snapshot, BeginReverify("core"))
    assert isinstance(reverify, Accepted)
    snapshot = apply(snapshot, reverify.events)
    assert _unit(snapshot).refreeze_green_required
    assert isinstance(decide(snapshot, Publish("core")), Refused)
    green = _record(snapshot, EvidenceKind.GREEN)
    assert isinstance(decide(green, Publish("core")), Accepted)


def test_respecifying_twice_before_freeze_keeps_the_last_bound_inventory() -> None:
    snapshot = _upgraded_green()
    for _ in range(2):
        snapshot = replace(snapshot, units=(replace(_unit(snapshot), status=UnitStatus.INVALIDATED),))
        decision = decide(snapshot, Respecify("core"))
        assert isinstance(decision, Accepted)
        snapshot = apply(snapshot, decision.events)
        assert _unit(snapshot).previous_acceptance_pack_digest == OLD_PACK
    freeze = decide(snapshot, FreezeAcceptance("core", "pack/next", NEW_PACK, OLD_PACK))
    assert isinstance(freeze, Accepted)
    refrozen = apply(snapshot, freeze.events)
    assert _unit(refrozen).refreeze_green_required
    assert not current_pack_evidence_is_green(refrozen, _unit(refrozen))


def test_refactor_and_other_pack_evidence_cannot_discharge_the_requirement() -> None:
    snapshot = _refreeze(_upgraded_green(), NEW_PACK)
    for kind, digest in [(EvidenceKind.REFACTOR, OLD_PACK), (EvidenceKind.GREEN, "a" * 64)]:
        recorded = _record(snapshot, kind, digest)
        assert _unit(recorded).refreeze_green_required
        assert not current_pack_evidence_is_green(recorded, _unit(recorded))
    green = _record(snapshot, EvidenceKind.GREEN)
    red = _record(green, EvidenceKind.RED)
    assert not current_pack_evidence_is_green(red, _unit(red))


@pytest.mark.parametrize("late", [False, True])
def test_legacy_respecify_events_replay_without_inventing_a_previous_inventory(late: bool) -> None:
    snapshot = _upgraded_green()
    snapshot = replace(snapshot, units=(replace(_unit(snapshot), status=UnitStatus.INVALIDATED),))
    decision = decide(snapshot, Respecify("core"))
    assert isinstance(decision, Accepted)
    record = EventRecord(
        decision.events[0], "2026-11-17T00:00:00+00:00", "GENESIS", snapshot.revision + 1,
        "legacy", "", "",
    )
    document = json.loads(encode_event_record(record))
    del document["payload"]["previous_pack_digest"]
    historical = decode_event_record(encode_document(document))
    respecified = apply(snapshot, (historical.folded_event,))
    assert _unit(respecified).previous_acceptance_pack_digest == ""
    freeze = decide(
        respecified,
        FreezeAcceptance("core", "pack/legacy", "" if late else NEW_PACK, "" if late else OLD_PACK),
    )
    assert isinstance(freeze, Accepted)
    replayed = apply(respecified, freeze.events)
    if late:
        binding = decide(replayed, BindPack("core", NEW_PACK, OLD_PACK))
        assert isinstance(binding, Accepted)
        replayed = apply(replayed, binding.events)
    assert not _unit(replayed).refreeze_green_required
    assert current_pack_evidence_is_green(replayed, _unit(replayed))


@pytest.mark.parametrize("status", [UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND])
def test_upgrading_published_units_keeps_standing_verdicts(status: UnitStatus) -> None:
    snapshot = _upgraded_green()
    unit = replace(_unit(snapshot), status=status, published_once=True)
    snapshot = replace(snapshot, units=(unit,))
    before = run_checks(snapshot)
    original = json.loads(encode_snapshot(snapshot))
    original["state_format"] = 5
    for raw in original["units"]:
        del raw["previous_acceptance_pack_digest"]
        del raw["refreeze_green_required"]
    upgraded, _steps = upgrade_document(original)
    assert run_checks(decode_snapshot(encode_document(upgraded))) == before


def test_formal_fingerprint_includes_the_new_gate_state() -> None:
    snapshot = _upgraded_green()
    unit = _unit(snapshot)
    for changed in [
        replace(unit, previous_acceptance_pack_digest=NEW_PACK),
        replace(unit, refreeze_green_required=True),
    ]:
        assert state_fingerprint(replace(snapshot, units=(changed,))) != state_fingerprint(snapshot)
