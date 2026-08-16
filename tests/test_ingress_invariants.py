"""Acceptance pack for self-hosted unit ingress-invariants@1 (D-11 pilot).

One discriminating test per external-review finding. Every test here FAILED against
the pre-change tree (red evidence recorded in the dogfood store before implementation)
and must PASS at completion. The implementer may not edit this file — a review finding
that says this pack is wrong is a DEVIATION adjudicated by the acceptance owner.

Common remedy the pack expects: one central snapshot-invariant validator applied at
EVERY ingress (codec decode, migration candidate, store commit), one shared pure
kind/outcome coherence predicate used by decide() AND the chain walk, tuple identity
everywhere a NAME@VERSION is compared, and an identifier grammar enforced at every
boundary.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from vouch_mk2.core import (
    Accepted,
    CompleteRelease,
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    Refused,
    apply,
    decide,
)
from vouch_mk2.core.model import (
    ClosureRecord,
    ClosureTriggerKind,
    LaunchCriteria,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.store import (
    ArtifactKind,
    DecodeError,
    Store,
    StoreError,
    decode_snapshot,
    digest_of,
    encode_evidence_event,
    encode_snapshot,
)

CRITERIA = LaunchCriteria(("healthy",), "1h", ("errors",), "ops")


def _unit(name: str, version: str = "v1", status: UnitStatus = UnitStatus.RELEASE_BOUND) -> Unit:
    return Unit(name, version, status, acceptance_pack_version="p")


# ---- P1: empty roster must be refused at EVERY ingress and can never complete ----


def test_p1_empty_roster_refused_by_snapshot_decoder() -> None:
    release = Release("r0", ReleaseArchetype.OPERATIONAL, ReleaseState.VERIFYING, (), CRITERIA, None)
    with pytest.raises(DecodeError, match="roster"):
        decode_snapshot(encode_snapshot(Snapshot(revision=1, releases=(release,))))


def test_p1_empty_roster_cannot_complete_even_if_fabricated_in_memory() -> None:
    release = Release("r0", ReleaseArchetype.OPERATIONAL, ReleaseState.VERIFYING, (), CRITERIA, None)
    decision = decide(Snapshot(revision=1, releases=(release,)), CompleteRelease("r0"))
    assert isinstance(decision, Refused)
    assert any("roster" in v.code or "roster" in v.detail for v in decision.violations)


def test_p1_empty_roster_refused_by_store_commit(tmp_path: Path) -> None:
    release = Release("r0", ReleaseArchetype.OPERATIONAL, ReleaseState.PREPARED, (), None, None)
    with Store.init(str(tmp_path / "s")) as store:
        with pytest.raises(StoreError, match="roster"):
            store.commit(Snapshot(revision=1, releases=(release,)))
        assert store.read_snapshot().revision == 0, "a refused commit must leave the store untouched"


# ---- P1: the artifact bound must be enforced on WRITE, not only on read ----


def test_p1_store_never_commits_a_state_it_cannot_read_back(tmp_path: Path) -> None:
    store = Store.init(str(tmp_path / "s"), max_artifact_bytes=256)
    big = Snapshot(revision=1, units=tuple(Unit(f"u{i}", "v1", UnitStatus.SPECIFIED) for i in range(30)))
    with pytest.raises(StoreError, match="bound"):
        store.commit(big)
    assert store.read_snapshot().revision == 0
    store.close()


def test_p1_store_refuses_oversize_objects_and_generated_files_on_write(tmp_path: Path) -> None:
    # Amended under DEV-ingress-01 (pack/2): the bound must also cover the initial
    # state (128 bytes), so a 64-byte bound correctly refuses Store.init itself.
    # The write-side rule is exercised with a bound the empty store fits under.
    store = Store.init(str(tmp_path / "s"), max_artifact_bytes=256)
    with store.write_locked():
        with pytest.raises(StoreError, match="bound"):
            store.put_object(ArtifactKind.OBJECT, b"x" * 257)
    with pytest.raises(StoreError, match="bound"):
        store.write_generated("map.json", b"y" * 257)
    store.close()


def test_p1_store_init_itself_honours_the_bound(tmp_path: Path) -> None:
    # The corollary the deviation surfaced: a store may never be initialized with
    # a state that exceeds its own read bound.
    with pytest.raises(StoreError, match="bound"):
        Store.init(str(tmp_path / "s"), max_artifact_bytes=64)


def test_p1_store_rejects_nonsensical_bound_at_construction(tmp_path: Path) -> None:
    with pytest.raises(StoreError):
        Store.init(str(tmp_path / "s"), max_artifact_bytes=0)


# ---- P1: migration must never commit a candidate that fails STRUCTURAL/lifecycle invariants ----


def test_p1_migration_refuses_structurally_invalid_candidate_before_touching_target(tmp_path: Path) -> None:
    from dataclasses import replace

    from vouch_mk2.core import AnchorMode
    from vouch_mk2.migrate import MigrationRefused, apply_migration, plan_migration, read_ledger

    fixture = Path(__file__).parent / "data" / "vouch06-fixture-ledger.json"
    plan = plan_migration(read_ledger(fixture.read_bytes()), AnchorMode.NONE, drop_qualified=True)
    # A dependency cycle is a STRUCTURAL invariant violation (D039's discriminator).
    a, b = plan.candidate.units[0], plan.candidate.units[1]
    cyclic = replace(
        plan.candidate,
        units=(replace(a, depends_on=(b.name,)), replace(b, depends_on=(a.name,))) + plan.candidate.units[2:],
    )
    broken = replace(plan, candidate=cyclic)
    target = tmp_path / "proj"
    target.mkdir()
    before = sorted(str(p) for p in target.rglob("*"))
    with pytest.raises(MigrationRefused):
        apply_migration(broken, str(target), None)
    assert sorted(str(p) for p in target.rglob("*")) == before, "refusal must precede every write"


# ---- P2: evidence kind/outcome coherence must be re-checked at rest ----


def test_p2_chain_walk_rejects_incoherent_red_that_passed(tmp_path: Path) -> None:
    from vouch_mk2.core import RecordEvidence
    from vouch_mk2.verify import run_artifact_checks
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict

    with Store.init(str(tmp_path / "s")) as store:
        store.commit(Snapshot(revision=1, units=(_unit("u", status=UnitStatus.SPECIFIED),)))
        # A canonical, correctly content-addressed RED event whose outcome is PASSED.
        bad = EvidenceEvent(UnitRef("u", "v1"), 1, "GENESIS", EvidenceKind.RED, "pytest", EvidenceOutcome.TESTS_PASSED, "t")
        data = encode_evidence_event(bad)
        digest = digest_of(ArtifactKind.EVIDENCE, data)
        # decide() refuses it — so plant it directly, as a hostile store would.
        assert isinstance(decide(store.read_snapshot(), RecordEvidence(bad, digest)), Refused)
        with store.write_locked():
            store.put_object(ArtifactKind.EVIDENCE, data)
        from vouch_mk2.core.model import EvidenceHead
        snapshot = store.read_snapshot()
        forged = Snapshot(
            revision=snapshot.revision + 1, units=snapshot.units,
            evidence_heads=(EvidenceHead("u", digest, 1, "GENESIS"),),
        )
        store.commit(forged)
        result = next(r for r in run_artifact_checks(store, forged) if r.check_id == mk2.CHECK_EVIDENCE_CHAINS)
        assert result.verdict is Verdict.FAIL
        assert "coheren" in result.detail or "outcome" in result.detail


# ---- P2: release-bound-rostered must compare exact refs ----


def test_p2_release_bound_rostered_uses_exact_refs() -> None:
    from vouch_mk2.checks.standing import check_release_bound_units_rostered
    from vouch_mk2.ir.schema import Verdict

    snapshot = Snapshot(
        revision=1,
        units=(_unit("u", "v1", UnitStatus.SUPERSEDED), _unit("u", "v2", UnitStatus.RELEASE_BOUND)),
        releases=(
            Release("r1", ReleaseArchetype.OPERATIONAL, ReleaseState.RETIRED, (UnitRef("u", "v1"),), CRITERIA,
                    ClosureRecord("t", ClosureTriggerKind.OPERATOR, (("u", "v1", "superseded"),), True, ())),
        ),
    )
    # Active u@v2 is release-bound but NO release references u@v2 — only historical u@v1.
    assert check_release_bound_units_rostered(snapshot).verdict is Verdict.FAIL


# ---- P2: identifier grammar — tuple identity everywhere; no injection into generated context ----


def test_p2_map_uses_tuple_identity_not_string_concatenation() -> None:
    from vouch_mk2.projections import system_map
    from vouch_mk2.store import decode_system_map, encode_system_map

    snapshot = Snapshot(revision=1, units=(_unit("a@b", "c", UnitStatus.SPECIFIED), _unit("a", "b@c", UnitStatus.SPECIFIED)))
    encoded = encode_system_map(system_map(snapshot), digest_of(ArtifactKind.STATE, encode_snapshot(snapshot)))
    decoded, _ = decode_system_map(encoded)  # must NOT raise a false duplicate
    assert len(decoded.units) == 2


def test_p2_identifiers_with_newlines_or_control_chars_are_refused_at_ingress() -> None:
    from vouch_mk2.core import SpecifyUnit

    for bad in ("evil\nname", "tab\tname", "", " leading", "trailing "):
        decision = decide(Snapshot(), SpecifyUnit(bad, "v1"))
        assert isinstance(decision, Refused), repr(bad)
    hostile = Snapshot(revision=1, units=(Unit("evil\n# injected", "v1", UnitStatus.SPECIFIED),))
    with pytest.raises(DecodeError):
        decode_snapshot(encode_snapshot(hostile))


def test_p2_agent_context_escapes_markdown_from_identifiers() -> None:
    from vouch_mk2.projections import agent_context

    # Even a name the grammar allows must never break out of its list item.
    snapshot = Snapshot(revision=1, units=(Unit("[link](x) `code` *em*", "v1", UnitStatus.SPECIFIED),))
    text = agent_context(snapshot)
    assert "[link](x)" not in text and "*em*" not in text


# ---- P2: lock builder must refuse a symlinked package ROOT ----


def test_p2_build_lock_refuses_symlinked_package_root(tmp_path: Path) -> None:
    from vouch_mk2.lock import build_lock

    real_src = Path(__file__).resolve().parents[1] / "src"
    os.symlink(real_src / "vouch_mk2", tmp_path / "vouch_mk2")
    with pytest.raises(StoreError, match="symlink"):
        build_lock(str(tmp_path))


# ---- P2: init must never overwrite an existing, differing suggestion sidecar ----


def test_p2_init_never_overwrites_a_differing_existing_sidecar(tmp_path: Path) -> None:
    from vouch_mk2.init import InitError, init_project
    from vouch_mk2.lock import build_lock

    lock = build_lock(str(Path(__file__).resolve().parents[1] / "src"))
    project = tmp_path / "proj"
    (project / ".github" / "workflows").mkdir(parents=True)
    (project / ".github" / "workflows" / "vouch.yml").write_bytes(b"human workflow\n")
    sidecar = project / ".github" / "workflows" / "vouch.yml.vouch-suggested"
    sidecar.write_bytes(b"user-owned suggestion\n")
    try:
        init_project(str(project), lock)
    except InitError:
        pass  # refusing is acceptable
    assert sidecar.read_bytes() == b"user-owned suggestion\n", "an existing differing sidecar is user content"


# ---- P3: the IR must refuse two targets for one (source, command) ----


def test_p3_ir_validate_refuses_ambiguous_source_command() -> None:
    from vouch_mk2.ir.schema import IrIdentity, PolicyIr, StateMachine, Transition, validate

    ambiguous = PolicyIr(
        identity=IrIdentity("x", "t", "t"),
        machines=(StateMachine("m", ("a", "b", "c"), "a", (
            Transition("a", "go", "b"), Transition("a", "go", "c"),
        )),),
    )
    assert any(v.code == "transition.ambiguous-command" for v in validate(ambiguous))


# ---- pack/3 (DEV-ingress-02): closure snapshots are ref-keyed; '@' refused in name/version ----


def test_p2_closure_snapshot_is_ref_keyed_and_survives_two_version_roster() -> None:
    from vouch_mk2.core import RollBack
    from vouch_mk2.core.invariants import validate_snapshot

    # A D-9 roster holding two versions of one name: rolling back must produce a
    # closure the validator ACCEPTS, with one row per exact ref.
    roster = (UnitRef("u", "v1"), UnitRef("u", "v2"))
    snapshot = Snapshot(
        revision=1,
        units=(_unit("u", "v1", UnitStatus.SUPERSEDED), _unit("u", "v2", UnitStatus.RELEASE_BOUND)),
        releases=(Release("r", ReleaseArchetype.OPERATIONAL, ReleaseState.DEPLOYING, roster, CRITERIA, None),),
    )
    assert validate_snapshot(snapshot) == ()
    decision = decide(snapshot, RollBack("r", trigger="operator"))
    assert isinstance(decision, Accepted), decision
    after = apply(snapshot, decision.events)
    assert validate_snapshot(after) == (), validate_snapshot(after)
    release = after.release("r")
    assert release is not None
    closure = release.closure
    assert closure is not None
    assert closure.roster_snapshot == (("u", "v1", "superseded"), ("u", "v2", "release-bound"))
    # and it round-trips through the codec
    assert decode_snapshot(encode_snapshot(after)) == after


def test_p2_at_sign_refused_in_unit_name_and_version() -> None:
    from vouch_mk2.core import SpecifyUnit

    assert isinstance(decide(Snapshot(), SpecifyUnit("a@b", "v1")), Refused)
    assert isinstance(decide(Snapshot(), SpecifyUnit("a", "b@c")), Refused)
    assert isinstance(decide(Snapshot(), SpecifyUnit("a", "v1")), Accepted)
    hostile = Snapshot(revision=1, units=(Unit("a@b", "c", UnitStatus.SPECIFIED),))
    with pytest.raises(DecodeError):
        decode_snapshot(encode_snapshot(hostile))
    # pack versions keep '@' (they are never parsed as NAME@VERSION)
    from vouch_mk2.core import FreezeAcceptance
    ok = Snapshot(revision=1, units=(Unit("a", "v1", UnitStatus.SPECIFIED),))
    assert isinstance(decide(ok, FreezeAcceptance("a", "pack/3@abc123")), Accepted)
