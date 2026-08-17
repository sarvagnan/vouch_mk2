"""Gate and deviation-chain regressions.

Each test names the 0.6 defect class it keeps closed: D046 (ungated publication),
I4 (publication leaves an open deviation), the NAME@VERSION identity chain, and the
D-3 amendment-impact and no-auto-unblock semantics.
"""

from __future__ import annotations

from vouch_mk2.core import (
    Accepted,
    AcceptanceFrozen,
    AmendDeviation,
    BeginReverify,
    CloseDeviation,
    Command,
    Deviation,
    DeviationAmended,
    DeviationStatus,
    DeviationType,
    FreezeAcceptance,
    MarkDone,
    MarkReady,
    OpenDeviation,
    Publish,
    Refused,
    RejectDeviation,
    Respecify,
    Snapshot,
    SpecifyUnit,
    StartWork,
    Unit,
    UnitBlocked,
    UnitInvalidated,
    UnitPublished,
    UnitRef,
    UnitStatus,
    apply,
    blocking_deviations,
    decide,
)
from vouch_mk2.ir.schema import Verdict


def refused_codes(snapshot: Snapshot, command: Command) -> tuple[str, ...]:
    decision = decide(snapshot, command)
    assert isinstance(decision, Refused)
    return tuple(v.code for v in decision.violations)


def accepted(snapshot: Snapshot, command: Command) -> Snapshot:
    decision = decide(snapshot, command)
    assert isinstance(decision, Accepted), decision
    return apply(snapshot, decision.events)


def unit(
    name: str,
    status: UnitStatus,
    *,
    version: str = "v1",
    depends_on: tuple[str, ...] = (),
    pack: str = "p1",
) -> Unit:
    return Unit(
        name=name,
        version=version,
        status=status,
        depends_on=depends_on,
        acceptance_pack_version=pack,
        acceptance_pack_digest="d" * 64 if pack else "",
    )


def test_full_happy_path_walkthrough() -> None:
    s = Snapshot()
    s = accepted(s, SpecifyUnit(name="u", version="v1"))
    s = accepted(s, FreezeAcceptance(name="u", pack_version="p1"))
    s = accepted(s, MarkReady(name="u"))
    s = accepted(s, StartWork(name="u"))
    s = accepted(s, MarkDone(name="u"))
    s = accepted(s, Publish(name="u"))
    u = s.unit("u")
    assert u is not None
    assert u.status is UnitStatus.PUBLISHED
    assert u.acceptance_pack_version == "p1"
    assert s.revision == 6


def test_publication_gate_record_is_carried_and_all_pass() -> None:
    s = Snapshot(
        units=(unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE),)
    )
    decision = decide(s, Publish(name="u"))
    assert isinstance(decision, Accepted)
    (event,) = decision.events
    assert isinstance(event, UnitPublished)
    assert event.gate.transition == "done-pending-acceptance->published"
    assert {c.obligation_id for c in event.gate.checks} == {
        "gate.dod.pack-frozen",
        "gate.dod.dependencies-satisfied",
        "gate.dod.no-blocking-deviations",
        "gate.dod.tier-evidence",
        "gate.dod.current-pack-green",
    }
    assert all(c.verdict is Verdict.PASS for c in event.gate.checks)
    _regression_tier_evidence_gate_refuses_standard_and_high_publication()
    _regression_publication_provenance_survives_lifecycle_status_changes()
    _regression_high_attestation_scopes_to_superseded_publication_provenance()


