"""Pure derivations over immutable evidence records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import assert_never

from vouch_mk2.core.model import (
    EvidenceEvent,
    EvidenceKind,
    EvidencePackSummary,
)


def fold_evidence_pack_summaries(
    events: Iterable[EvidenceEvent],
    *,
    initial: tuple[EvidencePackSummary, ...] = (),
) -> tuple[EvidencePackSummary, ...]:
    """Fold chronological non-legacy events into digest-sorted cache rows."""
    by_digest = {summary.pack_digest: summary for summary in initial}
    for event in events:
        if event.evidence_format == 1:
            continue
        previous = by_digest.get(event.pack_digest)
        red_seen = False if previous is None else previous.red_seen
        green = False if previous is None else previous.green
        match event.kind:
            case EvidenceKind.RED:
                red_seen = True
                green = False
            case EvidenceKind.GREEN:
                green = True
            case EvidenceKind.REFACTOR:
                pass
            case _:
                assert_never(event.kind)
        by_digest[event.pack_digest] = EvidencePackSummary(
            pack_digest=event.pack_digest,
            red_seen=red_seen,
            green=green,
        )
    return tuple(by_digest[digest] for digest in sorted(by_digest))


__all__ = ["fold_evidence_pack_summaries"]
