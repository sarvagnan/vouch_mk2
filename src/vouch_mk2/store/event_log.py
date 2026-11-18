"""Create and atomically commit content-addressed lifecycle event records."""

from __future__ import annotations

import datetime
import getpass
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace

from vouch_mk2.core import events
from vouch_mk2.core.decide import apply
from vouch_mk2.core.events import (
    AcceptanceFrozen,
    AcceptancePackBound,
    AcceptancePackRebound,
    DeviationAmended,
    DeviationOpened,
    Event,
    EventRecord,
    EvidenceRecorded,
    MergeRecorded,
    PreLogHistoryRecorded,
    ReleaseInvalidated,
    ReleasePrepared,
    UnitBlocked,
    UnitInvalidated,
    UnitReleaseBound,
)
from vouch_mk2.core.model import Snapshot, UnitRef
from vouch_mk2.store.codec import encode_event_record
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.errors import StoreError
from vouch_mk2.store.store import Store


type _ReplayRecords = dict[str, tuple[EventRecord, tuple[UnitRef, ...]]]


@dataclass(frozen=True, slots=True)
class ReplayHistory:
    """Physical chain records, their resolved subjects, and the bounds of their claims."""

    # In physical chain order, oldest first.
    records: _ReplayRecords
    source_heads: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReplayProvenance:
    """First positions of exact and carried digests, and heads with complete ancestry."""

    incorporated: dict[str, int]
    carried: dict[str, int]
    complete_heads: dict[str, int]

    def excludes(self, source_head: str, digest: str) -> bool:
        through = self.complete_heads.get(source_head)
        if through is None:
            return False
        return (
            self.incorporated.get(digest, through + 1) > through
            and self.carried.get(digest, through + 1) > through
        )


_COMPANION_KINDS = (UnitBlocked, UnitInvalidated, UnitReleaseBound, ReleaseInvalidated)

_CI_RUN_ID_VARIABLES = (
    "GITHUB_RUN_ID",
    "CI_PIPELINE_ID",
    "CIRCLE_WORKFLOW_ID",
    "BUILDKITE_BUILD_ID",
    "BUILD_BUILDID",
    "JENKINS_BUILD_ID",
    "BUILD_ID",
    "TRAVIS_BUILD_ID",
    "BITBUCKET_PIPELINE_UUID",
    "CODEBUILD_BUILD_ID",
    "DRONE_BUILD_NUMBER",
    "TEAMCITY_BUILD_ID",
)


def default_actor() -> str:
    """Return the OS account claim used when the caller supplies no actor."""
    return getpass.getuser()


def ci_run_id() -> str:
    """Return the first recognized CI-provider run identity claim."""
    return next(
        (os.environ[name] for name in _CI_RUN_ID_VARIABLES if os.environ.get(name)),
        "",
    )


def event_pack_digest(event: Event) -> str:
    """Return the pack digest duplicated in the record envelope, if any."""
    match event:
        case AcceptanceFrozen(pack_digest=digest):
            return digest
        case AcceptancePackBound(pack_digest=digest):
            return digest
        case AcceptancePackRebound(new_pack_digest=digest):
            return digest
        case EvidenceRecorded(event=evidence):
            return evidence.pack_digest
        case _:
            return ""


