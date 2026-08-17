from __future__ import annotations

from vouch_mk2.checks import (
    MECHANICAL_CHECKS,
    ORGANIZATIONAL_CHECKS,
    CheckReport,
    CheckResult,
    run_checks,
)
from vouch_mk2.checks.standing import (
    check_amended_deviations_complete,
    check_blocked_status_coherent,
    check_dependencies_exist,
    check_dependency_graph_acyclic,
    check_deviation_bindings_resolve,
    check_one_active_record_per_name,
    check_published_units_gated,
)
from vouch_mk2.core.model import (
    Deviation,
    DeviationStatus,
    DeviationType,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import ObligationScope, Verdict


def unit(
    name: str,
    status: UnitStatus = UnitStatus.SPECIFIED,
    *,
    version: str = "v1",
    depends_on: tuple[str, ...] = (),
    pack: str = "",
) -> Unit:
    return Unit(
        name=name,
        version=version,
        status=status,
        depends_on=depends_on,
        acceptance_pack_version=pack,
    )


def deviation(
    deviation_id: str,
    unit_name: str,
    *,
    unit_version: str = "v1",
    status: DeviationStatus = DeviationStatus.OPEN,
    amendment_version: str = "",
    impact_set: tuple[str, ...] = (),
) -> Deviation:
    return Deviation(
        deviation_id=deviation_id,
        deviation_type=DeviationType.PACK_DEFECT,
        unit=UnitRef(name=unit_name, version=unit_version),
        status=status,
        amendment_version=amendment_version,
        impact_set=impact_set,
    )


def result(report: CheckReport, check_id: str) -> CheckResult:
    for found in report.mechanical:
        if found.check_id == check_id:
            return found
    raise AssertionError(f"missing result {check_id!r}")


ARTIFACT_RESULTS = tuple(
    CheckResult(check_id=check_id, verdict=Verdict.PASS, detail="")
    for check_id in (
        mk2.CHECK_EVIDENCE_CHAINS,
        mk2.CHECK_EVIDENCE_ANCHORS,
        mk2.CHECK_BUNDLE_INVENTORIES,
        mk2.CHECK_FORMAL_CERTIFICATE,
        mk2.CHECK_MAP_FRESHNESS,
        mk2.CHECK_CONTEXT_FRESHNESS,
        mk2.CHECK_PACK_BOUND,
        mk2.CHECK_PACK_FILES_MATCH,
    )
)


def extra_evaluator(_snapshot: Snapshot) -> CheckResult:
    return CheckResult(check_id="check.extra", verdict=Verdict.PASS, detail="")


def test_real_registries_match_the_mk2_ir_and_meta_check_passes() -> None:
    declared_mechanical = {
        obligation.obligation_id
        for obligation in mk2.MK2.obligations
        if obligation.scope is ObligationScope.STANDING_CHECK
    }
    declared_organizational = {
        obligation.obligation_id
        for obligation in mk2.MK2.obligations
        if obligation.scope is ObligationScope.ORGANIZATIONAL
    }
    assert set(MECHANICAL_CHECKS) == declared_mechanical
    assert set(ORGANIZATIONAL_CHECKS) == declared_organizational
    meta = result(
        run_checks(Snapshot(), artifact_results=ARTIFACT_RESULTS),
        mk2.CHECK_REGISTRY_COMPLETE,
    )
    assert meta == CheckResult(
        check_id=mk2.CHECK_REGISTRY_COMPLETE,
        verdict=Verdict.PASS,
        detail="",
    )


def test_registry_meta_check_fails_for_missing_artifact_id() -> None:
    artifact_results = tuple(
        row for row in ARTIFACT_RESULTS if row.check_id != mk2.CHECK_EVIDENCE_ANCHORS
    )
    meta = result(
        run_checks(Snapshot(), artifact_results=artifact_results),
        mk2.CHECK_REGISTRY_COMPLETE,
    )
    assert meta.verdict is Verdict.FAIL
    assert mk2.CHECK_EVIDENCE_ANCHORS in meta.detail


def test_undeclared_artifact_result_is_guarded() -> None:
    report = run_checks(
        Snapshot(),
        artifact_results=ARTIFACT_RESULTS
        + (CheckResult("check.artifact-extra", Verdict.PASS, ""),),
    )
    extra = result(report, "check.artifact-extra")
    assert extra.verdict is Verdict.FAIL
    assert extra.detail == "undeclared artifact check"


def test_registry_meta_check_fails_for_missing_mechanical_id() -> None:
    mechanical = dict(MECHANICAL_CHECKS)
    del mechanical[mk2.CHECK_DEPENDENCIES_EXIST]
    meta = result(run_checks(Snapshot(), mechanical=mechanical), mk2.CHECK_REGISTRY_COMPLETE)
    assert meta.verdict is Verdict.FAIL
    assert mk2.CHECK_DEPENDENCIES_EXIST in meta.detail


def test_registry_meta_check_fails_for_extra_mechanical_id() -> None:
    mechanical = dict(MECHANICAL_CHECKS)
    mechanical["check.extra"] = extra_evaluator
    meta = result(run_checks(Snapshot(), mechanical=mechanical), mk2.CHECK_REGISTRY_COMPLETE)
    assert meta.verdict is Verdict.FAIL
    assert "check.extra" in meta.detail


def test_registry_meta_check_fails_for_missing_organizational_id() -> None:
    organizational = dict(ORGANIZATIONAL_CHECKS)
    del organizational[mk2.ORG_INDEPENDENCE]
    meta = result(
        run_checks(Snapshot(), organizational=organizational),
        mk2.CHECK_REGISTRY_COMPLETE,
    )
    assert meta.verdict is Verdict.FAIL
    assert mk2.ORG_INDEPENDENCE in meta.detail


def test_registry_meta_check_fails_for_extra_organizational_id() -> None:
    organizational = dict(ORGANIZATIONAL_CHECKS)
    organizational["org.extra"] = extra_evaluator
    meta = result(
        run_checks(Snapshot(), organizational=organizational),
        mk2.CHECK_REGISTRY_COMPLETE,
    )
    assert meta.verdict is Verdict.FAIL
    assert "org.extra" in meta.detail


def test_dependencies_exist_passes_for_resolved_dependencies() -> None:
    snapshot = Snapshot(units=(unit("a"), unit("b", depends_on=("a",))))
    assert check_dependencies_exist(snapshot).verdict is Verdict.PASS


def test_dependencies_exist_fails_for_dangling_dependency() -> None:
    found = check_dependencies_exist(
        Snapshot(units=(unit("b", depends_on=("ghost",)),))
    )
    assert found.verdict is Verdict.FAIL
    assert "b" in found.detail and "ghost" in found.detail


def test_dependency_graph_acyclic_passes_for_a_dag() -> None:
    snapshot = Snapshot(units=(unit("a"), unit("b", depends_on=("a",))))
    assert check_dependency_graph_acyclic(snapshot).verdict is Verdict.PASS


def test_dependency_graph_acyclic_names_a_cycle() -> None:
    snapshot = Snapshot(
        units=(unit("a", depends_on=("b",)), unit("b", depends_on=("a",)))
    )
    found = check_dependency_graph_acyclic(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "a" in found.detail and "b" in found.detail
    assert "cycle" in found.detail


def test_deviation_bindings_resolve_passes_for_current_binding() -> None:
    snapshot = Snapshot(units=(unit("u"),), deviations=(deviation("d", "u"),))
    assert check_deviation_bindings_resolve(snapshot).verdict is Verdict.PASS


def test_deviation_bindings_resolve_fails_for_missing_historical_binding() -> None:
    snapshot = Snapshot(
        units=(unit("u", version="v2"),),
        deviations=(deviation("d-stale", "u", unit_version="v1"),),
    )
    found = check_deviation_bindings_resolve(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "dangling" in found.detail and "d-stale" in found.detail


def test_deviation_bindings_resolve_fails_for_dangling_binding() -> None:
    found = check_deviation_bindings_resolve(
        Snapshot(deviations=(deviation("d-dangling", "ghost"),))
    )
    assert found.verdict is Verdict.FAIL
    assert "dangling" in found.detail and "d-dangling" in found.detail


def test_blocked_status_coherent_passes_for_matching_blocker() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.BLOCKED),),
        deviations=(deviation("d", "u"),),
    )
    result = check_blocked_status_coherent(snapshot)
    assert result.verdict is Verdict.PASS


