"""Composition of exploration, invariant evaluation, and mutation witnesses."""

from __future__ import annotations

from dataclasses import dataclass

from functools import lru_cache

from vouch_mk2.formal.explore import EmittedTables, Relation, emit_tables, explore
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.universe import CORE_V1, UNIVERSES, Universe
from vouch_mk2.formal.witnesses import WitnessVerdict, run_witness_harness


@dataclass(frozen=True, slots=True)
class InvariantVerdict:
    invariant_id: str
    held: bool
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UniverseRun:
    universe_name: str
    state_count: int
    transition_count: int
    invariants: tuple[InvariantVerdict, ...]
    relation: Relation


@dataclass(frozen=True, slots=True)
class FormalRun:
    universes: tuple[UniverseRun, ...]
    witnesses: tuple[WitnessVerdict, ...]
    tables: EmittedTables

    def universe(self, name: str) -> UniverseRun:
        for run in self.universes:
            if run.universe_name == name:
                return run
        raise LookupError(f"unknown formal universe {name!r}")


def _run_universe(universe: Universe) -> UniverseRun:
    relation = explore(universe)
    invariant_verdicts: list[InvariantVerdict] = []
    selected = (
        INVARIANTS
        if not universe.invariant_ids
        else tuple(
            invariant
            for invariant in INVARIANTS
            if invariant.invariant_id in universe.invariant_ids
        )
    )
    if universe.invariant_ids and {
        invariant.invariant_id for invariant in selected
    } != set(universe.invariant_ids):
        raise LookupError(
            f"universe {universe.name!r} names unknown invariant ids"
        )
    for invariant in selected:
        violations = invariant.check(relation)
        invariant_verdicts.append(
            InvariantVerdict(
                invariant_id=invariant.invariant_id,
                held=not violations,
                violations=violations,
            )
        )
    return UniverseRun(
        universe_name=universe.name,
        state_count=len(relation.states),
        transition_count=len(relation.transitions),
        invariants=tuple(
            sorted(invariant_verdicts, key=lambda item: item.invariant_id)
        ),
        relation=relation,
    )


@lru_cache(maxsize=1)
def run_formal() -> FormalRun:
    universes = tuple(_run_universe(universe) for universe in UNIVERSES)
    return FormalRun(
        universes=universes,
        witnesses=run_witness_harness(
            next(run.relation for run in universes if run.universe_name == CORE_V1.name)
        ),
        tables=emit_tables(),
    )


def passed(run: FormalRun) -> bool:
    return all(
        verdict.held
        for universe in run.universes
        for verdict in universe.invariants
    ) and all(
        verdict.discriminated for verdict in run.witnesses
    )
