"""I/O-side verification for D-4's split digest trust boundary.

The pure decision core validates evidence structure but cannot hash or read files.
This CI/verify layer loads the claimed bytes and proves their content addressing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
    PackRebound,
    RecordEvidence,
    RecordFormalCertificate,
    Snapshot,
    Tier,
    Unit,
    UnitStatus,
    apply,
    decide,
    evidence_kind_outcome_coherent,
    fold_evidence_pack_summaries,
)
from vouch_mk2.formal.certificate import CHECKER_VERSION, FormalCertificate
from vouch_mk2.formal.explore import emit_tables
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.formal.universe import UNIVERSES
from vouch_mk2.formal.witnesses import WITNESSES
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
    evidence_head_summaries_rebuilt: int


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


def _red_before_first_green(
    walk: _ChainWalk, *, rebound_history: tuple[PackRebound, ...] = ()
) -> str:
    events = tuple(event for _, event in reversed(walk.events))
    first_green = next(
        (index for index, event in enumerate(events) if _is_green(event.kind)),
        None,
    )
    if first_green is None or any(
        _is_red(event.kind) for event in events[:first_green]
    ):
        return ""
    green = events[first_green]
    rebound_green = any(
        rebound.new_test_set_digest == green.pack_digest
        and rebound.evidence_head_sequence < green.sequence
        and (
            index + 1 == len(rebound_history)
            or green.sequence
            <= rebound_history[index + 1].evidence_head_sequence
        )
        for index, rebound in enumerate(rebound_history)
    )
    if rebound_green:
        return ""
    return f"unit {walk.head.ref().label()!r} has no RED before its first GREEN"


def _fresh_rebound_green(walk: _ChainWalk, unit: Unit) -> bool:
    if not unit.rebound_history:
        return False
    boundary = unit.rebound_history[-1].evidence_head_sequence
    fresh_green = False
    green = False
    for _digest, event in reversed(walk.events):
        if (
            event.sequence <= boundary
            or event.pack_digest != unit.acceptance_test_set_digest
            or event.evidence_format == 1
        ):
            continue
        match event.kind:
            case EvidenceKind.RED:
                green = False
            case EvidenceKind.GREEN:
                fresh_green = True
                green = True
            case EvidenceKind.REFACTOR:
                pass
            case _:
                assert_never(event.kind)
    return fresh_green and green


def _pack_result(
    events: tuple[tuple[str, EvidenceEvent], ...], pack_digest: str
) -> tuple[bool, bool, bool]:
    """Return (found, red_seen, green) for one pack in chronological order."""
    summaries = fold_evidence_pack_summaries(
        (event for _digest, event in reversed(events))
    )
    summary = next(
        (item for item in summaries if item.pack_digest == pack_digest),
        None,
    )
    if summary is None:
        return False, False, False
    return True, summary.red_seen, summary.green


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
    if not walks:
        return CheckResult(
            check_id=mk2.CHECK_EVIDENCE_CHAINS,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                "IR applicability rule not met: subjects-present: evidence-heads"
            ),
        )
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
        history_error = _red_before_first_green(
            walk,
            rebound_history=unit.rebound_history,
        )
        if history_error:
            failures.append(history_error)
        derived_summaries = fold_evidence_pack_summaries(
            (event for _digest, event in reversed(walk.events))
        )
        if walk.head.pack_summaries != derived_summaries:
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
            walk.events, unit.acceptance_test_set_digest
        )
        if (
            snapshot.tier is not Tier.LIGHTWEIGHT
            and unit.status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}
            and unit.pack_binding != "rebound"
            and (
                not found
                or not green
                or not red_seen
            )
            and not all_legacy
        ):
            failures.append(
                f"unit {unit.ref().label()!r}: latest evidence for the "
                "current pack is not GREEN (test-set digest)"
            )
    walks_by_ref = {walk.head.ref(): walk for walk in walks}
    for unit in snapshot.units:
        if (
            unit.status is UnitStatus.SUPERSEDED
            or unit.pack_binding != "rebound"
        ):
            continue
        walk = walks_by_ref.get(unit.ref())
        if walk is None or walk.error or not _fresh_rebound_green(walk, unit):
            failures.append(
                f"unit {unit.ref().label()!r}: rebound after publication; "
                "a fresh GREEN later than the rebound evidence boundary is "
                "missing (no RED is required)"
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
    subjects = tuple(
        release
        for release in snapshot.releases
        if release.bundle_inventory_digest
    )
    if not subjects:
        return CheckResult(
            check_id=mk2.CHECK_BUNDLE_INVENTORIES,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                "IR applicability rule not met: subjects-present: "
                "bound-bundle-inventories"
            ),
        )
    failures: list[str] = []
    for release in subjects:
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


def _check_pack_bound_objects(
    store: Store, snapshot: Snapshot, walks: tuple[_ChainWalk, ...]
) -> CheckResult:
    standing = check_pack_bound(snapshot)
    if standing.verdict is Verdict.NOT_APPLICABLE:
        return standing
    details: list[str] = [standing.detail] if standing.detail else []
    failed = standing.verdict is Verdict.FAIL
    walks_by_ref = {walk.head.ref(): walk for walk in walks}
    if snapshot.tier is Tier.HIGH:
        stale_rebounds = tuple(
            unit.ref().label()
            for unit in snapshot.units
            if unit.status is not UnitStatus.SUPERSEDED
            and unit.pack_binding == "rebound"
            and (
                (walk := walks_by_ref.get(unit.ref())) is None
                or walk.error
                or not _fresh_rebound_green(walk, unit)
            )
        )
        if stale_rebounds:
            failed = True
            details.append(
                "high: rebound units lack fresh GREEN: "
                + ", ".join(stale_rebounds)
            )
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
    subjects = tuple(
        unit
        for unit in snapshot.units
        if unit.status is not UnitStatus.SUPERSEDED
        and unit.acceptance_pack_digest
    )
    if not subjects:
        return CheckResult(
            check_id=mk2.CHECK_PACK_FILES_MATCH,
            verdict=Verdict.NOT_APPLICABLE,
            detail=(
                "IR applicability rule not met: subjects-present: "
                "active-bound-pack-units"
            ),
        )
    failures: list[str] = []
    for unit in sorted(subjects, key=lambda item: item.ref().label()):
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
                project_root,
                tuple(
                    entry.path
                    for entry in inventory.entries
                    if inventory.pack_format == 1 or entry.role == "spec"
                ),
                tuple(
                    entry.path
                    for entry in inventory.entries
                    if inventory.pack_format == 2 and entry.role == "test"
                ),
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
        return CheckResult(
            mk2.CHECK_FORMAL_CERTIFICATE,
            Verdict.NOT_APPLICABLE,
            "IR applicability rule not met: subjects-present: formal-certificate",
        )
    try:
        certificate = decode_formal_certificate(
            store.get_object(ArtifactKind.FORMAL_CERTIFICATE, digest)
        )
    except StoreError as error:
        return _formal_failure(f"missing/corrupt formal certificate: {error}")

    if certificate.checker_version != CHECKER_VERSION:
        return _formal_failure("checker_version mismatch")
    current_tables = encode_formal_tables(emit_tables())
    current_tables_digest = digest_of(ArtifactKind.FORMAL_TABLES, current_tables)
    if certificate.tables_digest != current_tables_digest:
        return _formal_failure("tables_digest mismatch with current emitted policy tables")
    try:
        recorded_tables = store.get_object(
            ArtifactKind.FORMAL_TABLES, certificate.tables_digest
        )
    except StoreError as error:
        return _formal_failure(f"recorded formal tables missing/corrupt: {error}")
    if recorded_tables != current_tables:
        return _formal_failure("recorded formal tables differ from current emitted tables")
    expected_universe_names = {universe.name for universe in UNIVERSES}
    recorded_universe_names = {row[0] for row in certificate.universes}
    if (
        recorded_universe_names != expected_universe_names
        or len(certificate.universes) != len(recorded_universe_names)
    ):
        return _formal_failure(
            "formal universe registry mismatch: "
            f"recorded={tuple(sorted(recorded_universe_names))!r}, "
            f"current={tuple(sorted(expected_universe_names))!r}"
        )
    for name, _relation_digest, state_count, transition_count in certificate.universes:
        if state_count < 1 or transition_count < 1:
            return _formal_failure(
                f"formal universe {name!r} records an empty relation"
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
    all_invariant_ids = {invariant.invariant_id for invariant in INVARIANTS}
    expected_invariant_rows = {
        (universe.name, invariant_id)
        for universe in UNIVERSES
        for invariant_id in (
            universe.invariant_ids
            if universe.invariant_ids
            else tuple(sorted(all_invariant_ids))
        )
    }
    recorded_invariant_rows = {
        (universe_name, invariant_id)
        for universe_name, invariant_id, _held in certificate.invariants
    }
    if (
        recorded_invariant_rows != expected_invariant_rows
        or len(certificate.invariants) != len(recorded_invariant_rows)
    ):
        return _formal_failure("invariant registry mismatch")
    expected_witness_rows = {
        (witness.witness_id, witness.invariant_id) for witness in WITNESSES
    }
    recorded_witness_rows = {
        (witness_id, invariant_id)
        for witness_id, invariant_id, _discriminated in certificate.witnesses
    }
    if (
        recorded_witness_rows != expected_witness_rows
        or len(certificate.witnesses) != len(recorded_witness_rows)
    ):
        return _formal_failure("witness registry mismatch")
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
    return CheckResult(
        mk2.CHECK_FORMAL_CERTIFICATE,
        Verdict.PASS,
        detail=(
            "cheap certificate verification passed for the narrowed obligation: "
            "the certificate object resolves at its recorded digest and decodes "
            "strictly; checker identity is current; recorded tables resolve and "
            "equal freshly emitted tables; invariant/witness rows cover the current "
            "registries and are all true; residual not performed by `check`: "
            "regenerating the reachable relations and executing M1-M11 and the "
            "witness mutations over them — that is `vouch-mk2 formal`'s job"
        ),
    )


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
            return CheckResult(
                mk2.CHECK_CONTEXT_FRESHNESS,
                Verdict.NOT_APPLICABLE,
                "IR applicability rule not met: subjects-present: generated-context",
            )
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
    store: Store,
    snapshot: Snapshot,
    *,
    project: str | None = None,
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
                _check_pack_bound_objects(store, snapshot, walks),
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


def _rebuild_evidence_head_summaries(
    store: Store, snapshot: Snapshot
) -> tuple[Snapshot, int]:
    """Replace only summary caches, after validating their immutable chains."""
    walks = tuple(_walk_chain(store, head) for head in snapshot.evidence_heads)
    problems: list[str] = []
    for walk in walks:
        if walk.error:
            problems.append(f"unit {walk.head.ref().label()!r}: {walk.error}")
            continue
        for digest, event in walk.events:
            if not evidence_kind_outcome_coherent(event.kind, event.outcome):
                problems.append(
                    f"unit {walk.head.ref().label()!r}: incoherent evidence object "
                    f"{digest!r} at sequence {event.sequence}"
                )
    if problems:
        raise StoreError(
            "cannot rebuild evidence-head summaries from immutable chains: "
            + "; ".join(problems)
        )

    rebuilt = 0
    heads: list[EvidenceHead] = []
    for walk in walks:
        summaries = fold_evidence_pack_summaries(
            (event for _digest, event in reversed(walk.events))
        )
        if summaries != walk.head.pack_summaries:
            rebuilt += 1
            heads.append(replace(walk.head, pack_summaries=summaries))
        else:
            heads.append(walk.head)
    return replace(snapshot, evidence_heads=tuple(heads)), rebuilt


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
        snapshot = decode_snapshot(encode_document(upgraded))
        snapshot, rebuilt = _rebuild_evidence_head_summaries(store, snapshot)
        encode_snapshot(snapshot)
        return UpgradeResult(
            steps=tuple(steps),
            assertions=tuple(sorted(published_once.items())) if steps else (),
            projections_regenerated=False,
            evidence_head_summaries_rebuilt=rebuilt,
        )

    with store.write_locked():
        original = store.read_state_bytes()
        document = decode_raw_document(original)
        upgraded, steps = upgrade_document(
            document,
            published_once=published_once,
            store_name=os.path.dirname(store.generated_path("map.json")),
        )
        snapshot = decode_snapshot(encode_document(upgraded))
        snapshot, rebuilt = _rebuild_evidence_head_summaries(store, snapshot)
        encoded = encode_snapshot(snapshot)
        state_upgraded = bool(steps) or encoded != original
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
        evidence_head_summaries_rebuilt=rebuilt,
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
        formal_objects = (
            (ArtifactKind.FORMAL_CERTIFICATE, data),
            (ArtifactKind.FORMAL_TABLES, encode_formal_tables(run.tables)),
        )
        store.commit(
            apply(snapshot, decision.events),
            objects=formal_objects,
        )
    return digest
