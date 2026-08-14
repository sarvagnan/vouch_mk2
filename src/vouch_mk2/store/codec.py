"""Canonical Snapshot serialization and its single strict JSON ingress.

The state format is pre-1.0 and DISPOSABLE: fields are added without migration
and older state.json files become undecodable by design. The one supported
migration path is Phase 9's ``vouch-migrate`` (from Vouch 0.6.x); mk2-to-mk2
format stability begins at the first usable release, not before."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

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
from vouch_mk2.formal.certificate import FormalCertificate
from vouch_mk2.formal.explore import EmittedTables, Relation
from vouch_mk2.store.errors import DecodeError, StoreError
from vouch_mk2.store.paths import RelPath


def _canonical_json(document: object) -> bytes:
    text = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8") + b"\n"


def encode_snapshot(snapshot: Snapshot) -> bytes:
    """Encode every Snapshot field as stable canonical JSON plus one newline."""
    document = {
        "anchor_mode": snapshot.anchor_mode.value,
        "formal_certificate_digest": snapshot.formal_certificate_digest,
        "deviations": [
            {
                "amendment_version": deviation.amendment_version,
                "deviation_id": deviation.deviation_id,
                "deviation_type": deviation.deviation_type.value,
                "impact_set": list(deviation.impact_set),
                "release_impact_set": list(deviation.release_impact_set),
                "status": deviation.status.value,
                "unit": {
                    "name": deviation.unit.name,
                    "version": deviation.unit.version,
                },
            }
            for deviation in snapshot.deviations
        ],
        "revision": snapshot.revision,
        "evidence_heads": [
            {
                "anchored": head.anchored,
                "head": head.head,
                "head_sequence": head.head_sequence,
                "unit_name": head.unit_name,
            }
            for head in snapshot.evidence_heads
        ],
        "releases": [
            {
                "archetype": release.archetype.value,
                "closure": (
                    None
                    if release.closure is None
                    else {
                        "no_completion_claim": (
                            release.closure.no_completion_claim
                        ),
                        "observations": list(release.closure.observations),
                        "roster_snapshot": [
                            [name, status]
                            for name, status in release.closure.roster_snapshot
                        ],
                        "trigger": release.closure.trigger,
                        "trigger_kind": release.closure.trigger_kind.value,
                    }
                ),
                "criteria": (
                    None
                    if release.criteria is None
                    else {
                        "go_no_go_authority": (
                            release.criteria.go_no_go_authority
                        ),
                        "launch_criteria": list(
                            release.criteria.launch_criteria
                        ),
                        "rollback_triggers": list(
                            release.criteria.rollback_triggers
                        ),
                        "verification_window": (
                            release.criteria.verification_window
                        ),
                    }
                ),
                "includes": [
                    {"name": ref.name, "version": ref.version}
                    for ref in release.includes
                ],
                "release_id": release.release_id,
                "state": release.state.value,
                "bundle_inventory_digest": release.bundle_inventory_digest,
            }
            for release in snapshot.releases
        ],
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
    return _canonical_json(document)


def encode_evidence_event(event: EvidenceEvent) -> bytes:
    return _canonical_json(
        {
            "command_line": event.command_line,
            "kind": event.kind.value,
            "outcome": event.outcome.value,
            "parent": event.parent,
            "recorded_at": event.recorded_at,
            "sequence": event.sequence,
            "unit": {"name": event.unit.name, "version": event.unit.version},
        }
    )


def encode_bundle_inventory(inventory: BundleInventory) -> bytes:
    # Enforce the decoder's invariants at encode time too: a supported API
    # must never durably commit an artifact that can never be read back.
    previous = ""
    for entry in inventory.entries:
        RelPath.parse(entry.path)
        if entry.path <= previous and previous:
            raise StoreError(
                f"inventory entries must be strictly sorted by path; "
                f"{entry.path!r} follows {previous!r}"
            )
        previous = entry.path
    return _canonical_json(
        {
            "entries": [
                {"digest": entry.digest, "path": entry.path}
                for entry in inventory.entries
            ],
            "exclusions": list(inventory.exclusions),
        }
    )


def encode_formal_tables(tables: EmittedTables) -> bytes:
    return _canonical_json(tables)


def encode_formal_relation(relation: Relation) -> bytes:
    return _canonical_json(
        {
            "initial": relation.initial,
            "states": list(relation.states),
            "transitions": [list(row) for row in relation.transitions],
        }
    )


def encode_formal_certificate(certificate: FormalCertificate) -> bytes:
    return _canonical_json(
        {
            "checker_version": certificate.checker_version,
            "invariants": [
                [invariant_id, held]
                for invariant_id, held in sorted(certificate.invariants)
            ],
            "relation_digest": certificate.relation_digest,
            "state_count": certificate.state_count,
            "tables_digest": certificate.tables_digest,
            "transition_count": certificate.transition_count,
            "universe_name": certificate.universe_name,
            "witnesses": [
                [witness_id, invariant_id, discriminated]
                for witness_id, invariant_id, discriminated in sorted(
                    certificate.witnesses
                )
            ],
        }
    )


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
        result[key] = item
    return result


def _array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise DecodeError(f"{path}: expected array")
    return cast(list[object], value)


def _string(value: object, path: str) -> str:
    if type(value) is not str:
        raise DecodeError(f"{path}: expected string")
    return value


def _integer(value: object, path: str) -> int:
    # Exact type equality is intentional: bool is an int subclass in Python.
    if type(value) is not int:
        raise DecodeError(f"{path}: expected integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise DecodeError(f"{path}: expected boolean")
    return value


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
        "release_impact_set",
    }
)
_CRITERIA_FIELDS = frozenset(
    {
        "launch_criteria",
        "verification_window",
        "rollback_triggers",
        "go_no_go_authority",
    }
)
_CLOSURE_FIELDS = frozenset(
    {
        "trigger",
        "trigger_kind",
        "roster_snapshot",
        "no_completion_claim",
        "observations",
    }
)
_RELEASE_FIELDS = frozenset(
    {
        "release_id",
        "archetype",
        "state",
        "includes",
        "criteria",
        "closure",
        "bundle_inventory_digest",
    }
)
_EVIDENCE_HEAD_FIELDS = frozenset(
    {"unit_name", "head", "head_sequence", "anchored"}
)
_EVIDENCE_EVENT_FIELDS = frozenset(
    {"unit", "sequence", "parent", "kind", "command_line", "outcome", "recorded_at"}
)
_INVENTORY_ENTRY_FIELDS = frozenset({"path", "digest"})
_BUNDLE_INVENTORY_FIELDS = frozenset({"entries", "exclusions"})
_FORMAL_CERTIFICATE_FIELDS = frozenset(
    {
        "universe_name",
        "checker_version",
        "tables_digest",
        "relation_digest",
        "state_count",
        "transition_count",
        "invariants",
        "witnesses",
    }
)
_SNAPSHOT_FIELDS = frozenset(
    {
        "revision",
        "units",
        "deviations",
        "releases",
        "evidence_heads",
        "anchor_mode",
        "formal_certificate_digest",
    }
)


def _digest(value: object, path: str, *, allow_empty: bool = False) -> str:
    digest = _string(value, path)
    if allow_empty and digest == "":
        return digest
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise DecodeError(f"{path}: expected 64 lowercase hexadecimal characters")
    return digest


def _parent(value: object, path: str) -> str:
    parent = _string(value, path)
    if parent == "GENESIS":
        return parent
    return _digest(parent, path)


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
        release_impact_set=_strings(
            obj["release_impact_set"], _child(path, "release_impact_set")
        ),
    )


def _decode_criteria(value: object, path: str) -> LaunchCriteria | None:
    if value is None:
        return None
    obj = _fields(value, path, _CRITERIA_FIELDS)
    return LaunchCriteria(
        launch_criteria=_strings(
            obj["launch_criteria"], _child(path, "launch_criteria")
        ),
        verification_window=_string(
            obj["verification_window"], _child(path, "verification_window")
        ),
        rollback_triggers=_strings(
            obj["rollback_triggers"], _child(path, "rollback_triggers")
        ),
        go_no_go_authority=_string(
            obj["go_no_go_authority"], _child(path, "go_no_go_authority")
        ),
    )


# Roster snapshots record each member's status at closure time; "missing" is
# the honest marker for a member that was dangling when the record was taken.
_ROSTER_SNAPSHOT_STATUSES = frozenset(
    status.value for status in UnitStatus
) | {"missing"}


def _decode_roster_snapshot(
    value: object, path: str
) -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    for index, raw_entry in enumerate(_array(value, path)):
        entry_path = f"{path}[{index}]"
        pair = _array(raw_entry, entry_path)
        if len(pair) != 2:
            raise DecodeError(f"{entry_path}: expected two-item array")
        status = _string(pair[1], f"{entry_path}[1]")
        if status not in _ROSTER_SNAPSHOT_STATUSES:
            raise DecodeError(
                f"{entry_path}[1]: unknown roster status {status!r}"
            )
        entries.append((_string(pair[0], f"{entry_path}[0]"), status))
    return tuple(entries)


def _decode_closure(value: object, path: str) -> ClosureRecord | None:
    if value is None:
        return None
    obj = _fields(value, path, _CLOSURE_FIELDS)
    return ClosureRecord(
        trigger=_string(obj["trigger"], _child(path, "trigger")),
        trigger_kind=_enum_value(
            obj["trigger_kind"],
            _child(path, "trigger_kind"),
            ClosureTriggerKind,
        ),
        roster_snapshot=_decode_roster_snapshot(
            obj["roster_snapshot"], _child(path, "roster_snapshot")
        ),
        no_completion_claim=_boolean(
            obj["no_completion_claim"], _child(path, "no_completion_claim")
        ),
        observations=_strings(
            obj["observations"], _child(path, "observations")
        ),
    )


def _decode_release(value: object, path: str) -> Release:
    obj = _fields(value, path, _RELEASE_FIELDS)
    includes_path = _child(path, "includes")
    includes = tuple(
        _decode_unit_ref(item, f"{includes_path}[{index}]")
        for index, item in enumerate(_array(obj["includes"], includes_path))
    )
    seen_members: set[str] = set()
    for index, ref in enumerate(includes):
        if ref.name in seen_members:
            raise DecodeError(
                f"{includes_path}[{index}].name: duplicate roster member "
                f"{ref.name!r}"
            )
        seen_members.add(ref.name)
    return Release(
        release_id=_string(obj["release_id"], _child(path, "release_id")),
        archetype=_enum_value(
            obj["archetype"], _child(path, "archetype"), ReleaseArchetype
        ),
        state=_enum_value(obj["state"], _child(path, "state"), ReleaseState),
        includes=includes,
        criteria=_decode_criteria(obj["criteria"], _child(path, "criteria")),
        closure=_decode_closure(obj["closure"], _child(path, "closure")),
        bundle_inventory_digest=_digest(
            obj["bundle_inventory_digest"],
            _child(path, "bundle_inventory_digest"),
            allow_empty=True,
        ),
    )


def _decode_evidence_head(value: object, path: str) -> EvidenceHead:
    obj = _fields(value, path, _EVIDENCE_HEAD_FIELDS)
    sequence = _integer(obj["head_sequence"], _child(path, "head_sequence"))
    if sequence < 1:
        raise DecodeError(f"{_child(path, 'head_sequence')}: must be at least 1")
    unit_name = _string(obj["unit_name"], _child(path, "unit_name"))
    if not unit_name:
        raise DecodeError(f"{_child(path, 'unit_name')}: must be non-empty")
    return EvidenceHead(
        unit_name=unit_name,
        head=_digest(obj["head"], _child(path, "head")),
        head_sequence=sequence,
        anchored=_parent(obj["anchored"], _child(path, "anchored")),
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


def _decode_json(data: bytes, artifact: str) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DecodeError(f"$: {artifact} must be UTF-8: {error}") from error
    try:
        return cast(
            object, json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        )
    except json.JSONDecodeError as error:
        raise DecodeError(f"$: invalid JSON: {error}") from error


def decode_evidence_event(data: bytes) -> EvidenceEvent:
    obj = _fields(_decode_json(data, "evidence event"), "", _EVIDENCE_EVENT_FIELDS)
    sequence = _integer(obj["sequence"], "sequence")
    if sequence < 1:
        raise DecodeError("sequence: must be at least 1")
    event = EvidenceEvent(
        unit=_decode_unit_ref(obj["unit"], "unit"),
        sequence=sequence,
        parent=_parent(obj["parent"], "parent"),
        kind=_enum_value(obj["kind"], "kind", EvidenceKind),
        command_line=_string(obj["command_line"], "command_line"),
        outcome=_enum_value(obj["outcome"], "outcome", EvidenceOutcome),
        recorded_at=_string(obj["recorded_at"], "recorded_at"),
    )
    # Content-addressed artifacts must have exactly ONE byte representation per
    # logical value, or digest identity is not event identity.
    if encode_evidence_event(event) != data:
        raise DecodeError("$: non-canonical evidence event encoding")
    return event


def decode_bundle_inventory(data: bytes) -> BundleInventory:
    obj = _fields(
        _decode_json(data, "bundle inventory"), "", _BUNDLE_INVENTORY_FIELDS
    )
    entries: list[InventoryEntry] = []
    seen_paths: set[str] = set()
    previous = ""
    for index, raw in enumerate(_array(obj["entries"], "entries")):
        path = f"entries[{index}]"
        fields = _fields(raw, path, _INVENTORY_ENTRY_FIELDS)
        text = _string(fields["path"], f"{path}.path")
        try:
            RelPath.parse(text)
        except StoreError as error:
            raise DecodeError(f"{path}.path: {error}") from error
        if text in seen_paths:
            raise DecodeError(f"{path}.path: duplicate path {text!r}")
        if index > 0 and text < previous:
            raise DecodeError(f"{path}.path: entries must be sorted by path")
        seen_paths.add(text)
        previous = text
        entries.append(
            InventoryEntry(
                path=text,
                digest=_digest(fields["digest"], f"{path}.digest"),
            )
        )
    inventory = BundleInventory(
        entries=tuple(entries), exclusions=_strings(obj["exclusions"], "exclusions")
    )
    if encode_bundle_inventory(inventory) != data:
        raise DecodeError("$: non-canonical bundle inventory encoding")
    return inventory


def decode_formal_certificate(data: bytes) -> FormalCertificate:
    obj = _fields(
        _decode_json(data, "formal certificate"),
        "",
        _FORMAL_CERTIFICATE_FIELDS,
    )
    state_count = _integer(obj["state_count"], "state_count")
    transition_count = _integer(obj["transition_count"], "transition_count")
    if state_count < 0:
        raise DecodeError("state_count: must be non-negative")
    if transition_count < 0:
        raise DecodeError("transition_count: must be non-negative")
    invariants: list[tuple[str, bool]] = []
    for index, raw in enumerate(_array(obj["invariants"], "invariants")):
        path = f"invariants[{index}]"
        pair = _array(raw, path)
        if len(pair) != 2:
            raise DecodeError(f"{path}: expected two-item array")
        invariants.append(
            (
                _string(pair[0], f"{path}[0]"),
                _boolean(pair[1], f"{path}[1]"),
            )
        )
    witnesses: list[tuple[str, str, bool]] = []
    for index, raw in enumerate(_array(obj["witnesses"], "witnesses")):
        path = f"witnesses[{index}]"
        triple = _array(raw, path)
        if len(triple) != 3:
            raise DecodeError(f"{path}: expected three-item array")
        witnesses.append(
            (
                _string(triple[0], f"{path}[0]"),
                _string(triple[1], f"{path}[1]"),
                _boolean(triple[2], f"{path}[2]"),
            )
        )
    certificate = FormalCertificate(
        universe_name=_string(obj["universe_name"], "universe_name"),
        checker_version=_string(obj["checker_version"], "checker_version"),
        tables_digest=_digest(obj["tables_digest"], "tables_digest"),
        relation_digest=_digest(obj["relation_digest"], "relation_digest"),
        state_count=state_count,
        transition_count=transition_count,
        invariants=tuple(invariants),
        witnesses=tuple(witnesses),
    )
    if encode_formal_certificate(certificate) != data:
        raise DecodeError("$: non-canonical formal certificate encoding")
    return certificate


def decode_snapshot(data: bytes) -> Snapshot:
    """Strictly decode bytes into Snapshot; no raw JSON escapes this function."""
    # Decode UTF-8 explicitly first: json.loads on bytes BOM-sniffs and would
    # accept UTF-16/32 documents, giving one snapshot many byte encodings at
    # the single ingress (a UTF-8 BOM is also refused, by the JSON parser).
    obj = _fields(_decode_json(data, "state"), "", _SNAPSHOT_FIELDS)
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

    releases = tuple(
        _decode_release(item, f"releases[{index}]")
        for index, item in enumerate(_array(obj["releases"], "releases"))
    )
    seen_releases: set[str] = set()
    for index, release in enumerate(releases):
        if release.release_id in seen_releases:
            raise DecodeError(
                f"releases[{index}].release_id: duplicate release id "
                f"{release.release_id!r}"
            )
        seen_releases.add(release.release_id)

    evidence_heads = tuple(
        _decode_evidence_head(item, f"evidence_heads[{index}]")
        for index, item in enumerate(
            _array(obj["evidence_heads"], "evidence_heads")
        )
    )
    seen_heads: set[str] = set()
    for index, head in enumerate(evidence_heads):
        if head.unit_name in seen_heads:
            raise DecodeError(
                f"evidence_heads[{index}].unit_name: duplicate unit name "
                f"{head.unit_name!r}"
            )
        seen_heads.add(head.unit_name)

    return Snapshot(
        revision=revision,
        units=units,
        deviations=deviations,
        releases=releases,
        evidence_heads=evidence_heads,
        anchor_mode=_enum_value(obj["anchor_mode"], "anchor_mode", AnchorMode),
        formal_certificate_digest=_digest(
            obj["formal_certificate_digest"],
            "formal_certificate_digest",
            allow_empty=True,
        ),
    )
