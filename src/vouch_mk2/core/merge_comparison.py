"""Compare the state reached by merge replay with the proposed merged state."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import fields

from vouch_mk2.core.model import Snapshot


def _entry_differences[Entry: Hashable](
    collection: str,
    replayed: tuple[Entry, ...],
    merged: tuple[Entry, ...],
    label: Callable[[Entry], str],
) -> tuple[str, ...]:
    replayed_set, merged_set = set(replayed), set(merged)
    replayed_only = {label(entry) for entry in replayed_set - merged_set}
    merged_only = {label(entry) for entry in merged_set - replayed_set}
    return (
        *(f"{collection} {name}: only in replayed state"
          for name in sorted(replayed_only - merged_only)),
        *(f"{collection} {name}: only in MERGED"
          for name in sorted(merged_only - replayed_only)),
        *(f"{collection} {name}: differs between replay and MERGED"
          for name in sorted(replayed_only & merged_only)),
    )


def merge_snapshot_differences(replayed: Snapshot, merged: Snapshot) -> tuple[str, ...]:
    """Compare full entries as sets, and ignore the revision and event head."""
    differences = [
        *_entry_differences(
            "unit", replayed.units, merged.units, lambda unit: unit.ref().label(),
        ),
        *_entry_differences(
            "deviation", replayed.deviations, merged.deviations,
            lambda deviation: deviation.deviation_id,
        ),
        *_entry_differences(
            "release", replayed.releases, merged.releases, lambda release: release.release_id,
        ),
        *_entry_differences(
            "evidence head", replayed.evidence_heads, merged.evidence_heads,
            lambda head: head.ref().label(),
        ),
        *_entry_differences(
            "open-in-CI exception", replayed.ci_exceptions, merged.ci_exceptions,
            lambda exception: exception.unit_name,
        ),
    ]
    for field in fields(Snapshot):
        if field.name in {
            "revision", "event_head", "units", "deviations", "releases",
            "evidence_heads", "ci_exceptions",
        }:
            continue
        expected: object = getattr(replayed, field.name)
        actual: object = getattr(merged, field.name)
        if actual != expected:
            differences.append(f"{field.name}: replayed {expected!r}, MERGED {actual!r}")
    return tuple(differences)