def _regression_tier_evidence_gate_refuses_standard_and_high_publication() -> None:
    from vouch_mk2.core import EvidenceHead, EvidencePackSummary, Tier

    standard = Snapshot(
        tier=Tier.STANDARD,
        units=(unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE),),
    )
    assert refused_codes(standard, Publish("u")) == (
        "gate.dod.tier-evidence",
        "gate.dod.current-pack-green",
    )

    unanchored = Snapshot(
        tier=Tier.HIGH,
        units=(unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE),),
        evidence_heads=(
            EvidenceHead(
                "u",
                "a" * 64,
                1,
                "GENESIS",
                "v1",
                (EvidencePackSummary("d" * 64, True, True),),
            ),
        ),
    )
    decision = decide(unanchored, Publish("u"))
    assert isinstance(decision, Refused)
    assert decision.violations[0].code == "gate.dod.tier-evidence"
    assert "anchor" in decision.violations[0].detail
    assert "certificate" in decision.violations[0].detail

    ready = Snapshot(
        tier=Tier.HIGH,
        units=unanchored.units,
        evidence_heads=(
            EvidenceHead(
                "u",
                "a" * 64,
                1,
                "a" * 64,
                "v1",
                (EvidencePackSummary("d" * 64, True, True),),
            ),
        ),
        formal_certificate_digest="f" * 64,
    )
    assert isinstance(decide(ready, Publish("u")), Accepted)


def _regression_publication_provenance_survives_lifecycle_status_changes() -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core import SetTier, Supersede, Tier
    from vouch_mk2.ir import mk2

    snapshot = Snapshot(units=(unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE),))
    snapshot = accepted(snapshot, Publish("u"))
    published = snapshot.unit("u")
    assert published is not None and published.published_once
    snapshot = accepted(snapshot, SetTier(Tier.STANDARD))

    def evidence_verdict(state: Snapshot) -> Verdict:
        return next(
            row.verdict
            for row in run_checks(state).mechanical
            if row.check_id == mk2.CHECK_TIER_EVIDENCE_REQUIRED
        )

    assert evidence_verdict(snapshot) is Verdict.FAIL
    snapshot = accepted(
        snapshot,
        OpenDeviation("d1", DeviationType.PACK_DEFECT, published.ref()),
    )
    assert snapshot.unit("u") is not None
    assert snapshot.unit("u").published_once  # type: ignore[union-attr]
    assert evidence_verdict(snapshot) is Verdict.FAIL
    snapshot = accepted(snapshot, RejectDeviation("d1"))
    snapshot = accepted(snapshot, CloseDeviation("d1"))
    snapshot = accepted(snapshot, BeginReverify("u"))
    assert evidence_verdict(snapshot) is Verdict.FAIL
    snapshot = accepted(snapshot, Supersede("u"))
    assert snapshot.unit("u") is not None
    assert snapshot.unit("u").published_once  # type: ignore[union-attr]
    assert evidence_verdict(snapshot) is Verdict.FAIL


def _regression_high_attestation_scopes_to_superseded_publication_provenance() -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core import EvidenceHead, Tier
    from vouch_mk2.ir import mk2

    snapshot = Snapshot(
        tier=Tier.HIGH,
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.SUPERSEDED,
                acceptance_pack_version="p1",
                published_once=True,
            ),
        ),
        evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS", "v1"),),
    )
    row = next(
        result
        for result in run_checks(snapshot).mechanical
        if result.check_id == mk2.CHECK_TIER_HIGH_ATTESTATION
    )
    assert row.verdict is Verdict.FAIL
    assert "anchor" in row.detail and "certificate" in row.detail


def test_d046_publication_with_unsatisfied_dependency_is_refused() -> None:
    s = Snapshot(
        units=(
            unit("up", UnitStatus.IN_PROGRESS),
            unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE, depends_on=("up",)),
        )
    )
    assert refused_codes(s, Publish(name="u")) == ("gate.dependency-unsatisfied",)
    s2 = Snapshot(
        units=(
            unit("up", UnitStatus.PUBLISHED),
            unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE, depends_on=("up",)),
        )
    )
    assert isinstance(decide(s2, Publish(name="u")), Accepted)


def test_publication_with_vanished_dependency_names_the_right_defect() -> None:
    # A dependency that does not exist at all is an existence defect, not a
    # status defect — the code must say so or an operator repairs the wrong thing.
    s = Snapshot(
        units=(unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE, depends_on=("ghost",)),)
    )
    assert refused_codes(s, Publish(name="u")) == ("gate.dependency-unknown",)


