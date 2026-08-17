"""I/O-side verification for D-4's split digest trust boundary.

The pure decision core validates evidence structure but cannot hash or read files.
This CI/verify layer loads the claimed bytes and proves their content addressing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import assert_never

from vouch_mk2.bundle import build_pack_inventory
from vouch_mk2.checks import CheckResult, compose
from vouch_mk2.checks.tier import check_pack_bound, tier_check_applies
from vouch_mk2.core import (
    Accepted,
    AnchorMode,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    EvidencePackSummary,
    RecordEvidence,
    RecordFormalCertificate,
    Snapshot,
    Tier,
    UnitStatus,
    apply,
    decide,
    evidence_kind_outcome_coherent,
)
from vouch_mk2.formal.certificate import CHECKER_VERSION, FormalCertificate
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.io import read_regular_nofollow, write_atomic
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict, Violation
from vouch_mk2.projections import agent_context, system_map
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    StoreError,
    decode_bundle_inventory,
    decode_evidence_event,
    decode_formal_certificate,
    decode_pack_inventory,
    decode_snapshot,
    decode_system_map,
    digest_of,
    encode_document,
    encode_evidence_event,
    encode_formal_certificate,
    encode_formal_relation,
    encode_formal_tables,
    encode_snapshot,
    encode_system_map,
)
from vouch_mk2.store.codec import decode_raw_document
from vouch_mk2.store.upgrade import upgrade_document


@dataclass(frozen=True, slots=True)
class _ChainWalk:
    head: EvidenceHead
    events: tuple[tuple[str, EvidenceEvent], ...]
    error: str


class RefusedError(StoreError):
    """A pure lifecycle decision refused a verification-layer mutation."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        self.violations = violations
        super().__init__(
            "refused: "
            + "; ".join(
                f"{violation.code}: {violation.detail}"
                for violation in violations
            )
        )


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    """Observable outcome of one validated store-format operation."""

    steps: tuple[tuple[int, int], ...]
    assertions: tuple[tuple[str, bool], ...]
    projections_regenerated: bool


class ProjectionRegenerationError(StoreError):
    """Projection regeneration failed after upgrade state preparation."""

    def __init__(self, *, state_upgraded: bool, error: Exception) -> None:
        self.state_upgraded = state_upgraded
        self.projection_error = str(error)
        super().__init__(self.projection_error)


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _walk_chain(store: Store, head: EvidenceHead) -> _ChainWalk:
    events: list[tuple[str, EvidenceEvent]] = []
    digest = head.head
    expected_sequence = head.head_sequence
    for _ in range(head.head_sequence):
        if digest == "GENESIS":
            return _ChainWalk(
                head,
                tuple(events),
                f"reached GENESIS before sequence 1 (expected {expected_sequence})",
            )
        if not _valid_digest(digest):
            return _ChainWalk(head, tuple(events), f"malformed parent digest {digest!r}")
        try:
            event = decode_evidence_event(
                store.get_object(ArtifactKind.EVIDENCE, digest)
            )
        except StoreError as error:
            return _ChainWalk(head, tuple(events), f"object {digest!r}: {error}")
        events.append((digest, event))
        if event.unit != head.ref():
            return _ChainWalk(
                head,
                tuple(events),
                f"event binds unit {event.unit.label()!r}, expected {head.ref().label()!r}",
            )
        if event.sequence != expected_sequence:
            return _ChainWalk(
                head,
                tuple(events),
                f"event sequence {event.sequence}, expected {expected_sequence}",
            )
        digest = event.parent
        expected_sequence -= 1
    if digest != "GENESIS":
        reason = (
            f"chain exceeds declared head_sequence {head.head_sequence}"
            if _valid_digest(digest)
            else f"malformed final parent {digest!r}"
        )
        return _ChainWalk(head, tuple(events), reason)
    return _ChainWalk(head, tuple(events), "")


