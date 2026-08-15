"""I/O-side verification for D-4's split digest trust boundary.

The pure decision core validates evidence structure but cannot hash or read files.
This CI/verify layer loads the claimed bytes and proves their content addressing.
"""

from __future__ import annotations

import os
import secrets
import stat
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
    RecordFormalCertificate,
    Snapshot,
    apply,
    decide,
)
from vouch_mk2.formal.certificate import CHECKER_VERSION, FormalCertificate
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
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


def build_certificate(run: FormalRun) -> tuple[FormalCertificate, bytes]:
    tables_digest = digest_of(
        ArtifactKind.FORMAL_TABLES, encode_formal_tables(run.tables)
    )
    relation_digest = digest_of(
        ArtifactKind.FORMAL_RELATION, encode_formal_relation(run.relation)
    )
    certificate = FormalCertificate(
        universe_name=run.universe_name,
        checker_version=CHECKER_VERSION,
        tables_digest=tables_digest,
        relation_digest=relation_digest,
        state_count=run.state_count,
        transition_count=run.transition_count,
        invariants=tuple(
            sorted((verdict.invariant_id, verdict.held) for verdict in run.invariants)
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
    if certificate.universe_name != expected.universe_name:
        return _formal_failure("universe_name mismatch")
    if certificate.checker_version != expected.checker_version:
        return _formal_failure("checker_version mismatch")
    if certificate.tables_digest != expected.tables_digest:
        return _formal_failure("tables_digest mismatch")
    if certificate.relation_digest != expected.relation_digest:
        return _formal_failure("relation_digest mismatch")
    if (
        certificate.state_count != expected.state_count
        or certificate.transition_count != expected.transition_count
    ):
        return _formal_failure("relation counts mismatch")
    if certificate.invariants != expected.invariants:
        recorded = dict(certificate.invariants)
        recomputed = dict(expected.invariants)
        divergent = tuple(
            sorted(
                invariant_id
                for invariant_id in set(recorded) | set(recomputed)
                if recorded.get(invariant_id) is not recomputed.get(invariant_id)
            )
        )
        return _formal_failure(
            f"invariant verdict mismatch: {divergent!r} disagree with recomputation"
        )
    failed_invariant = next(
        (invariant_id for invariant_id, held in certificate.invariants if not held),
        "",
    )
    if failed_invariant:
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
    store.write_generated("map.json", data)
    return data


def _read_target_nofollow(path: str) -> bytes | None:
    """Read a caller-supplied target without following symlinks.

    A symlinked target (dangling or not) is refused: writing "the target" would
    otherwise create or overwrite the link's referent — a path the caller never
    named. None means the target is genuinely absent."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StoreError(f"{path}: cannot inspect context target: {error}") from error
    if stat.S_ISLNK(info.st_mode):
        raise StoreError(f"{path}: symlinked context target refused")
    if not stat.S_ISREG(info.st_mode):
        raise StoreError(f"{path}: context target is not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise StoreError(f"{path}: cannot read context target: {error}") from error
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _write_chosen_path(path: str, data: bytes) -> None:
    """Write via a same-directory temp + os.replace so a partial file is never
    visible under the destination name, and the destination is never opened
    for writing through a link (replace rebinds the name)."""
    directory = os.path.dirname(path) or "."
    temp = os.path.join(directory, f".{os.path.basename(path)}.{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except OSError as error:
        raise StoreError(f"{path}: cannot write generated context: {error}") from error
    try:
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path)
    except OSError as error:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise StoreError(f"{path}: cannot write generated context: {error}") from error


def emit_context(store: Store, target: str | None) -> str:
    """Emit store context and safely suggest changes to an optional caller path.

    D-6 sidecar pattern: an existing target whose bytes differ is NEVER touched;
    the emission goes to ``<target>.vouch-suggested``. Symlinked targets are
    refused rather than followed."""
    snapshot = store.read_snapshot()
    data = agent_context(snapshot).encode("utf-8")
    store.write_generated("vouch-context.md", data)
    if target is None:
        return store.generated_path("vouch-context.md")
    current = _read_target_nofollow(target)
    destination = target if current is None or current == data else target + ".vouch-suggested"
    _write_chosen_path(destination, data)
    return destination


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


def record_formal_certificate(store: Store, run: FormalRun | None = None) -> str:
    if run is None:
        run = run_formal()
    if not passed(run):
        failed_invariants = tuple(
            verdict.invariant_id for verdict in run.invariants if not verdict.held
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
    snapshot = store.read_snapshot()
    decision = decide(snapshot, RecordFormalCertificate(digest))
    if not isinstance(decision, Accepted):
        codes = tuple(violation.code for violation in decision.violations)
        raise StoreError(f"record-formal-certificate refused: {codes!r}")
    store.commit(
        apply(snapshot, decision.events),
        objects=((ArtifactKind.FORMAL_CERTIFICATE, data),),
    )
    return digest
