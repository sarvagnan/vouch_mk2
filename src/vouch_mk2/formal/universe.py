"""The canonical finite command universes used by the formal checker."""

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.core import (
    AdvanceAnchor,
    AmendDeviation,
    AnchorMode,
    BeginReverify,
    CloseDeviation,
    Command,
    CompleteRelease,
    Deviation,
    DeviationStatus,
    DeviationType,
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    FreezeAcceptance,
    LaunchCriteria,
    MarkDone,
    MarkFailedUnrecovered,
    MarkReady,
    OpenDeviation,
    PrepareRelease,
    Publish,
    RecordEvidence,
    RejectDeviation,
    Respecify,
    RetireRelease,
    RollBack,
    SetAnchorMode,
    SetReleaseCriteria,
    SpecifyUnit,
    StartDeploy,
    StartVerification,
    StartWork,
    Supersede,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)


@dataclass(frozen=True, slots=True)
class Universe:
    name: str
    commands: tuple[Command, ...]
    initial: Snapshot


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
        # while "b" exercises the full recovery loop (re-verify, respecify);
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
        ),
        Unit(
            name="u",
            version="v2",
            status=UnitStatus.PUBLISHED,
            acceptance_pack_version="p2",
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

UNIVERSES: tuple[Universe, ...] = (CORE_V1, HISTORY_V1)
