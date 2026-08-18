"""Create and atomically commit content-addressed lifecycle event records."""

from __future__ import annotations

import datetime
import getpass
import os
from collections.abc import Sequence
from dataclasses import replace

from vouch_mk2.core.decide import apply
from vouch_mk2.core.events import (
    AcceptanceFrozen,
    AcceptancePackBound,
    AcceptancePackRebound,
    Event,
    EventRecord,
    EvidenceRecorded,
    PreLogHistoryRecorded,
)
from vouch_mk2.core.model import Snapshot
from vouch_mk2.store.codec import encode_event_record
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.store import Store


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
) -> tuple[Snapshot, tuple[tuple[ArtifactKind, bytes], ...], tuple[EventRecord, ...]]:
    """Fold and envelope events in order, returning the derived snapshot."""
    timestamp = (
        datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        if recorded_at is None
        else recorded_at
    )
    current = snapshot
    objects: list[tuple[ArtifactKind, bytes]] = []
    records: list[EventRecord] = []
    for event in events:
        revision = (
            event.baseline.revision
            if isinstance(event, PreLogHistoryRecorded)
            else current.revision + 1
        )
        record = EventRecord(
            folded_event=event,
            recorded_at=timestamp,
            prev=current.event_head or "GENESIS",
            revision=revision,
            actor=actor,
            run_id=run_id,
            pack_digest=event_pack_digest(event),
        )
        data = encode_event_record(record)
        digest = digest_of(ArtifactKind.EVENT, data)
        current = apply(current, (event,), event_digests=(digest,))
        objects.append((ArtifactKind.EVENT, data))
        records.append(record)
    return current, tuple(objects), tuple(records)


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
    "build_event_records",
    "build_pre_log_checkpoint",
    "ci_run_id",
    "commit_events",
    "commit_pre_log_checkpoint",
    "default_actor",
    "event_pack_digest",
]