def test_i4_publication_with_blocking_deviation_is_refused() -> None:
    # Even against a fabricated snapshot whose status drifted (the unit claims
    # done-pending-acceptance while a rejected deviation is bound to it), the
    # gate re-derives the blocker set and refuses.
    for status in (DeviationStatus.OPEN, DeviationStatus.REJECTED):
        s = Snapshot(
            units=(unit("u", UnitStatus.DONE_PENDING_ACCEPTANCE),),
            deviations=(
                Deviation(
                    deviation_id="d1",
                    deviation_type=DeviationType.PACK_DEFECT,
                    unit=UnitRef(name="u", version="v1"),
                    status=status,
                ),
            ),
        )
        assert refused_codes(s, Publish(name="u")) == ("gate.blocking-deviations",)


def test_freeze_requires_pack_version() -> None:
    s = Snapshot(units=(unit("u", UnitStatus.SPECIFIED, pack=""),))
    assert refused_codes(s, FreezeAcceptance(name="u", pack_version="")) == (
        "gate.pack-version-missing",
    )


def test_freeze_requires_dependencies_to_exist() -> None:
    s = Snapshot(
        units=(unit("u", UnitStatus.SPECIFIED, depends_on=("ghost",), pack=""),)
    )
    assert refused_codes(s, FreezeAcceptance(name="u", pack_version="p1")) == (
        "gate.dependency-unknown",
    )


def test_specify_refuses_duplicates_and_unknown_dependencies() -> None:
    s = Snapshot(units=(unit("u", UnitStatus.SPECIFIED, pack=""),))
    assert refused_codes(s, SpecifyUnit(name="u", version="v2")) == ("unit.exists",)
    assert refused_codes(
        s, SpecifyUnit(name="w", version="v1", depends_on=("ghost",))
    ) == ("gate.dependency-unknown",)
    assert refused_codes(s, SpecifyUnit(name="", version="v1")) == (
        "identifier.invalid",
    )


def test_stale_name_version_binding_is_refused() -> None:
    s = Snapshot(units=(unit("u", UnitStatus.PUBLISHED, version="v2"),))
    assert refused_codes(
        s,
        OpenDeviation(
            deviation_id="d1",
            deviation_type=DeviationType.CONTRACT_CONFLICT,
            unit=UnitRef(name="u", version="v1"),
        ),
    ) == ("deviation.binding-stale",)


def test_open_deviation_blocks_and_accumulates() -> None:
    s = Snapshot(units=(unit("u", UnitStatus.IN_PROGRESS),))
    decision = decide(
        s,
        OpenDeviation(
            deviation_id="d1",
            deviation_type=DeviationType.PACK_DEFECT,
            unit=UnitRef(name="u", version="v1"),
        ),
    )
    assert isinstance(decision, Accepted)
    assert any(
        isinstance(e, UnitBlocked) and e.name == "u" for e in decision.events
    )
    s = apply(s, decision.events)

    # Second deviation accumulates without a second transition.
    decision2 = decide(
        s,
        OpenDeviation(
            deviation_id="d2",
            deviation_type=DeviationType.PLAN_GAP,
            unit=UnitRef(name="u", version="v1"),
        ),
    )
    assert isinstance(decision2, Accepted)
    assert not any(isinstance(e, UnitBlocked) for e in decision2.events)
    s = apply(s, decision2.events)
    u = s.unit("u")
    assert u is not None and u.status is UnitStatus.BLOCKED
    assert tuple(d.deviation_id for d in blocking_deviations(s, u)) == ("d1", "d2")

    # Unblocking is gated on ALL blockers closing (multi-blocker semantics).
    assert refused_codes(s, BeginReverify(name="u")) == ("gate.blocking-deviations",)
    s = accepted(s, RejectDeviation(deviation_id="d1"))
    s = accepted(s, CloseDeviation(deviation_id="d1"))
    assert refused_codes(s, BeginReverify(name="u")) == ("gate.blocking-deviations",)
    s = accepted(s, RejectDeviation(deviation_id="d2"))
    s = accepted(s, CloseDeviation(deviation_id="d2"))
    u = s.unit("u")
    assert u is not None and u.status is UnitStatus.BLOCKED  # D-3: no auto-unblock
    s = accepted(s, BeginReverify(name="u"))
    u = s.unit("u")
    assert u is not None and u.status is UnitStatus.RE_VERIFYING