def build_event_records(
    snapshot: Snapshot,
    events: tuple[Event, ...],
    *,
    actor: str,
    run_id: str,
    recorded_at: str | None = None,
    source_records: tuple[tuple[str, EventRecord] | None, ...] | None = None,
    satisfied: tuple[str, ...] = (),
) -> tuple[Snapshot, tuple[tuple[ArtifactKind, bytes], ...], tuple[EventRecord, ...]]:
    """Fold events, retaining source claims and roots for matched copies.

    During replay, an unmatched event is a generated companion. Its own EVENT
    digest is its root. Later copies carry that digest and inherit the flag.
    """
    if source_records is not None and len(source_records) != len(events):
        raise ValueError("source_records must contain one entry per event")
    timestamp = (
        datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        if recorded_at is None
        else recorded_at
    )
    current = snapshot
    objects: list[tuple[ArtifactKind, bytes]] = []
    records: list[EventRecord] = []
    for index, event in enumerate(events):
        counterpart = None if source_records is None else source_records[index]
        source_digest, source = ("", None) if counterpart is None else counterpart
        revision = (
            event.baseline.revision
            if isinstance(event, PreLogHistoryRecorded)
            else current.revision + 1
        )
        record = EventRecord(
            folded_event=event,
            recorded_at=timestamp if source is None else source.recorded_at,
            prev=current.event_head or "GENESIS",
            revision=revision,
            actor=actor if source is None else source.actor,
            run_id=run_id if source is None else source.run_id,
            pack_digest=event_pack_digest(event),
            origin="" if source is None else source.origin or source_digest,
            origin_format=None if source is None else source.origin_format or source.event_format,
            satisfied=satisfied,
            generated=(source_records is not None) if source is None else source.generated,
        )
        data = encode_event_record(record)
        digest = digest_of(ArtifactKind.EVENT, data)
        current = apply(
            current,
            (event,),
            event_digests=(digest,),
            event_run_ids=(record.run_id,),
        )
        objects.append((ArtifactKind.EVENT, data))
        records.append(record)
    return current, tuple(objects), tuple(records)


def event_unit_references(snapshot: Snapshot, event: events.Event) -> tuple[UnitRef, ...]:
    """Resolve operation targets only; by-name dependency lookups stay unpinned."""
    match event:
        case events.UnitSpecified(name=name, version=version):
            return (UnitRef(name, version),)
        case events.DeviationOpened(unit=ref):
            return (ref,)
        case events.EvidenceRecorded(event=evidence):
            return (evidence.unit,)
        case events.ReleasePrepared(includes=includes):
            return includes
        case events.AnchorAdvanced(unit_name=name, unit_version=version):
            if version:
                return (UnitRef(name, version),)
        case (
            events.AcceptanceFrozen(name=name)
            | events.AcceptancePackBound(name=name)
            | events.AcceptancePackRebound(name=name)
            | events.UnitMarkedReady(name=name)
            | events.WorkStarted(name=name)
            | events.UnitMarkedDone(name=name)
            | events.UnitPublished(name=name)
            | events.UnitSuperseded(name=name)
            | events.UnitRespecified(name=name)
            | events.ReverificationStarted(name=name)
            | events.UnitBlocked(name=name)
            | events.UnitInvalidated(name=name)
            | events.UnitReleaseBound(name=name)
        ):
            pass
        case _:
            return ()
    unit = snapshot.unit(name)
    return () if unit is None else (unit.ref(),)


def event_fact_key(event: events.Event) -> tuple[str, tuple[str, ...]]:
    """Identify correspondence within one decision, independently of ordering."""
    if isinstance(event, events.MergeRecorded):
        subject = (event.ours_head, event.theirs_head)
    elif isinstance(event, events.EvidenceRecorded):
        subject = (event.event.unit.label(),)
    else:
        subject = ()
        for field in ("name", "deviation_id", "release_id", "unit_name"):
            value = getattr(event, field, None)
            if isinstance(value, str):
                subject = (value,)
                break
    return type(event).__name__, subject


def event_batch_length(events: Sequence[Event], index: int) -> int:
    """Count a command's primary and the consecutive companions it emitted."""
    event = events[index]
    if isinstance(event, DeviationOpened):
        companion = events[index + 1] if index + 1 < len(events) else None
        if isinstance(companion, UnitBlocked):
            if companion.name != event.unit.name:
                raise StoreError("open-deviation event batch blocks a different unit")
            return 2
        return 1
    if isinstance(event, DeviationAmended):
        length = 1 + len(event.impact_set) + len(event.release_impact_set)
        if index + length > len(events):
            raise StoreError("amendment event batch is truncated")
        companions = events[index + 1 : index + length]
        unit_count = len(event.impact_set)
        actual_units = tuple(
            item.name
            for item in companions[:unit_count]
            if isinstance(item, UnitInvalidated)
            and item.trigger_deviation_id == event.deviation_id
        )
        if actual_units != tuple(event.impact_set):
            raise StoreError("amendment event batch has invalid unit companions")
        actual_releases = tuple(
            item.release_id
            for item in companions[unit_count:]
            if isinstance(item, ReleaseInvalidated)
        )
        if actual_releases != tuple(event.release_impact_set):
            raise StoreError("amendment event batch has invalid release companions")
        return length
    if isinstance(event, ReleasePrepared):
        length = 1
        included = {reference.name for reference in event.includes}
        while index + length < len(events):
            companion = events[index + length]
            if not isinstance(companion, UnitReleaseBound):
                break
            if companion.name not in included:
                raise StoreError("release preparation has an unrelated bound-unit companion")
            length += 1
        return length
    return 1


