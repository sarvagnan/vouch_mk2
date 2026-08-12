"""Standing mechanical consistency checks over an at-rest snapshot."""

from __future__ import annotations

from collections.abc import Mapping

from vouch_mk2.checks.results import CheckResult
from vouch_mk2.core.model import (
    DeviationStatus,
    Snapshot,
    UnitStatus,
    blocking_deviations,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict

# At rest, a published unit's upstreams must be SETTLED (published,
# release-bound, or superseded) — not the stricter satisfied set the publish
# gate uses. Superseding an upstream is history, never a standing failure
# (0.6's retain-releases lesson); an INVALIDATED upstream still fails here,
# consistently with the amendment closure having invalidated the consumer too.
_SETTLED_DEPENDENCY_STATUSES = frozenset(mk2.MK2.state_set("unit.settled").states)
_PUBLISHED_STATUSES = frozenset({UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND})
# D-3: an invalidated unit keeps its own blocking deviations (they gate its
# re-verification); a superseded unit may carry blockers it was abandoned with.
_BLOCKER_COHERENT_STATUSES = frozenset(
    {UnitStatus.BLOCKED, UnitStatus.SUPERSEDED, UnitStatus.INVALIDATED}
)


def check_dependencies_exist(snapshot: Snapshot) -> CheckResult:
    missing = tuple(
        sorted(
            (unit.name, dependency)
            for unit in snapshot.units
            for dependency in unit.depends_on
            if snapshot.unit(dependency) is None
        )
    )
    detail = "" if not missing else f"missing dependency bindings: {missing!r}"
    return CheckResult(
        check_id=mk2.CHECK_DEPENDENCIES_EXIST,
        verdict=Verdict.PASS if not missing else Verdict.FAIL,
        detail=detail,
    )


def _dependency_cycle(
    graph: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...] | None:
    # Iterative DFS: the graph comes from the store, so a deep chain must not
    # be able to blow the interpreter recursion limit out of the CLI's typed
    # exit-code contract.
    visited: set[str] = set()
    active: set[str] = set()
    path: list[str] = []
    for root in sorted(graph):
        if root in visited:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        active.add(root)
        path.append(root)
        while stack:
            name, edge_index = stack[-1]
            edges = tuple(
                sorted(d for d in graph[name] if d in graph)
            )
            if edge_index < len(edges):
                stack[-1] = (name, edge_index + 1)
                dependency = edges[edge_index]
                if dependency in active:
                    cycle_start = path.index(dependency)
                    return tuple(path[cycle_start:] + [dependency])
                if dependency not in visited:
                    stack.append((dependency, 0))
                    active.add(dependency)
                    path.append(dependency)
            else:
                stack.pop()
                active.remove(name)
                visited.add(name)
                path.pop()
    return None


def check_dependency_graph_acyclic(snapshot: Snapshot) -> CheckResult:
    # Detect duplicate names locally rather than silently collapsing them into
    # a last-wins dict: this check must never go vacuous if a second ingress
    # ever bypasses the codec's duplicate refusal.
    names: set[str] = set()
    duplicates: set[str] = set()
    for unit in snapshot.units:
        if unit.name in names:
            duplicates.add(unit.name)
        names.add(unit.name)
    if duplicates:
        return CheckResult(
            check_id=mk2.CHECK_DEPENDENCY_GRAPH_ACYCLIC,
            verdict=Verdict.FAIL,
            detail=f"duplicate unit names: {tuple(sorted(duplicates))!r}",
        )
    graph = {unit.name: unit.depends_on for unit in snapshot.units}
    cycle = _dependency_cycle(graph)
    return CheckResult(
        check_id=mk2.CHECK_DEPENDENCY_GRAPH_ACYCLIC,
        verdict=Verdict.PASS if cycle is None else Verdict.FAIL,
        detail="" if cycle is None else f"dependency cycle: {cycle!r}",
    )


def check_deviation_bindings_resolve(snapshot: Snapshot) -> CheckResult:
    dangling: list[str] = []
    stale: list[str] = []
    for deviation in snapshot.deviations:
        unit = snapshot.unit(deviation.unit.name)
        if unit is None:
            dangling.append(
                f"{deviation.deviation_id}:{deviation.unit.label()}"
            )
        elif unit.version != deviation.unit.version:
            stale.append(
                f"{deviation.deviation_id}:{deviation.unit.label()}"
                f" (current {unit.ref().label()})"
            )
    parts: list[str] = []
    if dangling:
        parts.append(f"dangling bindings: {tuple(sorted(dangling))!r}")
    if stale:
        parts.append(f"stale bindings: {tuple(sorted(stale))!r}")
    return CheckResult(
        check_id=mk2.CHECK_DEVIATION_BINDINGS_RESOLVE,
        verdict=Verdict.PASS if not parts else Verdict.FAIL,
        detail="; ".join(parts),
    )


def check_blocked_status_coherent(snapshot: Snapshot) -> CheckResult:
    # One direction only. "Blocked with zero blockers" is a LEGAL resting state
    # under D-3's no-auto-unblock: closing the last deviation leaves the unit
    # blocked until an explicit respecify/begin-reverify. The checkable
    # invariant is the converse: blocking deviations never bind a unit that is
    # active in the lifecycle.
    offenders: list[str] = []
    for unit in sorted(snapshot.units, key=lambda candidate: candidate.name):
        blockers = blocking_deviations(snapshot, unit)
        if blockers and unit.status not in _BLOCKER_COHERENT_STATUSES:
            blocker_ids = tuple(sorted(d.deviation_id for d in blockers))
            offenders.append(
                f"unit {unit.name!r} has blocking deviations {blocker_ids!r} "
                f"but status is {unit.status.value!r}"
            )
    return CheckResult(
        check_id=mk2.CHECK_BLOCKED_STATUS_COHERENT,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail="; ".join(offenders),
    )


def check_published_units_gated(snapshot: Snapshot) -> CheckResult:
    offenders: list[str] = []
    for unit in sorted(snapshot.units, key=lambda candidate: candidate.name):
        if unit.status not in _PUBLISHED_STATUSES:
            continue
        failures: list[str] = []
        if not unit.acceptance_pack_version:
            failures.append("acceptance_pack_version is empty")
        unsettled = tuple(
            dependency
            for dependency in unit.depends_on
            if (upstream := snapshot.unit(dependency)) is None
            or upstream.status.value not in _SETTLED_DEPENDENCY_STATUSES
        )
        if unsettled:
            failures.append(
                "dependencies outside IR state set "
                f"'unit.settled': {unsettled!r}"
            )
        blockers = blocking_deviations(snapshot, unit)
        if blockers:
            failures.append(
                "blocking deviations: "
                f"{tuple(sorted(d.deviation_id for d in blockers))!r}"
            )
        if failures:
            offenders.append(f"unit {unit.name!r}: {', '.join(failures)}")
    return CheckResult(
        check_id=mk2.CHECK_PUBLISHED_UNITS_GATED,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail="; ".join(offenders),
    )


def check_amended_deviations_complete(snapshot: Snapshot) -> CheckResult:
    # Checks the amendment *record*: version present and impact names resolving,
    # for amended deviations and for closed ones that carry an amendment record.
    # Whether the impact set corresponds to units actually invalidated is NOT
    # at-rest checkable: impacted units may legally recover (re-verify, respecify)
    # while the deviation is still amended, so any state-correspondence predicate
    # here would false-FAIL legal histories. That correspondence lives in
    # decide()'s amend path and its property tests.
    offenders: list[str] = []
    for deviation in sorted(
        snapshot.deviations, key=lambda candidate: candidate.deviation_id
    ):
        amended = deviation.status is DeviationStatus.AMENDED
        closed_with_record = deviation.status is DeviationStatus.CLOSED and (
            deviation.amendment_version != "" or deviation.impact_set != ()
        )
        if not amended and not closed_with_record:
            continue
        failures: list[str] = []
        if not deviation.amendment_version:
            failures.append("amendment_version is empty")
        dangling = tuple(
            sorted(name for name in deviation.impact_set if snapshot.unit(name) is None)
        )
        if dangling:
            failures.append(f"impact_set has unknown units {dangling!r}")
        if failures:
            offenders.append(
                f"deviation {deviation.deviation_id!r}: {', '.join(failures)}"
            )
    return CheckResult(
        check_id=mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail="; ".join(offenders),
    )
