"""Composition of exploration, invariant evaluation, and mutation witnesses."""

from __future__ import annotations

from dataclasses import dataclass

from functools import lru_cache

from vouch_mk2.formal.explore import EmittedTables, Relation, emit_tables, explore
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.universe import CORE_V1
from vouch_mk2.formal.witnesses import WitnessVerdict, run_witness_harness


@dataclass(frozen=True, slots=True)
class InvariantVerdict:
    invariant_id: str
    held: bool
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FormalRun:
    universe_name: str
    state_count: int
    transition_count: int
    invariants: tuple[InvariantVerdict, ...]
    witnesses: tuple[WitnessVerdict, ...]
    tables: EmittedTables
    relation: Relation


@lru_cache(maxsize=1)
def run_formal() -> FormalRun:
    relation = explore(CORE_V1)
    invariant_verdicts: list[InvariantVerdict] = []
    for invariant in INVARIANTS:
        violations = invariant.check(relation)
        invariant_verdicts.append(
            InvariantVerdict(
                invariant_id=invariant.invariant_id,
                held=not violations,
                violations=violations,
            )
        )
    return FormalRun(
        universe_name=CORE_V1.name,
        state_count=len(relation.states),
        transition_count=len(relation.transitions),
        invariants=tuple(
            sorted(invariant_verdicts, key=lambda item: item.invariant_id)
        ),
        witnesses=run_witness_harness(relation),
        tables=emit_tables(),
        relation=relation,
    )


def passed(run: FormalRun) -> bool:
    return all(verdict.held for verdict in run.invariants) and all(
        verdict.discriminated for verdict in run.witnesses
    )
