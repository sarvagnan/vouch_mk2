"""The canonical finite command universes used by the formal checker.

A shrink must be explained in the commit; dropping a command from a universe
is a coverage decision, not a fix.
"""

# Pinned coverage counts (2026-08-17), states/transitions:
# - core-v1 264,462/1,381,350: evidence.no-pack removes the previously accepted
#   RecordEvidence edges before freeze and after respecify clears the pack. No
#   command was dropped; the smaller relation is the new D-17 refusal surface.
# - history-v1 31,416/94,280: restored u's seeded-history respecify loop with
#   fresh p3 (p2 is already current) while retaining pack-binding@1 coverage.
# - tier-v1 294/2,024: D-18 adds both CI-policy values plus the valid
#   mark/clear exception cycle. The whitespace-reason and missing-exception
#   commands remain in the universe to cover their refusal paths; accepted
#   policy/exception facts multiply the prior tier/evidence state shapes.

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.core import (
    AdvanceAnchor,
    AmendDeviation,
    AnchorMode,
    BeginReverify,
    BindPack,
    CloseDeviation,
    ClearOpenInCi,
    Command,
    CompleteRelease,
    Deviation,
    DeviationStatus,
    DeviationType,
    CiPolicy,
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    FreezeAcceptance,
    LaunchCriteria,
    MarkDone,
    MarkOpenInCi,
    MarkFailedUnrecovered,
    MarkReady,
    OpenDeviation,
    PackEntry,
    PackInventory,
    PrepareRelease,
    Publish,
    RecordEvidence,
    RebindPack,
    RejectDeviation,
    Respecify,
    RetireRelease,
    RollBack,
    SetAnchorMode,
    SetCiPolicy,
    SetTier,
    SetReleaseCriteria,
    SpecifyUnit,
    StartDeploy,
    StartVerification,
    StartWork,
    Supersede,
    Snapshot,
    Tier,
    Unit,
    UnitRef,
    UnitStatus,
)


@dataclass(frozen=True, slots=True)
class Universe:
    name: str
    commands: tuple[Command, ...]
    initial: Snapshot
    invariant_ids: tuple[str, ...] = ()


_B = UnitRef(name="b", version="v1")
_A = UnitRef(name="a", version="v1")
_FIRST_DIGEST = "1" * 64
_SECOND_DIGEST = "2" * 64
_CRITERIA = LaunchCriteria(
    launch_criteria=("healthy",),
    verification_window="1h",
    rollback_triggers=("errors",),
    go_no_go_authority="ops",
)