def _red_before_first_green(walk: _ChainWalk) -> str:
    by_pack: dict[tuple[str, str], list[EvidenceEvent]] = {}
    for _, event in reversed(walk.events):
        by_pack.setdefault((event.unit.version, event.pack_digest), []).append(event)
    for (version, pack_digest), events in by_pack.items():
        first_green = next(
            (index for index, event in enumerate(events) if _is_green(event.kind)),
            None,
        )
        if first_green is not None and not any(
            _is_red(event.kind) for event in events[:first_green]
        ):
            return (
                f"unit {walk.head.unit_name!r} version {version!r} has no RED "
                f"before its first GREEN for pack {pack_digest or 'pre-D-17/label-only'!r}"
            )
    return ""


def _pack_result(
    events: tuple[tuple[str, EvidenceEvent], ...], pack_digest: str
) -> tuple[bool, bool, bool]:
    """Return (found, red_seen, green) for one pack in chronological order."""
    found = False
    red_seen = False
    green = False
    for _digest, event in reversed(events):
        if event.pack_digest != pack_digest or event.evidence_format == 1:
            continue
        found = True
        match event.kind:
            case EvidenceKind.RED:
                red_seen = True
                green = False
            case EvidenceKind.GREEN:
                green = red_seen
            case EvidenceKind.REFACTOR:
                pass
            case _:
                assert_never(event.kind)
    return found, red_seen, green


def _pack_summaries(
    events: tuple[tuple[str, EvidenceEvent], ...],
) -> tuple[EvidencePackSummary, ...]:
    """Recompute every distinct non-legacy digest summary from the chain."""
    digests = tuple(
        sorted(
            {
                event.pack_digest
                for _, event in events
                if event.evidence_format != 1
            }
        )
    )
    summaries: list[EvidencePackSummary] = []
    for pack_digest in digests:
        found, red_seen, green = _pack_result(events, pack_digest)
        if found:
            summaries.append(EvidencePackSummary(pack_digest, red_seen, green))
    return tuple(summaries)


def _is_green(kind: EvidenceKind) -> bool:
    match kind:
        case EvidenceKind.GREEN:
            return True
        case EvidenceKind.RED | EvidenceKind.REFACTOR:
            return False
        case _:
            assert_never(kind)


def _is_red(kind: EvidenceKind) -> bool:
    match kind:
        case EvidenceKind.RED:
            return True
        case EvidenceKind.GREEN | EvidenceKind.REFACTOR:
            return False
        case _:
            assert_never(kind)


def _check_evidence_chains(
    snapshot: Snapshot, walks: tuple[_ChainWalk, ...]
) -> CheckResult:
    failures: list[str] = []
    notices: list[str] = []
    for walk in walks:
        incoherent = tuple(
            event.sequence
            for _, event in walk.events
            if not evidence_kind_outcome_coherent(event.kind, event.outcome)
        )
        if incoherent:
            failures.append(
                f"unit {walk.head.unit_name!r}: incoherent outcome at evidence "
                f"sequence(s) {incoherent!r}"
            )
        # A head and every event bind one exact historical or current record.
        unit = snapshot.unit_ref(walk.head.ref())
        if unit is None:
            failures.append(
                f"unit {walk.head.ref().label()!r}: evidence head binds a unit "
                "absent from the snapshot"
            )
            continue
        if walk.error:
            failures.append(f"unit {walk.head.unit_name!r}: {walk.error}")
            continue
        history_error = _red_before_first_green(walk)
        if history_error:
            failures.append(history_error)
        if walk.head.pack_summaries != _pack_summaries(walk.events):
            failures.append(
                f"unit {unit.ref().label()!r}: evidence-head per-pack summary "
                "does not match its immutable chain"
            )
        has_legacy = any(
            event.evidence_format == 1 for _, event in walk.events
        )
        all_legacy = all(
            event.evidence_format == 1 for _, event in walk.events
        )
        if snapshot.tier is not Tier.LIGHTWEIGHT and has_legacy:
            notices.append(
                f"unit {unit.ref().label()!r}: pre-D-17 evidence is not "
                "current-pack evidence"
            )
        found, red_seen, green = _pack_result(
            walk.events, unit.acceptance_pack_digest
        )
        if (
            snapshot.tier is not Tier.LIGHTWEIGHT
            and unit.status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}
            and (not found or not red_seen or not green)
            and not all_legacy
        ):
            failures.append(
                f"unit {unit.ref().label()!r}: latest evidence for the "
                "current pack is not GREEN"
            )
    return CheckResult(
        check_id=mk2.CHECK_EVIDENCE_CHAINS,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join((*failures, *notices)),
    )


