from __future__ import annotations

import json

import pytest

from vouch_mk2.core.model import (
    Deviation,
    DeviationStatus,
    DeviationType,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.store import DecodeError, decode_snapshot, encode_snapshot


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
        )
        for index, status in enumerate(DeviationStatus)
    )
    return Snapshot(revision=7, units=units, deviations=deviations)


def _baseline_document() -> dict[str, object]:
    return {
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
            }
        ],
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


def test_negative_revision_names_revision_path() -> None:
    document = _baseline_document()
    document["revision"] = -1
    with pytest.raises(DecodeError, match="revision"):
        decode_snapshot(_json_bytes(document))


def test_duplicate_json_keys_are_refused_at_every_level() -> None:
    top = b'{"revision":0,"revision":9,"units":[],"deviations":[]}'
    with pytest.raises(DecodeError, match="duplicate object key 'revision'"):
        decode_snapshot(top)
    nested = (
        b'{"revision":0,"deviations":[],"units":[{"name":"a","name":"b",'
        b'"version":"1","status":"specified","depends_on":[],'
        b'"acceptance_pack_version":""}]}'
    )
    with pytest.raises(DecodeError, match="duplicate object key 'name'"):
        decode_snapshot(nested)


def test_non_utf8_encodings_are_refused() -> None:
    document = '{"revision":3,"units":[],"deviations":[]}'
    for encoding in ("utf-16", "utf-32"):
        with pytest.raises(DecodeError, match="must be UTF-8"):
            decode_snapshot(document.encode(encoding))


def test_utf8_bom_is_refused() -> None:
    with pytest.raises(DecodeError, match="invalid JSON"):
        decode_snapshot(b'\xef\xbb\xbf{"revision":0,"units":[],"deviations":[]}')
