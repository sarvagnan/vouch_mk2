"""The closed event union. Events are facts ``decide`` accepted; ``apply`` folds
them into the next snapshot. No event is emitted without its gates having passed —
a gated transition's event carries the gate record (the I10 lesson: publication
without a gate record is unrepresentable)."""

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.core.model import DeviationType, UnitRef
from vouch_mk2.ir.schema import Verdict


@dataclass(frozen=True, slots=True)
class GateCheck:
    obligation_id: str
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class GateRecord:
    """The evaluated gate of one accepted transition. Every check is PASS by
    construction — a non-PASS check refuses the command instead."""

    transition: str
    checks: tuple[GateCheck, ...]


@dataclass(frozen=True, slots=True)
class UnitSpecified:
    name: str
    version: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceFrozen:
    name: str
    pack_version: str
    gate: GateRecord


@dataclass(frozen=True, slots=True)
class UnitMarkedReady:
    name: str


@dataclass(frozen=True, slots=True)
class WorkStarted:
    name: str


@dataclass(frozen=True, slots=True)
class UnitMarkedDone:
    name: str


@dataclass(frozen=True, slots=True)
class UnitPublished:
    name: str
    gate: GateRecord


@dataclass(frozen=True, slots=True)
class UnitSuperseded:
    name: str


@dataclass(frozen=True, slots=True)
class UnitRespecified:
    """Return to draft: the recorded acceptance pack version is cleared; a new
    freeze must record a new one."""

    name: str
    gate: GateRecord


@dataclass(frozen=True, slots=True)
class ReverificationStarted:
    name: str
    gate: GateRecord


@dataclass(frozen=True, slots=True)
class DeviationOpened:
    deviation_id: str
    deviation_type: DeviationType
    unit: UnitRef


@dataclass(frozen=True, slots=True)
class UnitBlocked:
    """The unit entered ``blocked``. The blocker set is deliberately not carried:
    it is derived state (D-3) and an embedded copy would go stale as deviations
    accumulate or close — read ``blocking_deviations`` instead."""

    name: str


@dataclass(frozen=True, slots=True)
class DeviationAmended:
    deviation_id: str
    amendment_version: str
    impact_set: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnitInvalidated:
    name: str
    trigger_deviation_id: str


@dataclass(frozen=True, slots=True)
class DeviationRejected:
    deviation_id: str


@dataclass(frozen=True, slots=True)
class DeviationClosed:
    deviation_id: str


type Event = (
    UnitSpecified
    | AcceptanceFrozen
    | UnitMarkedReady
    | WorkStarted
    | UnitMarkedDone
    | UnitPublished
    | UnitSuperseded
    | UnitRespecified
    | ReverificationStarted
    | DeviationOpened
    | UnitBlocked
    | DeviationAmended
    | UnitInvalidated
    | DeviationRejected
    | DeviationClosed
)
