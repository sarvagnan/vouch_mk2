"""Semantic branch merge by replaying recorded commands through ``decide``."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vouch_mk2.core import commands as commands
from vouch_mk2.core import events as events
from vouch_mk2.core.decide import Accepted, Refused, apply, decide
from vouch_mk2.core.model import PackInventory, Snapshot
from vouch_mk2.ir.schema import Violation
from vouch_mk2.store.codec import (
    decode_bundle_inventory,
    decode_evidence_event,
    decode_event_record,
    decode_formal_certificate,
    decode_pack_inventory,
    encode_snapshot,
)
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.errors import StoreError
from vouch_mk2.store.event_log import build_event_records
from vouch_mk2.store.store import Store


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """The committed merge shape reported by the CLI."""

    mode: str
    snapshot: Snapshot
    replayed_records: int


@dataclass(frozen=True, slots=True)
class _LoggedEvent:
    digest: str
    payload: bytes
    record: events.EventRecord


def _read_verified_chain(
    store: Store, snapshot: Snapshot, branch: str
) -> tuple[_LoggedEvent, ...]:
    """Load a chain in chronological order and prove it derives its snapshot."""
    if not snapshot.event_head:
        if snapshot == Snapshot():
            return ()
        raise StoreError(f"merge.{branch}-history-invalid: non-empty snapshot has no event head")

    reverse_chain: list[_LoggedEvent] = []
    digest = snapshot.event_head
    expected_revision = snapshot.revision
    visited: set[str] = set()
    while digest != "GENESIS":
        if digest in visited:
            raise StoreError(f"merge.{branch}-history-invalid: event chain cycle at {digest!r}")
        visited.add(digest)
        payload = store.get_object(ArtifactKind.EVENT, digest)
        record = decode_event_record(payload)
        if record.revision != expected_revision:
            raise StoreError(
                f"merge.{branch}-history-invalid: revision {expected_revision} "
                f"records revision {record.revision}"
            )
        reverse_chain.append(_LoggedEvent(digest, payload, record))
        digest = record.prev
        expected_revision = record.revision - 1

    chain = tuple(reversed(reverse_chain))
    replay = Snapshot()
    for item in chain:
        try:
            replay = apply(
                replay,
                (item.record.folded_event,),
                event_digests=(item.digest,),
                event_run_ids=(item.record.run_id,),
            )
        except (LookupError, ValueError) as error:
            raise StoreError(
                f"merge.{branch}-history-invalid: revision "
                f"{item.record.revision} cannot replay: {error}"
            ) from error
    if encode_snapshot(replace(replay, event_head="")) != encode_snapshot(
        replace(snapshot, event_head="")
    ) or replay.event_head != snapshot.event_head:
        raise StoreError(
            f"merge.{branch}-history-invalid: event chain does not replay to snapshot"
        )
    return chain


def _event_label(event: events.Event) -> str:
    subject = ""
    for field in ("name", "deviation_id", "release_id", "unit_name"):
        value = getattr(event, field, None)
        if isinstance(value, str):
            subject = f" {field}={value!r}"
            break
    if isinstance(event, events.EvidenceRecorded):
        subject = f" unit={event.event.unit.label()!r}"
    return f"{type(event).__name__}{subject}"


def _conflict(item: _LoggedEvent, refusal: Refused) -> Refused:
    reasons = "; ".join(
        f"{violation.code}: {violation.detail}" for violation in refusal.violations
    )
    return Refused(
        violations=(
            Violation(
                code="merge.conflict",
                detail=(
                    f"event {_event_label(item.record.folded_event)} from revision "
                    f"{item.record.revision} was refused by decide(): {reasons}"
                ),
            ),
        )
    )


def _pack_inventory(store: Store, digest: str) -> PackInventory:
    return decode_pack_inventory(store.get_object(ArtifactKind.ACCEPTANCE_PACK, digest))


def _command_for_event(
    store: Store, event: events.Event, *, run_id: str = ""
) -> commands.Command | commands.RecordMerge:
    """Recover the command claim represented by a command's first event."""
    match event:
        case events.UnitSpecified(name=name, version=version, depends_on=depends_on):
            return commands.SpecifyUnit(name, version, depends_on)
        case events.AcceptanceFrozen(
            name=name,
            pack_version=pack_version,
            pack_digest=pack_digest,
            test_set_digest=test_set_digest,
        ):
            return commands.FreezeAcceptance(
                name, pack_version, pack_digest, test_set_digest
            )
        case events.AcceptancePackBound(
            name=name, pack_digest=pack_digest, test_set_digest=test_set_digest
        ):
            return commands.BindPack(name, pack_digest, test_set_digest)
        case events.AcceptancePackRebound(
            name=name,
            old_pack_digest=old_digest,
            new_pack_digest=new_digest,
            new_test_set_digest=test_set_digest,
            reason=reason,
        ):
            return commands.RebindPack(
                name=name,
                pack_digest=new_digest,
                test_set_digest=test_set_digest,
                reason=reason,
                old_inventory=_pack_inventory(store, old_digest),
                new_inventory=_pack_inventory(store, new_digest),
            )
        case events.UnitMarkedReady(name=name):
            return commands.MarkReady(name)
        case events.WorkStarted(name=name):
            return commands.StartWork(name)
        case events.UnitMarkedDone(name=name):
            return commands.MarkDone(name)
        case events.UnitPublished(name=name):
            return commands.Publish(name)
        case events.UnitSuperseded(name=name):
            return commands.Supersede(name)
        case events.UnitRespecified(name=name):
            return commands.Respecify(name)
        case events.ReverificationStarted(name=name):
            return commands.BeginReverify(name)
        case events.DeviationOpened(
            deviation_id=deviation_id, deviation_type=deviation_type, unit=unit
        ):
            return commands.OpenDeviation(deviation_id, deviation_type, unit)
        case events.DeviationAmended(
            deviation_id=deviation_id, amendment_version=amendment_version
        ):
            return commands.AmendDeviation(deviation_id, amendment_version)
        case events.DeviationRejected(deviation_id=deviation_id):
            return commands.RejectDeviation(deviation_id)
        case events.DeviationClosed(deviation_id=deviation_id):
            return commands.CloseDeviation(deviation_id)
        case events.ReleasePrepared(release_id=release_id, includes=includes):
            return commands.PrepareRelease(
                release_id, tuple(reference.name for reference in includes)
            )
        case events.ReleaseCriteriaSet(release_id=release_id, criteria=criteria):
            return commands.SetReleaseCriteria(release_id, criteria)
        case events.BundleBound(release_id=release_id, inventory_digest=digest):
            return commands.BindBundle(release_id, digest)
        case events.EvidenceRecorded(event=evidence, event_digest=digest):
            return commands.RecordEvidence(evidence, digest)
        case events.AnchorAdvanced(unit_name=name):
            return commands.AdvanceAnchor(name, run_id)
        case events.AnchorModeSet(mode=mode):
            return commands.SetAnchorMode(mode)
        case events.TierSet(tier=tier):
            return commands.SetTier(tier)
        case events.CiPolicySet(policy=policy):
            return commands.SetCiPolicy(policy)
        case events.OpenInCiMarked(name=name, reason=reason):
            return commands.MarkOpenInCi(name, reason)
        case events.OpenInCiCleared(name=name):
            return commands.ClearOpenInCi(name)
        case events.FormalCertificateRecorded(certificate_digest=digest):
            return commands.RecordFormalCertificate(digest)
        case events.MergeRecorded(ours_head=ours_head, theirs_head=theirs_head):
            return commands.RecordMerge(ours_head, theirs_head)
        case events.DeploymentStarted(release_id=release_id):
            return commands.StartDeploy(release_id)
        case events.VerificationStarted(release_id=release_id):
            return commands.StartVerification(release_id)
        case events.ReleaseSucceeded(release_id=release_id):
            return commands.CompleteRelease(release_id)
        case events.ReleaseRolledBack(release_id=release_id, closure=closure):
            return commands.RollBack(release_id, closure.trigger, closure.observations)
        case events.ReleaseFailedUnrecovered(release_id=release_id, closure=closure):
            return commands.MarkFailedUnrecovered(
                release_id, closure.trigger, closure.observations
            )
        case events.ReleaseRetired(release_id=release_id, closure=closure):
            return commands.RetireRelease(
                release_id, closure.trigger, closure.observations
            )
        case (
            events.PreLogHistoryRecorded()
            | events.UnitBlocked()
            | events.UnitInvalidated()
            | events.UnitReleaseBound()
            | events.ReleaseInvalidated()
        ):
            raise StoreError(
                f"event {_event_label(event)} is not a command-leading event"
            )


