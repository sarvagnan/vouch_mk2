"""I/O-side verification for D-4's split digest trust boundary.

The pure decision core validates evidence structure but cannot hash or read files.
This CI/verify layer loads the claimed bytes and proves their content addressing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from vouch_mk2.checks import CheckResult, compose
from vouch_mk2.core import (
    Accepted,
    AnchorMode,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    RecordEvidence,
    Snapshot,
    apply,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    StoreError,
    decode_bundle_inventory,
    decode_evidence_event,
    digest_of,
    encode_evidence_event,
)


@dataclass(frozen=True, slots=True)
class _ChainWalk:
    head: EvidenceHead
    events: tuple[tuple[str, EvidenceEvent], ...]
    error: str


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
        if event.unit.name != head.unit_name:
            return _ChainWalk(
                head,
                tuple(events),
                f"event binds unit {event.unit.name!r}, expected {head.unit_name!r}",
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
    by_version: dict[str, list[EvidenceEvent]] = {}
    for _, event in reversed(walk.events):
        by_version.setdefault(event.unit.version, []).append(event)
    for version, events in by_version.items():
        first_green = next(
            (index for index, event in enumerate(events) if _is_green(event.kind)),
            None,
        )
        if first_green is not None and not any(
            _is_red(event.kind) for event in events[:first_green]
        ):
            return (
                f"unit {walk.head.unit_name!r} version {version!r} has no RED "
                "before its first GREEN"
            )
    return ""


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
    for walk in walks:
        # A head must bind a unit the snapshot actually contains — a chain for
        # a ghost unit is fabricated ledger content, not evidence. Every event
        # must bind the unit's current version: mk2 has no re-versioning
        # command yet, so any other version in a chain is drift (revisit when
        # amendment-driven version bumps arrive).
        unit = snapshot.unit(walk.head.unit_name)
        if unit is None:
            failures.append(
                f"unit {walk.head.unit_name!r}: evidence head binds a unit "
                "absent from the snapshot"
            )
            continue
        if walk.error:
            failures.append(f"unit {walk.head.unit_name!r}: {walk.error}")
            continue
        stale = sorted(
            {
                event.unit.version
                for _, event in walk.events
                if event.unit.version != unit.version
            }
        )
        if stale:
            failures.append(
                f"unit {walk.head.unit_name!r}: chain events bind versions "
                f"{tuple(stale)!r}, unit is at {unit.version!r}"
            )
            continue
        history_error = _red_before_first_green(walk)
        if history_error:
            failures.append(history_error)
    return CheckResult(
        check_id=mk2.CHECK_EVIDENCE_CHAINS,
        verdict=Verdict.FAIL if failures else Verdict.PASS,
        detail="; ".join(failures),
    )


def _check_evidence_anchors(
    snapshot: Snapshot, walks: tuple[_ChainWalk, ...]
) -> CheckResult:
    match snapshot.anchor_mode:
        case AnchorMode.NONE:
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


def run_artifact_checks(store: Store, snapshot: Snapshot) -> tuple[CheckResult, ...]:
    """Run all IR-declared artifact checks, reusing one parent walk per head."""
    walks = tuple(_walk_chain(store, head) for head in snapshot.evidence_heads)
    return tuple(
        sorted(
            (
                _check_evidence_chains(snapshot, walks),
                _check_evidence_anchors(snapshot, walks),
                _check_bundle_inventories(store, snapshot),
            ),
            key=lambda result: result.check_id,
        )
    )


def record_evidence(store: Store, event: EvidenceEvent) -> str:
    snapshot = store.read_snapshot()
    data = encode_evidence_event(event)
    digest = digest_of(ArtifactKind.EVIDENCE, data)
    decision = decide(snapshot, RecordEvidence(event, digest))
    if not isinstance(decision, Accepted):
        codes = tuple(violation.code for violation in decision.violations)
        raise StoreError(f"record-evidence refused: {codes!r}")
    store.commit(
        apply(snapshot, decision.events),
        objects=((ArtifactKind.EVIDENCE, data),),
    )
    return digest
