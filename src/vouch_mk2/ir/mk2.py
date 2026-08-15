"""The mk2 Policy IR: the tables ``decide()`` executes.

Phase 5 scope: the unit, deviation, and operational-release lifecycles, plus the
closed evidence vocabulary, artifact-check obligations, and bundle binding. The unit machine's
states and (source, target) edge set are identical to the 0.6 machine transcribed in
``vouch_06.py`` — conformance is asserted in ``tests/test_mk2_ir.py`` — but every
edge carries a named command instead of 0.6's overloaded ``set-status``, and DoR/DoD
gate obligations are bound to the transition they guard: a transition and its gate
are one code path.

Semantics that deliberately differ from 0.6's implementation are recorded in
``DECISIONS.md`` D-3 (derived blocker set, no auto-unblock, amendment-impact scope).

Phase 4 makes ``release-bound`` reachable through release preparation and adds the
operational release machine with ledger-native launch criteria and full completion /
protective-closure claim taxonomy.
"""

from __future__ import annotations

from vouch_mk2.ir.schema import (
    AdjudicationEntry,
    Applicability,
    ClaimRule,
    ClaimShape,
    ClosedVocabulary,
    ConditionKind,
    IrIdentity,
    Obligation,
    ObligationScope,
    PolicyIr,
    StateMachine,
    StateSet,
    Transition,
)

UNIT_MACHINE = "unit"
DEVIATION_MACHINE = "deviation"
RELEASE_O_MACHINE = "release-operational"

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
CMD_PREPARE_RELEASE = "prepare-release"

# Operational release commands.
CMD_SET_CRITERIA = "set-criteria"
CMD_START_DEPLOY = "start-deploy"
CMD_START_VERIFICATION = "start-verification"
CMD_COMPLETE_RELEASE = "complete"
CMD_ROLL_BACK = "roll-back"
CMD_MARK_FAILED_UNRECOVERED = "mark-failed-unrecovered"
CMD_RETIRE_RELEASE = "retire"
CMD_BIND_BUNDLE = "bind-bundle"

# Evidence commands.
CMD_RECORD_EVIDENCE = "record-evidence"
CMD_ADVANCE_ANCHOR = "advance-anchor"
CMD_SET_ANCHOR_MODE = "set-anchor-mode"
CMD_RECORD_FORMAL_CERTIFICATE = "record-formal-certificate"

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
GATE_RELEASE_CRITERIA_BEFORE_PROGRESSION = (
    "gate.release.criteria-before-progression"
)
GATE_RELEASE_COMPLETION_UNITS_RECHECK = (
    "gate.release.completion-units-recheck"
)
GATE_RELEASE_COMPLETION_CRITERIA_RECORDED = (
    "gate.release.completion-criteria-recorded"
)
GATE_RELEASE_COMPLETION_ROSTER_RESOLVES = (
    "gate.release.completion-roster-resolves"
)
GATE_RELEASE_CLOSURE_RECORD_COMPLETE = "gate.release.closure-record-complete"
GATE_RELEASE_ROSTER_DEPENDENCY_CLOSED = "gate.release.roster-dependency-closed"
GATE_RELEASE_ROSTER_UNITS_ELIGIBLE = "gate.release.roster-units-eligible"

# Release claim obligations.
CLAIM_COMPLETION_RECHECK = "claim.completion-recheck"
CLAIM_CLOSURE_RECORD = "claim.closure-record"

# Phase 3 standing mechanical checks. These constants live with the IR because
# obligation ids are IR-owned; evaluators import them from here so declarations
# and registry keys cannot drift through duplicated string literals.
CHECK_REGISTRY_COMPLETE = "check.registry-complete"
CHECK_DEPENDENCIES_EXIST = "check.dependencies-exist"
CHECK_DEPENDENCY_GRAPH_ACYCLIC = "check.dependency-graph-acyclic"
CHECK_ONE_ACTIVE_RECORD_PER_NAME = "check.one-active-record-per-name"
CHECK_DEVIATION_BINDINGS_RESOLVE = "check.deviation-bindings-resolve"
CHECK_BLOCKED_STATUS_COHERENT = "check.blocked-status-coherent"
CHECK_PUBLISHED_UNITS_GATED = "check.published-units-gated"
CHECK_AMENDED_DEVIATIONS_COMPLETE = "check.amended-deviations-complete"
CHECK_RELEASE_ESCAPE_PROPERTY = "check.release-escape-property"
CHECK_RELEASE_ROSTERS_RESOLVE = "check.release-rosters-resolve"
CHECK_RELEASE_CRITERIA_BEFORE_PROGRESSION = (
    "check.release-criteria-before-progression"
)
CHECK_RELEASE_CLOSURE_RECORDS = "check.release-closure-records"
CHECK_RELEASE_BOUND_UNITS_ROSTERED = "check.release-bound-units-rostered"

