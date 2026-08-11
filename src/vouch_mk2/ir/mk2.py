"""The mk2 Policy IR: the tables ``decide()`` executes.

Phase 1 scope: the unit lifecycle and the deviation lifecycle. The unit machine's
states and (source, target) edge set are identical to the 0.6 machine transcribed in
``vouch_06.py`` — conformance is asserted in ``tests/test_mk2_ir.py`` — but every
edge carries a named command instead of 0.6's overloaded ``set-status``, and DoR/DoD
gate obligations are bound to the transition they guard: a transition and its gate
are one code path.

Semantics that deliberately differ from 0.6's implementation are recorded in
``DECISIONS.md`` D-3 (derived blocker set, no auto-unblock, amendment-impact scope).

``release-bound`` is declared but unreachable until Phase 4 introduces release
commands; no Phase 1 command produces it.
"""

from __future__ import annotations

from vouch_mk2.ir.schema import (
    AdjudicationEntry,
    ClosedVocabulary,
    IrIdentity,
    Obligation,
    PolicyIr,
    StateMachine,
    StateSet,
    Transition,
)

UNIT_MACHINE = "unit"
DEVIATION_MACHINE = "deviation"

TRIGGER_AMENDMENT_IMPACT = "amendment-impact"

# Unit lifecycle commands. CMD_SPECIFY creates a unit in the machine's initial
# state; it labels creation, not an edge.
CMD_SPECIFY = "specify"
CMD_FREEZE = "freeze"
CMD_MARK_READY = "mark-ready"
CMD_START = "start"
CMD_MARK_DONE = "mark-done"
CMD_PUBLISH = "publish"
CMD_SUPERSEDE = "supersede"
CMD_RESPECIFY = "respecify"
CMD_BEGIN_REVERIFY = "begin-reverify"
CMD_OPEN_DEVIATION = "open-deviation"

# Deviation lifecycle commands.
CMD_ADJUDICATE_AMEND = "adjudicate-amend"
CMD_ADJUDICATE_REJECT = "adjudicate-reject"
CMD_CLOSE_DEVIATION = "close-deviation"

# Gate obligations (DoR-Unit / DoD-Unit, Phase 1 structural subset).
GATE_DOR_PACK_RECORDED = "gate.dor.pack-recorded"
GATE_DOR_DEPENDENCIES_EXIST = "gate.dor.dependencies-exist"
GATE_DOR_NO_BLOCKING_DEVIATIONS = "gate.dor.no-blocking-deviations"
GATE_DOD_PACK_FROZEN = "gate.dod.pack-frozen"
GATE_DOD_DEPENDENCIES_SATISFIED = "gate.dod.dependencies-satisfied"
GATE_DOD_NO_BLOCKING_DEVIATIONS = "gate.dod.no-blocking-deviations"
GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS = "gate.unblock.no-blocking-deviations"
GATE_REVERIFY_PACK_RECORDED = "gate.reverify.pack-recorded"

_DOR_GATES = (
    GATE_DOR_PACK_RECORDED,
    GATE_DOR_DEPENDENCIES_EXIST,
    GATE_DOR_NO_BLOCKING_DEVIATIONS,
)
_DOD_GATES = (
    GATE_DOD_PACK_FROZEN,
    GATE_DOD_DEPENDENCIES_SATISFIED,
    GATE_DOD_NO_BLOCKING_DEVIATIONS,
)

_UNIT_STATES = (
    "specified",
    "acceptance-frozen",
    "ready",
    "in-progress",
    "blocked",
    "done-pending-acceptance",
    "published",
    "release-bound",
    "invalidated",
    "re-verifying",
    "superseded",
)

# Every state that may hold an open deviation gains an open-deviation edge into
# blocked — the same nine sources as 0.6's X→blocked set-status edges. A further
# deviation opened against an already-blocked unit accumulates without a transition.
_BLOCKABLE = (
    "specified",
    "acceptance-frozen",
    "ready",
    "in-progress",
    "done-pending-acceptance",
    "published",
    "release-bound",
    "invalidated",
    "re-verifying",
)