def _check_evidence_anchors(
    snapshot: Snapshot, walks: tuple[_ChainWalk, ...]
) -> CheckResult:
    match snapshot.anchor_mode:
        case AnchorMode.NONE:
            if tier_check_applies(snapshot, mk2.CHECK_EVIDENCE_ANCHORS):
                return CheckResult(
                    check_id=mk2.CHECK_EVIDENCE_ANCHORS,
                    verdict=Verdict.FAIL,
                    detail=(
                        f"{snapshot.tier.value} tier requires evidence anchoring; "
                        "anchor mode is none"
                    ),
                )
            return CheckResult(
                check_id=mk2.CHECK_EVIDENCE_ANCHORS,
                verdict=Verdict.NOT_APPLICABLE,
                detail="IR applicability rule not met: anchor-mode-is: ci",
            )
        case AnchorMode.CI:
            pass
        case _:
            assert_never(snapshot.anchor_mode)
    rows: list[tuple[Verdict, str]] = []
    for walk in walks:
        if walk.error:
            rows.append(
                (
                    Verdict.FAIL,
                    f"unit {walk.head.unit_name!r}: chain unverified: {walk.error}",
                )
            )
            continue
        digests = {digest for digest, _ in walk.events}
        if walk.head.anchored == "GENESIS" and walk.events:
            rows.append(
                (
                    Verdict.NOT_CHECKED,
                    f"unit {walk.head.unit_name!r} remains at GENESIS",
                )
            )
        elif walk.head.anchored != "GENESIS" and walk.head.anchored not in digests:
            rows.append(
                (
                    Verdict.FAIL,
                    f"unit {walk.head.unit_name!r} has dangling anchor {walk.head.anchored!r}",
                )
            )
        else:
            rows.append((Verdict.PASS, ""))
    verdict = Verdict.PASS if not rows else compose(row[0] for row in rows)
    return CheckResult(
        check_id=mk2.CHECK_EVIDENCE_ANCHORS,
        verdict=verdict,
        detail="; ".join(detail for _, detail in rows if detail),
    )


def _check_bundle_inventories(store: Store, snapshot: Snapshot) -> CheckResult:
    failures: list[str] = []
    for release in snapshot.releases:
        if not release.bundle_inventory_digest:
            continue
        try:
            decode_bundle_inventory(
                store.get_object(
                    ArtifactKind.BUNDLE_INVENTORY,
                    release.bundle_inventory_digest,
                )
            )
        except StoreError as error:
            failures.append(f"release {release.release_id!r}: {error}")
    return CheckResult(
        check_id=mk2.CHECK_BUNDLE_INVENTORIES,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join(failures),
    )


def _check_pack_bound_objects(store: Store, snapshot: Snapshot) -> CheckResult:
    standing = check_pack_bound(snapshot)
    if standing.verdict is Verdict.NOT_APPLICABLE:
        return standing
    details: list[str] = [standing.detail] if standing.detail else []
    failed = standing.verdict is Verdict.FAIL
    for unit in snapshot.units:
        if (
            unit.status is UnitStatus.SUPERSEDED
            or not unit.acceptance_pack_digest
        ):
            continue
        try:
            decode_pack_inventory(
                store.get_object(
                    ArtifactKind.ACCEPTANCE_PACK,
                    unit.acceptance_pack_digest,
                )
            )
        except StoreError as error:
            failed = True
            details.append(
                f"unit {unit.ref().label()!r}: bound pack object is missing or undecodable: {error}"
            )
    return CheckResult(
        check_id=mk2.CHECK_PACK_BOUND,
        verdict=Verdict.FAIL if failed else Verdict.PASS,
        detail="; ".join(details),
    )