# Phase 5 I/O-side artifact checks.
CHECK_EVIDENCE_CHAINS = "check.evidence-chains"
CHECK_EVIDENCE_ANCHORS = "check.evidence-anchors"
CHECK_BUNDLE_INVENTORIES = "check.bundle-inventories"
CHECK_FORMAL_CERTIFICATE = "check.formal-certificate"
CHECK_MAP_FRESHNESS = "check.map-freshness"

# Permanently NOT_CHECKED organizational claims (docs/claim-boundary.md).
ORG_ACCEPTANCE_OWNERSHIP = "org.acceptance-ownership"
ORG_INTENT_VALIDATION = "org.intent-validation"
ORG_INDEPENDENCE = "org.independence"
ORG_CAUSAL_RED_HISTORY = "org.causal-red-history"
ORG_PROTECTIVE_CLOSURE_TRUTHFULNESS = "org.protective-closure-truthfulness"

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
            source="published",
            command=CMD_PREPARE_RELEASE,
            target="release-bound",
            gates=(
                GATE_RELEASE_ROSTER_DEPENDENCY_CLOSED,
                GATE_RELEASE_ROSTER_UNITS_ELIGIBLE,
            ),
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

_RELEASE_O_STATES = (
    "prepared",
    "deploying",
    "verifying",
    "rolled-back",
    "succeeded",
    "failed-unrecovered",
    "retired",
    "invalidated",
)

