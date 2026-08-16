"""Private relation mutations proving each invariant can discriminate defects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from vouch_mk2.core import (
    ClosureRecord,
    ClosureTriggerKind,
    Deviation,
    DeviationStatus,
    DeviationType,
    EvidenceHead,
    ReleaseState,
    Snapshot,
    Tier,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.formal.explore import Relation, state_fingerprint
from vouch_mk2.formal.invariants import INVARIANTS, Invariant
from vouch_mk2.ir import mk2


@dataclass(frozen=True, slots=True)
class Witness:
    witness_id: str
    invariant_id: str
    description: str
    mutate: Callable[[Relation], Relation]


@dataclass(frozen=True, slots=True)
class WitnessVerdict:
    witness_id: str
    invariant_id: str
    discriminated: bool


class WitnessBuildError(Exception):
    """The invariant/witness registry is structurally invalid."""


def _with_representative(
    relation: Relation, snapshot: Snapshot
) -> tuple[Relation, str]:
    fingerprint = state_fingerprint(snapshot)
    representatives = dict(relation.representatives)
    representatives[fingerprint] = snapshot
    states = tuple(sorted(set(relation.states) | {fingerprint}))
    return (
        replace(
            relation,
            states=states,
            representatives=tuple(sorted(representatives.items())),
        ),
        fingerprint,
    )


def _replace_representative(
    relation: Relation, old: str, snapshot: Snapshot
) -> Relation:
    representatives = dict(relation.representatives)
    del representatives[old]
    new = state_fingerprint(snapshot)
    representatives[new] = snapshot
    transitions = tuple(
        sorted(
            (
                new if source == old else source,
                command,
                new if target == old else target,
            )
            for source, command, target in relation.transitions
        )
    )
    states = tuple(sorted(new if item == old else item for item in relation.states))
    initial = new if relation.initial == old else relation.initial
    return Relation(states, transitions, initial, tuple(sorted(representatives.items())))


def _first_snapshot(
    relation: Relation, predicate: Callable[[Snapshot], bool]
) -> tuple[str, Snapshot]:
    for fingerprint, snapshot in relation.representatives:
        if predicate(snapshot):
            return fingerprint, snapshot
    raise WitnessBuildError("real relation lacks a witness mutation seed")


def _mutate_m1(relation: Relation) -> Relation:
    source, snapshot = _first_snapshot(
        relation,
        lambda item: any(
            unit.status is UnitStatus.INVALIDATED for unit in item.units
        ),
    )
    unit = next(
        unit for unit in snapshot.units if unit.status is UnitStatus.INVALIDATED
    )
    target_snapshot = replace(
        snapshot,
        units=tuple(
            replace(current, status=UnitStatus.PUBLISHED)
            if current.name == unit.name
            else current
            for current in snapshot.units
        ),
    )
    mutated, target = _with_representative(relation, target_snapshot)
    return replace(
        mutated,
        transitions=tuple(
            sorted(
                set(mutated.transitions)
                | {
                    (source, mk2.CMD_PUBLISH, target),
                    (target, mk2.CMD_SET_ANCHOR_MODE, source),
                }
            )
        ),
    )


def _mutate_m2(relation: Relation) -> Relation:
    old, snapshot = _first_snapshot(
        relation,
        lambda item: any(
            unit.status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}
            for unit in item.units
        ),
    )
    unit = next(
        unit
        for unit in snapshot.units
        if unit.status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND}
    )
    corrupted = replace(
        snapshot,
        deviations=snapshot.deviations
        + (
            Deviation(
                deviation_id="w-m2",
                deviation_type=DeviationType.PACK_DEFECT,
                unit=unit.ref(),
                status=DeviationStatus.OPEN,
            ),
        ),
    )
    return _replace_representative(relation, old, corrupted)


def _mutate_m3(relation: Relation) -> Relation:
    representatives = dict(relation.representatives)
    for source, command, target in relation.transitions:
        after = representatives[target]
        before = representatives[source]
        release = next(
            (
                item
                for item in after.releases
                if item.state is ReleaseState.SUCCEEDED
                and (prior := before.release(item.release_id)) is not None
                and prior.state is not ReleaseState.SUCCEEDED
            ),
            None,
        )
        if release is None:
            continue
        prior_release = before.release(release.release_id)
        if prior_release is None or not prior_release.includes:
            continue
        name = prior_release.includes[0].name
        corrupted = replace(
            before,
            units=tuple(
                replace(unit, status=UnitStatus.SPECIFIED)
                if unit.name == name
                else unit
                for unit in before.units
            ),
        )
        mutated, forged_source = _with_representative(relation, corrupted)
        copied_outgoing = {
            (forged_source, outgoing_command, outgoing_target)
            for outgoing_source, outgoing_command, outgoing_target in relation.transitions
            if outgoing_source == source
        }
        return replace(
            mutated,
            transitions=tuple(
                sorted(set(mutated.transitions) | copied_outgoing)
            ),
        )
    raise WitnessBuildError("real relation lacks a succeeded-entry seed")


def _mutate_m4(relation: Relation) -> Relation:
    old, snapshot = _first_snapshot(
        relation,
        lambda item: any(
            release.state
            in {
                ReleaseState.ROLLED_BACK,
                ReleaseState.FAILED_UNRECOVERED,
                ReleaseState.RETIRED,
                ReleaseState.INVALIDATED,
            }
            for release in item.releases
        ),
    )
    release_id = next(
        release.release_id
        for release in snapshot.releases
        if release.state
        in {
            ReleaseState.ROLLED_BACK,
            ReleaseState.FAILED_UNRECOVERED,
            ReleaseState.RETIRED,
            ReleaseState.INVALIDATED,
        }
    )
    corrupted = replace(
        snapshot,
        releases=tuple(
            replace(release, closure=None)
            if release.release_id == release_id
            else release
            for release in snapshot.releases
        ),
    )
    return _replace_representative(relation, old, corrupted)


def _mutate_m5(relation: Relation) -> Relation:
    old, snapshot = _first_snapshot(
        relation,
        lambda item: any(
            release.state is ReleaseState.DEPLOYING
            for release in item.releases
        ),
    )
    corrupted = replace(
        snapshot,
        releases=tuple(
            replace(release, criteria=None)
            if release.state is ReleaseState.DEPLOYING
            else release
            for release in snapshot.releases
        ),
    )
    return _replace_representative(relation, old, corrupted)


def _mutate_m6(relation: Relation) -> Relation:
    snapshot = Snapshot(
        units=(
            replace(
                next(
                    unit
                    for _fingerprint, item in relation.representatives
                    for unit in item.units
                ),
                name="w-m6",
                status=UnitStatus.SPECIFIED,
                depends_on=(),
            ),
        )
    )
    mutated, _fingerprint = _with_representative(relation, snapshot)
    return mutated


def _mutate_m7(relation: Relation) -> Relation:
    source, snapshot = _first_snapshot(
        relation,
        lambda item: any(
            release.state
            in {ReleaseState.PREPARED, ReleaseState.DEPLOYING, ReleaseState.VERIFYING}
            for release in item.releases
        ),
    )
    representatives = dict(relation.representatives)
    protected = {
        mk2.CMD_ROLL_BACK,
        mk2.CMD_MARK_FAILED_UNRECOVERED,
        mk2.CMD_RETIRE_RELEASE,
    }
    transitions = tuple(
        row
        for row in relation.transitions
        if not (
            row[0] == source
            and row[1] in protected
            and any(
                release.state
                in {
                    ReleaseState.ROLLED_BACK,
                    ReleaseState.FAILED_UNRECOVERED,
                    ReleaseState.RETIRED,
                    ReleaseState.INVALIDATED,
                }
                for release in representatives[row[2]].releases
            )
        )
    )
    return replace(relation, transitions=transitions)


def _mutate_m8(relation: Relation) -> Relation:
    representatives = dict(relation.representatives)
    for source, command, target in relation.transitions:
        if command != mk2.CMD_ADJUDICATE_AMEND:
            continue
        before = representatives[source]
        after = representatives[target]
        amended = next(
            (
                deviation
                for deviation in after.deviations
                if deviation.status is DeviationStatus.AMENDED
                and (prior := before.deviation(deviation.deviation_id)) is not None
                and prior.status is not DeviationStatus.AMENDED
                and deviation.release_impact_set
            ),
            None,
        )
        if amended is None:
            continue
        affected = amended.release_impact_set[0]
        corrupted = replace(
            after,
            releases=tuple(
                replace(
                    release,
                    state=ReleaseState.PREPARED,
                    closure=None,
                )
                if release.release_id == affected
                else release
                for release in after.releases
            ),
        )
        mutated, forged_target = _with_representative(relation, corrupted)
        return replace(
            mutated,
            transitions=tuple(
                sorted(
                    set(mutated.transitions)
                    | {
                        (source, command, forged_target),
                        (forged_target, mk2.CMD_ROLL_BACK, target),
                    }
                )
            ),
        )
    raise WitnessBuildError("real relation lacks an affected-release amendment seed")


def _mutate_m9(relation: Relation) -> Relation:
    representatives = dict(relation.representatives)
    for source, command, target in relation.transitions:
        after = representatives[target]
        if not after.evidence_heads:
            continue
        name = after.evidence_heads[0].unit_name
        corrupted = replace(
            after,
            evidence_heads=tuple(
                replace(head, anchored="f" * 64)
                if head.unit_name == name
                else head
                for head in after.evidence_heads
            ),
        )
        mutated, forged_target = _with_representative(relation, corrupted)
        return replace(
            mutated,
            transitions=tuple(
                sorted(
                    set(mutated.transitions)
                    | {
                        (source, command, forged_target),
                        (forged_target, mk2.CMD_SET_ANCHOR_MODE, target),
                    }
                )
            ),
        )
    raise WitnessBuildError("real relation lacks an evidence-head seed")


def _mutate_m10(relation: Relation) -> Relation:
    old, snapshot = _first_snapshot(relation, lambda item: bool(item.units))
    existing = snapshot.units[0]
    corrupted = replace(
        snapshot,
        units=snapshot.units
        + (
            Unit(
                name=existing.name,
                version=f"{existing.version}-w10",
                status=UnitStatus.SPECIFIED,
                depends_on=existing.depends_on,
            ),
        ),
    )
    return _replace_representative(relation, old, corrupted)


def _mutate_m11(relation: Relation) -> Relation:
    _fingerprint, snapshot = _first_snapshot(
        relation,
        lambda item: any(
            unit.status is not UnitStatus.SPECIFIED for unit in item.units
        ),
    )
    before = replace(snapshot, tier=Tier.HIGH)
    after = replace(before, tier=Tier.LIGHTWEIGHT)
    mutated, source = _with_representative(relation, before)
    mutated, target = _with_representative(mutated, after)
    return replace(
        mutated,
        transitions=tuple(
            sorted(
                set(mutated.transitions)
                | {(source, mk2.CMD_SET_TIER, target)}
            )
        ),
    )


WITNESSES: tuple[Witness, ...] = (
    Witness("W1.direct-republication", "M1.no-direct-republication", "Forge invalidated -> published.", _mutate_m1),
    Witness("W2.blocked-published", "M2.published-units-gated", "Attach an open deviation to a published unit.", _mutate_m2),
    Witness("W3.invalid-success", "M3.succeeded-entry-valid", "Forge success from an invalid roster source.", _mutate_m3),
    Witness("W4.missing-closure", "M4.protective-closure-records", "Drop a protective closure record.", _mutate_m4),
    Witness("W5.late-criteria", "M5.criteria-before-progression", "Drop criteria from a progressed release.", _mutate_m5),
    Witness("W6.deadlock", "M6.no-nonterminal-deadlock", "Add an isolated unfinished state.", _mutate_m6),
    Witness("W7.no-escape", "M7.escape-property", "Remove one active state's protective exits.", _mutate_m7),
    Witness("W8.release-not-withdrawn", "M8.amendment-withdraws-releases", "Keep an amendment-affected release prepared.", _mutate_m8),
    Witness("W9.anchor-rotation", "M9.anchor-monotonic", "Rotate an anchor to an unrelated digest.", _mutate_m9),
    Witness("W10.second-active-record", "M10.one-active-record-per-name", "Add a second active version for an existing name.", _mutate_m10),
    Witness("W11.tier-lowering", "M11.tier-never-lowers-after-commitment", "Forge a tier-lowering transition after work is committed.", _mutate_m11),
)


def _catalog(invariants: tuple[Invariant, ...]) -> dict[str, Invariant]:
    result: dict[str, Invariant] = {}
    for invariant in invariants:
        if invariant.invariant_id in result:
            raise WitnessBuildError(
                f"duplicate invariant id {invariant.invariant_id!r}"
            )
        result[invariant.invariant_id] = invariant
    if not result:
        raise WitnessBuildError("invariant catalog must not be empty")
    return result


def validate_witness_registry(
    witnesses: tuple[Witness, ...],
    invariants: tuple[Invariant, ...] = INVARIANTS,
) -> None:
    catalog = _catalog(invariants)
    invariant_ids = set(catalog)
    witness_ids: set[str] = set()
    mutators: list[Callable[[Relation], Relation]] = []
    counts: dict[str, int] = {}
    for witness in witnesses:
        if witness.witness_id in invariant_ids or witness.witness_id in witness_ids:
            raise WitnessBuildError(f"witness id collision {witness.witness_id!r}")
        witness_ids.add(witness.witness_id)
        if witness.invariant_id not in invariant_ids:
            raise WitnessBuildError(
                f"witness {witness.witness_id!r} names unknown invariant "
                f"{witness.invariant_id!r}"
            )
        if any(witness.mutate is mutator for mutator in mutators):
            raise WitnessBuildError(
                f"witness {witness.witness_id!r} borrows a mutation"
            )
        mutators.append(witness.mutate)
        counts[witness.invariant_id] = counts.get(witness.invariant_id, 0) + 1
    for invariant_id in sorted(invariant_ids):
        count = counts.get(invariant_id, 0)
        if count != 1:
            raise WitnessBuildError(
                f"invariant {invariant_id!r} has {count} witnesses; expected 1"
            )


def run_witness_harness(
    relation: Relation,
    witnesses: tuple[Witness, ...] = WITNESSES,
    invariants: tuple[Invariant, ...] = INVARIANTS,
) -> tuple[WitnessVerdict, ...]:
    validate_witness_registry(witnesses, invariants)
    catalog = _catalog(invariants)
    verdicts: list[WitnessVerdict] = []
    for witness in sorted(witnesses, key=lambda item: item.witness_id):
        mutated = witness.mutate(relation)
        # Discrimination = the witness's OWN invariant fails on the mutated
        # relation. Collateral failures of other invariants are permitted and
        # do not disqualify: privacy is the one-witness-per-invariant ownership
        # mapping enforced by validate_witness_registry, not behavioral
        # exclusivity (0.6's applicability lesson — a mutation that flips a
        # unit to published may legitimately trip both the republication and
        # the gating invariant). Checking only the owned invariant also keeps
        # the harness linear instead of quadratic in the catalog.
        own = catalog[witness.invariant_id]
        verdicts.append(
            WitnessVerdict(
                witness_id=witness.witness_id,
                invariant_id=witness.invariant_id,
                discriminated=bool(own.check(mutated)),
            )
        )
    return tuple(verdicts)
