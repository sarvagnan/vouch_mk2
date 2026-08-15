"""Strict, report-first migration from the Vouch 0.6 ledger shape.

This module is deliberately I/O-side.  The current-format codec remains the
only runtime ingress; migration decodes the old shape, constructs one current
``Snapshot``, and proves that candidate with the current codec and standing
checks before a caller may approve it.
"""

from __future__ import annotations

import json
import os
import stat
from collections import Counter
from dataclasses import dataclass
from typing import cast

from vouch_mk2.checks import MECHANICAL_CHECKS, CheckResult
from vouch_mk2.core import (
    AnchorMode,
    ClosureRecord,
    ClosureTriggerKind,
    Deviation,
    DeviationStatus,
    DeviationType,
    LaunchCriteria,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.init import init_project
from vouch_mk2.store import (
    DecodeError,
    Store,
    StoreError,
    decode_snapshot,
    encode_snapshot,
)


@dataclass(frozen=True, slots=True)
class _LedgerUnit:
    name: str
    version: str
    status: UnitStatus
    depends_on: tuple[str, ...]
    acceptance_pack_version: str
    blocked_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LedgerDeviation:
    deviation_id: str
    deviation_type: DeviationType
    unit_binding: str
    status: DeviationStatus
    amendment_version: str
    impact_set: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LedgerClosure:
    trigger: str | None
    trigger_kind: str | None
    no_completion_claim: bool | None
    roster: tuple[tuple[str, str, str], ...]
    observations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LedgerRelease:
    release_id: str
    archetype: str
    state: ReleaseState
    includes: tuple[UnitRef, ...]
    launch_criteria: str | None
    verification_window: str | None
    rollback_triggers: str | None
    go_no_go_authority: str | None
    closure: _LedgerClosure | None
    bundle_inventory_digest: str | None


@dataclass(frozen=True, slots=True)
class _Counts:
    units: tuple[tuple[str, int], ...]
    deviations: tuple[tuple[str, int], ...]
    releases: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class LedgerDocument:
    """Typed 0.6 content plus diagnostics collected during strict decoding."""

    units: tuple[_LedgerUnit, ...]
    deviations: tuple[_LedgerDeviation, ...]
    releases: tuple[_LedgerRelease, ...]
    warnings: tuple[str, ...]
    dropped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A frozen, reviewable migration candidate.  It carries no write handle."""

    candidate: Snapshot
    warnings: tuple[str, ...]
    dropped: tuple[str, ...]
    refusals: tuple[str, ...]
    before: _Counts


class MigrationRefused(StoreError):
    """An approval was refused without changing the target."""


_TOP_FIELDS = frozenset({"ledger"})
_LEDGER_FIELDS = frozenset(
    {"project", "units", "deviations", "releases", "next_actions"}
)
_PROJECT_FIELDS = frozenset(
    {
        "claim_version",
        "assurance_tier",
        "independence_model",
        "runner_identity",
        "orchestration_identity",
        "committed_map_path",
        "common_pack_version",
        "current_phase",
        "schema_dialects",
        "methodology_events",
    }
)
_METHODOLOGY_FIELDS = frozenset(
    {"sequence", "parent_digest", "event", "delta", "effective_from_release_id"}
)
_UNIT_FIELDS = frozenset(
    {
        "name",
        "class",
        "contract_version",
        "depends_on",
        "independence_model",
        "status",
        "acceptance_pack_version",
        "artifact_digest",
        "gate_results",
        "blocked_on",
        "definition_path",
        "criticality",
        "approval_required",
    }
)
_DEVIATION_FIELDS = frozenset(
    {
        "id",
        "unit",
        "type",
        "frozen_artifact",
        "description",
        "status",
        "amendment_version",
        "impact_set",
    }
)
_RELEASE_FIELDS = frozenset(
    {
        "id",
        "archetype",
        "includes",
        "state",
        "frozen_at",
        "system_map_digest",
        "map_generator_identity",
        "map_input_manifest_digest",
        "launch_criteria",
        "verification_window",
        "rollback_triggers",
        "go_no_go_authority",
        "evaluation_procedure",
        "criteria_digest",
        "bundle_inventory_digest",
        "formal_certificate_digest",
        "closure_record",
        "outcome_deviations",
    }
)
_INCLUDE_FIELDS = frozenset({"name", "version"})
_CLOSURE_FIELDS = frozenset(
    {
        "trigger",
        "trigger_kind",
        "no_completion_claim",
        "roster",
        "observations",
        "retroactive",
    }
)
_ROSTER_FIELDS = frozenset({"name", "version", "status"})
_NEXT_ACTION_FIELDS = frozenset({"unit", "action"})
_PROTECTIVE_STATES = frozenset(
    {
        ReleaseState.RETIRED,
        ReleaseState.ROLLED_BACK,
        ReleaseState.FAILED_UNRECOVERED,
        ReleaseState.INVALIDATED,
    }
)


def _summary(value: object) -> str:
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        rendered = repr(value)
    if len(rendered) > 120:
        return rendered[:117] + "..."
    return rendered


def _child(path: str, field: str) -> str:
    return field if not path else f"{path}.{field}"


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


def _fields(
    value: object,
    path: str,
    known: frozenset[str],
    required: frozenset[str],
    warnings: list[str],
) -> dict[str, object]:
    result = _object(value, path)
    for field in sorted(result):
        if field not in known:
            warnings.append(
                f"{_child(path, field)}: unknown field ignored "
                f"(value={_summary(result[field])})"
            )
    for field in sorted(required):
        if field not in result:
            raise DecodeError(f"{_child(path, field)}: missing required field")
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
    if type(value) is not int:
        raise DecodeError(f"{path}: expected integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise DecodeError(f"{path}: expected boolean")
    return value


def _strings(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )


def _optional_string(fields: dict[str, object], field: str, path: str) -> str | None:
    if field not in fields:
        return None
    return _string(fields[field], _child(path, field))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DecodeError(f"$: duplicate object key {key!r}")
        result[key] = value
    return result


def _decode_json(data: bytes) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DecodeError(f"$: ledger must be UTF-8: {error}") from error
    try:
        return cast(
            object, json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        )
    except json.JSONDecodeError as error:
        raise DecodeError(f"$: invalid JSON: {error}") from error


def _record_drop(dropped: list[str], path: str, value: object) -> None:
    dropped.append(f"{path} = {_summary(value)}")


def _decode_project(
    value: object, warnings: list[str], dropped: list[str]
) -> None:
    path = "ledger.project"
    fields = _fields(
        value,
        path,
        _PROJECT_FIELDS,
        frozenset(
            {
                "claim_version",
                "assurance_tier",
                "independence_model",
                "runner_identity",
                "orchestration_identity",
                "common_pack_version",
                "current_phase",
                "schema_dialects",
                "methodology_events",
            }
        ),
        warnings,
    )
    for field in (
        "claim_version",
        "assurance_tier",
        "independence_model",
        "runner_identity",
        "orchestration_identity",
        "committed_map_path",
        "common_pack_version",
        "current_phase",
    ):
        if field in fields:
            _string(fields[field], _child(path, field))
    _strings(fields["schema_dialects"], f"{path}.schema_dialects")
    events = _array(fields["methodology_events"], f"{path}.methodology_events")
    for index, raw in enumerate(events):
        event_path = f"{path}.methodology_events[{index}]"
        event = _fields(
            raw,
            event_path,
            _METHODOLOGY_FIELDS,
            _METHODOLOGY_FIELDS,
            warnings,
        )
        _integer(event["sequence"], f"{event_path}.sequence")
        for field in _METHODOLOGY_FIELDS - {"sequence"}:
            _string(event[field], _child(event_path, field))
    for field in sorted(fields):
        if field in _PROJECT_FIELDS:
            _record_drop(dropped, _child(path, field), fields[field])


def _decode_unit(
    value: object,
    index: int,
    warnings: list[str],
    dropped: list[str],
) -> _LedgerUnit:
    path = f"ledger.units[{index}]"
    required = _UNIT_FIELDS - {"independence_model"}
    fields = _fields(value, path, _UNIT_FIELDS, required, warnings)
    name = _string(fields["name"], f"{path}.name")
    version = _string(fields["contract_version"], f"{path}.contract_version")
    try:
        status = UnitStatus(_string(fields["status"], f"{path}.status"))
    except ValueError as error:
        raise DecodeError(f"{path}.status: unknown enum value") from error
    depends_on = _strings(fields["depends_on"], f"{path}.depends_on")
    acceptance_pack_version = _string(
        fields["acceptance_pack_version"], f"{path}.acceptance_pack_version"
    )
    blocked_on = _strings(fields["blocked_on"], f"{path}.blocked_on")
    _string(fields["class"], f"{path}.class")
    _string(fields["artifact_digest"], f"{path}.artifact_digest")
    _strings(fields["gate_results"], f"{path}.gate_results")
    _string(fields["definition_path"], f"{path}.definition_path")
    _string(fields["criticality"], f"{path}.criticality")
    _boolean(fields["approval_required"], f"{path}.approval_required")
    if "independence_model" in fields:
        _string(fields["independence_model"], f"{path}.independence_model")
    for field in (
        "class",
        "artifact_digest",
        "gate_results",
        "blocked_on",
        "definition_path",
        "criticality",
        "approval_required",
        "independence_model",
    ):
        if field in fields:
            _record_drop(dropped, _child(path, field), fields[field])
    dropped.append(f"{path}.evidence_head = not migrated for {name}@{version}")
    return _LedgerUnit(
        name,
        version,
        status,
        depends_on,
        acceptance_pack_version,
        blocked_on,
    )


def _decode_deviation(
    value: object,
    index: int,
    warnings: list[str],
    dropped: list[str],
) -> _LedgerDeviation:
    path = f"ledger.deviations[{index}]"
    fields = _fields(
        value, path, _DEVIATION_FIELDS, _DEVIATION_FIELDS, warnings
    )
    try:
        deviation_type = DeviationType(
            _string(fields["type"], f"{path}.type")
        )
    except ValueError as error:
        raise DecodeError(f"{path}.type: unknown enum value") from error
    try:
        status = DeviationStatus(_string(fields["status"], f"{path}.status"))
    except ValueError as error:
        raise DecodeError(f"{path}.status: unknown enum value") from error
    _string(fields["frozen_artifact"], f"{path}.frozen_artifact")
    _string(fields["description"], f"{path}.description")
    _record_drop(dropped, f"{path}.frozen_artifact", fields["frozen_artifact"])
    _record_drop(dropped, f"{path}.description", fields["description"])
    return _LedgerDeviation(
        deviation_id=_string(fields["id"], f"{path}.id"),
        deviation_type=deviation_type,
        unit_binding=_string(fields["unit"], f"{path}.unit"),
        status=status,
        amendment_version=_string(
            fields["amendment_version"], f"{path}.amendment_version"
        ),
        impact_set=_strings(fields["impact_set"], f"{path}.impact_set"),
    )


def _decode_include(
    value: object, path: str, warnings: list[str]
) -> UnitRef:
    fields = _fields(
        value, path, _INCLUDE_FIELDS, _INCLUDE_FIELDS, warnings
    )
    return UnitRef(
        _string(fields["name"], f"{path}.name"),
        _string(fields["version"], f"{path}.version"),
    )


def _decode_closure(
    value: object,
    path: str,
    warnings: list[str],
    dropped: list[str],
) -> _LedgerClosure:
    fields = _fields(value, path, _CLOSURE_FIELDS, frozenset(), warnings)
    trigger = _optional_string(fields, "trigger", path)
    trigger_kind = _optional_string(fields, "trigger_kind", path)
    no_completion_claim = (
        None
        if "no_completion_claim" not in fields
        else _boolean(fields["no_completion_claim"], f"{path}.no_completion_claim")
    )
    roster_rows: list[tuple[str, str, str]] = []
    if "roster" in fields:
        for index, raw in enumerate(_array(fields["roster"], f"{path}.roster")):
            row_path = f"{path}.roster[{index}]"
            row = _fields(
                raw, row_path, _ROSTER_FIELDS, _ROSTER_FIELDS, warnings
            )
            name = _string(row["name"], f"{row_path}.name")
            version = _string(row["version"], f"{row_path}.version")
            status = _string(row["status"], f"{row_path}.status")
            roster_rows.append((name, version, status))
            _record_drop(dropped, f"{row_path}.version", row["version"])
    observations = (
        ()
        if "observations" not in fields
        else _strings(fields["observations"], f"{path}.observations")
    )
    if "retroactive" in fields:
        _boolean(fields["retroactive"], f"{path}.retroactive")
        _record_drop(dropped, f"{path}.retroactive", fields["retroactive"])
    return _LedgerClosure(
        trigger,
        trigger_kind,
        no_completion_claim,
        tuple(roster_rows),
        observations,
    )


def _decode_release(
    value: object,
    index: int,
    warnings: list[str],
    dropped: list[str],
) -> _LedgerRelease:
    path = f"ledger.releases[{index}]"
    fields = _fields(
        value,
        path,
        _RELEASE_FIELDS,
        frozenset({"id", "archetype", "includes", "state"}),
        warnings,
    )
    release_id = _string(fields["id"], f"{path}.id")
    archetype = _string(fields["archetype"], f"{path}.archetype")
    if archetype not in {"operational", "qualified"}:
        raise DecodeError(f"{path}.archetype: unknown enum value {archetype!r}")
    try:
        state = ReleaseState(_string(fields["state"], f"{path}.state"))
    except ValueError as error:
        raise DecodeError(f"{path}.state: unknown enum value") from error
    includes = tuple(
        _decode_include(raw, f"{path}.includes[{item_index}]", warnings)
        for item_index, raw in enumerate(
            _array(fields["includes"], f"{path}.includes")
        )
    )
    closure = (
        None
        if "closure_record" not in fields
        else _decode_closure(
            fields["closure_record"],
            f"{path}.closure_record",
            warnings,
            dropped,
        )
    )
    for field in (
        "frozen_at",
        "system_map_digest",
        "map_generator_identity",
        "map_input_manifest_digest",
        "evaluation_procedure",
        "criteria_digest",
        "formal_certificate_digest",
    ):
        if field in fields:
            _string(fields[field], _child(path, field))
            _record_drop(dropped, _child(path, field), fields[field])
    if "outcome_deviations" in fields:
        _strings(fields["outcome_deviations"], f"{path}.outcome_deviations")
        _record_drop(
            dropped, f"{path}.outcome_deviations", fields["outcome_deviations"]
        )
    return _LedgerRelease(
        release_id=release_id,
        archetype=archetype,
        state=state,
        includes=includes,
        launch_criteria=_optional_string(fields, "launch_criteria", path),
        verification_window=_optional_string(
            fields, "verification_window", path
        ),
        rollback_triggers=_optional_string(fields, "rollback_triggers", path),
        go_no_go_authority=_optional_string(
            fields, "go_no_go_authority", path
        ),
        closure=closure,
        bundle_inventory_digest=_optional_string(
            fields, "bundle_inventory_digest", path
        ),
    )


def _decode_next_actions(
    value: object, warnings: list[str], dropped: list[str]
) -> None:
    path = "ledger.next_actions"
    rows = _array(value, path)
    for index, raw in enumerate(rows):
        row_path = f"{path}[{index}]"
        fields = _fields(
            raw,
            row_path,
            _NEXT_ACTION_FIELDS,
            _NEXT_ACTION_FIELDS,
            warnings,
        )
        _string(fields["unit"], f"{row_path}.unit")
        _string(fields["action"], f"{row_path}.action")
        _record_drop(dropped, row_path, raw)


def read_ledger(data: bytes) -> LedgerDocument:
    """Strictly decode one 0.6 ``{"ledger": {...}}`` document.

    Known fields are type-checked even when D-8 drops them.  Unknown fields at
    every decoded object level are preserved as explicit warnings.
    """
    warnings: list[str] = []
    dropped: list[str] = []
    top = _fields(
        _decode_json(data), "", _TOP_FIELDS, frozenset({"ledger"}), warnings
    )
    ledger = _fields(
        top["ledger"],
        "ledger",
        _LEDGER_FIELDS,
        _LEDGER_FIELDS,
        warnings,
    )
    _decode_project(ledger["project"], warnings, dropped)
    units = tuple(
        _decode_unit(raw, index, warnings, dropped)
        for index, raw in enumerate(_array(ledger["units"], "ledger.units"))
    )
    deviations = tuple(
        _decode_deviation(raw, index, warnings, dropped)
        for index, raw in enumerate(
            _array(ledger["deviations"], "ledger.deviations")
        )
    )
    releases = tuple(
        _decode_release(raw, index, warnings, dropped)
        for index, raw in enumerate(
            _array(ledger["releases"], "ledger.releases")
        )
    )
    _decode_next_actions(ledger["next_actions"], warnings, dropped)
    return LedgerDocument(
        units=units,
        deviations=deviations,
        releases=releases,
        warnings=tuple(warnings),
        dropped=tuple(dropped),
    )


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _split_binding(binding: str) -> UnitRef | None:
    if "@" not in binding:
        return None
    name, version = binding.rsplit("@", 1)
    if not name or not version or "@" in version:
        return None
    return UnitRef(name, version)


def _counts(
    units: tuple[_LedgerUnit, ...] | tuple[Unit, ...],
    deviations: tuple[_LedgerDeviation, ...] | tuple[Deviation, ...],
    releases: tuple[_LedgerRelease, ...] | tuple[Release, ...],
) -> _Counts:
    unit_counts = Counter(unit.status.value for unit in units)
    deviation_counts = Counter(deviation.status.value for deviation in deviations)
    release_counts = Counter(release.state.value for release in releases)
    return _Counts(
        tuple(sorted(unit_counts.items())),
        tuple(sorted(deviation_counts.items())),
        tuple(sorted(release_counts.items())),
    )


def _closure_for(
    release: _LedgerRelease,
    refusals: list[str],
) -> ClosureRecord | None:
    closure = release.closure
    prefix = f"release {release.release_id!r}"
    if release.state in _PROTECTIVE_STATES and closure is None:
        refusals.append(f"{prefix}: protective state has no closure record")
        return None
    if closure is None:
        return None
    invalid = False
    if closure.trigger is None or not closure.trigger:
        refusals.append(f"{prefix}: closure trigger must be non-empty")
        invalid = True
    if closure.trigger_kind is None:
        refusals.append(f"{prefix}: closure trigger_kind is missing")
        invalid = True
    elif closure.trigger_kind not in {kind.value for kind in ClosureTriggerKind}:
        refusals.append(
            f"{prefix}: closure trigger_kind {closure.trigger_kind!r} is outside "
            "the amendment/operator/supersession vocabulary"
        )
        invalid = True
    if closure.no_completion_claim is not True:
        refusals.append(f"{prefix}: no_completion_claim must be true")
        invalid = True
    valid_statuses = {status.value for status in UnitStatus} | {"missing"}
    bad_statuses = tuple(
        sorted({status for _name, _version, status in closure.roster if status not in valid_statuses})
    )
    if bad_statuses:
        refusals.append(f"{prefix}: closure roster has unknown statuses {bad_statuses!r}")
        invalid = True
    if invalid:
        return None
    assert closure.trigger is not None
    assert closure.trigger_kind is not None
    assert closure.no_completion_claim is not None
    return ClosureRecord(
        trigger=closure.trigger,
        trigger_kind=ClosureTriggerKind(closure.trigger_kind),
        roster_snapshot=tuple(
            sorted((name, status) for name, _version, status in closure.roster)
        ),
        no_completion_claim=closure.no_completion_claim,
        observations=closure.observations,
    )


def _criteria_for(
    release: _LedgerRelease, refusals: list[str]
) -> LaunchCriteria | None:
    values = (
        release.launch_criteria,
        release.verification_window,
        release.rollback_triggers,
        release.go_no_go_authority,
    )
    present = sum(value is not None for value in values)
    if present == 0:
        return None
    if present != 4:
        refusals.append(
            f"release {release.release_id!r}: partial criteria; all four fields "
            "must be present or all absent"
        )
        return None
    launch, window, rollback, authority = values
    assert launch is not None
    assert window is not None
    assert rollback is not None
    assert authority is not None
    if not (launch.strip() and window.strip() and rollback.strip() and authority.strip()):
        # Present-but-empty criteria would let start-deploy pass its
        # criteria-before-progression gate on nothing (0.6 I5 lesson).
        refusals.append(
            f"release {release.release_id!r}: criteria fields present but empty"
        )
        return None
    return LaunchCriteria((launch,), window, (rollback,), authority)


def plan_migration(
    doc: LedgerDocument,
    anchor_mode: AnchorMode,
    *,
    drop_qualified: bool,
) -> MigrationPlan:
    """Map D-8 fields into one revision-1 candidate without performing I/O."""
    warnings = list(doc.warnings)
    dropped = list(doc.dropped)
    refusals: list[str] = []
    units = tuple(
        Unit(
            unit.name,
            unit.version,
            unit.status,
            unit.depends_on,
            unit.acceptance_pack_version,
        )
        for unit in doc.units
    )
    deviations: list[Deviation] = []
    for legacy in doc.deviations:
        ref = _split_binding(legacy.unit_binding)
        if ref is None:
            refusals.append(
                f"deviation {legacy.deviation_id!r}: bare or malformed unit "
                f"binding {legacy.unit_binding!r}; expected NAME@VERSION"
            )
            continue
        impact: list[str] = []
        release_impact: list[str] = []
        for item in legacy.impact_set:
            if item.startswith("release:"):
                release_impact.append(item.removeprefix("release:"))
            else:
                impact.append(item)
        deviations.append(
            Deviation(
                legacy.deviation_id,
                legacy.deviation_type,
                ref,
                legacy.status,
                legacy.amendment_version,
                tuple(impact),
                tuple(release_impact),
            )
        )

    releases: list[Release] = []
    for legacy in doc.releases:
        closure = _closure_for(legacy, refusals)
        criteria = _criteria_for(legacy, refusals)
        if legacy.archetype == "qualified":
            if drop_qualified:
                dropped.append(
                    f"release {legacy.release_id!r} = qualified archetype omitted "
                    f"(state={legacy.state.value!r})"
                )
            else:
                refusals.append(
                    f"release {legacy.release_id!r}: qualified archetype is not "
                    "representable; pass --drop-qualified to omit it"
                )
            continue
        digest = legacy.bundle_inventory_digest or ""
        if digest and not _valid_digest(digest):
            warnings.append(
                f"release {legacy.release_id!r}: bundle_inventory_digest "
                f"{digest!r} is not 64 lowercase hex and was dropped"
            )
            dropped.append(
                f"release {legacy.release_id!r}.bundle_inventory_digest = "
                f"{_summary(digest)}"
            )
            digest = ""
        releases.append(
            Release(
                release_id=legacy.release_id,
                archetype=ReleaseArchetype.OPERATIONAL,
                state=legacy.state,
                includes=legacy.includes,
                criteria=criteria,
                closure=closure,
                bundle_inventory_digest=digest,
            )
        )

    candidate = Snapshot(
        revision=1,
        units=units,
        deviations=tuple(deviations),
        releases=tuple(releases),
        evidence_heads=(),
        anchor_mode=anchor_mode,
        formal_certificate_digest="",
    )
    for legacy in doc.units:
        ref = UnitRef(legacy.name, legacy.version)
        derived = tuple(
            sorted(
                deviation.deviation_id
                for deviation in candidate.deviations
                if deviation.unit == ref
                and deviation.status
                in {DeviationStatus.OPEN, DeviationStatus.REJECTED}
            )
        )
        stored = tuple(sorted(legacy.blocked_on))
        if stored != derived:
            warnings.append(
                f"unit {ref.label()!r}: stored blocked_on {stored!r} diverges "
                f"from derived blockers {derived!r}"
            )
    return MigrationPlan(
        candidate=candidate,
        warnings=tuple(warnings),
        dropped=tuple(dropped),
        refusals=tuple(refusals),
        before=_counts(doc.units, doc.deviations, doc.releases),
    )


def validate_candidate(snapshot: Snapshot) -> tuple[CheckResult, ...]:
    """Round-trip the candidate, then run only standing mechanical checks.

    Artifact checks honestly require a store.  They are therefore absent here
    and run after approval through the normal ``vouch-mk2 check`` command.  The
    registry-completeness meta-check is also excluded because its expected
    missing-artifact failure would describe the dry-run environment, not the
    candidate.
    """
    results: list[CheckResult] = []
    try:
        decoded = decode_snapshot(encode_snapshot(snapshot))
    except StoreError as error:
        results.append(
            CheckResult("migration.snapshot-round-trip", Verdict.FAIL, str(error))
        )
    else:
        results.append(
            CheckResult(
                "migration.snapshot-round-trip",
                Verdict.PASS if decoded == snapshot else Verdict.FAIL,
                "" if decoded == snapshot else "decoded snapshot differs from candidate",
            )
        )
    for check_id in sorted(MECHANICAL_CHECKS):
        if check_id == mk2.CHECK_REGISTRY_COMPLETE:
            continue
        results.append(MECHANICAL_CHECKS[check_id](snapshot))
    return tuple(results)


def _count_map(rows: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return dict(rows)


def _count_lines(before: _Counts, snapshot: Snapshot) -> tuple[str, ...]:
    after = _counts(snapshot.units, snapshot.deviations, snapshot.releases)
    lines: list[str] = []
    for entity, old_rows, new_rows in (
        ("unit", before.units, after.units),
        ("deviation", before.deviations, after.deviations),
        ("release", before.releases, after.releases),
    ):
        old = _count_map(old_rows)
        new = _count_map(new_rows)
        for state in sorted(set(old) | set(new)):
            lines.append(f"| {entity} | {state} | {old.get(state, 0)} | {new.get(state, 0)} |")
    return tuple(lines)


def _section(lines: list[str], title: str, rows: tuple[str, ...]) -> None:
    lines.extend(("", f"## {title}", ""))
    if rows:
        lines.extend(f"- {row}" for row in rows)
    else:
        lines.append("- None")


def render_diff(
    plan: MigrationPlan, validation: tuple[CheckResult, ...]
) -> str:
    """Render the complete review surface used by both dry-run and approval."""
    lines = [
        "# Vouch 0.6 to mk2 migration report",
        "",
        "Artifact checks require the approved store and run via `vouch-mk2 check`.",
        "",
        "## Entity counts by status/state",
        "",
        "| entity | status/state | before | after |",
        "|---|---|---:|---:|",
        *_count_lines(plan.before, plan.candidate),
    ]
    _section(lines, "Warnings", plan.warnings)
    _section(lines, "Dropped fields and entities", plan.dropped)
    _section(lines, "Refusals", plan.refusals)
    failures = tuple(
        f"{result.check_id}: {result.detail or 'failed without detail'}"
        for result in validation
        if result.verdict is Verdict.FAIL
    )
    _section(lines, "Validation FAIL rows", failures)
    return "\n".join(lines) + "\n"


def _require_target(target_root: str) -> str:
    root = os.path.abspath(target_root)
    try:
        info = os.lstat(root)
    except OSError as error:
        raise StoreError(f"{root}: cannot inspect migration target: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StoreError(f"{root}: migration target must be a non-symlink directory")
    store_path = os.path.join(root, ".vouch")
    try:
        os.lstat(store_path)
    except FileNotFoundError:
        return store_path
    except OSError as error:
        raise StoreError(f"{store_path}: cannot inspect target store: {error}") from error
    raise MigrationRefused(f"{store_path}: store already exists; migration refused")


def apply_migration(
    plan: MigrationPlan, target_root: str, lock_bytes: bytes | None
) -> str:
    """Approve one valid plan into a fresh store and persist its report."""
    if plan.refusals:
        raise MigrationRefused(
            f"migration has {len(plan.refusals)} refusal(s); target was not changed"
        )
    validation = validate_candidate(plan.candidate)
    # A candidate that cannot round-trip through the codec would persist an
    # UNDECODABLE state.json — that is never approvable. Standing-check FAILs
    # that record genuine 0.6 data defects (e.g. an impact set naming a unit
    # that never existed) ARE approvable: they are reported, and the store
    # stays fully readable and checkable — the migrator does not launder data.
    unreadable = tuple(
        result
        for result in validation
        if result.check_id == "migration.snapshot-round-trip"
        and result.verdict is not Verdict.PASS
    )
    if unreadable:
        raise MigrationRefused(
            "candidate does not round-trip through the mk2 codec; target was not "
            f"changed: {unreadable[0].detail}"
        )
    store_path = _require_target(target_root)
    report = render_diff(plan, validation).encode("utf-8")
    if lock_bytes is None:
        store = Store.init(store_path)
    else:
        init_project(target_root, lock_bytes)
        store = Store.open(store_path)
    with store:
        store.commit(plan.candidate)
        store.write_generated("migration-report.md", report)
        return store.generated_path("migration-report.md")


def _read_config_anchor(data: bytes) -> AnchorMode:
    """Decode the one policy-bearing 0.6 config field used by the CLI."""
    root = _object(_decode_json(data), "")
    anchor_value = root.get("anchor")
    if anchor_value is None:
        return AnchorMode.NONE
    anchor = _object(anchor_value, "anchor")
    mode_value = anchor.get("mode")
    if mode_value is None:
        return AnchorMode.NONE
    mode = _string(mode_value, "anchor.mode")
    try:
        return AnchorMode(mode)
    except ValueError as error:
        raise DecodeError(f"anchor.mode: unknown enum value {mode!r}") from error


__all__ = [
    "LedgerDocument",
    "MigrationPlan",
    "MigrationRefused",
    "apply_migration",
    "plan_migration",
    "read_ledger",
    "render_diff",
    "validate_candidate",
]