_RELEASE_O = StateMachine(
    name=RELEASE_O_MACHINE,
    states=_RELEASE_O_STATES,
    initial="prepared",
    command_transitions=(
        # Criteria are recorded while prepared; the self-loop keeps set-criteria
        # legality in the tables (like the blocked open-deviation self-loop)
        # instead of hand-coded in decide().
        Transition(
            source="prepared",
            command=CMD_SET_CRITERIA,
            target="prepared",
        ),
        Transition(
            source="prepared",
            command=CMD_BIND_BUNDLE,
            target="prepared",
        ),
        Transition(
            source="prepared",
            command=CMD_START_DEPLOY,
            target="deploying",
            gates=(GATE_RELEASE_CRITERIA_BEFORE_PROGRESSION,),
        ),
        Transition(
            source="deploying",
            command=CMD_START_VERIFICATION,
            target="verifying",
        ),
        Transition(
            source="verifying",
            command=CMD_COMPLETE_RELEASE,
            target="succeeded",
            gates=(
                GATE_RELEASE_COMPLETION_UNITS_RECHECK,
                GATE_RELEASE_COMPLETION_CRITERIA_RECORDED,
                GATE_RELEASE_COMPLETION_ROSTER_RESOLVES,
            ),
        ),
        Transition(
            source="deploying",
            command=CMD_ROLL_BACK,
            target="rolled-back",
            gates=(GATE_RELEASE_CLOSURE_RECORD_COMPLETE,),
        ),
        Transition(
            source="verifying",
            command=CMD_ROLL_BACK,
            target="rolled-back",
            gates=(GATE_RELEASE_CLOSURE_RECORD_COMPLETE,),
        ),
        Transition(
            source="rolled-back",
            command=CMD_MARK_FAILED_UNRECOVERED,
            target="failed-unrecovered",
            gates=(GATE_RELEASE_CLOSURE_RECORD_COMPLETE,),
        ),
        *(
            Transition(
                source=source,
                command=CMD_RETIRE_RELEASE,
                target="retired",
                gates=(GATE_RELEASE_CLOSURE_RECORD_COMPLETE,),
            )
            for source in ("prepared", "deploying", "verifying", "succeeded")
        ),
    ),
    adjudication_entries=(
        AdjudicationEntry(
            trigger="amendment",
            target="invalidated",
            sources=_RELEASE_O_STATES,
            excluded_sources=("invalidated",),
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
        scope=ObligationScope.TRANSITION_GATE,
        description="Freeze records a non-empty acceptance pack version.",
    ),
    Obligation(
        obligation_id=GATE_DOR_DEPENDENCIES_EXIST,
        scope=ObligationScope.TRANSITION_GATE,
        description="Every declared dependency names a unit present in the snapshot.",
    ),
    Obligation(
        obligation_id=GATE_DOR_NO_BLOCKING_DEVIATIONS,
        scope=ObligationScope.TRANSITION_GATE,
        description="No deviation bound to this NAME@VERSION is open or rejected.",
    ),
    Obligation(
        obligation_id=GATE_DOD_PACK_FROZEN,
        scope=ObligationScope.TRANSITION_GATE,
        description="A frozen acceptance pack version is recorded on the unit.",
    ),
    Obligation(
        obligation_id=GATE_DOD_DEPENDENCIES_SATISFIED,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "Every declared dependency is in a satisfied status "
            "(the unit.satisfied-dependency state set)."
        ),
    ),
    Obligation(
        obligation_id=GATE_DOD_NO_BLOCKING_DEVIATIONS,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "No deviation bound to this NAME@VERSION is open or rejected "
            "(publication never leaves an open deviation behind)."
        ),
    ),
    Obligation(
        obligation_id=GATE_UNBLOCK_NO_BLOCKING_DEVIATIONS,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "Leaving blocked requires every blocking deviation to be closed; "
            "unblocking is explicit, never a side effect (D-3)."
        ),
    ),
    Obligation(
        obligation_id=GATE_REVERIFY_PACK_RECORDED,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "Re-verification requires a recorded acceptance pack version; a unit "
            "with none returns via respecify."
        ),
    ),
    Obligation(
        obligation_id=GATE_RELEASE_CRITERIA_BEFORE_PROGRESSION,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "A release advances out of prepared only with recorded launch criteria."
        ),
    ),
    Obligation(
        obligation_id=GATE_RELEASE_COMPLETION_UNITS_RECHECK,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "On every transition into succeeded, every roster member re-checks: "
            "exists at the bound version, status release-bound, non-empty pack, "
            "and no blocking deviations."
        ),
    ),
    Obligation(
        obligation_id=GATE_RELEASE_COMPLETION_CRITERIA_RECORDED,
        scope=ObligationScope.TRANSITION_GATE,
        description="A completion claim requires recorded launch criteria.",
    ),
    Obligation(
        obligation_id=GATE_RELEASE_COMPLETION_ROSTER_RESOLVES,
        scope=ObligationScope.TRANSITION_GATE,
        description="Every include resolves NAME@VERSION.",
    ),
    Obligation(
        obligation_id=GATE_RELEASE_CLOSURE_RECORD_COMPLETE,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "A protective closure requires a non-empty trigger; record "
            "incompleteness is the only permitted refusal, never roster health."
        ),
    ),
    Obligation(
        obligation_id=GATE_RELEASE_ROSTER_DEPENDENCY_CLOSED,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "The roster contains every dependency of every member — the 0.6 I9 lesson."
        ),
    ),
    Obligation(
        obligation_id=GATE_RELEASE_ROSTER_UNITS_ELIGIBLE,
        scope=ObligationScope.TRANSITION_GATE,
        description=(
            "Every roster member is published or release-bound, has a recorded "
            "pack, and no blocking deviations."
        ),
    ),
    Obligation(
        obligation_id=CLAIM_COMPLETION_RECHECK,
        scope=ObligationScope.CLAIM,
        description=(
            "Re-check included unit-done, integrity, criteria digest, and roster "
            "bindings on every transition into a completion claim."
        ),
    ),
    Obligation(
        obligation_id=CLAIM_CLOSURE_RECORD,
        scope=ObligationScope.CLAIM,
        description=(
            "Record the trigger, required trigger_kind, frozen-roster status "
            "snapshot, no_completion_claim: true, and observations; record "
            "incompleteness is the only permitted refusal, never roster health."
        ),
    ),
    Obligation(
        obligation_id=CHECK_REGISTRY_COMPLETE,
        scope=ObligationScope.STANDING_CHECK,
        description="Every Phase 3 IR check obligation has exactly one registered evaluator.",
    ),
    Obligation(
        obligation_id=CHECK_DEPENDENCIES_EXIST,
        scope=ObligationScope.STANDING_CHECK,
        description="Every unit dependency resolves to a unit in the snapshot.",
    ),
    Obligation(
        obligation_id=CHECK_DEPENDENCY_GRAPH_ACYCLIC,
        scope=ObligationScope.STANDING_CHECK,
        description="The unit dependency graph contains no cycle.",
    ),
    Obligation(
        obligation_id=CHECK_ONE_ACTIVE_RECORD_PER_NAME,
        scope=ObligationScope.STANDING_CHECK,
        description="Every unit name has at most one non-superseded record.",
    ),
    Obligation(
        obligation_id=CHECK_DEVIATION_BINDINGS_RESOLVE,
        scope=ObligationScope.STANDING_CHECK,
        description="Every deviation binds the current version of an existing unit.",
    ),
    Obligation(
        obligation_id=CHECK_BLOCKED_STATUS_COHERENT,
        scope=ObligationScope.STANDING_CHECK,
        description=(
            "Blocking deviations only ever bind units in blocked, superseded, or "
            "invalidated status. (The converse is legal: D-3's no-auto-unblock "
            "means a unit stays blocked after its last blocker closes, until an "
            "explicit respecify or begin-reverify.)"
        ),
    ),
    Obligation(
        obligation_id=CHECK_PUBLISHED_UNITS_GATED,
        scope=ObligationScope.STANDING_CHECK,
        description="Every published or release-bound unit still satisfies publication gates.",
    ),
    Obligation(
        obligation_id=CHECK_AMENDED_DEVIATIONS_COMPLETE,
        scope=ObligationScope.STANDING_CHECK,
        description="Every amended deviation records its amendment and a resolving impact set.",
    ),
    Obligation(
        obligation_id=CHECK_RELEASE_ESCAPE_PROPERTY,
        scope=ObligationScope.STANDING_CHECK,
        description="Every active operational release state has a protective escape edge.",
    ),
    Obligation(
        obligation_id=CHECK_RELEASE_ROSTERS_RESOLVE,
        scope=ObligationScope.STANDING_CHECK,
        description="Active and succeeded release rosters resolve at their bound versions.",
    ),
    Obligation(
        obligation_id=CHECK_RELEASE_CRITERIA_BEFORE_PROGRESSION,
        scope=ObligationScope.STANDING_CHECK,
        description="Every progressed release retains ledger-native launch criteria.",
    ),
    Obligation(
        obligation_id=CHECK_RELEASE_CLOSURE_RECORDS,
        scope=ObligationScope.STANDING_CHECK,
        description="Release closure records match their claim-bearing state semantics.",
    ),
    Obligation(
        obligation_id=CHECK_RELEASE_BOUND_UNITS_ROSTERED,
        scope=ObligationScope.STANDING_CHECK,
        description="Every release-bound unit appears in at least one release roster.",
    ),
    Obligation(
        obligation_id=CHECK_EVIDENCE_CHAINS,
        scope=ObligationScope.ARTIFACT_CHECK,
        description="Evidence heads resolve to coherent content-addressed chains.",
    ),
    Obligation(
        obligation_id=CHECK_EVIDENCE_ANCHORS,
        scope=ObligationScope.ARTIFACT_CHECK,
        description="CI evidence anchors name real ancestors of their chains.",
        applicability=(
            Applicability(kind=ConditionKind.ANCHOR_MODE_IS, value="ci"),
        ),
    ),
    Obligation(
        obligation_id=CHECK_BUNDLE_INVENTORIES,
        scope=ObligationScope.ARTIFACT_CHECK,
        description="Bound release inventories resolve and decode canonically.",
    ),
    Obligation(
        obligation_id=CHECK_FORMAL_CERTIFICATE,
        scope=ObligationScope.ARTIFACT_CHECK,
        description=(
            "A recorded formal certificate matches a fresh exhaustive run."
        ),
    ),
    Obligation(
        obligation_id=CHECK_MAP_FRESHNESS,
        scope=ObligationScope.ARTIFACT_CHECK,
        description="A committed system map exactly matches the authoritative snapshot.",
    ),
    Obligation(
        obligation_id=ORG_ACCEPTANCE_OWNERSHIP,
        scope=ObligationScope.ORGANIZATIONAL,
        description="Acceptance ownership is real.",
    ),
    Obligation(
        obligation_id=ORG_INTENT_VALIDATION,
        scope=ObligationScope.ORGANIZATIONAL,
        description="Intent validation.",
    ),
    Obligation(
        obligation_id=ORG_INDEPENDENCE,
        scope=ObligationScope.ORGANIZATIONAL,
        description="Independence between real persons.",
    ),
    Obligation(
        obligation_id=ORG_CAUSAL_RED_HISTORY,
        scope=ObligationScope.ORGANIZATIONAL,
        description="Causal red history.",
    ),
    Obligation(
        obligation_id=ORG_PROTECTIVE_CLOSURE_TRUTHFULNESS,
        scope=ObligationScope.ORGANIZATIONAL,
        description="Protective-closure truthfulness.",
    ),
)

