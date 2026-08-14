"""Deterministic BFS over the exact ``decide``/``apply`` relation."""

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.core import Accepted, Snapshot, apply, decide
from vouch_mk2.formal.universe import Universe
from vouch_mk2.ir import mk2

type EmittedTransition = tuple[str, str, str, tuple[str, ...]]
type EmittedAdjudication = tuple[
    str, str, tuple[str, ...], tuple[str, ...]
]
type EmittedMachine = tuple[
    str,
    tuple[str, ...],
    str,
    tuple[EmittedTransition, ...],
    tuple[EmittedAdjudication, ...],
]
type EmittedClaim = tuple[str, str, str, tuple[str, ...]]
type EmittedTables = tuple[tuple[EmittedMachine, ...], tuple[EmittedClaim, ...]]


@dataclass(frozen=True, slots=True)
class Relation:
    states: tuple[str, ...]
    transitions: tuple[tuple[str, str, str], ...]
    initial: str
    representatives: tuple[tuple[str, Snapshot], ...]


class StateLimitExceeded(Exception):
    """Exploration exceeded its explicit bound; no partial run is returned."""


def state_fingerprint(snapshot: Snapshot) -> str:
    """Return a deterministic fingerprint of all state except ``revision``."""
    units = tuple(
        (
            unit.name,
            unit.version,
            unit.status.value,
            unit.acceptance_pack_version,
            tuple(unit.depends_on),
        )
        for unit in sorted(snapshot.units, key=lambda item: item.name)
    )
    deviations = tuple(
        (
            deviation.deviation_id,
            deviation.deviation_type.value,
            deviation.unit.name,
            deviation.unit.version,
            deviation.status.value,
            deviation.amendment_version,
            tuple(deviation.impact_set),
            tuple(deviation.release_impact_set),
        )
        for deviation in sorted(
            snapshot.deviations, key=lambda item: item.deviation_id
        )
    )
    releases = tuple(
        (
            release.release_id,
            release.archetype.value,
            release.state.value,
            tuple((ref.name, ref.version) for ref in release.includes),
            None
            if release.criteria is None
            else (
                tuple(release.criteria.launch_criteria),
                release.criteria.verification_window,
                tuple(release.criteria.rollback_triggers),
                release.criteria.go_no_go_authority,
            ),
            None
            if release.closure is None
            else (
                release.closure.trigger,
                release.closure.trigger_kind.value,
                tuple(release.closure.roster_snapshot),
                release.closure.no_completion_claim,
                tuple(release.closure.observations),
            ),
            release.bundle_inventory_digest,
        )
        for release in sorted(snapshot.releases, key=lambda item: item.release_id)
    )
    heads = tuple(
        (head.unit_name, head.head, head.head_sequence, head.anchored)
        for head in sorted(
            snapshot.evidence_heads, key=lambda item: item.unit_name
        )
    )
    return repr(
        (
            ("units", units),
            ("deviations", deviations),
            ("releases", releases),
            ("evidence_heads", heads),
            ("anchor_mode", snapshot.anchor_mode.value),
            ("formal_certificate_digest", snapshot.formal_certificate_digest),
        )
    )


def explore(universe: Universe) -> Relation:
    initial_snapshot = Snapshot()
    initial = state_fingerprint(initial_snapshot)
    representatives: dict[str, Snapshot] = {initial: initial_snapshot}
    frontier = [initial]
    cursor = 0
    transitions: set[tuple[str, str, str]] = set()
    while cursor < len(frontier):
        source = frontier[cursor]
        cursor += 1
        snapshot = representatives[source]
        for command in universe.commands:
            decision = decide(snapshot, command)
            if not isinstance(decision, Accepted):
                continue
            target_snapshot = apply(snapshot, decision.events)
            target = state_fingerprint(target_snapshot)
            transitions.add((source, command.ir_command, target))
            if target in representatives:
                continue
            if len(representatives) >= 100_000:
                raise StateLimitExceeded(
                    f"universe {universe.name!r} exceeded 100000 states"
                )
            representatives[target] = target_snapshot
            frontier.append(target)
    return Relation(
        states=tuple(sorted(representatives)),
        transitions=tuple(sorted(transitions)),
        initial=initial,
        representatives=tuple(sorted(representatives.items())),
    )


def emit_tables() -> EmittedTables:
    machines: list[EmittedMachine] = []
    for machine in sorted(mk2.MK2.machines, key=lambda item: item.name):
        transitions: tuple[EmittedTransition, ...] = tuple(
            sorted(
                (
                    row.source,
                    row.command,
                    row.target,
                    tuple(sorted(row.gates)),
                )
                for row in machine.command_transitions
            )
        )
        adjudications: tuple[EmittedAdjudication, ...] = tuple(
            sorted(
                (
                    entry.trigger,
                    entry.target,
                    tuple(sorted(entry.sources)),
                    tuple(sorted(entry.excluded_sources)),
                )
                for entry in machine.adjudication_entries
            )
        )
        machines.append(
            (
                machine.name,
                tuple(sorted(machine.states)),
                machine.initial,
                transitions,
                adjudications,
            )
        )
    claims: tuple[EmittedClaim, ...] = tuple(
        sorted(
            (
                claim.machine,
                claim.state,
                claim.shape.value,
                tuple(sorted(claim.obligations)),
            )
            for claim in mk2.MK2.claim_rules
        )
    )
    return tuple(machines), claims
