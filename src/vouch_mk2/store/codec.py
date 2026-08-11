"""Canonical Snapshot serialization and its single strict JSON ingress."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from vouch_mk2.core.model import (
    Deviation,
    DeviationStatus,
    DeviationType,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.store.errors import DecodeError


def encode_snapshot(snapshot: Snapshot) -> bytes:
    """Encode every Snapshot field as stable canonical JSON plus one newline."""
    document = {
        "deviations": [
            {
                "amendment_version": deviation.amendment_version,
                "deviation_id": deviation.deviation_id,
                "deviation_type": deviation.deviation_type.value,
                "impact_set": list(deviation.impact_set),
                "status": deviation.status.value,
                "unit": {
                    "name": deviation.unit.name,
                    "version": deviation.unit.version,
                },
            }
            for deviation in snapshot.deviations
        ],
        "revision": snapshot.revision,
        "units": [
            {
                "acceptance_pack_version": unit.acceptance_pack_version,
                "depends_on": list(unit.depends_on),
                "name": unit.name,
                "status": unit.status.value,
                "version": unit.version,
            }
            for unit in snapshot.units
        ],
    }
    text = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8") + b"\n"


def _child(path: str, field: str) -> str:
    return field if path == "" else f"{path}.{field}"


def _object(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        raise DecodeError(f"{path or '$'}: expected object")
    raw = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw.items():
        if type(key) is not str:
            raise DecodeError(f"{path or '$'}: expected string field name")
        result[cast(str, key)] = item
    return result


def _array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise DecodeError(f"{path}: expected array")
    return cast(list[object], value)


def _string(value: object, path: str) -> str:
    if type(value) is not str:
        raise DecodeError(f"{path}: expected string")
    return cast(str, value)


def _integer(value: object, path: str) -> int:
    # Exact type equality is intentional: bool is an int subclass in Python.
    if type(value) is not int:
        raise DecodeError(f"{path}: expected integer")
    return cast(int, value)


def _fields(
    value: object,
    path: str,
    required: frozenset[str],
) -> dict[str, object]:
    obj = _object(value, path)
    for field in obj:
        if field not in required:
            raise DecodeError(f"{_child(path, field)}: unknown field")
    for field in sorted(required):
        if field not in obj:
            raise DecodeError(f"{_child(path, field)}: missing required field")
    return obj


def _strings(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )


def _enum_value[
    E
](value: object, path: str, enum_type: Callable[[str], E]) -> E:
    text = _string(value, path)
    try:
        return enum_type(text)
    except ValueError as error:
        raise DecodeError(f"{path}: unknown enum value {text!r}") from error


_UNIT_FIELDS = frozenset(
    {"name", "version", "status", "depends_on", "acceptance_pack_version"}
)
_UNIT_REF_FIELDS = frozenset({"name", "version"})
_DEVIATION_FIELDS = frozenset(
    {
        "deviation_id",
        "deviation_type",
        "unit",
        "status",
        "amendment_version",
        "impact_set",
    }
)
_SNAPSHOT_FIELDS = frozenset({"revision", "units", "deviations"})


def _decode_unit(value: object, path: str) -> Unit:
    obj = _fields(value, path, _UNIT_FIELDS)
    return Unit(
        name=_string(obj["name"], _child(path, "name")),
        version=_string(obj["version"], _child(path, "version")),
        status=_enum_value(obj["status"], _child(path, "status"), UnitStatus),
        depends_on=_strings(obj["depends_on"], _child(path, "depends_on")),
        acceptance_pack_version=_string(
            obj["acceptance_pack_version"],
            _child(path, "acceptance_pack_version"),
        ),
    )


def _decode_unit_ref(value: object, path: str) -> UnitRef:
    obj = _fields(value, path, _UNIT_REF_FIELDS)
    return UnitRef(
        name=_string(obj["name"], _child(path, "name")),
        version=_string(obj["version"], _child(path, "version")),
    )


def _decode_deviation(value: object, path: str) -> Deviation:
    obj = _fields(value, path, _DEVIATION_FIELDS)
    return Deviation(
        deviation_id=_string(
            obj["deviation_id"], _child(path, "deviation_id")
        ),
        deviation_type=_enum_value(
            obj["deviation_type"],
            _child(path, "deviation_type"),
            DeviationType,
        ),
        unit=_decode_unit_ref(obj["unit"], _child(path, "unit")),
        status=_enum_value(
            obj["status"], _child(path, "status"), DeviationStatus
        ),
        amendment_version=_string(
            obj["amendment_version"],
            _child(path, "amendment_version"),
        ),
        impact_set=_strings(obj["impact_set"], _child(path, "impact_set")),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    # json.loads is last-wins on duplicate keys by default — a crafted document
    # could show one value to a human reader and another to the decoder.
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DecodeError(f"$: duplicate object key {key!r}")
        result[key] = value
    return result


def decode_snapshot(data: bytes) -> Snapshot:
    """Strictly decode bytes into Snapshot; no raw JSON escapes this function."""
    # Decode UTF-8 explicitly first: json.loads on bytes BOM-sniffs and would
    # accept UTF-16/32 documents, giving one snapshot many byte encodings at
    # the single ingress (a UTF-8 BOM is also refused, by the JSON parser).
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DecodeError(f"$: state must be UTF-8: {error}") from error
    try:
        raw = cast(
            object, json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        )
    except json.JSONDecodeError as error:
        raise DecodeError(f"$: invalid JSON: {error}") from error

    obj = _fields(raw, "", _SNAPSHOT_FIELDS)
    revision = _integer(obj["revision"], "revision")
    if revision < 0:
        raise DecodeError("revision: must be non-negative")

    units = tuple(
        _decode_unit(item, f"units[{index}]")
        for index, item in enumerate(_array(obj["units"], "units"))
    )
    seen_units: set[str] = set()
    for index, unit in enumerate(units):
        if unit.name in seen_units:
            raise DecodeError(f"units[{index}].name: duplicate unit name {unit.name!r}")
        seen_units.add(unit.name)

    deviations = tuple(
        _decode_deviation(item, f"deviations[{index}]")
        for index, item in enumerate(
            _array(obj["deviations"], "deviations")
        )
    )
    seen_deviations: set[str] = set()
    for index, deviation in enumerate(deviations):
        if deviation.deviation_id in seen_deviations:
            raise DecodeError(
                f"deviations[{index}].deviation_id: duplicate deviation id "
                f"{deviation.deviation_id!r}"
            )
        seen_deviations.add(deviation.deviation_id)

    return Snapshot(revision=revision, units=units, deviations=deviations)