def _source_batch_length(chain: tuple[_LoggedEvent, ...], index: int) -> int:
    """Skip companion facts emitted by the source command's one decision."""
    event = chain[index].record.folded_event
    if isinstance(event, events.DeviationOpened):
        companion = (
            None
            if index + 1 >= len(chain)
            else chain[index + 1].record.folded_event
        )
        if isinstance(companion, events.UnitBlocked):
            if companion.name != event.unit.name:
                raise StoreError("open-deviation event batch blocks a different unit")
            return 2
        return 1
    if isinstance(event, events.DeviationAmended):
        length = 1 + len(event.impact_set) + len(event.release_impact_set)
        if index + length > len(chain):
            raise StoreError("amendment event batch is truncated")
        companions = tuple(
            item.record.folded_event for item in chain[index + 1 : index + length]
        )
        unit_count = len(event.impact_set)
        expected_units = tuple(event.impact_set)
        actual_units = tuple(
            item.name
            for item in companions[:unit_count]
            if isinstance(item, events.UnitInvalidated)
            and item.trigger_deviation_id == event.deviation_id
        )
        if actual_units != expected_units:
            raise StoreError("amendment event batch has invalid unit companions")
        expected_releases = tuple(event.release_impact_set)
        actual_releases = tuple(
            item.release_id
            for item in companions[unit_count:]
            if isinstance(item, events.ReleaseInvalidated)
        )
        if actual_releases != expected_releases:
            raise StoreError("amendment event batch has invalid release companions")
        return length
    if isinstance(event, events.ReleasePrepared):
        length = 1
        included = {reference.name for reference in event.includes}
        while index + length < len(chain):
            companion = chain[index + length].record.folded_event
            if not isinstance(companion, events.UnitReleaseBound):
                break
            if companion.name not in included:
                raise StoreError("release preparation has an unrelated bound-unit companion")
            length += 1
        return length
    return 1