def test_respecify_clears_pack_and_requires_no_blockers() -> None:
    s = Snapshot(
        units=(unit("u", UnitStatus.BLOCKED),),
        deviations=(
            Deviation(
                deviation_id="d1",
                deviation_type=DeviationType.PACK_DEFECT,
                unit=UnitRef(name="u", version="v1"),
                status=DeviationStatus.OPEN,
            ),
        ),
    )
    assert refused_codes(s, Respecify(name="u")) == ("gate.blocking-deviations",)
    s = accepted(s, AmendDeviation(deviation_id="d1", amendment_version="v2"))
    # The amend invalidated the unit; respecify is the D-3 escape edge and is
    # legal now that the amended deviation no longer blocks.
    s = accepted(s, Respecify(name="u"))
    u = s.unit("u")
    assert u is not None
    assert u.status is UnitStatus.SPECIFIED
    assert u.acceptance_pack_version == ""

    s2 = Snapshot(units=(unit("u", UnitStatus.BLOCKED),))
    s2 = accepted(s2, Respecify(name="u"))
    u = s2.unit("u")
    assert u is not None
    assert u.status is UnitStatus.SPECIFIED
    assert u.acceptance_pack_version == ""


def test_begin_reverify_requires_recorded_pack() -> None:
    s = Snapshot(units=(unit("u", UnitStatus.BLOCKED, pack=""),))
    assert refused_codes(s, BeginReverify(name="u")) == ("gate.pack-version-missing",)


def test_amendment_impact_closure_d3() -> None:
    # u <- v <- w (superseded) and v <- x (specified): the closure invalidates
    # u, v, x and leaves the superseded w untouched.
    s = Snapshot(
        units=(
            unit("u", UnitStatus.BLOCKED),
            unit("v", UnitStatus.PUBLISHED, depends_on=("u",)),
            unit("w", UnitStatus.SUPERSEDED, depends_on=("v",)),
            unit("x", UnitStatus.SPECIFIED, depends_on=("v",), pack=""),
        ),
        deviations=(
            Deviation(
                deviation_id="d1",
                deviation_type=DeviationType.CONTRACT_CONFLICT,
                unit=UnitRef(name="u", version="v1"),
                status=DeviationStatus.OPEN,
            ),
        ),
    )
    decision = decide(s, AmendDeviation(deviation_id="d1", amendment_version="v2"))
    assert isinstance(decision, Accepted)
    amended = [e for e in decision.events if isinstance(e, DeviationAmended)]
    assert amended and amended[0].impact_set == ("u", "v", "x")
    invalidated = {
        e.name for e in decision.events if isinstance(e, UnitInvalidated)
    }
    assert invalidated == {"u", "v", "x"}
    after = apply(s, decision.events)
    statuses = {u.name: u.status for u in after.units}
    assert statuses["u"] is UnitStatus.INVALIDATED
    assert statuses["v"] is UnitStatus.INVALIDATED
    assert statuses["x"] is UnitStatus.INVALIDATED
    assert statuses["w"] is UnitStatus.SUPERSEDED
    d = after.deviation("d1")
    assert d is not None
    assert d.status is DeviationStatus.AMENDED
    assert d.amendment_version == "v2"
    assert d.impact_set == ("u", "v", "x")


def test_amend_requires_amendment_version() -> None:
    s = Snapshot(
        units=(unit("u", UnitStatus.BLOCKED),),
        deviations=(
            Deviation(
                deviation_id="d1",
                deviation_type=DeviationType.PACK_DEFECT,
                unit=UnitRef(name="u", version="v1"),
                status=DeviationStatus.OPEN,
            ),
        ),
    )
    assert refused_codes(s, AmendDeviation(deviation_id="d1", amendment_version="")) == (
        "deviation.amendment-version-required",
    )


