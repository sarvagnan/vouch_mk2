"""The closed command union. Every mutation enters through exactly one of these.

Each lifecycle command carries the IR command label it is decided under
(``ir_command``); ``decide`` looks the (status, label) pair up in the mk2 IR unit
machine, so legality lives in the tables, not in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from vouch_mk2.core.model import (
    AnchorMode,
    CiPolicy,
    DeviationType,
    EvidenceEvent,
    LaunchCriteria,
    PackInventory,
    Tier,
    UnitRef,
)
from vouch_mk2.ir import mk2


@dataclass(frozen=True, slots=True)
class SpecifyUnit:
    """Create a new unit in status ``specified``. Not a transition: there is no
    prior state, so its label has no IR edge."""

    ir_command: ClassVar[str] = mk2.CMD_SPECIFY
    name: str
    version: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FreezeAcceptance:
    ir_command: ClassVar[str] = mk2.CMD_FREEZE
    name: str
    pack_version: str
    pack_digest: str = ""
    test_set_digest: str = ""


@dataclass(frozen=True, slots=True)
class BindPack:
    """Bind the current acceptance-pack version to immutable inventory bytes."""

    ir_command: ClassVar[str] = mk2.CMD_BIND_PACK
    name: str
    pack_digest: str
    test_set_digest: str = ""


@dataclass(frozen=True, slots=True)
class RebindPack:
    """Replace a published unit's pack after a shared-interface adaptation."""

    ir_command: ClassVar[str] = mk2.CMD_REBIND_PACK
    name: str
    pack_digest: str
    reason: str
    test_set_digest: str = ""
    old_inventory: PackInventory | None = None
    new_inventory: PackInventory | None = None


@dataclass(frozen=True, slots=True)
class MarkReady:
    ir_command: ClassVar[str] = mk2.CMD_MARK_READY
    name: str


@dataclass(frozen=True, slots=True)
class StartWork:
    ir_command: ClassVar[str] = mk2.CMD_START
    name: str


@dataclass(frozen=True, slots=True)
class MarkDone:
    ir_command: ClassVar[str] = mk2.CMD_MARK_DONE
    name: str


@dataclass(frozen=True, slots=True)
class Publish:
    ir_command: ClassVar[str] = mk2.CMD_PUBLISH
    name: str


@dataclass(frozen=True, slots=True)
class Supersede:
    ir_command: ClassVar[str] = mk2.CMD_SUPERSEDE
    name: str


@dataclass(frozen=True, slots=True)
class Respecify:
    ir_command: ClassVar[str] = mk2.CMD_RESPECIFY
    name: str


@dataclass(frozen=True, slots=True)
class BeginReverify:
    ir_command: ClassVar[str] = mk2.CMD_BEGIN_REVERIFY
    name: str


@dataclass(frozen=True, slots=True)
class OpenDeviation:
    ir_command: ClassVar[str] = mk2.CMD_OPEN_DEVIATION
    deviation_id: str
    deviation_type: DeviationType
    unit: UnitRef


@dataclass(frozen=True, slots=True)
class AmendDeviation:
    ir_command: ClassVar[str] = mk2.CMD_ADJUDICATE_AMEND
    deviation_id: str
    amendment_version: str


@dataclass(frozen=True, slots=True)
class RejectDeviation:
    ir_command: ClassVar[str] = mk2.CMD_ADJUDICATE_REJECT
    deviation_id: str


@dataclass(frozen=True, slots=True)
class CloseDeviation:
    ir_command: ClassVar[str] = mk2.CMD_CLOSE_DEVIATION
    deviation_id: str


@dataclass(frozen=True, slots=True)
class PrepareRelease:
    ir_command: ClassVar[str] = mk2.CMD_PREPARE_RELEASE
    release_id: str
    includes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SetReleaseCriteria:
    ir_command: ClassVar[str] = mk2.CMD_SET_CRITERIA
    release_id: str
    criteria: LaunchCriteria


