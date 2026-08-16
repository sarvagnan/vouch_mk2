"""Pure aggregate status facts for a snapshot and its derived work."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from vouch_mk2.core.model import Snapshot
from vouch_mk2.projections.next_actions import NextAction


@dataclass(frozen=True, slots=True)
class StatusFacts:
    unit_counts: tuple[tuple[str, int], ...]
    deviation_counts: tuple[tuple[str, int], ...]
    release_counts: tuple[tuple[str, int], ...]
    evidence_chains: int
    anchored_current: int
    anchor_mode: str
    tier: str
    formal_certificate_recorded: bool
    next_action_count: int


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
        anchor_mode=snapshot.anchor_mode.value,
        tier=snapshot.tier.value,
        formal_certificate_recorded=bool(snapshot.formal_certificate_digest),
        next_action_count=len(actions),
    )