CORE_V1 = Universe(
    name="core-v1",
    commands=(
        SpecifyUnit(name="b", version="v1"),
        SpecifyUnit(name="a", version="v1", depends_on=("b",)),
        FreezeAcceptance(name="b", pack_version="p-b"),
        FreezeAcceptance(name="b", pack_version="p-b2"),
        MarkReady(name="b"),
        StartWork(name="b"),
        MarkDone(name="b"),
        Publish(name="b"),
        FreezeAcceptance(name="a", pack_version="p-a"),
        MarkReady(name="a"),
        StartWork(name="a"),
        MarkDone(name="a"),
        Publish(name="a"),
        # EXIT coverage must exist for BOTH units: an asymmetric universe
        # manufactures fake dead-ends (a blocked "a" with no exit command looks
        # deadlocked even though decide() would accept one) and falsifies the
        # liveness invariant M6 against the universe rather than the system.
        # "a" gets supersession — a terminating exit from every stuck state —
        # while "b" exercises the full recovery loop (re-verify, respecify) — a second
        # label keeps re-freeze reachable now that freeze refuses a reused label;
        # giving both units the loop exceeds the exploration bound.
        Supersede(name="b"),
        BeginReverify(name="b"),
        Respecify(name="b"),
        Supersede(name="a"),
        OpenDeviation(
            deviation_id="d1",
            deviation_type=DeviationType.PACK_DEFECT,
            unit=_B,
        ),
        AmendDeviation(deviation_id="d1", amendment_version="v2"),
        CloseDeviation(deviation_id="d1"),
        OpenDeviation(
            deviation_id="d2",
            deviation_type=DeviationType.PLAN_GAP,
            unit=_A,
        ),
        RejectDeviation(deviation_id="d2"),
        CloseDeviation(deviation_id="d2"),
        PrepareRelease(release_id="r1", includes=("a", "b")),
        SetReleaseCriteria(release_id="r1", criteria=_CRITERIA),
        StartDeploy(release_id="r1"),
        StartVerification(release_id="r1"),
        CompleteRelease(release_id="r1"),
        RollBack(release_id="r1", trigger="errors"),
        MarkFailedUnrecovered(release_id="r1", trigger="unrecovered"),
        RetireRelease(release_id="r1", trigger="retired"),
        SetAnchorMode(AnchorMode.NONE),
        SetAnchorMode(AnchorMode.CI),
        RecordEvidence(
            event=EvidenceEvent(
                unit=_B,
                sequence=1,
                parent="GENESIS",
                kind=EvidenceKind.RED,
                command_line="formal red",
                outcome=EvidenceOutcome.TESTS_FAILED,
                recorded_at="2026-08-13T00:00:00Z",
                pack_digest="",
            ),
            event_digest=_FIRST_DIGEST,
        ),
        RecordEvidence(
            event=EvidenceEvent(
                unit=_B,
                sequence=2,
                parent=_FIRST_DIGEST,
                kind=EvidenceKind.GREEN,
                command_line="formal green",
                outcome=EvidenceOutcome.TESTS_PASSED,
                recorded_at="2026-08-13T00:01:00Z",
                pack_digest="",
            ),
            event_digest=_SECOND_DIGEST,
        ),
        AdvanceAnchor(unit_name="b"),
    ),
    initial=Snapshot(),
)


# SEEDED, NOT REACHABLE: D-9's two-version history cannot yet be produced by a
# decide() command.  Migration can produce it, so it is a first-class formal
# start state until amendment-driven re-versioning exists.
HISTORY_V1_INITIAL = Snapshot(
    units=(
        Unit(
            name="u",
            version="v1",
            status=UnitStatus.SUPERSEDED,
            acceptance_pack_version="p1",
            published_once=True,
        ),
        Unit(
            name="u",
            version="v2",
            status=UnitStatus.PUBLISHED,
            acceptance_pack_version="p2",
            published_once=True,
        ),
        Unit(
            name="w",
            version="v1",
            status=UnitStatus.SPECIFIED,
            depends_on=("u",),
        ),
    ),
    deviations=(
        Deviation(
            deviation_id="d0",
            deviation_type=DeviationType.PACK_DEFECT,
            unit=UnitRef(name="u", version="v1"),
            status=DeviationStatus.CLOSED,
            amendment_version="v2",
            impact_set=("u",),
        ),
    ),
)

_U_V2 = UnitRef(name="u", version="v2")
_HISTORY_CRITERIA = LaunchCriteria(
    launch_criteria=("healthy",),
    verification_window="1h",
    rollback_triggers=("errors",),
    go_no_go_authority="ops",
)