def _provenance_ancestors(
    head: str,
    *chains: tuple[_LoggedEvent, ...],
) -> frozenset[str]:
    """Return linear and recorded-merge ancestors visible in either branch."""
    records = {item.digest: item for chain in chains for item in chain}
    found: set[str] = set()
    pending = [head]
    while pending:
        digest = pending.pop()
        if not digest or digest == "GENESIS" or digest in found:
            continue
        found.add(digest)
        item = records.get(digest)
        if item is None:
            continue
        if item.record.prev != "GENESIS":
            pending.append(item.record.prev)
        event = item.record.folded_event
        if isinstance(event, events.MergeRecorded):
            pending.extend((event.ours_head, event.theirs_head))
    return frozenset(found)


def _source_records_to_replay(
    chain: tuple[_LoggedEvent, ...],
    incorporated: frozenset[str],
) -> tuple[_LoggedEvent, ...]:
    """Exclude source records already represented by our provenance DAG.

    A merge record closes the linear block replayed from ``theirs_head`` after
    ``ours_head``.  When our branch already incorporates that auxiliary parent,
    the block is provenance, not new semantic work (the reverse-merge case).
    """
    positions = {item.digest: index for index, item in enumerate(chain)}
    incorporated_positions = tuple(
        index for index, item in enumerate(chain) if item.digest in incorporated
    )
    if not incorporated_positions:
        return chain
    start = max(incorporated_positions) + 1
    skipped: set[int] = set()
    for index in range(start, len(chain)):
        event = chain[index].record.folded_event
        if not isinstance(event, events.MergeRecorded):
            continue
        if event.theirs_head not in incorporated:
            continue
        ours_index = positions.get(event.ours_head)
        if ours_index is None or ours_index >= index:
            raise StoreError(
                "merge.theirs-history-invalid: merge provenance does not name "
                "an earlier linear ours head"
            )
        skipped.update(range(max(start, ours_index + 1), index + 1))
    return tuple(
        item
        for index, item in enumerate(chain[start:], start=start)
        if index not in skipped
    )