def test_blocked_without_blocker_is_legal_awaiting_explicit_unblock() -> None:
    # D-3 no-auto-unblock: closing the last deviation leaves the unit blocked
    # until an explicit respecify/begin-reverify, so this state is coherent.
    snapshot = Snapshot(units=(unit("u", UnitStatus.BLOCKED),))
    result = check_blocked_status_coherent(snapshot)
    assert result.verdict is Verdict.NOT_APPLICABLE
    assert "subjects-present: blocking-deviations" in result.detail


def test_blocked_status_coherent_fails_for_blocker_on_nonblocked_unit() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.IN_PROGRESS),),
        deviations=(deviation("d", "u"),),
    )
    found = check_blocked_status_coherent(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "d" in found.detail and "in-progress" in found.detail


def test_blocked_status_coherent_allows_superseded_unit_with_blocker() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.SUPERSEDED),),
        deviations=(deviation("d", "u"),),
    )
    assert check_blocked_status_coherent(snapshot).verdict is Verdict.PASS


def test_published_units_gated_passes_when_all_three_conditions_hold() -> None:
    snapshot = Snapshot(
        units=(
            unit("up", UnitStatus.PUBLISHED, pack="p-up"),
            unit(
                "u",
                UnitStatus.PUBLISHED,
                depends_on=("up",),
                pack="p-u",
            ),
        )
    )
    assert check_published_units_gated(snapshot).verdict is Verdict.PASS


