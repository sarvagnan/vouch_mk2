"""Tier-scoped checks generated from D-12's first-class IR matrix bindings."""

from __future__ import annotations

from vouch_mk2.checks.results import CheckResult
from vouch_mk2.core.model import Snapshot, UnitStatus
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Coverage, RequirementLevel, TierCell, TierRequirement, Verdict


_PUBLISHED_STATUSES = frozenset({UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND})


def _evidence_requirement() -> TierRequirement:
    for requirement in mk2.MK2.tier_requirements:
        if requirement.control_id == mk2.TIER_EVIDENCE_CONTROL:
            return requirement
    raise LookupError(
        f"mk2 tier matrix has no {mk2.TIER_EVIDENCE_CONTROL!r} control"
    )


def tier_evidence_cell(snapshot: Snapshot) -> TierCell:
    return _evidence_requirement().cell(snapshot.tier)


def tier_check_applies(snapshot: Snapshot, check_id: str) -> bool:
    return check_id in tier_evidence_cell(snapshot).mechanical_checks


def check_tier_evidence_required(snapshot: Snapshot) -> CheckResult:
    if not tier_check_applies(snapshot, mk2.CHECK_TIER_EVIDENCE_REQUIRED):
        return CheckResult(
            check_id=mk2.CHECK_TIER_EVIDENCE_REQUIRED,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                f"IR tier matrix: {snapshot.tier.value} "
                f"{mk2.TIER_EVIDENCE_CONTROL} does not require evidence chains"
            ),
        )
    offenders = tuple(
        sorted(
            unit.ref().label()
            for unit in snapshot.units
            if (unit.status in _PUBLISHED_STATUSES or unit.published_once)
            and snapshot.evidence_head(unit.name) is None
        )
    )
    return CheckResult(
        check_id=mk2.CHECK_TIER_EVIDENCE_REQUIRED,
        verdict=Verdict.FAIL if offenders else Verdict.PASS,
        detail=(
            ""
            if not offenders
            else f"{snapshot.tier.value}: published units lack evidence heads: {offenders!r}"
        ),
    )


def check_tier_high_attestation(snapshot: Snapshot) -> CheckResult:
    if not tier_check_applies(snapshot, mk2.CHECK_TIER_HIGH_ATTESTATION):
        return CheckResult(
            check_id=mk2.CHECK_TIER_HIGH_ATTESTATION,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                f"IR tier matrix: {snapshot.tier.value} "
                f"{mk2.TIER_EVIDENCE_CONTROL} does not require high attestation"
            ),
        )
    failures: list[str] = []
    for unit in sorted(snapshot.units, key=lambda item: item.ref().label()):
        if unit.status not in _PUBLISHED_STATUSES and not unit.published_once:
            continue
        head = snapshot.evidence_head(unit.name)
        if head is None:
            failures.append(f"unit {unit.ref().label()!r} has no chain to anchor")
        elif head.anchored == "GENESIS":
            failures.append(f"unit {unit.ref().label()!r} remains at GENESIS anchor")
    if not snapshot.formal_certificate_digest:
        failures.append("formal certificate is not recorded")
    return CheckResult(
        check_id=mk2.CHECK_TIER_HIGH_ATTESTATION,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join(failures),
    )


def check_tier_controls(snapshot: Snapshot) -> CheckResult:
    rows: list[str] = []
    waived: list[str] = []
    for requirement in mk2.MK2.tier_requirements:
        cell = requirement.cell(snapshot.tier)
        qualifiers = tuple(part for part in (cell.scope, cell.detail) if part)
        suffix = f" ({'; '.join(qualifiers)})" if qualifiers else ""
        label = (
            f"{snapshot.tier.value}: {requirement.description} "
            f"[{requirement.control_id}] is {cell.level.value}{suffix}"
        )
        if cell.level is RequirementLevel.WAIVED:
            waived.append(f"{requirement.description} [{requirement.control_id}]")
            continue
        if cell.coverage is Coverage.FULL:
            continue
        if cell.coverage is Coverage.PARTIAL:
            mechanical = ", ".join(
                cell.mechanical_evidence + cell.mechanical_checks
            )
            rows.append(
                f"{label} — PARTIALLY CHECKED (mechanical: {mechanical}) "
                f"residual: {cell.residual}"
            )
            continue
        prefix = (
            "trigger evaluation NOT CHECKED; residual requirement: "
            if cell.level is RequirementLevel.RISK_TRIGGERED
            else ""
        )
        rows.append(f"{label} — NOT CHECKED: {prefix}{cell.residual}")
    if waived:
        rows.append(
            f"waived at {snapshot.tier.value}: " + ", ".join(waived)
        )
    return CheckResult(
        check_id=mk2.ORG_TIER_CONTROLS,
        verdict=Verdict.NOT_CHECKED,
        detail="; ".join(rows),
    )


__all__ = [
    "check_tier_controls",
    "check_tier_evidence_required",
    "check_tier_high_attestation",
    "tier_check_applies",
    "tier_evidence_cell",
]