def _snapshot_supporting_references(snapshot: Snapshot) -> set[tuple[ArtifactKind, str]]:
    references: set[tuple[ArtifactKind, str]] = set()
    for unit in snapshot.units:
        if unit.acceptance_pack_digest:
            references.add(
                (ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest)
            )
        for rebound in unit.rebound_history:
            references.add(
                (ArtifactKind.ACCEPTANCE_PACK, rebound.old_inventory_digest)
            )
            references.add(
                (ArtifactKind.ACCEPTANCE_PACK, rebound.new_inventory_digest)
            )
    for release in snapshot.releases:
        if release.bundle_inventory_digest:
            references.add(
                (ArtifactKind.BUNDLE_INVENTORY, release.bundle_inventory_digest)
            )
    for head in snapshot.evidence_heads:
        if head.head != "GENESIS":
            references.add((ArtifactKind.EVIDENCE, head.head))
    if snapshot.formal_certificate_digest:
        references.add(
            (ArtifactKind.FORMAL_CERTIFICATE, snapshot.formal_certificate_digest)
        )
    return references


def _event_supporting_references(
    event: events.Event,
) -> set[tuple[ArtifactKind, str]]:
    match event:
        case events.PreLogHistoryRecorded(baseline=baseline):
            return _snapshot_supporting_references(baseline)
        case events.AcceptanceFrozen(pack_digest=digest) if digest:
            return {(ArtifactKind.ACCEPTANCE_PACK, digest)}
        case events.AcceptancePackBound(pack_digest=digest):
            return {(ArtifactKind.ACCEPTANCE_PACK, digest)}
        case events.AcceptancePackRebound(
            old_pack_digest=old_digest, new_pack_digest=new_digest
        ):
            return {
                (ArtifactKind.ACCEPTANCE_PACK, old_digest),
                (ArtifactKind.ACCEPTANCE_PACK, new_digest),
            }
        case events.BundleBound(inventory_digest=digest):
            return {(ArtifactKind.BUNDLE_INVENTORY, digest)}
        case events.EvidenceRecorded(event_digest=digest):
            return {(ArtifactKind.EVIDENCE, digest)}
        case events.FormalCertificateRecorded(certificate_digest=digest):
            return {(ArtifactKind.FORMAL_CERTIFICATE, digest)}
        case _:
            return set()


def _supporting_objects(
    ours: Store,
    theirs: Store,
    candidate: Snapshot,
    source_records: tuple[_LoggedEvent, ...],
) -> tuple[tuple[ArtifactKind, bytes], ...]:
    """Resolve and copy only non-event objects reached by the merged chain."""
    pending = _snapshot_supporting_references(candidate)
    for item in source_records:
        pending.update(_event_supporting_references(item.record.folded_event))

    availability: dict[tuple[int, ArtifactKind], frozenset[str]] = {}

    def available(store: Store, kind: ArtifactKind) -> frozenset[str]:
        key = (id(store), kind)
        if key not in availability:
            availability[key] = frozenset(store.canonical_object_digests(kind))
        return availability[key]

    copied: dict[tuple[ArtifactKind, str], bytes] = {}
    visited: set[tuple[ArtifactKind, str]] = set()
    while pending:
        kind, digest = pending.pop()
        reference = (kind, digest)
        if reference in visited:
            continue
        visited.add(reference)
        if digest in available(ours, kind):
            payload = ours.get_object(kind, digest)
            copy = False
        elif digest in available(theirs, kind):
            payload = theirs.get_object(kind, digest)
            copy = True
        else:
            raise StoreError(
                f"merge.supporting-object-missing: {kind.value} object {digest!r}"
            )

        if kind is ArtifactKind.ACCEPTANCE_PACK:
            decode_pack_inventory(payload)
        elif kind is ArtifactKind.BUNDLE_INVENTORY:
            decode_bundle_inventory(payload)
        elif kind is ArtifactKind.EVIDENCE:
            evidence = decode_evidence_event(payload)
            if evidence.parent != "GENESIS":
                pending.add((ArtifactKind.EVIDENCE, evidence.parent))
        elif kind is ArtifactKind.FORMAL_CERTIFICATE:
            certificate = decode_formal_certificate(payload)
            pending.add((ArtifactKind.FORMAL_TABLES, certificate.tables_digest))

        if copy:
            copied[reference] = payload
    return tuple(
        (kind, copied[(kind, digest)])
        for kind, digest in sorted(copied, key=lambda item: (item[0].value, item[1]))
    )


def _commit_if_unchanged(
    root: str,
    before: Snapshot,
    candidate: Snapshot,
    objects: tuple[tuple[ArtifactKind, bytes], ...],
) -> None:
    """Acquire the write capability only after all refusal paths have passed."""
    with Store.open(root) as store:
        with store.write_locked():
            current = store.read_snapshot()
            if current != before:
                raise StoreError("merge.concurrent-update: our branch changed during merge")
            store.commit_merge(
                candidate,
                objects=objects,
                expected_revision=before.revision,
            )