def _validate_replay_block(block: Sequence[tuple[str, EventRecord]]) -> None:
    events = tuple(record.folded_event for _, record in block)
    index = 0
    while index < len(block):
        digest, primary = block[index]
        if isinstance(events[index], _COMPANION_KINDS):
            raise StoreError(f"companion event {digest} has no command primary in its merge block")
        if primary.generated:
            raise StoreError(f"generated on event {digest} is invalid for a command primary")
        if not primary.origin:
            raise StoreError(f"command primary {digest} in a version-2 merge block requires an origin")
        end = index + event_batch_length(events, index)
        for digest, companion in block[index + 1 : end]:
            if not companion.origin and not companion.generated:
                raise StoreError(f"replayed companion {digest} requires an origin or generated flag")
        index = end


def validate_replay_placement(chain: Sequence[tuple[str, EventRecord]]) -> dict[str, str]:
    """Check physical replay blocks and return the source head that bounds each claim."""
    positions = {digest: index for index, (digest, _record) in enumerate(chain)}
    source_heads: dict[str, str] = {}
    block_end = -1
    block_start = len(chain)
    block_source_head = ""
    for index in range(len(chain) - 1, -1, -1):
        digest, record = chain[index]
        event = record.folded_event
        if record.event_format == 2 and isinstance(event, MergeRecorded):
            boundary = positions.get(event.ours_head, index)
            if boundary >= index:
                raise StoreError(f"merge record {digest} has no earlier linear ours head")
            if not record.origin:
                block_end = index
                block_start = boundary + 1
                block_source_head = event.theirs_head
                _validate_replay_block(chain[block_start:block_end])
        if (record.origin or record.generated) and not block_start <= index < block_end:
            field = "generated" if record.generated else "origin"
            raise StoreError(f"{field} on event {digest} is outside a version-2 merge block")
        if record.origin or record.satisfied:
            source_heads[digest] = block_source_head
    return source_heads


def _origin_claims(record: EventRecord, units: tuple[UnitRef, ...]) -> tuple[object, ...]:
    event = record.folded_event
    # The receiving boundary changes in every copy. The source head does not.
    if isinstance(event, MergeRecorded):
        subject = (event.theirs_head,)
    elif isinstance(event, events.FormalCertificateRecorded):
        subject = (event.certificate_digest,)
    else:
        _, subject = event_fact_key(event)
    return (
        type(event), subject, units, record.actor, record.run_id, record.recorded_at,
        record.origin_format or record.event_format, record.generated,
    )


def index_replay_provenance(history: ReplayHistory, records: _ReplayRecords) -> ReplayProvenance:
    """Extend each prefix's coverage through exact predecessors and merge parents."""
    incorporated: dict[str, int] = {}
    carried: dict[str, int] = {}
    complete_heads: dict[str, int] = {}
    complete = True
    for position, digest in enumerate(history.records):
        # Each ancestor contributes once, at the first prefix that reaches it.
        pending = [digest]
        while pending:
            ancestor = pending.pop()
            if not ancestor or ancestor == "GENESIS" or ancestor in incorporated:
                continue
            incorporated[ancestor] = position
            entry = records.get(ancestor)
            if entry is None:
                # Subsequent prefixes still depend on this unavailable parent.
                complete = False
                continue
            record, _ = entry
            if record.origin:
                carried.setdefault(record.origin, position)
            for satisfied in record.satisfied:
                carried.setdefault(satisfied, position)
            pending.append(record.prev)
            event = record.folded_event
            if isinstance(event, MergeRecorded):
                pending.extend((event.ours_head, event.theirs_head))
        if complete:
            complete_heads[digest] = position
    return ReplayProvenance(incorporated, carried, complete_heads)