def test_published_units_gated_fails_for_empty_pack_only() -> None:
    found = check_published_units_gated(
        Snapshot(units=(unit("u", UnitStatus.PUBLISHED, pack=""),))
    )
    assert found.verdict is Verdict.FAIL
    assert "acceptance_pack_version" in found.detail
    assert "dependencies" not in found.detail and "blocking" not in found.detail


def test_published_units_gated_fails_for_invalidated_dependency_only() -> None:
    # The only upstream status that cannot legally coexist with a published
    # consumer: amendment invalidation propagates through the consumer closure,
    # so a published unit over an invalidated upstream is drift.
    snapshot = Snapshot(
        units=(
            unit("up", UnitStatus.INVALIDATED, pack="p-up"),
            unit("u", UnitStatus.PUBLISHED, depends_on=("up",), pack="p-u"),
        )
    )
    found = check_published_units_gated(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "invalidated" in found.detail and "up" in found.detail
    assert "acceptance_pack_version" not in found.detail
    assert "blocking" not in found.detail


def test_published_units_gated_accepts_blocked_dependency() -> None:
    # Legal history: upstream published, consumer published, upstream later
    # takes a deviation and blocks. The consumer stays healthy at rest.
    snapshot = Snapshot(
        units=(
            unit("up", UnitStatus.BLOCKED, pack="p-up"),
            unit("u", UnitStatus.PUBLISHED, depends_on=("up",), pack="p-u"),
        ),
        deviations=(deviation("d", "up"),),
    )
    assert check_published_units_gated(snapshot).verdict is Verdict.PASS


def test_published_units_gated_accepts_superseded_dependency() -> None:
    # Superseding a published upstream is history, not a standing failure
    # (0.6 retain-releases; the unit.settled state set). A consumer published
    # through the strict gate stays healthy at rest.
    snapshot = Snapshot(
        units=(
            unit("up", UnitStatus.SUPERSEDED),
            unit("u", UnitStatus.PUBLISHED, depends_on=("up",), pack="p-u"),
        )
    )
    assert check_published_units_gated(snapshot).verdict is Verdict.PASS


def test_published_units_gated_fails_for_blocking_deviation_only() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.PUBLISHED, pack="p-u"),),
        deviations=(deviation("d", "u"),),
    )
    found = check_published_units_gated(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "blocking deviations" in found.detail and "d" in found.detail
    assert "acceptance_pack_version" not in found.detail
    assert "dependencies" not in found.detail


def test_amended_deviations_complete_passes_for_complete_amendment() -> None:
    snapshot = Snapshot(
        units=(unit("u"),),
        deviations=(
            deviation(
                "d",
                "u",
                status=DeviationStatus.AMENDED,
                amendment_version="v2",
                impact_set=("u",),
            ),
        ),
    )
    assert check_amended_deviations_complete(snapshot).verdict is Verdict.PASS


def test_amended_deviations_complete_fails_for_empty_version() -> None:
    snapshot = Snapshot(
        units=(unit("u"),),
        deviations=(
            deviation(
                "d",
                "u",
                status=DeviationStatus.AMENDED,
                impact_set=("u",),
            ),
        ),
    )
    found = check_amended_deviations_complete(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "amendment_version" in found.detail


def test_amended_deviations_complete_fails_for_dangling_impact() -> None:
    snapshot = Snapshot(
        units=(unit("u"),),
        deviations=(
            deviation(
                "d",
                "u",
                status=DeviationStatus.AMENDED,
                amendment_version="v2",
                impact_set=("ghost",),
            ),
        ),
    )
    found = check_amended_deviations_complete(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "impact_set" in found.detail and "ghost" in found.detail


def test_organizational_checks_are_always_not_checked_with_detail() -> None:
    snapshots = (
        Snapshot(),
        Snapshot(
            revision=9,
            units=(unit("u", UnitStatus.BLOCKED),),
            deviations=(deviation("d", "u"),),
        ),
    )
    for snapshot in snapshots:
        report = run_checks(snapshot)
        assert len(report.organizational) == 6
        assert all(
            found.verdict is Verdict.NOT_CHECKED and found.detail
            for found in report.organizational
        )


def test_organizational_rows_do_not_affect_composition() -> None:
    report = CheckReport(
        revision=0,
        mechanical=(
            CheckResult(check_id="check.pass", verdict=Verdict.PASS, detail=""),
        ),
        organizational=tuple(
            evaluator(Snapshot()) for evaluator in ORGANIZATIONAL_CHECKS.values()
        ),
    )
    assert report.composed() is Verdict.PASS


def test_drifted_snapshot_report_composes_to_fail() -> None:
    # A blocking deviation bound to an in-progress unit is unreachable through
    # decide() (opening a deviation forces blocked) — pure state drift.
    report = run_checks(
        Snapshot(
            units=(unit("u", UnitStatus.IN_PROGRESS, pack="p"),),
            deviations=(deviation("d", "u"),),
        )
    )
    assert report.composed() is Verdict.FAIL


def test_blocked_coherence_accepts_invalidated_unit_with_own_blockers() -> None:
    # D-3: an invalidated unit keeps its own blocking deviations; they gate its
    # re-verification. Reachable via legal commands (open deviation on app,
    # then amend an upstream deviation whose closure invalidates app).
    snapshot = Snapshot(
        units=(unit("app", UnitStatus.INVALIDATED, pack="p"),),
        deviations=(deviation("d-app", "app"),),
    )
    assert check_blocked_status_coherent(snapshot).verdict is Verdict.PASS


def test_one_active_record_per_name_fails_for_two_active_versions() -> None:
    snapshot = Snapshot(
        units=(
            unit("u", version="v1"),
            unit("u", version="v2"),
        )
    )
    found = check_one_active_record_per_name(snapshot)
    assert found.verdict is Verdict.FAIL
    assert found.check_id == mk2.CHECK_ONE_ACTIVE_RECORD_PER_NAME
    assert "duplicate unit names" in found.detail


def test_one_active_record_per_name_passes_for_superseded_twin() -> None:
    snapshot = Snapshot(
        units=(
            unit("u", UnitStatus.SUPERSEDED, version="v1"),
            unit("u", UnitStatus.PUBLISHED, version="v2", pack="p2"),
        )
    )
    assert check_one_active_record_per_name(snapshot).verdict is Verdict.PASS


def test_dependency_cycle_detection_survives_deep_chains() -> None:
    # Iterative DFS: a chain far beyond the interpreter recursion limit must
    # neither crash nor false-FAIL.
    units = [unit("u0")]
    units.extend(
        unit(f"u{i}", depends_on=(f"u{i - 1}",)) for i in range(1, 3000)
    )
    snapshot = Snapshot(units=tuple(units))
    assert check_dependency_graph_acyclic(snapshot).verdict is Verdict.PASS


def test_closed_deviation_with_amendment_record_is_still_validated() -> None:
    snapshot = Snapshot(
        units=(unit("u", UnitStatus.INVALIDATED, pack="p"),),
        deviations=(
            deviation(
                "d",
                "u",
                status=DeviationStatus.CLOSED,
                amendment_version="v2",
                impact_set=("u", "ghost"),
            ),
        ),
    )
    found = check_amended_deviations_complete(snapshot)
    assert found.verdict is Verdict.FAIL
    assert "ghost" in found.detail


def test_mislabeled_mechanical_evaluator_fails_under_registry_key() -> None:
    def liar(snapshot: Snapshot) -> CheckResult:
        del snapshot
        return CheckResult(
            check_id=mk2.CHECK_DEPENDENCIES_EXIST,
            verdict=Verdict.PASS,
            detail="",
        )

    mechanical = dict(MECHANICAL_CHECKS)
    mechanical[mk2.CHECK_PUBLISHED_UNITS_GATED] = liar
    report = run_checks(Snapshot(), mechanical=mechanical)
    ids = [result.check_id for result in report.mechanical]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    guarded = next(
        result
        for result in report.mechanical
        if result.check_id == mk2.CHECK_PUBLISHED_UNITS_GATED
    )
    assert guarded.verdict is Verdict.FAIL
    assert "mislabeled" in guarded.detail
    assert report.composed() is Verdict.FAIL


def test_organizational_evaluator_returning_pass_fails_the_run() -> None:
    def fake_org(snapshot: Snapshot) -> CheckResult:
        del snapshot
        return CheckResult(
            check_id=mk2.ORG_INDEPENDENCE,
            verdict=Verdict.PASS,
            detail="verified independent",
        )

    organizational = dict(ORGANIZATIONAL_CHECKS)
    organizational[mk2.ORG_INDEPENDENCE] = fake_org
    report = run_checks(Snapshot(), organizational=organizational)
    guarded = next(
        result
        for result in report.organizational
        if result.check_id == mk2.ORG_INDEPENDENCE
    )
    assert guarded.verdict is Verdict.FAIL
    assert "organizational" in guarded.detail
    assert report.composed() is Verdict.FAIL


def test_artifact_not_applicable_without_ir_rule_is_guarded() -> None:
    # check.evidence-chains declares no applicability, so a NOT_APPLICABLE
    # claim for it must be converted to FAIL (the vacuous-coverage guard).
    tampered = tuple(
        CheckResult(
            check_id=row.check_id,
            verdict=(
                Verdict.NOT_APPLICABLE
                if row.check_id == mk2.CHECK_EVIDENCE_CHAINS
                else row.verdict
            ),
            detail="",
        )
        for row in ARTIFACT_RESULTS
    )
    report = run_checks(Snapshot(), artifact_results=tampered)
    guarded = result(report, mk2.CHECK_EVIDENCE_CHAINS)
    assert guarded.verdict is Verdict.FAIL
    assert "applicability" in guarded.detail
    assert report.composed() is Verdict.FAIL


def test_artifact_not_applicable_with_ir_rule_and_detail_is_accepted() -> None:
    # check.evidence-anchors DOES declare an applicability rule; a cited
    # NOT_APPLICABLE passes through.
    rows = tuple(
        CheckResult(
            check_id=row.check_id,
            verdict=(
                Verdict.NOT_APPLICABLE
                if row.check_id == mk2.CHECK_EVIDENCE_ANCHORS
                else row.verdict
            ),
            detail=(
                "IR applicability rule not met: anchor-mode-is: ci"
                if row.check_id == mk2.CHECK_EVIDENCE_ANCHORS
                else ""
            ),
        )
        for row in ARTIFACT_RESULTS
    )
    report = run_checks(Snapshot(), artifact_results=rows)
    accepted = result(report, mk2.CHECK_EVIDENCE_ANCHORS)
    assert accepted.verdict is Verdict.NOT_APPLICABLE
    assert report.composed() is Verdict.PASS


def test_duplicate_artifact_results_fail_completeness() -> None:
    report = run_checks(
        Snapshot(), artifact_results=ARTIFACT_RESULTS + (ARTIFACT_RESULTS[0],)
    )
    meta = result(report, mk2.CHECK_REGISTRY_COMPLETE)
    assert meta.verdict is Verdict.FAIL
    assert "duplicated" in meta.detail


def test_tier_evidence_obligation_follows_a_published_unit_through_status_changes() -> None:
    # Review F1(b): the obligation is provenance-scoped (published_once), so an
    # accepted OpenDeviation (-> blocked) or Supersede cannot make it disappear.
    from vouch_mk2.core import Accepted, apply, decide
    from vouch_mk2.core.commands import OpenDeviation, Supersede
    from vouch_mk2.core.model import DeviationType, Snapshot, Tier, Unit, UnitRef, UnitStatus
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict

    def verdict(snapshot: Snapshot) -> Verdict:
        report = run_checks(snapshot)
        return next(r for r in report.mechanical if r.check_id == mk2.CHECK_TIER_EVIDENCE_REQUIRED).verdict

    published = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(Unit("u", "v1", UnitStatus.PUBLISHED, acceptance_pack_version="p", published_once=True),),
    )
    assert verdict(published) is Verdict.FAIL
    for command in (
        OpenDeviation("DEV-x", DeviationType.PACK_DEFECT, UnitRef("u", "v1")),
        Supersede("u"),
    ):
        decision = decide(published, command)
        assert isinstance(decision, Accepted), command
        after = apply(published, decision.events)
        unit = after.unit("u")
        assert unit is not None and unit.status is not UnitStatus.PUBLISHED
        assert verdict(after) is Verdict.FAIL, command
    # And a unit that was never published is not caught by the widened scope.
    never = Snapshot(revision=1, tier=Tier.STANDARD, units=(Unit("u", "v1", UnitStatus.BLOCKED, acceptance_pack_version="p"),))
    assert verdict(never) is Verdict.NOT_APPLICABLE