def _check_pack_files_match(
    store: Store, snapshot: Snapshot, project: str | None
) -> CheckResult:
    if not tier_check_applies(snapshot, mk2.CHECK_PACK_FILES_MATCH):
        return CheckResult(
            check_id=mk2.CHECK_PACK_FILES_MATCH,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                f"IR tier matrix {snapshot.tier.value} cell: pack-file verification "
                "is not applicable; no project root verification required"
            ),
        )
    if project is None:
        return CheckResult(
            check_id=mk2.CHECK_PACK_FILES_MATCH,
            verdict=Verdict.NOT_CHECKED,
            detail="no project root; pass --project",
        )
    project_root = os.path.abspath(project)
    failures: list[str] = []
    for unit in sorted(snapshot.units, key=lambda item: item.ref().label()):
        if (
            unit.status is UnitStatus.SUPERSEDED
            or not unit.acceptance_pack_digest
        ):
            continue
        try:
            inventory = decode_pack_inventory(
                store.get_object(
                    ArtifactKind.ACCEPTANCE_PACK,
                    unit.acceptance_pack_digest,
                )
            )
        except StoreError as error:
            failures.append(
                f"unit {unit.ref().label()!r}: pack inventory unavailable: {error}"
            )
            continue
        try:
            actual = build_pack_inventory(
                project_root, tuple(entry.path for entry in inventory.entries)
            )
        except StoreError as error:
            failures.append(f"unit {unit.ref().label()!r}: missing pack file: {error}")
            continue
        actual_by_path = {entry.path: entry for entry in actual.entries}
        for expected in inventory.entries:
            found = actual_by_path[expected.path]
            if found.size != expected.size or found.sha256 != expected.sha256:
                failures.append(
                    f"unit {unit.ref().label()!r}: changed pack file {expected.path!r}"
                )
    return CheckResult(
        check_id=mk2.CHECK_PACK_FILES_MATCH,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join(
            (f"project root used: {project_root}", *failures)
        ),
    )


def build_certificate(run: FormalRun) -> tuple[FormalCertificate, bytes]:
    tables_digest = digest_of(
        ArtifactKind.FORMAL_TABLES, encode_formal_tables(run.tables)
    )
    certificate = FormalCertificate(
        checker_version=CHECKER_VERSION,
        tables_digest=tables_digest,
        universes=tuple(
            sorted(
                (
                    universe.universe_name,
                    digest_of(
                        ArtifactKind.FORMAL_RELATION,
                        encode_formal_relation(universe.relation),
                    ),
                    universe.state_count,
                    universe.transition_count,
                )
                for universe in run.universes
            )
        ),
        invariants=tuple(
            sorted(
                (
                    universe.universe_name,
                    verdict.invariant_id,
                    verdict.held,
                )
                for universe in run.universes
                for verdict in universe.invariants
            )
        ),
        witnesses=tuple(
            sorted(
                (
                    verdict.witness_id,
                    verdict.invariant_id,
                    verdict.discriminated,
                )
                for verdict in run.witnesses
            )
        ),
    )
    return certificate, encode_formal_certificate(certificate)


def _formal_failure(detail: str) -> CheckResult:
    return CheckResult(
        check_id=mk2.CHECK_FORMAL_CERTIFICATE,
        verdict=Verdict.FAIL,
        detail=detail,
    )


def _check_formal_certificate(store: Store, snapshot: Snapshot) -> CheckResult:
    digest = snapshot.formal_certificate_digest
    if not digest:
        return CheckResult(mk2.CHECK_FORMAL_CERTIFICATE, Verdict.PASS, "")
    try:
        certificate = decode_formal_certificate(
            store.get_object(ArtifactKind.FORMAL_CERTIFICATE, digest)
        )
    except StoreError as error:
        return _formal_failure(f"missing/corrupt formal certificate: {error}")

    fresh = run_formal()
    expected, _data = build_certificate(fresh)
    if certificate.checker_version != expected.checker_version:
        return _formal_failure("checker_version mismatch")
    if certificate.tables_digest != expected.tables_digest:
        return _formal_failure("tables_digest mismatch")
    if certificate.universes != expected.universes:
        recorded_universes = {row[0]: row for row in certificate.universes}
        expected_universes = {row[0]: row for row in expected.universes}
        divergent = sorted(
            name
            for name in set(recorded_universes) | set(expected_universes)
            if recorded_universes.get(name) != expected_universes.get(name)
        )
        return _formal_failure(
            f"formal universe relation mismatch: {divergent!r} disagree with recomputation"
        )
    if certificate.invariants != expected.invariants:
        recorded = {
            (universe_name, invariant_id): held
            for universe_name, invariant_id, held in certificate.invariants
        }
        recomputed = {
            (universe_name, invariant_id): held
            for universe_name, invariant_id, held in expected.invariants
        }
        divergent = tuple(
            sorted(
                key
                for key in set(recorded) | set(recomputed)
                if recorded.get(key) is not recomputed.get(key)
            )
        )
        return _formal_failure(
            f"invariant verdict mismatch: {divergent!r} disagree with recomputation"
        )
    failed_invariant = next(
        (
            (universe_name, invariant_id)
            for universe_name, invariant_id, held in certificate.invariants
            if not held
        ),
        None,
    )
    if failed_invariant is not None:
        return _formal_failure(f"invariant {failed_invariant!r} did not hold")
    if certificate.witnesses != expected.witnesses:
        return _formal_failure("witness verdict mismatch")
    failed_witness = next(
        (
            witness_id
            for witness_id, _invariant_id, discriminated in certificate.witnesses
            if not discriminated
        ),
        "",
    )
    if failed_witness:
        return _formal_failure(f"witness {failed_witness!r} did not discriminate")
    return CheckResult(mk2.CHECK_FORMAL_CERTIFICATE, Verdict.PASS, "")


