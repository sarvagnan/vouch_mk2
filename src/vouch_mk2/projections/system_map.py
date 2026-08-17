"""Complete, presentation-free projection of one authoritative snapshot."""

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.core.model import (
    ClosureRecord,
    EvidenceHead,
    LaunchCriteria,
    PackRebound,
    Snapshot,
    UnitRef,
    blocking_deviations,
)
from vouch_mk2.projections.next_actions import NextAction, next_actions
from vouch_mk2.projections.status_facts import StatusFacts, status_facts


@dataclass(frozen=True, slots=True)
class SystemMapUnit:
    name: str
    version: str
    status: str
    depends_on: tuple[str, ...]
    acceptance_pack_version: str
    acceptance_pack_digest: str
    acceptance_test_set_digest: str
    pack_history: tuple[str, ...]
    rebound_history: tuple[PackRebound, ...]
    pack_binding: str
    published_once: bool
    blocking_deviation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SystemMapDeviation:
    deviation_id: str
    deviation_type: str
    unit: UnitRef
    status: str
    amendment_version: str
    impact_set: tuple[str, ...]
    release_impact_set: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SystemMapRelease:
    release_id: str
    archetype: str
    state: str
    includes: tuple[UnitRef, ...]
    criteria: LaunchCriteria | None
    closure: ClosureRecord | None
    bundle_inventory_digest: str


@dataclass(frozen=True, slots=True)
class SystemMap:
    revision: int
    units: tuple[SystemMapUnit, ...]
    producer_edges: tuple[tuple[str, str], ...]
    deviations: tuple[SystemMapDeviation, ...]
    releases: tuple[SystemMapRelease, ...]
    evidence_heads: tuple[EvidenceHead, ...]
    anchor_mode: str
    tier: str
    formal_certificate_digest: str
    next_actions: tuple[NextAction, ...]
    status_facts: StatusFacts


def system_map(snapshot: Snapshot) -> SystemMap:
    actions = next_actions(snapshot)
    units = tuple(
        sorted(
            (
                SystemMapUnit(
                    name=unit.name,
                    version=unit.version,
                    status=unit.status.value,
                    depends_on=unit.depends_on,
                    acceptance_pack_version=unit.acceptance_pack_version,
                    acceptance_pack_digest=unit.acceptance_pack_digest,
                    acceptance_test_set_digest=unit.acceptance_test_set_digest,
                    pack_history=unit.pack_history,
                    rebound_history=unit.rebound_history,
                    pack_binding=unit.pack_binding,
                    published_once=unit.published_once,
                    blocking_deviation_ids=tuple(
                        sorted(
                            deviation.deviation_id
                            for deviation in blocking_deviations(snapshot, unit)
                        )
                    ),
                )
                for unit in snapshot.units
            ),
            key=lambda row: row.name,
        )
    )
    return SystemMap(
        revision=snapshot.revision,
        units=units,
        producer_edges=tuple(
            sorted(
                (dependency, unit.name)
                for unit in snapshot.units
                for dependency in unit.depends_on
            )
        ),
        deviations=tuple(
            sorted(
                (
                    SystemMapDeviation(
                        deviation_id=deviation.deviation_id,
                        deviation_type=deviation.deviation_type.value,
                        unit=deviation.unit,
                        status=deviation.status.value,
                        amendment_version=deviation.amendment_version,
                        impact_set=deviation.impact_set,
                        release_impact_set=deviation.release_impact_set,
                    )
                    for deviation in snapshot.deviations
                ),
                key=lambda row: row.deviation_id,
            )
        ),
        releases=tuple(
            sorted(
                (
                    SystemMapRelease(
                        release_id=release.release_id,
                        archetype=release.archetype.value,
                        state=release.state.value,
                        includes=release.includes,
                        criteria=release.criteria,
                        closure=release.closure,
                        bundle_inventory_digest=release.bundle_inventory_digest,
                    )
                    for release in snapshot.releases
                ),
                key=lambda row: row.release_id,
            )
        ),
        evidence_heads=tuple(
            sorted(
                snapshot.evidence_heads,
                key=lambda head: (head.unit_name, head.unit_version),
            )
        ),
        anchor_mode=snapshot.anchor_mode.value,
        tier=snapshot.tier.value,
        formal_certificate_digest=snapshot.formal_certificate_digest,
        next_actions=actions,
        status_facts=status_facts(snapshot, actions),
    )
