from __future__ import annotations

import json
from typing import cast

import pytest

from vouch_mk2.core.model import (
    AnchorMode,
    BundleInventory,
    ClosureRecord,
    ClosureTriggerKind,
    Deviation,
    DeviationStatus,
    DeviationType,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    EvidenceOutcome,
    InventoryEntry,
    LaunchCriteria,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.store import (
    DecodeError,
    decode_bundle_inventory,
    decode_evidence_event,
    decode_snapshot,
    encode_bundle_inventory,
    encode_evidence_event,
    encode_snapshot,
)


def _populated_snapshot() -> Snapshot:
    units = (
        Unit(
            name="producer",
            version="2",
            status=UnitStatus.PUBLISHED,
            acceptance_pack_version="pack-2",
        ),
        Unit(
            name="consumer",
            version="3",
            status=UnitStatus.RE_VERIFYING,
            depends_on=("producer",),
            acceptance_pack_version="pack-3",
        ),
    )
    deviations = tuple(
        Deviation(
            deviation_id=f"D-{index}",
            deviation_type=DeviationType.PLAN_GAP,
            unit=UnitRef(name="consumer", version="3"),
            status=status,
            amendment_version="4" if status is DeviationStatus.AMENDED else "",
            impact_set=("consumer", "downstream"),
            release_impact_set=("r-both",),
        )
        for index, status in enumerate(DeviationStatus)
    )
    criteria = LaunchCriteria(
        launch_criteria=("healthy",),
        verification_window="30m",
        rollback_triggers=("errors",),
        go_no_go_authority="ops",
    )
    closure = ClosureRecord(
        trigger="operator stop",
        trigger_kind=ClosureTriggerKind.OPERATOR,
        roster_snapshot=(("consumer", "release-bound"),),
        no_completion_claim=True,
        observations=("latency",),
    )
    ref = UnitRef(name="consumer", version="3")
    releases = (
        Release("r-both", ReleaseArchetype.OPERATIONAL, ReleaseState.RETIRED, (ref,), criteria, closure, "a" * 64),
        Release("r-criteria", ReleaseArchetype.OPERATIONAL, ReleaseState.PREPARED, (ref,), criteria, None),
        Release("r-closure", ReleaseArchetype.OPERATIONAL, ReleaseState.INVALIDATED, (ref,), None, closure),
        Release("r-null", ReleaseArchetype.OPERATIONAL, ReleaseState.PREPARED, (ref,), None, None),
    )
    return Snapshot(
        revision=7, units=units, deviations=deviations, releases=releases
    )


def _baseline_document() -> dict[str, object]:
    return {
        "anchor_mode": "none",
        "evidence_heads": [],
        "formal_certificate_digest": "",
        "revision": 1,
        "units": [
            {
                "name": "alpha",
                "version": "1",
                "status": "specified",
                "depends_on": [],
                "acceptance_pack_version": "",
            }
        ],
        "deviations": [
            {
                "deviation_id": "D-1",
                "deviation_type": "plan-gap",
                "unit": {"name": "alpha", "version": "1"},
                "status": "open",
                "amendment_version": "",
                "impact_set": [],
                "release_impact_set": [],
            }
        ],
        "releases": [],
    }


def _release_document() -> dict[str, object]:
    return {
        "bundle_inventory_digest": "",
        "release_id": "r1",
        "archetype": "operational",
        "state": "retired",
        "includes": [{"name": "alpha", "version": "1"}],
        "criteria": {
            "launch_criteria": ["healthy"],
            "verification_window": "1h",
            "rollback_triggers": ["errors"],
            "go_no_go_authority": "ops",
        },
        "closure": {
            "trigger": "operator",
            "trigger_kind": "operator",
            "roster_snapshot": [["alpha", "release-bound"]],
            "no_completion_claim": True,
            "observations": [],
        },
    }


def _json_bytes(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def test_snapshot_round_trip_identity() -> None:
    snapshot = _populated_snapshot()
    assert decode_snapshot(encode_snapshot(snapshot)) == snapshot


def test_snapshot_encoding_is_byte_stable_and_canonical() -> None:
    snapshot = _populated_snapshot()
    first = encode_snapshot(snapshot)
    second = encode_snapshot(snapshot)
    assert first == second
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")


def test_unknown_top_level_field_names_its_path() -> None:
    document = _baseline_document()
    document["surprise"] = True
    with pytest.raises(DecodeError, match="surprise"):
        decode_snapshot(_json_bytes(document))


@pytest.mark.parametrize(
    ("collection", "path"),
    (("units", "units\\[0\\]\\.surprise"), ("deviations", "deviations\\[0\\]\\.surprise")),
)
def test_unknown_nested_field_names_its_path(collection: str, path: str) -> None:
    document = _baseline_document()
    nested = document[collection]
    assert isinstance(nested, list)
    item = nested[0]
    assert isinstance(item, dict)
    item["surprise"] = True
    with pytest.raises(DecodeError, match=path):
        decode_snapshot(_json_bytes(document))


def test_missing_required_field_names_its_path() -> None:
    document = _baseline_document()
    del document["revision"]
    with pytest.raises(DecodeError, match="revision"):
        decode_snapshot(_json_bytes(document))


@pytest.mark.parametrize("wrong", ("1", True))
def test_wrong_revision_type_names_its_path(wrong: object) -> None:
    document = _baseline_document()
    document["revision"] = wrong
    with pytest.raises(DecodeError, match="revision"):
        decode_snapshot(_json_bytes(document))


@pytest.mark.parametrize(
    ("collection", "path"),
    (("units", "units\\[0\\]\\.status"), ("deviations", "deviations\\[0\\]\\.status")),
)
def test_unknown_status_enum_names_its_path(collection: str, path: str) -> None:
    document = _baseline_document()
    nested = document[collection]
    assert isinstance(nested, list)
    item = nested[0]
    assert isinstance(item, dict)
    item["status"] = "not-a-status"
    with pytest.raises(DecodeError, match=path):
        decode_snapshot(_json_bytes(document))


def test_duplicate_unit_name_names_second_name_path() -> None:
    document = _baseline_document()
    units = document["units"]
    assert isinstance(units, list)
    units.append(dict(units[0]))
    with pytest.raises(DecodeError, match="units\\[1\\]\\.name"):
        decode_snapshot(_json_bytes(document))


def test_duplicate_deviation_id_names_second_id_path() -> None:
    document = _baseline_document()
    deviations = document["deviations"]
    assert isinstance(deviations, list)
    deviations.append(dict(deviations[0]))
    with pytest.raises(DecodeError, match="deviations\\[1\\]\\.deviation_id"):
        decode_snapshot(_json_bytes(document))


def test_duplicate_release_id_names_second_id_path() -> None:
    document = _baseline_document()
    document["releases"] = [_release_document(), _release_document()]
    with pytest.raises(DecodeError, match="releases\\[1\\]\\.release_id"):
        decode_snapshot(_json_bytes(document))


def test_release_invalid_trigger_kind_is_refused() -> None:
    document = _baseline_document()
    release = _release_document()
    closure = release["closure"]
    assert isinstance(closure, dict)
    closure["trigger_kind"] = "mystery"
    document["releases"] = [release]
    with pytest.raises(DecodeError, match="trigger_kind"):
        decode_snapshot(_json_bytes(document))


def test_release_bundle_digest_shape_is_strict() -> None:
    document = _baseline_document()
    release = _release_document()
    release["bundle_inventory_digest"] = "BAD"
    document["releases"] = [release]
    with pytest.raises(DecodeError, match="bundle_inventory_digest"):
        decode_snapshot(_json_bytes(document))


@pytest.mark.parametrize(("nested", "field"), (("criteria", "verification_window"), ("closure", "observations")))
def test_release_missing_nested_field_is_refused(nested: str, field: str) -> None:
    document = _baseline_document()
    release = _release_document()
    value = release[nested]
    assert isinstance(value, dict)
    del value[field]
    document["releases"] = [release]
    with pytest.raises(DecodeError, match=f"{nested}\\.{field}"):
        decode_snapshot(_json_bytes(document))


def test_release_criteria_must_be_object_or_null() -> None:
    document = _baseline_document()
    release = _release_document()
    release["criteria"] = []
    document["releases"] = [release]
    with pytest.raises(DecodeError, match="criteria: expected object"):
        decode_snapshot(_json_bytes(document))


def test_release_includes_must_be_array() -> None:
    document = _baseline_document()
    release = _release_document()
    release["includes"] = {"name": "alpha", "version": "1"}
    document["releases"] = [release]
    with pytest.raises(DecodeError, match="includes: expected array"):
        decode_snapshot(_json_bytes(document))


def test_closure_no_completion_claim_must_be_bool_but_false_decodes() -> None:
    document = _baseline_document()
    release = _release_document()
    closure = release["closure"]
    assert isinstance(closure, dict)
    closure["no_completion_claim"] = "true"
    document["releases"] = [release]
    with pytest.raises(DecodeError, match="no_completion_claim: expected boolean"):
        decode_snapshot(_json_bytes(document))
    closure["no_completion_claim"] = False
    decoded = decode_snapshot(_json_bytes(document))
    assert decoded.releases[0].closure is not None
    assert decoded.releases[0].closure.no_completion_claim is False


def test_negative_revision_names_revision_path() -> None:
    document = _baseline_document()
    document["revision"] = -1
    with pytest.raises(DecodeError, match="revision"):
        decode_snapshot(_json_bytes(document))


def test_duplicate_json_keys_are_refused_at_every_level() -> None:
    top = b'{"anchor_mode":"none","evidence_heads":[],"formal_certificate_digest":"","revision":0,"revision":9,"units":[],"deviations":[],"releases":[]}'
    with pytest.raises(DecodeError, match="duplicate object key 'revision'"):
        decode_snapshot(top)
    nested = (
        b'{"anchor_mode":"none","evidence_heads":[],"formal_certificate_digest":"","revision":0,"deviations":[],"releases":[],"units":[{"name":"a","name":"b",'
        b'"version":"1","status":"specified","depends_on":[],'
        b'"acceptance_pack_version":""}]}'
    )
    with pytest.raises(DecodeError, match="duplicate object key 'name'"):
        decode_snapshot(nested)


def test_non_utf8_encodings_are_refused() -> None:
    document = '{"anchor_mode":"none","evidence_heads":[],"formal_certificate_digest":"","revision":3,"units":[],"deviations":[],"releases":[]}'
    for encoding in ("utf-16", "utf-32"):
        with pytest.raises(DecodeError, match="must be UTF-8"):
            decode_snapshot(document.encode(encoding))


def test_utf8_bom_is_refused() -> None:
    with pytest.raises(DecodeError, match="invalid JSON"):
        decode_snapshot(b'\xef\xbb\xbf{"anchor_mode":"none","evidence_heads":[],"formal_certificate_digest":"","revision":0,"units":[],"deviations":[],"releases":[]}')


def test_duplicate_roster_member_is_refused() -> None:
    document = _baseline_document()
    release = _release_document()
    release["includes"] = [
        {"name": "alpha", "version": "1"},
        {"name": "alpha", "version": "2"},
    ]
    document["releases"] = [release]
    with pytest.raises(DecodeError, match=r"includes\[1\]\.name.*duplicate"):
        decode_snapshot(_json_bytes(document))


def test_unknown_roster_snapshot_status_is_refused() -> None:
    document = _baseline_document()
    release = _release_document()
    closure = release["closure"]
    assert isinstance(closure, dict)
    closure["roster_snapshot"] = [["alpha", "banana"]]
    document["releases"] = [release]
    with pytest.raises(DecodeError, match="unknown roster status 'banana'"):
        decode_snapshot(_json_bytes(document))


def test_phase5_snapshot_round_trip_and_head_refusals() -> None:
    snapshot = Snapshot(
        revision=8,
        evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS"),),
        anchor_mode=AnchorMode.CI,
    )
    assert decode_snapshot(encode_snapshot(snapshot)) == snapshot

    document = json.loads(encode_snapshot(snapshot))
    assert isinstance(document, dict)
    heads = document["evidence_heads"]
    assert isinstance(heads, list)
    heads.append(dict(heads[0]))
    with pytest.raises(DecodeError, match="duplicate unit name"):
        decode_snapshot(_json_bytes(document))


def test_unknown_anchor_mode_is_refused() -> None:
    document = _baseline_document()
    document["anchor_mode"] = "command"
    with pytest.raises(DecodeError, match="anchor_mode"):
        decode_snapshot(_json_bytes(document))


@pytest.mark.parametrize(
    ("field", "wrong", "message"),
    (
        ("head_sequence", 0, "head_sequence"),
        ("head_sequence", True, "expected integer"),
        ("anchored", "bad", "64 lowercase"),
        ("head", "BAD", "64 lowercase"),
    ),
)
def test_evidence_head_field_refusals(
    field: str, wrong: object, message: str
) -> None:
    document = _baseline_document()
    document["evidence_heads"] = [
        {
            "unit_name": "u",
            "head": "a" * 64,
            "head_sequence": 1,
            "anchored": "GENESIS",
        }
    ]
    heads = document["evidence_heads"]
    assert isinstance(heads, list) and isinstance(heads[0], dict)
    cast(dict[str, object], heads[0])[field] = wrong
    with pytest.raises(DecodeError, match=message):
        decode_snapshot(_json_bytes(document))


def _evidence_document() -> dict[str, object]:
    return {
        "unit": {"name": "u", "version": "v1"},
        "sequence": 1,
        "parent": "GENESIS",
        "kind": "red",
        "command_line": "pytest",
        "outcome": "tests-failed",
        "recorded_at": "opaque",
    }


def test_evidence_event_round_trip() -> None:
    event = EvidenceEvent(
        UnitRef("u", "v1"),
        1,
        "GENESIS",
        EvidenceKind.RED,
        "pytest",
        EvidenceOutcome.TESTS_FAILED,
        "opaque",
    )
    assert decode_evidence_event(encode_evidence_event(event)) == event


@pytest.mark.parametrize(
    ("field", "wrong", "message"),
    (
        ("kind", "blue", "kind"),
        ("outcome", "unknown", "outcome"),
        ("sequence", "1", "sequence"),
        ("sequence", True, "sequence"),
        ("sequence", 0, "sequence"),
        ("parent", "bad", "parent"),
        ("command_line", [], "command_line"),
        ("recorded_at", 1, "recorded_at"),
        ("unit", [], "unit"),
    ),
)
def test_evidence_event_bad_fields_are_refused(
    field: str, wrong: object, message: str
) -> None:
    document = _evidence_document()
    document[field] = wrong
    with pytest.raises(DecodeError, match=message):
        decode_evidence_event(_json_bytes(document))


def test_evidence_event_bad_unit_nesting_is_refused() -> None:
    for unit in (
        {"name": 1, "version": "v1"},
        {"name": "u", "version": 1},
        {"name": "u"},
        {"name": "u", "version": "v1", "extra": True},
    ):
        document = _evidence_document()
        document["unit"] = unit
        with pytest.raises(DecodeError, match="unit"):
            decode_evidence_event(_json_bytes(document))


def test_evidence_event_unknown_and_missing_fields_are_refused() -> None:
    document = _evidence_document()
    document["extra"] = True
    with pytest.raises(DecodeError, match="extra"):
        decode_evidence_event(_json_bytes(document))
    del document["extra"]
    del document["recorded_at"]
    with pytest.raises(DecodeError, match="recorded_at"):
        decode_evidence_event(_json_bytes(document))


def _inventory_document() -> dict[str, object]:
    return {
        "entries": [
            {"path": "a.txt", "digest": "a" * 64},
            {"path": "b.txt", "digest": "b" * 64},
        ],
        "exclusions": ["tmp/**"],
    }


def test_bundle_inventory_round_trip() -> None:
    inventory = BundleInventory(
        entries=(InventoryEntry("a", "a" * 64),), exclusions=("tmp/**",)
    )
    assert decode_bundle_inventory(encode_bundle_inventory(inventory)) == inventory


@pytest.mark.parametrize(
    ("paths", "message"),
    (
        (("b", "a"), "sorted"),
        (("a", "a"), "duplicate"),
        (("../a", "b"), "traversal"),
    ),
)
def test_bundle_inventory_path_refusals(
    paths: tuple[str, str], message: str
) -> None:
    document = _inventory_document()
    document["entries"] = [
        {"path": paths[0], "digest": "a" * 64},
        {"path": paths[1], "digest": "b" * 64},
    ]
    with pytest.raises(DecodeError, match=message):
        decode_bundle_inventory(_json_bytes(document))


def test_bundle_inventory_strict_fields_and_digest() -> None:
    document = _inventory_document()
    entries = document["entries"]
    assert isinstance(entries, list) and isinstance(entries[0], dict)
    entries[0]["digest"] = "BAD"
    with pytest.raises(DecodeError, match="digest"):
        decode_bundle_inventory(_json_bytes(document))
    entries[0]["digest"] = "a" * 64
    entries[0]["extra"] = True
    with pytest.raises(DecodeError, match="extra"):
        decode_bundle_inventory(_json_bytes(document))
