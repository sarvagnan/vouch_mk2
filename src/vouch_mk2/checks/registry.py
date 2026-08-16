"""Evaluator registries and complete, deterministic check dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from vouch_mk2.checks.organizational import (
    check_acceptance_ownership,
    check_causal_red_history,
    check_independence,
    check_intent_validation,
    check_protective_closure_truthfulness,
)
from vouch_mk2.checks.results import CheckReport, CheckResult
from vouch_mk2.checks.tier import (
    check_tier_controls,
    check_tier_evidence_required,
    check_tier_high_attestation,
)
from vouch_mk2.checks.standing import (
    check_amended_deviations_complete,
    check_blocked_status_coherent,
    check_dependencies_exist,
    check_dependency_graph_acyclic,
    check_deviation_bindings_resolve,
    check_one_active_record_per_name,
    check_published_units_gated,
    check_release_bound_units_rostered,
    check_release_closure_records,
    check_release_criteria_before_progression,
    check_release_escape_property,
    check_release_rosters_resolve,
)
from vouch_mk2.core.model import Snapshot
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import ObligationScope, Verdict

type CheckEvaluator = Callable[[Snapshot], CheckResult]


def _check_registry_complete(
    snapshot: Snapshot,
    mechanical: Mapping[str, CheckEvaluator],
    organizational: Mapping[str, CheckEvaluator],
    artifact_results: tuple[CheckResult, ...],
) -> CheckResult:
    del snapshot
    declared_mechanical = {
        obligation.obligation_id
        for obligation in mk2.MK2.obligations
        if obligation.scope is ObligationScope.STANDING_CHECK
    } | {mk2.CHECK_REGISTRY_COMPLETE}
    declared_organizational = {
        obligation.obligation_id
        for obligation in mk2.MK2.obligations
        if obligation.scope is ObligationScope.ORGANIZATIONAL
    }
    declared_artifact = {
        obligation.obligation_id
        for obligation in mk2.MK2.obligations
        if obligation.scope is ObligationScope.ARTIFACT_CHECK
    }

    parts: list[str] = []
    mechanical_keys = set(mechanical)
    organizational_keys = set(organizational)
    artifact_ids = {result.check_id for result in artifact_results}
    missing_mechanical = tuple(sorted(declared_mechanical - mechanical_keys))
    extra_mechanical = tuple(sorted(mechanical_keys - declared_mechanical))
    missing_organizational = tuple(
        sorted(declared_organizational - organizational_keys)
    )
    extra_organizational = tuple(
        sorted(organizational_keys - declared_organizational)
    )
    missing_artifact = tuple(sorted(declared_artifact - artifact_ids))
    extra_artifact = tuple(sorted(artifact_ids - declared_artifact))
    duplicate_artifact = tuple(
        sorted(
            {
                result.check_id
                for result in artifact_results
                if sum(
                    1 for other in artifact_results if other.check_id == result.check_id
                )
                > 1
            }
        )
    )
    if missing_mechanical:
        parts.append(f"mechanical missing: {missing_mechanical!r}")
    if extra_mechanical:
        parts.append(f"mechanical extra: {extra_mechanical!r}")
    if missing_organizational:
        parts.append(f"organizational missing: {missing_organizational!r}")
    if extra_organizational:
        parts.append(f"organizational extra: {extra_organizational!r}")
    if missing_artifact:
        parts.append(f"artifact missing: {missing_artifact!r}")
    if extra_artifact:
        parts.append(f"artifact extra: {extra_artifact!r}")
    if duplicate_artifact:
        parts.append(f"artifact duplicated: {duplicate_artifact!r}")
    return CheckResult(
        check_id=mk2.CHECK_REGISTRY_COMPLETE,
        verdict=Verdict.PASS if not parts else Verdict.FAIL,
        detail="; ".join(parts),
    )


def _check_default_registry_complete(snapshot: Snapshot) -> CheckResult:
    return _check_registry_complete(
        snapshot, MECHANICAL_CHECKS, ORGANIZATIONAL_CHECKS, ()
    )


MECHANICAL_CHECKS: Mapping[str, CheckEvaluator] = {
    mk2.CHECK_REGISTRY_COMPLETE: _check_default_registry_complete,
    mk2.CHECK_DEPENDENCIES_EXIST: check_dependencies_exist,
    mk2.CHECK_DEPENDENCY_GRAPH_ACYCLIC: check_dependency_graph_acyclic,
    mk2.CHECK_ONE_ACTIVE_RECORD_PER_NAME: check_one_active_record_per_name,
    mk2.CHECK_DEVIATION_BINDINGS_RESOLVE: check_deviation_bindings_resolve,
    mk2.CHECK_BLOCKED_STATUS_COHERENT: check_blocked_status_coherent,
    mk2.CHECK_PUBLISHED_UNITS_GATED: check_published_units_gated,
    mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE: check_amended_deviations_complete,
    mk2.CHECK_RELEASE_ESCAPE_PROPERTY: check_release_escape_property,
    mk2.CHECK_RELEASE_ROSTERS_RESOLVE: check_release_rosters_resolve,
    mk2.CHECK_RELEASE_CRITERIA_BEFORE_PROGRESSION: (
        check_release_criteria_before_progression
    ),
    mk2.CHECK_RELEASE_CLOSURE_RECORDS: check_release_closure_records,
    mk2.CHECK_RELEASE_BOUND_UNITS_ROSTERED: check_release_bound_units_rostered,
    mk2.CHECK_TIER_EVIDENCE_REQUIRED: check_tier_evidence_required,
    mk2.CHECK_TIER_HIGH_ATTESTATION: check_tier_high_attestation,
}

ORGANIZATIONAL_CHECKS: Mapping[str, CheckEvaluator] = {
    mk2.ORG_ACCEPTANCE_OWNERSHIP: check_acceptance_ownership,
    mk2.ORG_INTENT_VALIDATION: check_intent_validation,
    mk2.ORG_INDEPENDENCE: check_independence,
    mk2.ORG_CAUSAL_RED_HISTORY: check_causal_red_history,
    mk2.ORG_PROTECTIVE_CLOSURE_TRUTHFULNESS: check_protective_closure_truthfulness,
    mk2.ORG_TIER_CONTROLS: check_tier_controls,
}


def _guard_mechanical(check_id: str, result: CheckResult) -> CheckResult:
    # An evaluator whose result carries a different id than it was registered
    # under would otherwise vanish from the report while registry completeness
    # (which sees only mapping keys) still says PASS — the vacuous-coverage
    # class this phase exists to close. The row keeps the REGISTRY's id.
    if result.check_id != check_id:
        return CheckResult(
            check_id=check_id,
            verdict=Verdict.FAIL,
            detail=(
                f"evaluator returned a mislabeled result "
                f"(check_id {result.check_id!r}); its verdict is untrusted"
            ),
        )
    return result


def _guard_organizational(check_id: str, result: CheckResult) -> CheckResult:
    # docs/claim-boundary.md: no command converts an organizational claim into
    # a mechanical pass. That is a mechanism here, not a convention: any
    # organizational evaluator returning something other than a correctly
    # labeled NOT_CHECKED becomes a FAIL row, and CheckReport.composed()
    # fails the run on organizational FAILs.
    if result.check_id != check_id or result.verdict is not Verdict.NOT_CHECKED:
        return CheckResult(
            check_id=check_id,
            verdict=Verdict.FAIL,
            detail=(
                f"organizational evaluator violated its contract (returned "
                f"check_id {result.check_id!r}, verdict "
                f"{result.verdict.value!r}); organizational claims are "
                f"permanently NOT_CHECKED"
            ),
        )
    return result


def run_checks(
    snapshot: Snapshot,
    mechanical: Mapping[str, CheckEvaluator] = MECHANICAL_CHECKS,
    organizational: Mapping[str, CheckEvaluator] = ORGANIZATIONAL_CHECKS,
    artifact_results: tuple[CheckResult, ...] = (),
) -> CheckReport:
    """Run every supplied evaluator once, ordered by check id.

    The registry-completeness result is always present and is computed against
    these exact injected mappings. Every produced result is guarded: its id must
    match the registry key it was dispatched under, and organizational results
    must be NOT_CHECKED. Evaluator exceptions intentionally propagate.
    """
    mechanical_results: list[CheckResult] = []
    mechanical_ids = set(mechanical) | {mk2.CHECK_REGISTRY_COMPLETE}
    for check_id in sorted(mechanical_ids):
        if check_id == mk2.CHECK_REGISTRY_COMPLETE:
            result = _check_registry_complete(
                snapshot, mechanical, organizational, artifact_results
            )
        else:
            result = _guard_mechanical(check_id, mechanical[check_id](snapshot))
        mechanical_results.append(result)

    artifact_obligations = {
        obligation.obligation_id: obligation
        for obligation in mk2.MK2.obligations
        if obligation.scope is ObligationScope.ARTIFACT_CHECK
    }
    for result in artifact_results:
        obligation = artifact_obligations.get(result.check_id)
        if obligation is None:
            mechanical_results.append(
                CheckResult(
                    check_id=result.check_id,
                    verdict=Verdict.FAIL,
                    detail="undeclared artifact check",
                )
            )
        elif result.verdict is Verdict.NOT_APPLICABLE and (
            not result.detail
            or (
                not obligation.applicability
                and not (
                    result.check_id == mk2.CHECK_MAP_FRESHNESS
                    and "map.json is absent" in result.detail
                )
            )
        ):
            # The same vacuous-coverage guard the organizational rows have: a
            # NOT_APPLICABLE that cites no IR applicability rule (or whose IR
            # obligation declares none) would silently drop a check from
            # coverage while composing clean. Map freshness has one explicit
            # artifact-state exception: no projection exists to compare.
            mechanical_results.append(
                CheckResult(
                    check_id=result.check_id,
                    verdict=Verdict.FAIL,
                    detail=(
                        "artifact result claims NOT_APPLICABLE without an IR "
                        "applicability rule to cite"
                    ),
                )
            )
        else:
            mechanical_results.append(result)

    organizational_results = tuple(
        _guard_organizational(check_id, organizational[check_id](snapshot))
        for check_id in sorted(organizational)
    )
    return CheckReport(
        revision=snapshot.revision,
        mechanical=tuple(
            sorted(mechanical_results, key=lambda result: result.check_id)
        ),
        organizational=organizational_results,
    )