_STATE_SETS = (
    StateSet(
        name="unit.satisfied-dependency",
        machine=UNIT_MACHINE,
        states=("published", "release-bound"),
    ),
    # 0.6's settled_unit_statuses, carried over: the at-rest form of dependency
    # health. Publication requires *satisfied* upstreams; a unit that is already
    # published stays healthy when an upstream is later superseded (0.6's
    # retain-releases philosophy: supersession is history, not failure).
    StateSet(
        name="unit.settled",
        machine=UNIT_MACHINE,
        states=("published", "release-bound", "superseded"),
    ),
    StateSet(
        name="release.active.operational",
        machine=RELEASE_O_MACHINE,
        states=("prepared", "deploying", "verifying"),
    ),
    StateSet(
        name="release.protective.operational",
        machine=RELEASE_O_MACHINE,
        states=("rolled-back", "failed-unrecovered", "retired", "invalidated"),
    ),
)

_VOCABULARIES = (
    ClosedVocabulary(
        name="deviation-types",
        values=("pack-defect", "contract-conflict", "plan-gap", "capability-request"),
    ),
    ClosedVocabulary(
        name="closure-trigger-kinds",
        values=("amendment", "operator", "supersession"),
    ),
    ClosedVocabulary(
        name="evidence-kinds",
        values=("red", "green", "refactor"),
    ),
    ClosedVocabulary(
        name="evidence-outcomes",
        values=("tests-failed", "tests-passed"),
    ),
    ClosedVocabulary(name="anchor-modes", values=("none", "ci")),
)

_CLAIM_RULES = (
    ClaimRule(
        machine=RELEASE_O_MACHINE,
        state="succeeded",
        shape=ClaimShape.COMPLETION_CLAIM,
        obligations=(CLAIM_COMPLETION_RECHECK,),
    ),
    *(
        ClaimRule(
            machine=RELEASE_O_MACHINE,
            state=state,
            shape=ClaimShape.PROTECTIVE_CLOSURE,
            obligations=(CLAIM_CLOSURE_RECORD,),
        )
        for state in ("rolled-back", "failed-unrecovered", "retired", "invalidated")
    ),
)

MK2 = PolicyIr(
    identity=IrIdentity(
        name="vouch-mk2-lifecycle",
        version="phase-5",
        source="IMPLEMENTATION-PLAN.md §4 Phase 5; DECISIONS.md D-4; ir/vouch_06.py",
    ),
    machines=(_UNIT, _DEVIATION, _RELEASE_O),
    claim_rules=_CLAIM_RULES,
    obligations=_OBLIGATIONS,
    state_sets=_STATE_SETS,
    vocabularies=_VOCABULARIES,
)
