"""I/O-side verification for D-4's split digest trust boundary.

The pure decision core validates evidence structure but cannot hash or read files.
This CI/verify layer loads the claimed bytes and proves their content addressing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from vouch_mk2.checks import CheckResult, compose
from vouch_mk2.checks.tier import tier_check_applies
from vouch_mk2.core import (
    Accepted,
    AnchorMode,
    EvidenceEvent,
    EvidenceHead,
    EvidenceKind,
    RecordEvidence,
    RecordFormalCertificate,
    Snapshot,
    apply,
    decide,
    evidence_kind_outcome_coherent,
)
from vouch_mk2.formal.certificate import CHECKER_VERSION, FormalCertificate
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict, Violation
from vouch_mk2.io import read_regular_nofollow, write_atomic
from vouch_mk2.projections import agent_context, system_map
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    StoreError,
    decode_bundle_inventory,
    decode_evidence_event,
    decode_formal_certificate,
    decode_system_map,
    digest_of,
    encode_evidence_event,
    encode_formal_certificate,
    encode_formal_relation,
    encode_formal_tables,
    encode_snapshot,
    encode_system_map,
)


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
        return CheckResult(mk2.CHECK_MAP_FRESHNESS, Verdict.PASS, "")
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


def run_artifact_checks(store: Store, snapshot: Snapshot) -> tuple[CheckResult, ...]:
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
