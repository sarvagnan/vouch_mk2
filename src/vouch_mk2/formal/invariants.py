"""Hand-authored invariants, independent of the emitted policy tables (D-5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vouch_mk2.core import (
    DeviationStatus,
    ReleaseState,
    Snapshot,
    Tier,
    UnitStatus,
    blocking_deviations,
)
from vouch_mk2.formal.explore import Relation
from vouch_mk2.ir import mk2


@dataclass(frozen=True, slots=True)
class Invariant:
    invariant_id: str
    description: str
    check: Callable[[Relation], tuple[str, ...]]


def _representatives(relation: Relation) -> dict[str, Snapshot]:
    return dict(relation.representatives)


def _no_direct_republication(relation: Relation) -> tuple[str, ...]:
    """mk2 M1, descended from vouch 0.6 invariant I1."""
    representatives = _representatives(relation)
    violations: list[str] = []
    for source, command, target in relation.transitions:
        before = representatives[source]
        after = representatives[target]
        for unit in before.units:
            # Track the SAME record across the transition by exact ref (D-9):
            # a re-version that supersedes invalidated u@v1 and publishes u@v2
            # is not u@v1 being republished.
            changed = after.unit_ref(unit.ref())
            if (
                changed is not None
                and unit.status is UnitStatus.INVALIDATED
                and changed.status is UnitStatus.PUBLISHED
            ):
                violations.append(
                    f"{command}: unit {unit.name!r} moved invalidated -> published"
                )
    return tuple(violations)


def _published_units_gated(relation: Relation) -> tuple[str, ...]:
    """mk2 M2, descended from vouch 0.6 invariant I4."""
    violations: list[str] = []
    for fingerprint, snapshot in relation.representatives:
        for unit in snapshot.units:
            if unit.status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}:
                blockers = blocking_deviations(snapshot, unit)
                if blockers:
                    violations.append(
                        f"{fingerprint}: unit {unit.name!r} has blockers "
                        f"{tuple(item.deviation_id for item in blockers)!r}"
                    )
    return tuple(violations)


def _succeeded_entry_valid(relation: Relation) -> tuple[str, ...]:
    """mk2 M3, descended from vouch 0.6 invariant I2."""
    representatives = _representatives(relation)
    violations: list[str] = []
    for source, command, target in relation.transitions:
        before = representatives[source]
        after = representatives[target]
        for release in after.releases:
            prior = before.release(release.release_id)
            if (
                release.state is not ReleaseState.SUCCEEDED
                or prior is None
                or prior.state is ReleaseState.SUCCEEDED
            ):
                continue
            bad: list[str] = []
            for ref in prior.includes:
                unit = before.unit_ref(ref)
                if unit is None:
                    bad.append(f"{ref.name}: missing")
                elif (
                    unit.status is not UnitStatus.RELEASE_BOUND
                    or not unit.acceptance_pack_version
                    or blocking_deviations(before, unit)
                ):
                    bad.append(f"{ref.name}: not completion-valid")
            if bad:
                violations.append(
                    f"{command}: release {release.release_id!r} entered succeeded "
                    f"with {tuple(bad)!r}"
                )
    return tuple(violations)


_PROTECTIVE = {
    ReleaseState.ROLLED_BACK,
    ReleaseState.FAILED_UNRECOVERED,
    ReleaseState.RETIRED,
    ReleaseState.INVALIDATED,
}


def _protective_closure_records(relation: Relation) -> tuple[str, ...]:
    """mk2 M4, descended from vouch 0.6 invariant I3b."""
    violations: list[str] = []
    for fingerprint, snapshot in relation.representatives:
        for release in snapshot.releases:
            closure = release.closure
            if release.state in _PROTECTIVE and (
                closure is None
                or not closure.trigger
                or closure.no_completion_claim is not True
            ):
                violations.append(
                    f"{fingerprint}: release {release.release_id!r} has an "
                    "incomplete protective closure"
                )
    return tuple(violations)


def _criteria_before_progression(relation: Relation) -> tuple[str, ...]:
    """mk2 M5, descended from vouch 0.6 invariant I5."""
    excluded = {
        ReleaseState.PREPARED,
        ReleaseState.RETIRED,
        ReleaseState.INVALIDATED,
    }
    violations: list[str] = []
    for fingerprint, snapshot in relation.representatives:
        for release in snapshot.releases:
            if release.state not in excluded and release.criteria is None:
                violations.append(
                    f"{fingerprint}: release {release.release_id!r} progressed "
                    "without criteria"
                )
    return tuple(violations)


# Bookkeeping commands that are legal from (almost) every state and therefore
# cannot witness lifecycle progress. "Every state has SOME exit" is trivially
# true wherever set-anchor-mode is legal — the vacuous form of this invariant —
# so M6 counts only non-configuration exits.
_CONFIGURATION_COMMANDS = frozenset(
    {
        mk2.CMD_SET_ANCHOR_MODE,
        mk2.CMD_SET_TIER,
        mk2.CMD_ADVANCE_ANCHOR,
        mk2.CMD_RECORD_FORMAL_CERTIFICATE,
    }
)


def _is_quiescent(snapshot: Snapshot) -> bool:
    """A state where nothing is owed: every unit superseded, every release in a
    terminal state, every deviation closed. Such states may legally idle."""
    active_release_states = {
        ReleaseState.PREPARED,
        ReleaseState.DEPLOYING,
        ReleaseState.VERIFYING,
    }
    return (
        all(unit.status is UnitStatus.SUPERSEDED for unit in snapshot.units)
        and all(
            release.state not in active_release_states
            for release in snapshot.releases
        )
        and all(
            deviation.status is DeviationStatus.CLOSED
            for deviation in snapshot.deviations
        )
    )


def _no_nonterminal_deadlock(relation: Relation) -> tuple[str, ...]:
    """mk2 M6, descended from vouch 0.6 invariant I6.

    Every reachable state that still owes progress (a non-superseded unit, an
    active release, or an unclosed deviation) has at least one outgoing
    NON-CONFIGURATION transition that changes state."""
    progressing = {
        source
        for source, command, target in relation.transitions
        if source != target and command not in _CONFIGURATION_COMMANDS
    }
    violations: list[str] = []
    for fingerprint, snapshot in relation.representatives:
        if fingerprint not in progressing and not _is_quiescent(snapshot):
            violations.append(
                f"{fingerprint}: active state has no non-configuration exit"
            )
    return tuple(violations)


def _escape_property(relation: Relation) -> tuple[str, ...]:
    """mk2 M7, descended from vouch 0.6 invariant I11."""
    active = {
        ReleaseState.PREPARED,
        ReleaseState.DEPLOYING,
        ReleaseState.VERIFYING,
    }
    protective_commands = {
        mk2.CMD_ROLL_BACK,
        mk2.CMD_MARK_FAILED_UNRECOVERED,
        mk2.CMD_RETIRE_RELEASE,
    }
    representatives = _representatives(relation)
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for source, command, target in relation.transitions:
        if source != target:
            outgoing.setdefault(source, []).append((command, target))
    violations: list[str] = []
    for fingerprint, snapshot in relation.representatives:
        for release in snapshot.releases:
            if release.state not in active:
                continue
            escaped = False
            for command, target in outgoing.get(fingerprint, ()):
                target_release = representatives[target].release(release.release_id)
                if (
                    command in protective_commands
                    and target_release is not None
                    and target_release.state in _PROTECTIVE
                ):
                    escaped = True
                    break
            if not escaped:
                violations.append(
                    f"{fingerprint}: active release {release.release_id!r} "
                    "has no protective exit"
                )
    return tuple(violations)


def _amendment_withdraws_releases(relation: Relation) -> tuple[str, ...]:
    """mk2 M8, descended from vouch 0.6 invariant I12."""
    representatives = _representatives(relation)
    violations: list[str] = []
    for source, command, target in relation.transitions:
        if command != mk2.CMD_ADJUDICATE_AMEND:
            continue
        before = representatives[source]
        after = representatives[target]
        amended = tuple(
            deviation
            for deviation in after.deviations
            if deviation.status is DeviationStatus.AMENDED
            and (
                (prior := before.deviation(deviation.deviation_id)) is not None
                and prior.status is not DeviationStatus.AMENDED
            )
        )
        for deviation in amended:
            affected_ids = set(deviation.release_impact_set)
            impacted_units = set(deviation.impact_set)
            for release in after.releases:
                roster = {ref.name for ref in release.includes}
                if (
                    release.release_id in affected_ids
                    or impacted_units.intersection(roster)
                ) and release.state is not ReleaseState.INVALIDATED:
                    violations.append(
                        f"{command}: deviation {deviation.deviation_id!r} left "
                        f"release {release.release_id!r} in {release.state.value!r}"
                    )
    return tuple(violations)


def _anchor_monotonic(relation: Relation) -> tuple[str, ...]:
    """mk2 M9, defined by mk2 DECISIONS.md D-4."""
    representatives = _representatives(relation)
    violations: list[str] = []
    for source, command, target in relation.transitions:
        before = representatives[source]
        after = representatives[target]
        names = {head.unit_name for head in before.evidence_heads} | {
            head.unit_name for head in after.evidence_heads
        }
        for name in sorted(names):
            prior = before.evidence_head(name)
            current = after.evidence_head(name)
            if current is None:
                violations.append(f"{command}: evidence head {name!r} disappeared")
                continue
            if prior is None:
                if current.anchored != "GENESIS":
                    violations.append(
                        f"{command}: new head {name!r} has non-GENESIS anchor"
                    )
                continue
            if current.head_sequence < prior.head_sequence:
                violations.append(
                    f"{command}: head sequence for {name!r} decreased"
                )
            if current.anchored not in {prior.anchored, current.head}:
                violations.append(
                    f"{command}: anchor for {name!r} moved from "
                    f"{prior.anchored!r} to unrelated {current.anchored!r}"
                )
    return tuple(violations)


def _one_active_record_per_name(relation: Relation) -> tuple[str, ...]:
    """mk2 M10, defined by DECISIONS.md D-9."""
    representatives = _representatives(relation)
    violations: list[str] = []
    for fingerprint, snapshot in relation.representatives:
        active_by_name: dict[str, int] = {}
        for unit in snapshot.units:
            if unit.status is UnitStatus.SUPERSEDED:
                continue
            active_by_name[unit.name] = active_by_name.get(unit.name, 0) + 1
        duplicates = tuple(
            sorted(name for name, count in active_by_name.items() if count > 1)
        )
        if duplicates:
            violations.append(
                f"{fingerprint}: multiple active records for names {duplicates!r}"
            )

    for source, command, target in relation.transitions:
        before = representatives[source]
        after = representatives[target]
        for historical in before.units:
            if historical.status is not UnitStatus.SUPERSEDED:
                continue
            current = after.unit_ref(historical.ref())
            if current != historical:
                violations.append(
                    f"{command}: superseded record {historical.ref().label()!r} "
                    "changed or disappeared"
                )
    return tuple(violations)


def _tier_never_lowers_after_commitment(relation: Relation) -> tuple[str, ...]:
    """mk2 M11, defined by DECISIONS.md D-12."""
    representatives = _representatives(relation)
    order = tuple(Tier)
    violations: list[str] = []
    for source, command, target in relation.transitions:
        before = representatives[source]
        after = representatives[target]
        committed = any(
            unit.status is not UnitStatus.SPECIFIED for unit in before.units
        )
        if committed and order.index(after.tier) < order.index(before.tier):
            violations.append(
                f"{command}: tier lowered from {before.tier.value!r} to "
                f"{after.tier.value!r} after work was committed"
            )
    return tuple(violations)


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "M1.no-direct-republication",
        "0.6 I1: invalidated units cannot move directly to published.",
        _no_direct_republication,
    ),
    Invariant(
        "M2.published-units-gated",
        "0.6 I4: published and release-bound units have no blocking deviations.",
        _published_units_gated,
    ),
    Invariant(
        "M3.succeeded-entry-valid",
        "0.6 I2: every succeeded entry rechecks its full roster.",
        _succeeded_entry_valid,
    ),
    Invariant(
        "M4.protective-closure-records",
        "0.6 I3b: protective releases retain complete closure records.",
        _protective_closure_records,
    ),
    Invariant(
        "M5.criteria-before-progression",
        "0.6 I5: operational progression requires predeclared criteria.",
        _criteria_before_progression,
    ),
    Invariant(
        "M6.no-nonterminal-deadlock",
        "0.6 I6: every active reachable state has a non-configuration exit.",
        _no_nonterminal_deadlock,
    ),
    Invariant(
        "M7.escape-property",
        "0.6 I11: every active release has a protective closure exit.",
        _escape_property,
    ),
    Invariant(
        "M8.amendment-withdraws-releases",
        "0.6 I12: amendment impact invalidates intersecting releases.",
        _amendment_withdraws_releases,
    ),
    Invariant(
        "M9.anchor-monotonic",
        "mk2 D-4: evidence anchors advance monotonically to a chain head.",
        _anchor_monotonic,
    ),
    Invariant(
        "M10.one-active-record-per-name",
        "mk2 D-9: at most one active record exists per name and history is inert.",
        _one_active_record_per_name,
    ),
    Invariant(
        "M11.tier-never-lowers-after-commitment",
        "mk2 D-12: assurance tier cannot lower after any unit leaves specified.",
        _tier_never_lowers_after_commitment,
    ),
)
