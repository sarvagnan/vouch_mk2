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

from vouch_mk2.ir.schema import Tier


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


class CiPolicy(Enum):
    LEDGER = "ledger"
    STRICT = "strict"


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
    pack_digest: str = ""
    evidence_format: int = 2


@dataclass(frozen=True, slots=True)
class EvidencePackSummary:
    """The immutable-chain result for one non-legacy pack digest."""

    pack_digest: str
    red_seen: bool
    green: bool


@dataclass(frozen=True, slots=True)
class EvidenceHead:
    unit_name: str
    head: str
    head_sequence: int
    anchored: str
    unit_version: str = ""
    pack_summaries: tuple[EvidencePackSummary, ...] = ()
    anchor_run_id: str = ""

    def ref(self) -> UnitRef:
        return UnitRef(name=self.unit_name, version=self.unit_version)


@dataclass(frozen=True, slots=True)
class PackRebound:
    """One append-only post-publication pack adaptation epoch."""

    old_inventory_digest: str
    new_inventory_digest: str
    old_test_set_digest: str
    new_test_set_digest: str
    reason: str
    evidence_head_sequence: int


@dataclass(frozen=True, slots=True)
class CiException:
    """An exact ``NAME@VERSION`` allowed to remain open under strict CI."""

    # The format-3 field is retained as ``name`` on disk, but its value is an
    # exact UnitRef label. Bare names from the short-lived pre-fix encoder are
    # readable for compatibility and are never treated as active exemptions.
    unit_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class Unit:
    name: str
    version: str
    status: UnitStatus
    depends_on: tuple[str, ...] = ()
    acceptance_pack_version: str = ""
    published_once: bool = False
    acceptance_pack_digest: str = ""
    acceptance_test_set_digest: str = ""
    pack_history: tuple[str, ...] = ()
    pack_binding: str = ""
    rebound_history: tuple[PackRebound, ...] = ()

    def __post_init__(self) -> None:
        # Format 2 predates the provenance spelling in the owner-pinned golden.
        # A bound digest without an explicit provenance is therefore the
        # canonical at-freeze case; only the security-relevant late case needs
        # an additional serialized field.
        if self.acceptance_pack_digest and not self.pack_binding:
            object.__setattr__(self, "pack_binding", "at-freeze")
        if self.acceptance_pack_digest and not self.acceptance_test_set_digest:
            object.__setattr__(
                self,
                "acceptance_test_set_digest",
                self.acceptance_pack_digest,
            )

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
class PackEntry:
    path: str
    sha256: str
    size: int
    role: str = ""


@dataclass(frozen=True, slots=True)
class PackInventory:
    entries: tuple[PackEntry, ...]
    pack_format: int = 1


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
    event_head: str = ""
    units: tuple[Unit, ...] = ()
    deviations: tuple[Deviation, ...] = ()
    releases: tuple[Release, ...] = ()
    evidence_heads: tuple[EvidenceHead, ...] = ()
    anchor_mode: AnchorMode = AnchorMode.NONE
    tier: Tier = Tier.LIGHTWEIGHT
    formal_certificate_digest: str = ""
    ci_policy: CiPolicy = CiPolicy.STRICT
    ci_exceptions: tuple[CiException, ...] = ()

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
        """Resolve the evidence head for the ACTIVE record named ``unit_name``."""
        unit = self.unit(unit_name)
        if unit is None or unit.status is UnitStatus.SUPERSEDED:
            return None
        return self.evidence_head_ref(unit.ref())

    def evidence_head_ref(self, ref: UnitRef) -> EvidenceHead | None:
        """Resolve an exact historical or current evidence-chain head."""
        for head in self.evidence_heads:
            if head.ref() == ref:
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