def validate_replay_claims(
    history: ReplayHistory, records: _ReplayRecords, provenance: Sequence[ReplayProvenance],
) -> None:
    """Check known originals and require all claims to occur in complete source histories."""
    # Placement visits each closing marker before the copies it bounds.
    for digest, source_head in history.source_heads.items():
        record, units = history.records[digest]
        origin = records.get(record.origin)
        if origin is not None:
            original, original_units = origin
            if original.origin:
                raise StoreError(f"event {digest} names origin {record.origin}, which is itself a copy")
            if _origin_claims(record, units) != _origin_claims(original, original_units):
                raise StoreError(f"event {digest} disagrees with origin {record.origin}")
        if record.origin and any(index.excludes(source_head, record.origin) for index in provenance):
            raise StoreError(
                f"event {digest} names origin {record.origin} absent from the history "
                f"through source head {source_head}"
            )
        for entry in record.satisfied:
            target = records.get(entry)
            if target is not None:
                satisfied, _ = target
                if satisfied.origin or not isinstance(
                    satisfied.folded_event, (events.OpenInCiCleared, *_COMPANION_KINDS)
                ):
                    raise StoreError(
                        f"event {digest} has satisfied entry {entry} that must name a first "
                        "occurrence of an open-in-CI clear or companion event"
                    )
            if any(index.excludes(source_head, entry) for index in provenance):
                raise StoreError(
                    f"event {digest} has satisfied entry {entry} absent from the history "
                    f"through source head {source_head}"
                )


def commit_events(
    store: Store,
    snapshot: Snapshot,
    events: tuple[Event, ...],
    *,
    actor: str,
    run_id: str,
    objects: Sequence[tuple[ArtifactKind, bytes]] = (),
    recorded_at: str | None = None,
) -> Snapshot:
    """Place supporting objects and event records in the snapshot's one commit."""
    next_snapshot, event_objects, _records = build_event_records(
        snapshot,
        events,
        actor=actor,
        run_id=run_id,
        recorded_at=recorded_at,
    )
    store.commit(
        next_snapshot,
        objects=tuple(objects) + event_objects,
        expected_revision=snapshot.revision,
    )
    return next_snapshot


def build_pre_log_checkpoint(
    snapshot: Snapshot,
    *,
    description: str,
    actor: str,
    run_id: str,
    recorded_at: str | None = None,
) -> tuple[Snapshot, tuple[tuple[ArtifactKind, bytes], ...], tuple[EventRecord, ...]]:
    """Build the one-record checkpoint reserved for attested earlier history."""
    marker = PreLogHistoryRecorded(
        description=description,
        baseline=replace(snapshot, event_head=""),
    )
    return build_event_records(
        Snapshot(),
        (marker,),
        actor=actor,
        run_id=run_id,
        recorded_at=recorded_at,
    )


def commit_pre_log_checkpoint(
    store: Store,
    snapshot: Snapshot,
    *,
    description: str | None = None,
    actor: str | None = None,
    run_id: str | None = None,
    objects: Sequence[tuple[ArtifactKind, bytes]] = (),
    recorded_at: str | None = None,
    expected_revision: int = 0,
    allow_format_upgrade: bool = False,
) -> Snapshot:
    """Commit one explicit attested-history checkpoint from empty log history."""
    baseline = replace(snapshot, event_head="")
    candidate, event_objects, _records = build_pre_log_checkpoint(
        baseline,
        description=(
            f"pre-log revisions 1..{baseline.revision}: caller-attested history"
            if description is None
            else description
        ),
        actor=default_actor() if actor is None else actor,
        run_id=ci_run_id() if run_id is None else run_id,
        recorded_at=recorded_at,
    )
    store.commit(
        candidate,
        objects=tuple(objects) + event_objects,
        expected_revision=expected_revision,
        allow_format_upgrade=allow_format_upgrade,
    )
    return candidate


__all__ = [
    "ReplayHistory",
    "ReplayProvenance",
    "build_event_records",
    "build_pre_log_checkpoint",
    "ci_run_id",
    "commit_events",
    "commit_pre_log_checkpoint",
    "default_actor",
    "event_batch_length",
    "event_fact_key",
    "event_pack_digest",
    "event_unit_references",
    "index_replay_provenance",
    "validate_replay_claims",
    "validate_replay_placement",
]
