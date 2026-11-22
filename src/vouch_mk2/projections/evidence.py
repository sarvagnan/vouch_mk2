"""Pure view of a unit's recorded evidence and current-pack witnesses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

from vouch_mk2.core.invariants import current_pack_evidence_is_green
from vouch_mk2.core.model import (
    EvidenceEvent, EvidenceKind, Snapshot, TestOutcome, TestReport, Unit,
)


class EvidenceRecordView(TypedDict):
    sequence: int
    kind: str
    outcome: str
    test_set: str
    command: str
    recorded_at: str
    digest: str
    counts: Literal["red", "green", ""]
    test_report: str


class TestComparison(TypedDict):
    relation: Literal["equal", "superset", "subset", "overlap", "disjoint"]
    red_only: list[str]
    green_only: list[str]
    red_failures_not_passed: list[str]


class UnitEvidenceView(TypedDict):
    unit: str
    status: str
    current_test_set: str
    current_pack_green: bool
    records: list[EvidenceRecordView]
    notes: list[str]
    chain_error: str
    test_comparison: TestComparison | None


class EvidenceView(TypedDict):
    revision: int
    units: list[UnitEvidenceView]


def evidence_witnesses(
    snapshot: Snapshot,
    unit: Unit,
    newest_first: tuple[tuple[str, EvidenceEvent], ...],
) -> tuple[EvidenceEvent | None, EvidenceEvent | None]:
    if not current_pack_evidence_is_green(snapshot, unit):
        return None, None
    test_set = unit.acceptance_test_set_digest
    green: EvidenceEvent | None = None
    for _, event in newest_first:
        if not event.pack_digest or event.pack_digest != test_set:
            continue
        if green is None and event.kind is EvidenceKind.GREEN:
            green = event
        elif green is not None and event.kind is EvidenceKind.RED:
            return event, green
    return None, green


def _compare_reports(red: TestReport, green: TestReport) -> TestComparison:
    red_only = sorted(red.keys() - green.keys())
    green_only = sorted(green.keys() - red.keys())
    if not red_only and not green_only:
        relation = "equal"
    elif not red_only:
        relation = "superset"
    elif not green_only:
        relation = "subset"
    elif len(red_only) == len(red):
        relation = "disjoint"
    else:
        relation = "overlap"
    return {
        "relation": relation,
        "red_only": red_only,
        "green_only": green_only,
        "red_failures_not_passed": sorted(
            test_id for test_id, outcome in red.items()
            if outcome in (TestOutcome.FAILED, TestOutcome.ERROR)
            and green.get(test_id) is not TestOutcome.PASSED
        ),
    }


def project_unit_evidence(
    snapshot: Snapshot,
    unit: Unit,
    newest_first: tuple[tuple[str, EvidenceEvent], ...],
    *,
    chain_error: str = "",
    reports: Mapping[str, TestReport] | None = None,
) -> UnitEvidenceView:
    current_green = current_pack_evidence_is_green(snapshot, unit)
    test_set = unit.acceptance_test_set_digest
    if chain_error:
        newest_first = ()
    red, green = evidence_witnesses(snapshot, unit, newest_first)
    notes: list[str] = []
    comparison = None
    if red is not None and green is not None:
        if red.command_line != green.command_line:
            notes.append("GREEN ran a different command from RED")
        if reports is not None and red.test_report and green.test_report:
            comparison = _compare_reports(reports[red.test_report], reports[green.test_report])
            if comparison["red_only"]:
                notes.append("GREEN's report lacks tests RED's report lists")
            if comparison["red_failures_not_passed"]:
                notes.append("a test that failed in RED's report did not pass in GREEN's report")
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
            "test_report": event.test_report,
        })
    return {
        "unit": unit.ref().label(),
        "status": unit.status.value,
        "current_test_set": test_set,
        "current_pack_green": current_green,
        "records": records,
        "notes": notes,
        "chain_error": chain_error,
        "test_comparison": comparison,
    }