_UNIT = StateMachine(
    name=UNIT_MACHINE,
    states=_UNIT_STATES,
    initial="specified",
    command_transitions=(
        Transition(
            source="specified",
            command=CMD_FREEZE,
            target="acceptance-frozen",
            gates=_DOR_GATES,
        ),
        Transition(source="acceptance-frozen", command=CMD_MARK_READY, target="ready"),
        Transition(source="ready", command=CMD_START, target="in-progress"),
        Transition(
            source="in-progress", command=CMD_MARK_DONE, target="done-pending-acceptance"
        ),
        Transition(
            source="done-pending-acceptance",
            command=CMD_PUBLISH,
            target="published",
            gates=_DOD_GATES,
        ),
        Transition(
            source="re-verifying",
            command=CMD_PUBLISH,
            target="published",
            gates=_DOD_GATES,
        ),
        Transition(
            source="blocked",
            command=CMD_RESPECIFY,
            target="specified",
            gates=(GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS,),
        ),
        # mk2 addition over 0.6 (DECISIONS.md D-3): the escape edge for units
        # invalidated before an acceptance pack was ever recorded — without it,
        # a never-frozen unit caught in an amendment impact closure could only
        # be superseded or take a fabricated deviation.
        Transition(
            source="invalidated",
            command=CMD_RESPECIFY,
            target="specified",
            gates=(GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS,),
        ),
        Transition(
            source="blocked",
            command=CMD_BEGIN_REVERIFY,
            target="re-verifying",
            gates=(GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS, GATE_REVERIFY_PACK_RECORDED),
        ),
        Transition(
            source="invalidated",
            command=CMD_BEGIN_REVERIFY,
            target="re-verifying",
            gates=(GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS, GATE_REVERIFY_PACK_RECORDED),
        ),
        Transition(source="blocked", command=CMD_SUPERSEDE, target="superseded"),
        Transition(source="published", command=CMD_SUPERSEDE, target="superseded"),
        Transition(source="release-bound", command=CMD_SUPERSEDE, target="superseded"),
        Transition(source="invalidated", command=CMD_SUPERSEDE, target="superseded"),
        Transition(source="re-verifying", command=CMD_SUPERSEDE, target="superseded"),
        *(
            Transition(source=source, command=CMD_OPEN_DEVIATION, target="blocked")
            for source in _BLOCKABLE
        ),
        # mk2 addition over 0.6: further deviations against an already-blocked
        # unit accumulate through this self-loop, so the accumulation path is
        # table-visible and carries any gates the edge ever gains.
        Transition(source="blocked", command=CMD_OPEN_DEVIATION, target="blocked"),
    ),
    adjudication_entries=(
        # D-3: amendment impact = reflexive transitive consumer closure. Only
        # superseded units are excluded; already-invalidated members re-record
        # idempotently so every amendment's impact set is complete.
        AdjudicationEntry(
            trigger=TRIGGER_AMENDMENT_IMPACT,
            target="invalidated",
            sources=_UNIT_STATES,
            excluded_sources=("superseded",),
        ),
    ),
)

_DEVIATION = StateMachine(
    name=DEVIATION_MACHINE,
    states=("open", "amended", "rejected", "closed"),
    initial="open",
    command_transitions=(
        Transition(source="open", command=CMD_ADJUDICATE_AMEND, target="amended"),
        Transition(source="open", command=CMD_ADJUDICATE_REJECT, target="rejected"),
        Transition(source="amended", command=CMD_CLOSE_DEVIATION, target="closed"),
        Transition(source="rejected", command=CMD_CLOSE_DEVIATION, target="closed"),
    ),
)

_OBLIGATIONS = (
    Obligation(
        obligation_id=GATE_DOR_PACK_RECORDED,
        description="Freeze records a non-empty acceptance pack version.",
    ),
    Obligation(
        obligation_id=GATE_DOR_DEPENDENCIES_EXIST,
        description="Every declared dependency names a unit present in the snapshot.",
    ),
    Obligation(
        obligation_id=GATE_DOR_NO_BLOCKING_DEVIATIONS,
        description="No deviation bound to this NAME@VERSION is open or rejected.",
    ),
    Obligation(
        obligation_id=GATE_DOD_PACK_FROZEN,
        description="A frozen acceptance pack version is recorded on the unit.",
    ),
    Obligation(
        obligation_id=GATE_DOD_DEPENDENCIES_SATISFIED,
        description=(
            "Every declared dependency is in a satisfied status "
            "(the unit.satisfied-dependency state set)."
        ),
    ),
    Obligation(
        obligation_id=GATE_DOD_NO_BLOCKING_DEVIATIONS,
        description=(
            "No deviation bound to this NAME@VERSION is open or rejected "
            "(publication never leaves an open deviation behind)."
        ),
    ),
    Obligation(
        obligation_id=GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS,
        description=(
            "Leaving blocked requires every blocking deviation to be closed; "
            "unblocking is explicit, never a side effect (D-3)."
        ),
    ),
    Obligation(
        obligation_id=GATE_REVERIFY_PACK_RECORDED,
        description=(
            "Re-verification requires a recorded acceptance pack version; a unit "
            "with none returns via respecify."
        ),
    ),
)

_STATE_SETS = (
    StateSet(
        name="unit.satisfied-dependency",
        machine=UNIT_MACHINE,
        states=("published", "release-bound"),
    ),
)

_VOCABULARIES = (
    ClosedVocabulary(
        name="deviation-types",
        values=("pack-defect", "contract-conflict", "plan-gap", "capability-request"),
    ),
)

MK2 = PolicyIr(
    identity=IrIdentity(
        name="vouch-mk2-lifecycle",
        version="phase-1",
        source="IMPLEMENTATION-PLAN.md §4 Phase 1; DECISIONS.md D-3; ir/vouch_06.py",
    ),
    machines=(_UNIT, _DEVIATION),
    obligations=_OBLIGATIONS,
    state_sets=_STATE_SETS,
    vocabularies=_VOCABULARIES,
)