def merge_store_roots(
    ours_root: str,
    theirs_root: str,
    *,
    actor: str,
    run_id: str,
) -> MergeOutcome | Refused:
    """Merge THEIRS into OURS without writing until the full replay is accepted."""
    with (
        Store.open(ours_root, read_only=True) as ours_store,
        Store.open(theirs_root, read_only=True) as theirs_store,
    ):
        ours = ours_store.read_snapshot()
        theirs = theirs_store.read_snapshot()
        ours_chain = _read_verified_chain(ours_store, ours, "ours")
        theirs_chain = _read_verified_chain(theirs_store, theirs, "theirs")

        ours_positions = {item.digest: index for index, item in enumerate(ours_chain)}
        shared = tuple(item for item in theirs_chain if item.digest in ours_positions)
        if not shared:
            return Refused(
                violations=(
                    Violation(
                        "merge.unrelated-histories",
                        "the branches share no event record digest",
                    ),
                )
            )
        ancestor = max(shared, key=lambda item: ours_positions[item.digest])

        if ours.event_head == theirs.event_head:
            return MergeOutcome("no-op", ours, 0)

        incorporated = _provenance_ancestors(
            ours.event_head, ours_chain, theirs_chain
        )
        if theirs.event_head in incorporated:
            return MergeOutcome("no-op", ours, 0)

        if ancestor.digest == theirs.event_head:
            return MergeOutcome("no-op", ours, 0)

        if ancestor.digest == ours.event_head:
            suffix_start = next(
                index for index, item in enumerate(theirs_chain) if item.digest == ancestor.digest
            ) + 1
            extension = tuple(
                (ArtifactKind.EVENT, item.payload) for item in theirs_chain[suffix_start:]
            )
            candidate = theirs
            source_records = theirs_chain[suffix_start:]
            supporting = _supporting_objects(
                ours_store, theirs_store, candidate, source_records
            )
            objects = supporting + extension
            mode = "fast-forward"
            replayed = 0
        else:
            source_suffix = _source_records_to_replay(theirs_chain, incorporated)
            current = ours
            replayed_events: list[events.Event] = []
            index = 0
            while index < len(source_suffix):
                item = source_suffix[index]
                try:
                    command = _command_for_event(
                        theirs_store,
                        item.record.folded_event,
                        run_id=item.record.run_id,
                    )
                    batch_length = _source_batch_length(source_suffix, index)
                except StoreError as error:
                    return Refused(
                        violations=(
                            Violation(
                                "merge.conflict",
                                f"event {_event_label(item.record.folded_event)} from revision "
                                f"{item.record.revision} cannot be replayed: {error}",
                            ),
                        )
                    )
                decision = decide(current, command)
                if isinstance(decision, Refused):
                    return _conflict(item, decision)
                current = apply(current, decision.events)
                replayed_events.extend(decision.events)
                index += batch_length

            merge_decision = decide(
                current, commands.RecordMerge(ours.event_head, theirs.event_head)
            )
            if not isinstance(merge_decision, Accepted):
                raise StoreError("merge record was refused after a successful replay")
            replayed_events.extend(merge_decision.events)
            candidate, event_objects, _records = build_event_records(
                ours,
                tuple(replayed_events),
                actor=actor,
                run_id=run_id,
            )
            staged_chain = ours_chain + tuple(
                _LoggedEvent(
                    digest_of(ArtifactKind.EVENT, payload),
                    payload,
                    decode_event_record(payload),
                )
                for kind, payload in event_objects
                if kind is ArtifactKind.EVENT
            )
            replay_check = Snapshot()
            for staged in staged_chain:
                replay_check = apply(
                    replay_check,
                    (staged.record.folded_event,),
                    event_digests=(staged.digest,),
                    event_run_ids=(staged.record.run_id,),
                )
            if replay_check != candidate:
                raise StoreError(
                    "merge.replay-invalid: staged event chain does not derive candidate snapshot"
                )
            supporting = _supporting_objects(
                ours_store, theirs_store, candidate, source_suffix
            )
            objects = supporting + event_objects
            mode = "replayed"
            replayed = len(source_suffix)

    _commit_if_unchanged(ours_root, ours, candidate, objects)
    return MergeOutcome(mode, candidate, replayed)


__all__ = ["MergeOutcome", "merge_store_roots"]