def _check_map_freshness(store: Store, snapshot: Snapshot) -> CheckResult:
    committed = store.read_generated("map.json")
    if committed is None:
        return CheckResult(
            mk2.CHECK_MAP_FRESHNESS,
            Verdict.NOT_APPLICABLE,
            "map.json is absent; generate with `vouch-mk2 generate`",
        )
    expected = encode_system_map(
        system_map(snapshot),
        digest_of(ArtifactKind.STATE, encode_snapshot(snapshot)),
    )
    if committed == expected:
        return CheckResult(mk2.CHECK_MAP_FRESHNESS, Verdict.PASS, "")
    try:
        decode_system_map(committed)
    except StoreError as error:
        return CheckResult(
            mk2.CHECK_MAP_FRESHNESS,
            Verdict.FAIL,
            f"map.json is undecodable: {error}",
        )
    return CheckResult(
        mk2.CHECK_MAP_FRESHNESS,
        Verdict.FAIL,
        "map.json is stale: regenerate with `vouch-mk2 generate`",
    )


def _check_context_freshness(store: Store, snapshot: Snapshot) -> CheckResult:
    committed = store.read_generated("vouch-context.md")
    if committed is None:
        if store.read_generated("map.json") is None:
            return CheckResult(mk2.CHECK_CONTEXT_FRESHNESS, Verdict.PASS, "")
        return CheckResult(
            mk2.CHECK_CONTEXT_FRESHNESS,
            Verdict.FAIL,
            "vouch-context.md is absent: regenerate with `vouch-mk2 generate`",
        )
    expected = agent_context(snapshot).encode("utf-8")
    if committed == expected:
        return CheckResult(mk2.CHECK_CONTEXT_FRESHNESS, Verdict.PASS, "")
    try:
        committed.decode("utf-8")
    except UnicodeDecodeError as error:
        return CheckResult(
            mk2.CHECK_CONTEXT_FRESHNESS,
            Verdict.FAIL,
            f"vouch-context.md is undecodable: {error}",
        )
    return CheckResult(
        mk2.CHECK_CONTEXT_FRESHNESS,
        Verdict.FAIL,
        "vouch-context.md is stale: regenerate with `vouch-mk2 generate`",
    )


def run_artifact_checks(
    store: Store, snapshot: Snapshot, *, project: str | None = None
) -> tuple[CheckResult, ...]:
    """Run all IR-declared artifact checks, reusing one parent walk per head."""
    walks = tuple(_walk_chain(store, head) for head in snapshot.evidence_heads)
    return tuple(
        sorted(
            (
                _check_evidence_chains(snapshot, walks),
                _check_evidence_anchors(snapshot, walks),
                _check_bundle_inventories(store, snapshot),
                _check_formal_certificate(store, snapshot),
                _check_map_freshness(store, snapshot),
                _check_context_freshness(store, snapshot),
                _check_pack_bound_objects(store, snapshot),
                _check_pack_files_match(store, snapshot, project),
            ),
            key=lambda result: result.check_id,
        )
    )


