"""D-36 upgrade, replay and repeated-freeze regressions beyond the frozen pack."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.checks import run_checks
from vouch_mk2.core import Accepted, FreezeAcceptance, Respecify, apply, decide
from vouch_mk2.core.events import EvidenceRecorded, EventRecord
from vouch_mk2.core.invariants import current_pack_evidence_is_green
from vouch_mk2.core.model import (
    EvidenceEvent, EvidenceKind, EvidenceOutcome, Snapshot, Unit, UnitStatus,
)
from vouch_mk2.formal.explore import state_fingerprint
from vouch_mk2.store import (
    DecodeError, decode_event_record, decode_snapshot, encode_document,
    encode_event_record, encode_snapshot,
)
from vouch_mk2.store.upgrade import UPGRADERS, upgrade_document


FIXTURES = Path(__file__).parent / "fixtures" / "state-format"
OLD_PACK = "c" * 64
NEW_PACK = "e" * 64


def _upgraded_green() -> Snapshot:
    document = json.loads((FIXTURES / "format5-refreeze.json").read_bytes())
    upgraded, steps = upgrade_document(document)
    assert steps == [(5, 6)]
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
    snapshot = decode_snapshot(encode_document(upgraded))
    assert snapshot.revision == original["revision"]
    unit = _unit(snapshot)
    assert unit.previous_acceptance_pack_digest == ""
    assert not unit.refreeze_green_required
    assert current_pack_evidence_is_green(snapshot, unit)
    refrozen = _refreeze(snapshot, NEW_PACK)
    assert not current_pack_evidence_is_green(refrozen, _unit(refrozen))


def test_format6_canonical_bytes_match_the_new_golden() -> None:
    original = json.loads((FIXTURES / "format5.golden.json").read_bytes())
    upgraded, steps = upgrade_document(original)
    assert steps == [(5, 6)]
    golden = (FIXTURES / "format6.golden.json").read_bytes()
    assert encode_snapshot(decode_snapshot(encode_document(upgraded))) == golden
    assert encode_snapshot(decode_snapshot(golden)) == golden


@pytest.mark.parametrize("field", ["previous_acceptance_pack_digest", "refreeze_green_required"])
def test_format6_requires_both_new_fields(field: str) -> None:
    document = json.loads(encode_snapshot(_upgraded_green()))
    del document["units"][0][field]
    with pytest.raises(DecodeError, match=field):
        decode_snapshot(encode_document(document))


@pytest.mark.parametrize(
    ("field", "value"),
    [("previous_acceptance_pack_digest", "bad"), ("refreeze_green_required", 1)],
)
def test_format6_strictly_validates_the_new_fields(field: str, value: object) -> None:
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
    assert _unit(snapshot).previous_acceptance_pack_digest == ""


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


def test_legacy_respecify_events_replay_without_inventing_a_previous_inventory() -> None:
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
    freeze = decide(respecified, FreezeAcceptance("core", "pack/legacy", NEW_PACK, OLD_PACK))
    assert isinstance(freeze, Accepted)
    replayed = apply(respecified, freeze.events)
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
