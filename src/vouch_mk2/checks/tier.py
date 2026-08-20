"""Tier-scoped checks generated from D-12's first-class IR matrix bindings."""

from __future__ import annotations

from vouch_mk2.checks.results import CheckResult
from vouch_mk2.core.model import Snapshot, Unit, UnitStatus
from vouch_mk2.core.invariants import current_pack_evidence_is_green
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Coverage, RequirementLevel, TierCell, TierRequirement, Verdict


_PUBLISHED_STATUSES = frozenset({UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND})


def _unit_label(unit: Unit) -> str:
    return unit.ref().label()


def _requirement(control_id: str) -> TierRequirement:
    for requirement in mk2.MK2.tier_requirements:
        if requirement.control_id == control_id:
            return requirement
    raise LookupError(
        f"mk2 tier matrix has no {control_id!r} control"
    )


def tier_evidence_cell(snapshot: Snapshot) -> TierCell:
    return _requirement(mk2.TIER_EVIDENCE_CONTROL).cell(snapshot.tier)


def tier_check_applies(snapshot: Snapshot, check_id: str) -> bool:
    return any(
        check_id in requirement.cell(snapshot.tier).mechanical_checks
        for requirement in mk2.MK2.tier_requirements
    )


def check_pack_bound(snapshot: Snapshot) -> CheckResult:
    rebound = tuple(
        sorted(
            unit.ref().label()
            for unit in snapshot.units
            if unit.status is not UnitStatus.SUPERSEDED
            and unit.pack_binding == "rebound"
        )
    )
    rebound_details = tuple(
        f"{unit.ref().label()} old inventory {item.old_inventory_digest} -> "
        f"new inventory {item.new_inventory_digest}; old test set "
        f"{item.old_test_set_digest} -> new test set {item.new_test_set_digest}; "
        f"evidence head sequence {item.evidence_head_sequence}; reason {item.reason!r}"
        for unit in sorted(snapshot.units, key=_unit_label)
        if unit.status is not UnitStatus.SUPERSEDED
        for item in unit.rebound_history
    )
    if not tier_check_applies(snapshot, mk2.CHECK_PACK_BOUND):
        lightweight_notices = (
            ()
            if not rebound
            else ("rebound after publication: " + ", ".join(rebound),)
        ) + rebound_details
        return CheckResult(
            check_id=mk2.CHECK_PACK_BOUND,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                f"IR tier matrix: {snapshot.tier.value} label-only pack "
                "binding is permitted"
                + (
                    "; " + "; ".join(lightweight_notices)
                    if lightweight_notices
                    else ""
                )
            ),
        )
    subjects = tuple(
        unit
        for unit in snapshot.units
        if unit.status is not UnitStatus.SUPERSEDED
        and unit.acceptance_pack_version
    )
    if not subjects:
        return CheckResult(
            check_id=mk2.CHECK_PACK_BOUND,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                "IR tier matrix applicability rule not met: subjects-present: "
                "active-units-with-pack-versions"
            ),
        )
    offenders = tuple(
        sorted(
            unit.ref().label()
            for unit in snapshot.units
            if unit.status is not UnitStatus.SUPERSEDED
            and unit.acceptance_pack_version
            and not unit.acceptance_pack_digest
        )
    )
    late = tuple(
        sorted(
            unit.ref().label()
            for unit in snapshot.units
            if unit.status is not UnitStatus.SUPERSEDED
            and unit.pack_binding == "late"
        )
    )
    rebound_without_green = tuple(
        unit.ref().label()
        for unit in snapshot.units
        if unit.status is not UnitStatus.SUPERSEDED
        and unit.pack_binding == "rebound"
        and not current_pack_evidence_is_green(snapshot, unit)
    )
    failures: list[str] = []
    if offenders:
        failures.append(
            f"{snapshot.tier.value}: acceptance packs are unbound: {offenders!r}"
        )
    if snapshot.tier.value == "high" and late:
        failures.append("high: acceptance packs bound late: " + ", ".join(late))
    if snapshot.tier.value == "high" and rebound_without_green:
        failures.append(
            "high: rebound units lack fresh GREEN: "
            + ", ".join(rebound_without_green)
        )
    notices: list[str] = []
    if late:
        notices.append("bound late: " + ", ".join(late))
    if rebound:
        notices.append("rebound after publication: " + ", ".join(rebound))
    notices.extend(rebound_details)
    return CheckResult(
        check_id=mk2.CHECK_PACK_BOUND,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join((*failures, *notices)),
    )


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
    subjects = tuple(
        unit
        for unit in snapshot.units
        if unit.status in _PUBLISHED_STATUSES or unit.published_once
    )
    if not subjects:
        return CheckResult(
            check_id=mk2.CHECK_TIER_EVIDENCE_REQUIRED,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                "IR applicability rule not met: subjects-present: "
                "published-once-units"
            ),
        )
    offenders = tuple(
        sorted(
            unit.ref().label()
            for unit in subjects
            if snapshot.evidence_head_ref(unit.ref()) is None
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
    for unit in sorted(snapshot.units, key=_unit_label):
        if unit.status not in _PUBLISHED_STATUSES and not unit.published_once:
            continue
        head = snapshot.evidence_head_ref(unit.ref())
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


def _legacy_tier_control_detail(snapshot: Snapshot) -> str:
    """Stable context-projection layout; never selected from a store fact."""
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
        rows.append(f"waived at {snapshot.tier.value}: " + ", ".join(waived))
    return "; ".join(rows)


def check_tier_controls(
    snapshot: Snapshot, *, legacy_projection: bool = False
) -> CheckResult:
    if legacy_projection:
        return CheckResult(
            check_id=mk2.ORG_TIER_CONTROLS,
            verdict=Verdict.NOT_CHECKED,
            detail=_legacy_tier_control_detail(snapshot),
        )
    rows: list[str] = []
    for requirement in mk2.MK2.tier_requirements:
        cell = requirement.cell(snapshot.tier)
        qualifiers = tuple(part for part in (cell.scope, cell.detail) if part)
        suffix = f" ({'; '.join(qualifiers)})" if qualifiers else ""
        label = (
            f"{snapshot.tier.value} {requirement.description} "
            f"[{requirement.control_id}] {cell.level.value}{suffix}"
        )
        if cell.level is RequirementLevel.WAIVED:
            rows.append(f"{label} — WAIVED / no obligation")
            continue
        if cell.coverage is Coverage.FULL:
            mechanical = ", ".join(
                cell.mechanical_evidence + cell.mechanical_checks
            )
            rows.append(f"{label} — FULL; mech={mechanical}")
            continue
        if cell.coverage is Coverage.PARTIAL:
            mechanical = ", ".join(
                cell.mechanical_evidence + cell.mechanical_checks
            )
            rows.append(f"{label} — PARTIAL; mech={mechanical}")
            rows.append(
                f"{requirement.description} — residual={cell.residual}"
            )
            continue
        prefix = (
            "trigger evaluation NOT CHECKED; residual requirement: "
            if cell.level is RequirementLevel.RISK_TRIGGERED
            else ""
        )
        rows.append(f"{label} — NOT CHECKED")
        rows.append(
            f"{requirement.description} — residual={prefix}{cell.residual}"
        )
    return CheckResult(
        check_id=mk2.ORG_TIER_CONTROLS,
        verdict=Verdict.NOT_CHECKED,
        detail="\n".join(rows),
    )


__all__ = [
    "check_pack_bound",
    "check_tier_controls",
    "check_tier_evidence_required",
    "check_tier_high_attestation",
    "tier_check_applies",
    "tier_evidence_cell",
]