@dataclass(frozen=True, slots=True)
class BindBundle:
    ir_command: ClassVar[str] = mk2.CMD_BIND_BUNDLE
    release_id: str
    inventory_digest: str


@dataclass(frozen=True, slots=True)
class RecordEvidence:
    ir_command: ClassVar[str] = mk2.CMD_RECORD_EVIDENCE
    event: EvidenceEvent
    event_digest: str


@dataclass(frozen=True, slots=True)
class AdvanceAnchor:
    ir_command: ClassVar[str] = mk2.CMD_ADVANCE_ANCHOR
    unit_name: str


@dataclass(frozen=True, slots=True)
class SetAnchorMode:
    ir_command: ClassVar[str] = mk2.CMD_SET_ANCHOR_MODE
    mode: AnchorMode


@dataclass(frozen=True, slots=True)
class SetTier:
    ir_command: ClassVar[str] = mk2.CMD_SET_TIER
    tier: Tier


@dataclass(frozen=True, slots=True)
class SetCiPolicy:
    """Choose whether CI reports ledger coherence or requires terminal units."""

    ir_command: ClassVar[str] = mk2.CMD_SET_CI_POLICY
    policy: CiPolicy


@dataclass(frozen=True, slots=True)
class MarkOpenInCi:
    """Record why one nonterminal unit may remain open under strict CI."""

    ir_command: ClassVar[str] = mk2.CMD_MARK_OPEN_IN_CI
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class ClearOpenInCi:
    """Remove a unit's recorded strict-CI exception."""

    ir_command: ClassVar[str] = mk2.CMD_CLEAR_OPEN_IN_CI
    name: str


@dataclass(frozen=True, slots=True)
class RecordFormalCertificate:
    ir_command: ClassVar[str] = mk2.CMD_RECORD_FORMAL_CERTIFICATE
    certificate_digest: str


@dataclass(frozen=True, slots=True)
class StartDeploy:
    ir_command: ClassVar[str] = mk2.CMD_START_DEPLOY
    release_id: str


@dataclass(frozen=True, slots=True)
class StartVerification:
    ir_command: ClassVar[str] = mk2.CMD_START_VERIFICATION
    release_id: str


@dataclass(frozen=True, slots=True)
class CompleteRelease:
    ir_command: ClassVar[str] = mk2.CMD_COMPLETE_RELEASE
    release_id: str


@dataclass(frozen=True, slots=True)
class RollBack:
    ir_command: ClassVar[str] = mk2.CMD_ROLL_BACK
    release_id: str
    trigger: str
    observations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkFailedUnrecovered:
    ir_command: ClassVar[str] = mk2.CMD_MARK_FAILED_UNRECOVERED
    release_id: str
    trigger: str
    observations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetireRelease:
    ir_command: ClassVar[str] = mk2.CMD_RETIRE_RELEASE
    release_id: str
    trigger: str
    observations: tuple[str, ...] = ()


type UnitTransitionCommand = (
    FreezeAcceptance
    | MarkReady
    | StartWork
    | MarkDone
    | Publish
    | Supersede
    | Respecify
    | BeginReverify
)

type DeviationCommand = AmendDeviation | RejectDeviation | CloseDeviation

type ReleaseTransitionCommand = (
    StartDeploy
    | StartVerification
    | CompleteRelease
    | RollBack
    | MarkFailedUnrecovered
    | RetireRelease
)

type Command = (
    SpecifyUnit
    | UnitTransitionCommand
    | OpenDeviation
    | DeviationCommand
    | PrepareRelease
    | SetReleaseCriteria
    | BindBundle
    | BindPack
    | RebindPack
    | RecordEvidence
    | AdvanceAnchor
    | SetAnchorMode
    | SetTier
    | SetCiPolicy
    | MarkOpenInCi
    | ClearOpenInCi
    | RecordFormalCertificate
    | ReleaseTransitionCommand
)
