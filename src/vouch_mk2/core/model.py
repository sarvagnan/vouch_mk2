"""Domain state: closed, frozen, and free of I/O.

The snapshot is the one authoritative picture of a project. Everything here is a
value; mutation happens only by ``decide`` emitting events and ``apply`` folding
them into a new snapshot.

Deviation blocking is derived, never stored (DECISIONS.md D-3): a unit's blocker
set is *defined* by ``blocking_deviations`` over the deviation records, so a stored
array can never drift from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UnitStatus(Enum):
    SPECIFIED = "specified"
    ACCEPTANCE_FROZEN = "acceptance-frozen"
    READY = "ready"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    DONE_PENDING_ACCEPTANCE = "done-pending-acceptance"
    PUBLISHED = "published"
    RELEASE_BOUND = "release-bound"
    INVALIDATED = "invalidated"
    RE_VERIFYING = "re-verifying"
    SUPERSEDED = "superseded"


class DeviationType(Enum):
    PACK_DEFECT = "pack-defect"
    CONTRACT_CONFLICT = "contract-conflict"
    PLAN_GAP = "plan-gap"
    CAPABILITY_REQUEST = "capability-request"


class DeviationStatus(Enum):
    OPEN = "open"
    AMENDED = "amended"
    REJECTED = "rejected"
    CLOSED = "closed"


class ReleaseArchetype(Enum):
    OPERATIONAL = "operational"


class ReleaseState(Enum):
    PREPARED = "prepared"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    ROLLED_BACK = "rolled-back"
    SUCCEEDED = "succeeded"
    FAILED_UNRECOVERED = "failed-unrecovered"
    RETIRED = "retired"
    INVALIDATED = "invalidated"


class EvidenceKind(Enum):
    RED = "red"
    GREEN = "green"
    REFACTOR = "refactor"


class EvidenceOutcome(Enum):
    TESTS_FAILED = "tests-failed"
    TESTS_PASSED = "tests-passed"


class AnchorMode(Enum):
    NONE = "none"
    CI = "ci"


class ClosureTriggerKind(Enum):
    AMENDMENT = "amendment"
    OPERATOR = "operator"
    SUPERSESSION = "supersession"


# A deviation blocks its unit until it is closed: rejected-but-unclosed still
# blocks (closure is the executor's acknowledgment to resume as written).
BLOCKING_DEVIATION_STATUSES = frozenset(
    {DeviationStatus.OPEN, DeviationStatus.REJECTED}
)


@dataclass(frozen=True, slots=True)
class UnitRef:
    """A resolved NAME@VERSION unit binding. A bare-name binding is unrepresentable."""

    name: str
    version: str

    def label(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    unit: UnitRef
    sequence: int
    parent: str
    kind: EvidenceKind
    command_line: str
    outcome: EvidenceOutcome
    recorded_at: str


@dataclass(frozen=True, slots=True)
class EvidenceHead:
    unit_name: str
    head: str
    head_sequence: int
    anchored: str


@dataclass(frozen=True, slots=True)
class Unit:
    name: str
    version: str
    status: UnitStatus
    depends_on: tuple[str, ...] = ()
    acceptance_pack_version: str = ""

    def ref(self) -> UnitRef:
        return UnitRef(name=self.name, version=self.version)


@dataclass(frozen=True, slots=True)
class LaunchCriteria:
    """Ledger-native release criteria.

    These fields live directly on the release, never in a sidecar, closing the
    criteria-sidecar drift chain inherited from 0.6.
    """

    launch_criteria: tuple[str, ...]
    verification_window: str
    rollback_triggers: tuple[str, ...]
    go_no_go_authority: str


@dataclass(frozen=True, slots=True)
class ClosureRecord:
    trigger: str
    trigger_kind: ClosureTriggerKind
    roster_snapshot: tuple[tuple[str, str, str], ...]
    no_completion_claim: bool
    observations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Release:
    release_id: str
    archetype: ReleaseArchetype
    state: ReleaseState
    includes: tuple[UnitRef, ...]
    criteria: LaunchCriteria | None
    closure: ClosureRecord | None
    bundle_inventory_digest: str = ""


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class BundleInventory:
    entries: tuple[InventoryEntry, ...]
    exclusions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Deviation:
    deviation_id: str
    deviation_type: DeviationType
    unit: UnitRef
    status: DeviationStatus
    amendment_version: str = ""
    impact_set: tuple[str, ...] = ()
    release_impact_set: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    revision: int = 0
    units: tuple[Unit, ...] = ()
    deviations: tuple[Deviation, ...] = ()
    releases: tuple[Release, ...] = ()
    evidence_heads: tuple[EvidenceHead, ...] = ()
    anchor_mode: AnchorMode = AnchorMode.NONE
    formal_certificate_digest: str = ""

    def unit(self, name: str) -> Unit | None:
        """The CURRENT record for ``name`` (D-9): the one non-superseded record
        if any; otherwise the superseded record with the greatest version
        string — a defined, tuple-order-independent choice (the codec accepts
        either order), used only for diagnostics since every lifecycle command
        refuses a superseded record anyway."""
        historical: Unit | None = None
        for unit in self.units:
            if unit.name != name:
                continue
            if unit.status is not UnitStatus.SUPERSEDED:
                return unit
            if historical is None or unit.version > historical.version:
                historical = unit
        return historical

    def unit_ref(self, ref: UnitRef) -> Unit | None:
        """Resolve an exact historical or current ``NAME@VERSION`` binding."""
        for unit in self.units:
            if unit.ref() == ref:
                return unit
        return None

    def deviation(self, deviation_id: str) -> Deviation | None:
        for deviation in self.deviations:
            if deviation.deviation_id == deviation_id:
                return deviation
        return None

    def release(self, release_id: str) -> Release | None:
        for release in self.releases:
            if release.release_id == release_id:
                return release
        return None

    def evidence_head(self, unit_name: str) -> EvidenceHead | None:
        for head in self.evidence_heads:
            if head.unit_name == unit_name:
                return head
        return None

    def consumers_of(self, name: str) -> tuple[str, ...]:
        """Units that declare ``name`` as a dependency (the reverse producer edge)."""
        return tuple(
            sorted(unit.name for unit in self.units if name in unit.depends_on)
        )


def blocking_deviations(snapshot: Snapshot, unit: Unit) -> tuple[Deviation, ...]:
    """The derived blocker set for a unit — the only definition of 'blocked on'."""
    return tuple(
        deviation
        for deviation in snapshot.deviations
        if deviation.unit == unit.ref()
        and deviation.status in BLOCKING_DEVIATION_STATUSES
    )
