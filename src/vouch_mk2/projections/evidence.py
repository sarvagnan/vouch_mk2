"""Pure view of a unit's recorded evidence and current-pack witnesses."""

from __future__ import annotations

from typing import Literal, TypedDict

from vouch_mk2.core.invariants import current_pack_evidence_is_green
from vouch_mk2.core.model import EvidenceEvent, EvidenceKind, Snapshot, Unit


class EvidenceRecordView(TypedDict):
    sequence: int
    kind: str
    outcome: str
    test_set: str
    command: str
    recorded_at: str
    digest: str
    counts: Literal["red", "green", ""]


class UnitEvidenceView(TypedDict):
    unit: str
    status: str
    current_test_set: str
    current_pack_green: bool
    records: list[EvidenceRecordView]
    notes: list[str]
    chain_error: str


class EvidenceView(TypedDict):
    revision: int
    units: list[UnitEvidenceView]


def _witnesses(
    newest_first: tuple[tuple[str, EvidenceEvent], ...],
    test_set: str,
) -> tuple[EvidenceEvent | None, EvidenceEvent | None]:
    green: EvidenceEvent | None = None
    for _, event in newest_first:
        if not event.pack_digest or event.pack_digest != test_set:
            continue
        if green is None and event.kind is EvidenceKind.GREEN:
            green = event
        elif green is not None and event.kind is EvidenceKind.RED:
            return event, green
    return None, green


def project_unit_evidence(
    snapshot: Snapshot,
    unit: Unit,
    newest_first: tuple[tuple[str, EvidenceEvent], ...],
    *,
    chain_error: str = "",
) -> UnitEvidenceView:
    current_green = current_pack_evidence_is_green(snapshot, unit)
    test_set = unit.acceptance_test_set_digest
    if chain_error:
        newest_first = ()
    red, green = _witnesses(newest_first, test_set) if current_green else (None, None)
    notes: list[str] = []
    if red is not None and green is not None and red.command_line != green.command_line:
        notes.append("GREEN ran a different command from RED")
    records: list[EvidenceRecordView] = []
    for digest, event in reversed(newest_first):
        counts: Literal["red", "green", ""] = ""
        if event is red:
            counts = "red"
        elif event is green:
            counts = "green"
        records.append({
            "sequence": event.sequence,
            "kind": event.kind.value,
            "outcome": event.outcome.value,
            "test_set": event.pack_digest,
            "command": event.command_line,
            "recorded_at": event.recorded_at,
            "digest": digest,
            "counts": counts,
        })
    return {
        "unit": unit.ref().label(),
        "status": unit.status.value,
        "current_test_set": test_set,
        "current_pack_green": current_green,
        "records": records,
        "notes": notes,
        "chain_error": chain_error,
    }