def emit_system_map(store: Store) -> bytes:
    """Read one snapshot, emit its canonical map, and return the written bytes."""
    snapshot = store.read_snapshot()
    data = encode_system_map(
        system_map(snapshot),
        digest_of(ArtifactKind.STATE, encode_snapshot(snapshot)),
    )
    with store.write_locked():
        store.write_generated("map.json", data)
    return data


def emit_context(store: Store, target: str | None) -> str:
    """Emit store context and safely suggest changes to an optional caller path.

    D-6 sidecar pattern: an existing target whose bytes differ is NEVER touched;
    the emission goes to ``<target>.vouch-suggested``. Symlinked targets are
    refused rather than followed."""
    snapshot = store.read_snapshot()
    data = agent_context(snapshot).encode("utf-8")
    with store.write_locked():
        store.write_generated("vouch-context.md", data)
    if target is None:
        return store.generated_path("vouch-context.md")
    current = read_regular_nofollow(target, "context target")
    destination = target if current is None or current == data else target + ".vouch-suggested"
    write_atomic(destination, data, 0o644, "generated context")
    return destination


def upgrade_store(
    store: Store,
    *,
    dry_run: bool,
    published_once: Mapping[str, bool],
) -> UpgradeResult:
    """Validate and apply one format upgrade, then regenerate projections.

    A real operation holds one outer writer lock across the raw state read,
    pure document upgrade, strict decode, authoritative replacement, and both
    projection writes. A dry run performs the same preparation read-only.
    """
    if dry_run:
        document = decode_raw_document(store.read_state_bytes())
        upgraded, steps = upgrade_document(
            document,
            published_once=published_once,
            store_name=os.path.dirname(store.generated_path("map.json")),
        )
        encoded = encode_document(upgraded)
        decode_snapshot(encoded)
        return UpgradeResult(
            steps=tuple(steps),
            assertions=tuple(sorted(published_once.items())) if steps else (),
            projections_regenerated=False,
        )

    with store.write_locked():
        document = decode_raw_document(store.read_state_bytes())
        upgraded, steps = upgrade_document(
            document,
            published_once=published_once,
            store_name=os.path.dirname(store.generated_path("map.json")),
        )
        encoded = encode_document(upgraded)
        decode_snapshot(encoded)
        state_upgraded = bool(steps)
        if state_upgraded:
            store.replace_state_for_upgrade(encoded)
        try:
            emit_system_map(store)
            emit_context(store, None)
        except Exception as error:
            raise ProjectionRegenerationError(
                state_upgraded=state_upgraded,
                error=error,
            ) from error
    return UpgradeResult(
        steps=tuple(steps),
        assertions=tuple(sorted(published_once.items())) if steps else (),
        projections_regenerated=True,
    )


def record_evidence(store: Store, event: EvidenceEvent) -> str:
    with store.write_locked():
        snapshot = store.read_snapshot()
        data = encode_evidence_event(event)
        digest = digest_of(ArtifactKind.EVIDENCE, data)
        decision = decide(snapshot, RecordEvidence(event, digest))
        if not isinstance(decision, Accepted):
            raise RefusedError(decision.violations)
        store.commit(
            apply(snapshot, decision.events),
            objects=((ArtifactKind.EVIDENCE, data),),
        )
    return digest


def record_formal_certificate(store: Store, run: FormalRun | None = None) -> str:
    if run is None:
        run = run_formal()
    if not passed(run):
        failed_invariants = tuple(
            (universe.universe_name, verdict.invariant_id)
            for universe in run.universes
            for verdict in universe.invariants
            if not verdict.held
        )
        failed_witnesses = tuple(
            verdict.witness_id
            for verdict in run.witnesses
            if not verdict.discriminated
        )
        raise StoreError(
            "formal run failed: "
            f"invariants={failed_invariants!r}, witnesses={failed_witnesses!r}"
        )
    _certificate, data = build_certificate(run)
    digest = digest_of(ArtifactKind.FORMAL_CERTIFICATE, data)
    with store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, RecordFormalCertificate(digest))
        if not isinstance(decision, Accepted):
            raise RefusedError(decision.violations)
        store.commit(
            apply(snapshot, decision.events),
            objects=((ArtifactKind.FORMAL_CERTIFICATE, data),),
        )
    return digest
