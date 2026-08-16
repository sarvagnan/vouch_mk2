"""Standing mechanical consistency checks over an at-rest snapshot."""

from __future__ import annotations

from collections.abc import Mapping

from vouch_mk2.checks.results import CheckResult
from vouch_mk2.core.model import (
    DeviationStatus,
    ReleaseState,
    Snapshot,
    UnitStatus,
    blocking_deviations,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import StateMachine, Verdict

# At rest, almost ANY upstream status is legally reachable under a published
# consumer: the upstream was satisfied at publish time, but may since have been
# blocked (deviation), superseded, respecified, re-frozen, or re-verified —
# all without touching the consumer. The ONE status that cannot legally
# coexist is INVALIDATED: amendment invalidation propagates through the
# consumer closure in the same decision, and a consumer cannot republish over
# an invalidated upstream (the strict publish gate refuses). So the truthful
# at-rest predicate is: upstream exists and is not invalidated.
_PUBLISHED_STATUSES = frozenset({UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND})
# D-3: an invalidated unit keeps its own blocking deviations (they gate its
# re-verification); a superseded unit may carry blockers it was abandoned with.
_BLOCKER_COHERENT_STATUSES = frozenset(
    {UnitStatus.BLOCKED, UnitStatus.SUPERSEDED, UnitStatus.INVALIDATED}
)
_ACTIVE_RELEASE_STATES = frozenset(
    mk2.MK2.state_set("release.active.operational").states
)
_PROTECTIVE_RELEASE_STATES = frozenset(
    mk2.MK2.state_set("release.protective.operational").states
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
    graph = {
        unit.name: unit.depends_on
        for unit in snapshot.units
        if unit.status is not UnitStatus.SUPERSEDED
    }
    cycle = _dependency_cycle(graph)
    return CheckResult(
        check_id=mk2.CHECK_DEPENDENCY_GRAPH_ACYCLIC,
        verdict=Verdict.PASS if cycle is None else Verdict.FAIL,
        detail="" if cycle is None else f"dependency cycle: {cycle!r}",
    )


def check_one_active_record_per_name(snapshot: Snapshot) -> CheckResult:
    names: set[str] = set()
    duplicates: set[str] = set()
    for unit in snapshot.units:
        if unit.status is UnitStatus.SUPERSEDED:
            continue
        if unit.name in names:
            duplicates.add(unit.name)
        names.add(unit.name)
    return CheckResult(
        check_id=mk2.CHECK_ONE_ACTIVE_RECORD_PER_NAME,
        verdict=Verdict.PASS if not duplicates else Verdict.FAIL,
        detail=(
            ""
            if not duplicates
            else f"duplicate unit names (active): {tuple(sorted(duplicates))!r}"
        ),
    )


def check_deviation_bindings_resolve(snapshot: Snapshot) -> CheckResult:
    dangling: list[str] = []
    for deviation in snapshot.deviations:
        unit = snapshot.unit_ref(deviation.unit)
        if unit is None:
            dangling.append(
                f"{deviation.deviation_id}:{deviation.unit.label()}"
            )
    parts: list[str] = []
    if dangling:
        parts.append(f"dangling bindings: {tuple(sorted(dangling))!r}")
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
        bad_upstreams = tuple(
            dependency
            for dependency in unit.depends_on
            if (upstream := snapshot.unit(dependency)) is None
            or upstream.status is UnitStatus.INVALIDATED
        )
        if bad_upstreams:
            failures.append(
                f"dependencies missing or invalidated: {bad_upstreams!r}"
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
            deviation.amendment_version != ""
            or deviation.impact_set != ()
            or deviation.release_impact_set != ()
        )
        if not amended and not closed_with_record:
            continue
        failures: list[str] = []
        if not deviation.amendment_version:
            failures.append("amendment_version is empty")
        dangling_units = tuple(
            sorted(
                name
                for name in deviation.impact_set
                if snapshot.unit(name) is None
            )
        )
        if dangling_units:
            failures.append(f"impact_set has unknown units {dangling_units!r}")
        dangling_releases = tuple(
            sorted(
                release_id
                for release_id in deviation.release_impact_set
                if snapshot.release(release_id) is None
            )
        )
        if dangling_releases:
            failures.append(
                f"release_impact_set has unknown releases {dangling_releases!r}"
            )
        if failures:
            offenders.append(
                f"deviation {deviation.deviation_id!r}: {', '.join(failures)}"
            )
    return CheckResult(
        check_id=mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail="; ".join(offenders),
    )


# The only gates a protective escape may carry: record completeness. An escape
# gated on anything roster-dependent would reintroduce the I3 inversion — a
# contaminated release trapped in an active state.
_ESCAPE_ALLOWED_GATES = frozenset({mk2.GATE_RELEASE_CLOSURE_RECORD_COMPLETE})


def escape_property_violations(
    machine: StateMachine,
    active_states: frozenset[str],
    protective_states: frozenset[str],
) -> tuple[str, ...]:
    """Active states lacking a protective escape whose gates are ALL
    roster-independent. Edge existence alone is not enough: a protective edge
    gated on roster health is exactly the I3 regression this property guards."""
    return tuple(
        state
        for state in sorted(active_states)
        if not any(
            transition.source == state
            and transition.target in protective_states
            and set(transition.gates) <= _ESCAPE_ALLOWED_GATES
            for transition in machine.command_transitions
        )
    )


def check_release_escape_property(snapshot: Snapshot) -> CheckResult:
    del snapshot
    unsafe = escape_property_violations(
        mk2.MK2.machine(mk2.RELEASE_O_MACHINE),
        _ACTIVE_RELEASE_STATES,
        _PROTECTIVE_RELEASE_STATES,
    )
    return CheckResult(
        check_id=mk2.CHECK_RELEASE_ESCAPE_PROPERTY,
        verdict=Verdict.PASS if not unsafe else Verdict.FAIL,
        detail=(
            ""
            if not unsafe
            else f"active release states without ungated protective escape: {unsafe!r}"
        ),
    )


def check_release_rosters_resolve(snapshot: Snapshot) -> CheckResult:
    offenders: list[str] = []
    audited = _ACTIVE_RELEASE_STATES | {ReleaseState.SUCCEEDED.value}
    for release in snapshot.releases:
        if release.state.value not in audited:
            continue
        for ref in release.includes:
            unit = snapshot.unit_ref(ref)
            if unit is None:
                offenders.append(
                    f"release {release.release_id!r}: {ref.label()} is missing"
                )
    return CheckResult(
        check_id=mk2.CHECK_RELEASE_ROSTERS_RESOLVE,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail="; ".join(offenders),
    )


def check_release_criteria_before_progression(snapshot: Snapshot) -> CheckResult:
    # `prepared -> retired` is a legal protective escape whose only gate is
    # closure-record completeness, and a criteria-less `prepared` release can be
    # amendment-invalidated — so RETIRED and INVALIDATED must both remain
    # at-rest legal without criteria. Every other progressed state is reachable
    # only through the criteria-gated `prepared -> deploying` edge.
    offenders = tuple(
        sorted(
            release.release_id
            for release in snapshot.releases
            if release.state
            not in {
                ReleaseState.PREPARED,
                ReleaseState.RETIRED,
                ReleaseState.INVALIDATED,
            }
            and release.criteria is None
        )
    )
    return CheckResult(
        check_id=mk2.CHECK_RELEASE_CRITERIA_BEFORE_PROGRESSION,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail=(
            ""
            if not offenders
            else f"progressed releases without criteria: {offenders!r}"
        ),
    )


def check_release_closure_records(snapshot: Snapshot) -> CheckResult:
    offenders: list[str] = []
    for release in snapshot.releases:
        closure = release.closure
        if release.state.value in _PROTECTIVE_RELEASE_STATES:
            if closure is None:
                offenders.append(
                    f"release {release.release_id!r}: protective state has no closure"
                )
            elif not closure.trigger or not closure.no_completion_claim:
                offenders.append(
                    f"release {release.release_id!r}: closure trigger/no-completion "
                    "record is incomplete"
                )
            elif {
                (name, version)
                for name, version, _status in closure.roster_snapshot
            } != {(ref.name, ref.version) for ref in release.includes}:
                # decide() snapshots exactly the roster; anything else is a
                # fabricated or truncated record.
                offenders.append(
                    f"release {release.release_id!r}: closure roster snapshot "
                    "does not correspond to the release roster"
                )
        elif release.state is ReleaseState.SUCCEEDED:
            if closure is not None:
                offenders.append(
                    f"release {release.release_id!r}: succeeded release has closure"
                )
        elif release.state.value in _ACTIVE_RELEASE_STATES and closure is not None:
            offenders.append(
                f"release {release.release_id!r}: active release has closure"
            )
    return CheckResult(
        check_id=mk2.CHECK_RELEASE_CLOSURE_RECORDS,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail="; ".join(offenders),
    )


def check_release_bound_units_rostered(snapshot: Snapshot) -> CheckResult:
    rostered = {
        ref for release in snapshot.releases for ref in release.includes
    }
    offenders = tuple(
        sorted(
            unit.ref().label()
            for unit in snapshot.units
            if unit.status is UnitStatus.RELEASE_BOUND and unit.ref() not in rostered
        )
    )
    return CheckResult(
        check_id=mk2.CHECK_RELEASE_BOUND_UNITS_ROSTERED,
        verdict=Verdict.PASS if not offenders else Verdict.FAIL,
        detail=(
            ""
            if not offenders
            else f"release-bound units absent from all rosters: {offenders!r}"
        ),
    )