def test_unknown_references_are_named_violations() -> None:
    s = Snapshot()
    assert refused_codes(s, Publish(name="ghost")) == ("unit.unknown",)
    assert refused_codes(s, CloseDeviation(deviation_id="ghost")) == (
        "deviation.unknown",
    )
    assert refused_codes(
        s,
        OpenDeviation(
            deviation_id="d1",
            deviation_type=DeviationType.PLAN_GAP,
            unit=UnitRef(name="ghost", version="v1"),
        ),
    ) == ("unit.unknown",)


def test_frozen_event_carries_dor_gate_record() -> None:
    s = Snapshot(units=(unit("u", UnitStatus.SPECIFIED, pack=""),))
    decision = decide(s, FreezeAcceptance(name="u", pack_version="p1"))
    assert isinstance(decision, Accepted)
    (event,) = decision.events
    assert isinstance(event, AcceptanceFrozen)
    assert event.gate.transition == "specified->acceptance-frozen"
    assert all(c.verdict is Verdict.PASS for c in event.gate.checks)


def test_superseded_records_are_inert_in_amendment_impact() -> None:
    # D-9: a superseded version's dependency edges connect nothing. Amending an
    # upstream that only the SUPERSEDED u@v1 depended on must not invalidate the
    # current u@v2 (which has no such dependency).
    s = Snapshot(
        units=(
            unit("x", UnitStatus.BLOCKED, pack="p"),
            Unit("u", "v1", UnitStatus.SUPERSEDED, depends_on=("x",), acceptance_pack_version="p1"),
            Unit("u", "v2", UnitStatus.PUBLISHED, depends_on=(), acceptance_pack_version="p2"),
        ),
        deviations=(
            Deviation("d", DeviationType.CONTRACT_CONFLICT, UnitRef("x", "v1"), DeviationStatus.OPEN),
        ),
    )
    decision = decide(s, AmendDeviation(deviation_id="d", amendment_version="v2"))
    assert isinstance(decision, Accepted)
    invalidated = {e.name for e in decision.events if isinstance(e, UnitInvalidated)}
    assert invalidated == {"x"}
    after = apply(s, decision.events)
    current = after.unit("u")
    assert current is not None and current.version == "v2"
    assert current.status is UnitStatus.PUBLISHED
    v1 = after.unit_ref(UnitRef("u", "v1"))
    assert v1 is not None and v1.status is UnitStatus.SUPERSEDED


def test_evidence_cannot_bind_a_superseded_version() -> None:
    from vouch_mk2.core import EvidenceEvent, EvidenceKind, EvidenceOutcome, RecordEvidence

    s = Snapshot(
        units=(
            Unit("u", "v1", UnitStatus.SUPERSEDED, acceptance_pack_version="p1"),
            Unit("u", "v2", UnitStatus.PUBLISHED, acceptance_pack_version="p2"),
        )
    )
    stale = RecordEvidence(
        EvidenceEvent(UnitRef("u", "v1"), 1, "GENESIS", EvidenceKind.RED, "pytest",
                      EvidenceOutcome.TESTS_FAILED, "t1"),
        "1" * 64,
    )
    assert refused_codes(s, stale) == ("evidence.binding-stale",)
    current = RecordEvidence(
        EvidenceEvent(UnitRef("u", "v2"), 1, "GENESIS", EvidenceKind.RED, "pytest",
                      EvidenceOutcome.TESTS_FAILED, "t1"),
        "1" * 64,
    )
    assert isinstance(decide(s, current), Accepted)


def test_current_record_fallback_is_order_independent() -> None:
    a = Unit("u", "v1", UnitStatus.SUPERSEDED, acceptance_pack_version="p")
    b = Unit("u", "v2", UnitStatus.SUPERSEDED, acceptance_pack_version="p")
    forward = Snapshot(units=(a, b)).unit("u")
    backward = Snapshot(units=(b, a)).unit("u")
    assert forward is not None and backward is not None
    assert forward.version == backward.version == "v2"