HISTORY_V1 = Universe(
    name="history-v1",
    commands=(
        # Current u lifecycle from the seeded published record.
        FreezeAcceptance(name="u", pack_version="p2"),
        FreezeAcceptance(name="u", pack_version="p3"),
        BindPack(name="u", pack_digest="e" * 64),
        MarkReady(name="u"),
        StartWork(name="u"),
        MarkDone(name="u"),
        Publish(name="u"),
        Supersede(name="u"),
        BeginReverify(name="u"),
        Respecify(name="u"),
        OpenDeviation(
            deviation_id="d1",
            deviation_type=DeviationType.PACK_DEFECT,
            unit=_U_V2,
        ),
        AmendDeviation(deviation_id="d1", amendment_version="v3"),
        CloseDeviation(deviation_id="d1"),
        # Publish dependent w, then exercise one release over the current refs.
        FreezeAcceptance(name="w", pack_version="p-w"),
        BindPack(name="w", pack_digest="f" * 64),
        MarkReady(name="w"),
        StartWork(name="w"),
        MarkDone(name="w"),
        Publish(name="w"),
        Supersede(name="w"),
        PrepareRelease(release_id="r1", includes=("u", "w")),
        SetReleaseCriteria(release_id="r1", criteria=_HISTORY_CRITERIA),
        StartDeploy(release_id="r1"),
        StartVerification(release_id="r1"),
        CompleteRelease(release_id="r1"),
        RollBack(release_id="r1", trigger="errors"),
        MarkFailedUnrecovered(release_id="r1", trigger="unrecovered"),
        RetireRelease(release_id="r1", trigger="retired"),
        # As in core-v1, the dependent needs its own deviation exit so an
        # early supersession of u cannot manufacture a universe-only deadlock.
        OpenDeviation(
            deviation_id="d2",
            deviation_type=DeviationType.PLAN_GAP,
            unit=UnitRef(name="w", version="v1"),
        ),
        RejectDeviation(deviation_id="d2"),
        CloseDeviation(deviation_id="d2"),
    ),
    initial=HISTORY_V1_INITIAL,
)

TIER_V1 = Universe(
    name="tier-v1",
    commands=(
        SetTier(Tier.LIGHTWEIGHT),
        SetTier(Tier.STANDARD),
        SetTier(Tier.HIGH),
        SetCiPolicy(CiPolicy.LEDGER),
        SetCiPolicy(CiPolicy.STRICT),
        MarkOpenInCi(name="tier-unit", reason=" "),
        MarkOpenInCi(name="tier-unit", reason="bounded formal exception"),
        ClearOpenInCi(name="tier-unit"),
        SpecifyUnit(name="tier-unit", version="v1"),
        FreezeAcceptance(name="tier-unit", pack_version="pack-v1"),
        BindPack(name="tier-unit", pack_digest="e" * 64),
        MarkReady(name="tier-unit"),
        StartWork(name="tier-unit"),
        RecordEvidence(
            event=EvidenceEvent(
                unit=UnitRef(name="tier-unit", version="v1"),
                sequence=1,
                parent="GENESIS",
                kind=EvidenceKind.RED,
                command_line="formal tier red",
                outcome=EvidenceOutcome.TESTS_FAILED,
                recorded_at="2026-08-17T00:00:00Z",
                pack_digest="e" * 64,
            ),
            event_digest=_FIRST_DIGEST,
        ),
        RecordEvidence(
            event=EvidenceEvent(
                unit=UnitRef(name="tier-unit", version="v1"),
                sequence=2,
                parent=_FIRST_DIGEST,
                kind=EvidenceKind.GREEN,
                command_line="formal tier green",
                outcome=EvidenceOutcome.TESTS_PASSED,
                recorded_at="2026-08-17T00:01:00Z",
                pack_digest="e" * 64,
            ),
            event_digest=_SECOND_DIGEST,
        ),
        MarkDone(name="tier-unit"),
        Publish(name="tier-unit"),
        RebindPack(
            name="tier-unit",
            pack_digest="f" * 64,
            test_set_digest="f" * 64,
            reason="adapt to a changed shared interface",
            old_inventory=PackInventory(
                (PackEntry("tests/t.py", "e" * 64, 1),),
                pack_format=1,
            ),
            new_inventory=PackInventory(
                (PackEntry("tests/t.py", "f" * 64, 1),),
                pack_format=1,
            ),
        ),
    ),
    initial=Snapshot(tier=Tier.HIGH),
    invariant_ids=("M11.tier-never-lowers-after-commitment",),
)

UNIVERSES: tuple[Universe, ...] = (CORE_V1, HISTORY_V1, TIER_V1)
