"""Pure aggregate status facts for a snapshot and its derived work."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from vouch_mk2.core.model import Snapshot, UnitStatus
from vouch_mk2.projections.next_actions import NextAction


@dataclass(frozen=True, slots=True)
class StatusFacts:
    unit_counts: tuple[tuple[str, int], ...]
    deviation_counts: tuple[tuple[str, int], ...]
    release_counts: tuple[tuple[str, int], ...]
    evidence_chains: int
    anchored_current: int
    anchor_run_claims: tuple[tuple[str, str], ...]
    anchor_mode: str
    tier: str
    ci_policy: str
    ci_exceptions: tuple[tuple[str, str], ...]
    formal_certificate_recorded: bool
    next_action_count: int
    unbound_pack_units: tuple[str, ...]
    late_bound_pack_units: tuple[str, ...]
    rebound_pack_units: tuple[str, ...]
    pack_rebounds: tuple[str, ...]


def _counts(values: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    counts = Counter(values)
    return tuple(sorted(counts.items()))


def status_facts(
    snapshot: Snapshot, actions: tuple[NextAction, ...]
) -> StatusFacts:
    return StatusFacts(
        unit_counts=_counts(tuple(unit.status.value for unit in snapshot.units)),
        deviation_counts=_counts(
            tuple(deviation.status.value for deviation in snapshot.deviations)
        ),
        release_counts=_counts(
            tuple(release.state.value for release in snapshot.releases)
        ),
        evidence_chains=len(snapshot.evidence_heads),
        anchored_current=sum(
            head.anchored == head.head for head in snapshot.evidence_heads
        ),
        anchor_run_claims=tuple(
            (head.ref().label(), head.anchor_run_id)
            for head in sorted(
                snapshot.evidence_heads,
                key=lambda item: item.ref().label(),
            )
            if head.anchor_run_id
        ),
        anchor_mode=snapshot.anchor_mode.value,
        tier=snapshot.tier.value,
        ci_policy=snapshot.ci_policy.value,
        ci_exceptions=tuple(
            (item.unit_name, item.reason)
            for item in sorted(
                snapshot.ci_exceptions, key=lambda row: row.unit_name
            )
        ),
        formal_certificate_recorded=bool(snapshot.formal_certificate_digest),
        next_action_count=len(actions),
        unbound_pack_units=tuple(
            sorted(
                unit.ref().label()
                for unit in snapshot.units
                if unit.status is not UnitStatus.SUPERSEDED
                and unit.acceptance_pack_version
                and not unit.acceptance_pack_digest
            )
        ),
        late_bound_pack_units=tuple(
            sorted(
                unit.ref().label()
                for unit in snapshot.units
                if unit.status is not UnitStatus.SUPERSEDED
                and unit.pack_binding == "late"
            )
        ),
        rebound_pack_units=tuple(
            sorted(
                unit.ref().label()
                for unit in snapshot.units
                if unit.status is not UnitStatus.SUPERSEDED
                and unit.pack_binding == "rebound"
            )
        ),
        pack_rebounds=tuple(
            f"{unit.ref().label()} "
            f"old_inventory={rebound.old_inventory_digest} "
            f"new_inventory={rebound.new_inventory_digest} "
            f"old_test_set={rebound.old_test_set_digest} "
            f"new_test_set={rebound.new_test_set_digest} "
            f"evidence_head_sequence={rebound.evidence_head_sequence} "
            f"reason={rebound.reason!r}"
            for unit in sorted(snapshot.units, key=lambda item: item.ref().label())
            for rebound in unit.rebound_history
        ),
    )
