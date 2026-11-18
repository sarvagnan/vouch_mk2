"""Semantic branch merge by replaying recorded commands through ``decide``."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, replace

from vouch_mk2.core import commands as commands
from vouch_mk2.core import events as events
from vouch_mk2.core.decide import Accepted, Refused, apply, decide
from vouch_mk2.core.model import PackInventory, Snapshot, UnitRef
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
from vouch_mk2.store.event_log import (
    ReplayHistory,
    build_event_records,
    event_batch_length,
    event_fact_key,
    event_unit_references,
    validate_replay_claims,
    validate_replay_placement,
)
from vouch_mk2.store.store import Store


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """The committed merge shape reported by the CLI."""

    mode: str
    snapshot: Snapshot
    replayed_records: int
    skipped_records: int = 0
    incorporated_batches: int = 0


@dataclass(frozen=True, slots=True)
class _LoggedEvent:
    digest: str
    payload: bytes
    record: events.EventRecord

    @property
    def root(self) -> str:
        return self.record.origin or self.digest

    @property
    def root_format(self) -> int:
        return self.record.origin_format or self.record.event_format


@dataclass(frozen=True, slots=True)
class _SourceFact:
    item: _LoggedEvent
    before: Snapshot


def _replay_conflict(item: _LoggedEvent, detail: str) -> Refused:
    return Refused(violations=(Violation(
        "merge.conflict",
        f"event {_event_label(item.record.folded_event)} from revision "
        f"{item.record.revision} {detail}",
    ),))


def _source_batches(facts: tuple[_SourceFact, ...]) -> tuple[tuple[_SourceFact, ...], ...]:
    """Derive context and command boundaries before any provenance filtering."""
    source_events = tuple(fact.item.record.folded_event for fact in facts)
    batches: list[tuple[_SourceFact, ...]] = []
    index = 0
    while index < len(facts):
        length = event_batch_length(source_events, index)
        batches.append(facts[index:index + length])
        index += length
    return tuple(batches)


def _check_batch_identity(
    source: tuple[_SourceFact, ...], snapshot: Snapshot,
    replayed: tuple[events.Event, ...],
) -> Refused | None:
    """Check all source facts, including companions omitted by the new decision."""
    counterparts: dict[tuple[str, tuple[str, ...]], tuple[UnitRef, ...]] = {}
    current = snapshot
    for event in replayed:
        counterparts[event_fact_key(event)] = event_unit_references(current, event)
        current = apply(current, (event,))
    for fact in source:
        event = fact.item.record.folded_event
        expected = event_unit_references(fact.before, event)
        actual = counterparts.get(event_fact_key(event), event_unit_references(snapshot, event))
        if expected != actual:
            return _replay_conflict(
                fact.item,
                "would act on " + ", ".join(ref.label() for ref in actual)
                + " instead of " + ", ".join(ref.label() for ref in expected),
            )
    return None


def _artifact_reference_key(
    item: tuple[ArtifactKind, str],
) -> tuple[str, str]:
    return item[0].value, item[1]


def _read_verified_chain(
    store: Store, snapshot: Snapshot, branch: str
) -> tuple[tuple[_SourceFact, ...], ReplayHistory]:
    """Load and fold a chain in chronological order, retaining the context of each fact."""
    if not snapshot.event_head:
        if snapshot == Snapshot():
            return (), {}
        raise StoreError(f"merge.{branch}-history-invalid: non-empty snapshot has no event head")

    reverse_chain: list[_LoggedEvent] = []
    digest = snapshot.event_head
    expected_revision = snapshot.revision
    visited: set[str] = set()
    while digest != "GENESIS":
        if digest in visited:
            raise StoreError(f"merge.{branch}-history-invalid: event chain cycle at {digest!r}")
        visited.add(digest)
        try:
            payload = store.get_object(ArtifactKind.EVENT, digest)
            record = decode_event_record(payload)
        except StoreError as error:
            raise StoreError(
                f"merge.{branch}-history-invalid: event {digest}: {error}"
            ) from error
        if record.revision != expected_revision:
            raise StoreError(
                f"merge.{branch}-history-invalid: revision {expected_revision} "
                f"records revision {record.revision}"
            )
        reverse_chain.append(_LoggedEvent(digest, payload, record))
        digest = record.prev
        expected_revision = record.revision - 1

    chain = tuple(reversed(reverse_chain))
    try:
        validate_replay_placement(tuple((item.digest, item.record) for item in chain))
    except StoreError as error:
        raise StoreError(f"merge.{branch}-history-invalid: {error}") from error
    replay = Snapshot()
    facts: list[_SourceFact] = []
    history: ReplayHistory = {}
    for item in chain:
        facts.append(_SourceFact(item, replay))
        history[item.digest] = (item.record, event_unit_references(replay, item.record.folded_event))
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
    try:
        validate_replay_claims(history, history)
    except StoreError as error:
        raise StoreError(f"merge.{branch}-history-invalid: {error}") from error
    return tuple(facts), history


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
                name, pack_version, pack_digest, test_set_digest,
                effects_declared=bool(pack_digest) and _pack_inventory(store, pack_digest).effects is not None,
            )
        case events.AcceptancePackBound(
            name=name, pack_digest=pack_digest, test_set_digest=test_set_digest
        ):
            return commands.BindPack(
                name, pack_digest, test_set_digest,
                effects_declared=_pack_inventory(store, pack_digest).effects is not None,
            )
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
                old_pack_digest=old_digest,
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
        case events.AnchorAdvanced(unit_name=name, unit_version=version):
            return commands.AdvanceAnchor(name, run_id, unit_version=version)
        case events.AnchorModeSet(mode=mode):
            return commands.SetAnchorMode(mode)
        case events.TierSet(tier=tier):
            return commands.SetTier(tier)
        case events.EffectsRequired():
            return commands.RequireEffects()
        case events.CiPolicySet(policy=policy):
            return commands.SetCiPolicy(policy)
        case events.OpenInCiMarked(name=name, reason=reason):
            return commands.MarkOpenInCi(name.split("@", 1)[0], reason)
        case events.OpenInCiCleared(name=name):
            return commands.ClearOpenInCi(name.split("@", 1)[0])
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
    batches: tuple[tuple[_SourceFact, ...], ...],
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
    batch_ends = {
        fact.item.digest: positions[batch[-1].item.digest]
        for batch in batches
        for fact in batch
    }
    skipped: set[int] = set()
    for index in range(start, len(chain)):
        event = chain[index].record.folded_event
        if not isinstance(event, events.MergeRecorded):
            continue
        if event.theirs_head not in incorporated:
            continue
        ours_index = positions.get(event.ours_head)
        if ours_index is None or ours_index >= index:
            raise _invalid_merge_boundary(chain[index])
        # A translated boundary names its exact counterpart. Companions generated
        # after that counterpart still belong to the command before the block.
        block_start = batch_ends[event.ours_head] + 1
        skipped.update(range(max(start, block_start), index + 1))
    return tuple(
        item
        for index, item in enumerate(chain[start:], start=start)
        if index not in skipped
    )


def _invalid_merge_boundary(item: _LoggedEvent) -> StoreError:
    return StoreError(
        "merge.theirs-history-invalid: merge provenance does not name "
        f"an earlier linear ours head for record {item.digest}"
    )


def _legacy_ancestry_refusal(
    ours: tuple[_LoggedEvent, ...],
    theirs: tuple[_LoggedEvent, ...],
    remaining: set[str],
    primaries: set[str],
    shared_revision: int,
) -> Refused | None:
    """Apply D-41 after exact provenance filtering and before origin selection."""
    positions = {item.digest: index for index, item in enumerate(theirs)}
    has_legacy_merge = any(
        item.record.revision > shared_revision
        and item.record.event_format == 1
        and isinstance(item.record.folded_event, events.MergeRecorded)
        for item in ours
    )
    block_start = len(theirs)
    for index in range(len(theirs) - 1, -1, -1):
        item = theirs[index]
        event = item.record.folded_event
        if item.record.revision <= shared_revision:
            break
        if item.record.event_format == 1 and isinstance(event, events.MergeRecorded):
            boundary = positions.get(event.ours_head, index)
            if boundary >= index:
                if item.digest in remaining:
                    raise _invalid_merge_boundary(item)
            else:
                block_start = min(block_start, boundary + 1)
        if item.digest not in remaining:
            continue
        if index >= block_start:
            detail = "is inside a version-1 merge block in theirs"
        elif has_legacy_merge and item.digest in primaries and item.root_format == 1:
            detail = "has a version-1 root and ours has a version-1 merge after the shared cut"
        else:
            continue
        return Refused((Violation("merge.ancestry-unproven", f"event {item.digest} {detail}"),))
    return None


def _check_excluded_companions(
    batch: tuple[_SourceFact, ...],
    current: Snapshot,
) -> Refused | None:
    """Require flagged companions to agree with the staged state."""
    for fact in batch:
        item = fact.item
        if not item.record.generated:
            continue
        event = item.record.folded_event
        after = apply(fact.before, (event,))
        if isinstance(event, events.ReleaseInvalidated):
            expected = after.release(event.release_id)
            actual = current.release(event.release_id)
            holds = (
                expected is not None and actual is not None
                and actual.state == expected.state
                and (event.closure is None or actual.closure == event.closure)
            )
        else:
            refs = event_unit_references(fact.before, event)
            expected_unit = after.unit_ref(refs[0]) if refs else None
            actual_unit = current.unit_ref(refs[0]) if refs else None
            holds = (
                expected_unit is not None and actual_unit is not None
                and actual_unit.status == expected_unit.status
            )
        if not holds:
            return _replay_conflict(item, "has an effect missing from ours after skipping its batch")
    return None


def _stage_events(
    snapshot: Snapshot,
    replayed: tuple[events.Event, ...],
    *,
    actor: str,
    run_id: str,
    recorded_at: str,
    counterparts: tuple[tuple[str, events.EventRecord] | None, ...] | None = None,
    satisfied: tuple[str, ...] = (),
) -> tuple[Snapshot, tuple[_LoggedEvent, ...]]:
    next_snapshot, objects, _records = build_event_records(
        snapshot, replayed, actor=actor, run_id=run_id,
        recorded_at=recorded_at, source_records=counterparts, satisfied=satisfied,
    )
    return next_snapshot, tuple(
        _LoggedEvent(digest_of(kind, payload), payload, decode_event_record(payload))
        for kind, payload in objects
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
        for kind, digest in sorted(copied, key=_artifact_reference_key)
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
        ours_facts, ours_history = _read_verified_chain(ours_store, ours, "ours")
        theirs_facts, theirs_history = _read_verified_chain(theirs_store, theirs, "theirs")
        ours_chain = tuple(fact.item for fact in ours_facts)
        theirs_chain = tuple(fact.item for fact in theirs_facts)

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
        def ours_position(item: _LoggedEvent) -> int:
            return ours_positions[item.digest]

        ancestor = max(shared, key=ours_position)

        try:
            validate_replay_claims(theirs_history, ours_history)
            # Our coverage can refer to records only their chain holds.
            validate_replay_claims(ours_history, theirs_history)
        except StoreError as error:
            raise StoreError(f"merge.theirs-history-invalid: {error}") from error

        if ancestor.digest == theirs.event_head:
            return MergeOutcome("no-op", ours, 0)

        skipped = 0
        incorporated_batches = 0
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
            incorporated = _provenance_ancestors(ours.event_head, ours_chain, theirs_chain)
            if theirs.event_head in incorporated:
                return MergeOutcome("no-op", ours, 0)
            source_batches = _source_batches(theirs_facts)
            source_suffix = _source_records_to_replay(theirs_chain, incorporated, source_batches)
            selected = {item.digest for item in source_suffix}
            legacy_refusal = _legacy_ancestry_refusal(
                ours_chain, theirs_chain, selected,
                {batch[0].item.digest for batch in source_batches}, ancestor.record.revision,
            )
            if legacy_refusal is not None:
                return legacy_refusal

            coverage = {
                digest for item in ours_chain
                for digest in (item.digest, item.root, *item.record.satisfied)
            }
            current = ours
            boundaries: dict[str, str] = {}
            staged: list[_LoggedEvent] = []
            satisfied: dict[str, None] = {}
            recorded_at = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
            for batch in source_batches:
                item = batch[0].item
                in_selection = sum(fact.item.digest in selected for fact in batch)
                if in_selection and item.root in coverage:
                    selected.difference_update(fact.item.digest for fact in batch)
                    incorporated_batches += 1
                    in_selection = 0
                if not in_selection:
                    if batch[-1].item.record.revision > ancestor.record.revision:
                        companion_refusal = _check_excluded_companions(batch, current)
                        if companion_refusal is not None:
                            return companion_refusal
                    # The initial source prefix crosses at our pre-merge head.
                    # Later filtered batches cross after the work already staged.
                    for fact in batch:
                        boundaries[fact.item.digest] = current.event_head
                    continue
                if in_selection != len(batch):
                    return _replay_conflict(item, "has a partially filtered command batch")
                source_event = item.record.folded_event
                try:
                    command = _command_for_event(
                        theirs_store, source_event, run_id=item.record.run_id,
                    )
                except StoreError as error:
                    return _replay_conflict(item, f"cannot be replayed: {error}")
                if isinstance(source_event, events.AnchorAdvanced):
                    references = event_unit_references(batch[0].before, source_event)
                    if not references:
                        return _replay_conflict(item, "has no source unit reference")
                    ref = references[0]
                    command = commands.AdvanceAnchor(
                        ref.name, item.record.run_id, unit_version=ref.version,
                    )
                    source_head = batch[0].before.evidence_head_ref(ref)
                    ours_head = current.evidence_head_ref(ref)
                    if source_head is None or ours_head is None or source_head.head != ours_head.head:
                        return _replay_conflict(
                            item, f"for {ref.label()} promoted head "
                            f"{None if source_head is None else source_head.head}; ours has head "
                            f"{None if ours_head is None else ours_head.head}",
                        )
                decision = decide(current, command)
                if isinstance(decision, Refused):
                    if (
                        isinstance(source_event, events.OpenInCiCleared)
                        and isinstance(command, commands.ClearOpenInCi)
                        and len(decision.violations) == 1
                        and decision.violations[0].code == "ci-policy.exception-missing"
                    ):
                        # The refusal proves absence; resolve exactly as decide()
                        # does before treating the clear as already satisfied.
                        # Historical presence alone must not bypass D-25.
                        unit = current.unit(command.name)
                        if unit is not None and unit.ref().label() == source_event.name:
                            skipped += 1
                            satisfied[item.root] = None
                            boundaries[item.digest] = current.event_head
                            continue
                    return _conflict(item, decision)
                identity_refusal = _check_batch_identity(batch, current, decision.events)
                if identity_refusal is not None:
                    return identity_refusal
                if isinstance(source_event, (events.OpenInCiMarked, events.OpenInCiCleared)):
                    replayed_event = decision.events[0]
                    if (
                        not isinstance(replayed_event, (events.OpenInCiMarked, events.OpenInCiCleared))
                        or replayed_event.name != source_event.name
                    ):
                        return _replay_conflict(
                            item, f"would replay as {_event_label(replayed_event)} "
                            "instead of the recorded exact reference",
                        )
                originals = {event_fact_key(fact.item.record.folded_event): fact.item for fact in batch}
                counterparts = tuple(originals.get(event_fact_key(event)) for event in decision.events)
                matched = {source.digest for source in counterparts if source is not None}
                for fact in batch:
                    if fact.item.digest not in matched:
                        satisfied[fact.item.root] = None
                replayed_batch = decision.events
                if isinstance(source_event, events.MergeRecorded):
                    boundary = boundaries.get(source_event.ours_head)
                    if boundary is None:
                        raise _invalid_merge_boundary(item)
                    replayed_batch = (replace(source_event, ours_head=boundary),)

                boundary_head = current.event_head
                current, staged_batch = _stage_events(
                    current, replayed_batch, actor=actor, run_id=run_id,
                    recorded_at=recorded_at,
                    counterparts=tuple(
                        (source.digest, source.record) if source is not None else None
                        for source in counterparts
                    ),
                )
                staged.extend(staged_batch)
                replayed_digests = {
                    source.digest: record.digest
                    for source, record in zip(counterparts, staged_batch, strict=True)
                    if source is not None
                }
                for fact in batch[:-1]:
                    boundary_head = replayed_digests.get(fact.item.digest, boundary_head)
                    boundaries[fact.item.digest] = boundary_head
                last_source = batch[-1].item.digest
                boundaries[last_source] = replayed_digests.get(last_source, current.event_head)

            for item in theirs_chain:
                satisfied.update(dict.fromkeys(item.record.satisfied))

            merge_decision = decide(
                current, commands.RecordMerge(ours.event_head, theirs.event_head)
            )
            if not isinstance(merge_decision, Accepted):
                raise StoreError("merge record was refused after a successful replay")
            candidate, merge_records = _stage_events(
                current, merge_decision.events, actor=actor, run_id=run_id,
                recorded_at=recorded_at, satisfied=tuple(satisfied),
            )
            staged.extend(merge_records)
            replay_check = Snapshot()
            for item in ours_chain + tuple(staged):
                replay_check = apply(
                    replay_check,
                    (item.record.folded_event,),
                    event_digests=(item.digest,),
                    event_run_ids=(item.record.run_id,),
                )
            if replay_check != candidate:
                raise StoreError(
                    "merge.replay-invalid: staged event chain does not derive candidate snapshot"
                )
            replayed_suffix = tuple(item for item in source_suffix if item.digest in selected)
            supporting = _supporting_objects(ours_store, theirs_store, candidate, replayed_suffix)
            objects = supporting + tuple((ArtifactKind.EVENT, item.payload) for item in staged)
            mode = "replayed"
            replayed = len(replayed_suffix) - skipped

    _commit_if_unchanged(ours_root, ours, candidate, objects)
    return MergeOutcome(mode, candidate, replayed, skipped, incorporated_batches)


__all__ = ["MergeOutcome", "merge_store_roots"]
