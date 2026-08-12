"""The Vouch 0.6.2 lifecycle transcribed as Policy IR data.

This is the Phase 0 exit-criterion artifact (IMPLEMENTATION-PLAN.md §4): the IR
schema must be able to express the full 0.6 lifecycle as data. The transcription is
diffed against 0.6's own machine-readable tables
(``vouch/verification-kit/policy/lifecycle-policy.json``, vendored under
``tests/data/``) and against the machine.md §14 terminal-state tables in
``tests/test_vouch06_transcription.py``.

Sources, per element:

- state machines and classification sets: ``lifecycle-policy.json`` (kit-5);
- the ``freeze`` edge and ``invalidated`` adjudication entries: machine.md §9/§11/§14
  and ``bin/vouch`` (set-status refuses to enter ``acceptance-frozen``; amendment
  adjudication is the only entry into ``invalidated``);
- claim shapes and obligations: machine.md §14 terminal-state tables;
- tier requirements: machine.md §13 conformance matrix.

Deliberately not transcribed: ``unit_stage_projection`` (a map-presentation concern,
Phase 7) and ``methodology_deltas`` (0.6 history, not lifecycle).
"""

from __future__ import annotations

from vouch_mk2.ir.schema import (
    AdjudicationEntry,
    ClaimRule,
    ClaimShape,
    ClosedVocabulary,
    IrIdentity,
    Obligation,
    ObligationScope,
    PolicyIr,
    RequirementLevel,
    StateMachine,
    StateSet,
    TierCell,
    TierRequirement,
    Transition,
)

UNIT_MACHINE = "unit"
RELEASE_O_MACHINE = "release-operational"
RELEASE_Q_MACHINE = "release-qualified"
DEVIATION_MACHINE = "deviation"

OBLIGATION_COMPLETION_RECHECK = "obligation.completion-recheck"
OBLIGATION_NEGATIVE_OUTCOME = "obligation.negative-outcome-record"
OBLIGATION_CLOSURE_RECORD = "obligation.closure-record"


def _set_status(source: str, targets: tuple[str, ...]) -> tuple[Transition, ...]:
    return tuple(
        Transition(source=source, command="set-status", target=target)
        for target in targets
    )


def _advance(source: str, targets: tuple[str, ...]) -> tuple[Transition, ...]:
    return tuple(
        Transition(source=source, command="advance", target=target)
        for target in targets
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

_UNIT = StateMachine(
    name=UNIT_MACHINE,
    states=_UNIT_STATES,
    initial="specified",
    command_transitions=(
        Transition(source="specified", command="freeze", target="acceptance-frozen"),
        *_set_status("specified", ("blocked",)),
        *_set_status("acceptance-frozen", ("blocked", "ready")),
        *_set_status("ready", ("blocked", "in-progress")),
        *_set_status("in-progress", ("blocked", "done-pending-acceptance")),
        *_set_status("blocked", ("re-verifying", "specified", "superseded")),
        *_set_status("done-pending-acceptance", ("blocked", "published")),
        *_set_status("published", ("blocked", "release-bound", "superseded")),
        *_set_status("release-bound", ("blocked", "superseded")),
        *_set_status("invalidated", ("blocked", "re-verifying", "superseded")),
        *_set_status("re-verifying", ("blocked", "published", "superseded")),
    ),
    adjudication_entries=(
        # machine.md §9 step 6: amendment impact invalidates the amended unit and
        # every published unit downstream, withdrawing release eligibility.
        AdjudicationEntry(
            trigger="amendment-impact",
            target="invalidated",
            sources=("published", "release-bound"),
        ),
    ),
)

_O_STATES = (
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
    states=_O_STATES,
    initial="prepared",
    command_transitions=(
        *_advance("prepared", ("deploying", "retired")),
        *_advance("deploying", ("retired", "rolled-back", "verifying")),
        *_advance("verifying", ("retired", "rolled-back", "succeeded")),
        *_advance("rolled-back", ("failed-unrecovered",)),
        *_advance("succeeded", ("retired",)),
    ),
    adjudication_entries=(
        AdjudicationEntry(
            trigger="amendment",
            target="invalidated",
            sources=_O_STATES,
            excluded_sources=("invalidated",),
        ),
        # Opt-in `invalidate-releases` supersession; the exclusions are 0.6's
        # supersession_no_op_release_states.
        AdjudicationEntry(
            trigger="supersession",
            target="invalidated",
            sources=_O_STATES,
            excluded_sources=(
                "retired",
                "rolled-back",
                "failed-unrecovered",
                "invalidated",
            ),
        ),
    ),
)

_Q_STATES = (
    "prepared",
    "verifying",
    "succeeded",
    "failed-unrecovered",
    "retired",
    "invalidated",
)

_RELEASE_Q = StateMachine(
    name=RELEASE_Q_MACHINE,
    states=_Q_STATES,
    initial="prepared",
    command_transitions=(
        *_advance("prepared", ("retired", "verifying")),
        *_advance("verifying", ("failed-unrecovered", "retired", "succeeded")),
        *_advance("succeeded", ("retired",)),
        *_advance("failed-unrecovered", ("retired",)),
    ),
    adjudication_entries=(
        AdjudicationEntry(
            trigger="amendment",
            target="invalidated",
            sources=_Q_STATES,
            excluded_sources=("invalidated",),
        ),
        AdjudicationEntry(
            trigger="supersession",
            target="invalidated",
            sources=_Q_STATES,
            excluded_sources=("retired", "failed-unrecovered", "invalidated"),
        ),
    ),
)

_DEVIATION = StateMachine(
    name=DEVIATION_MACHINE,
    states=("open", "amended", "rejected", "closed"),
    initial="open",
    command_transitions=(
        Transition(source="open", command="adjudicate-amend", target="amended"),
        Transition(source="open", command="adjudicate-reject", target="rejected"),
        Transition(source="amended", command="close", target="closed"),
        Transition(source="rejected", command="close", target="closed"),
    ),
)

_OBLIGATIONS = (
    Obligation(
        obligation_id=OBLIGATION_COMPLETION_RECHECK,
        scope=ObligationScope.CLAIM,
        description=(
            "Re-check included unit-done, integrity, criteria digest, and roster "
            "bindings on every transition into a completion claim."
        ),
    ),
    Obligation(
        obligation_id=OBLIGATION_NEGATIVE_OUTCOME,
        scope=ObligationScope.CLAIM,
        description=(
            "Retain the predeclared criteria and record outcome_deviations; "
            "no roster-health obligation."
        ),
    ),
    Obligation(
        obligation_id=OBLIGATION_CLOSURE_RECORD,
        scope=ObligationScope.CLAIM,
        description=(
            "Record the trigger, required trigger_kind, frozen-roster status "
            "snapshot, no_completion_claim: true, and observations; record "
            "incompleteness is the only permitted refusal, never roster health."
        ),
    ),
)

_CLAIM_RULES = (
    ClaimRule(
        machine=RELEASE_O_MACHINE,
        state="succeeded",
        shape=ClaimShape.COMPLETION_CLAIM,
        obligations=(OBLIGATION_COMPLETION_RECHECK,),
    ),
    ClaimRule(
        machine=RELEASE_O_MACHINE,
        state="rolled-back",
        shape=ClaimShape.PROTECTIVE_CLOSURE,
        obligations=(OBLIGATION_CLOSURE_RECORD,),
    ),
    ClaimRule(
        machine=RELEASE_O_MACHINE,
        state="failed-unrecovered",
        shape=ClaimShape.PROTECTIVE_CLOSURE,
        obligations=(OBLIGATION_CLOSURE_RECORD,),
    ),
    ClaimRule(
        machine=RELEASE_O_MACHINE,
        state="retired",
        shape=ClaimShape.PROTECTIVE_CLOSURE,
        obligations=(OBLIGATION_CLOSURE_RECORD,),
    ),
    ClaimRule(
        machine=RELEASE_O_MACHINE,
        state="invalidated",
        shape=ClaimShape.PROTECTIVE_CLOSURE,
        obligations=(OBLIGATION_CLOSURE_RECORD,),
    ),
    ClaimRule(
        machine=RELEASE_Q_MACHINE,
        state="succeeded",
        shape=ClaimShape.COMPLETION_CLAIM,
        obligations=(OBLIGATION_COMPLETION_RECHECK,),
    ),
    ClaimRule(
        machine=RELEASE_Q_MACHINE,
        state="failed-unrecovered",
        shape=ClaimShape.NEGATIVE_EVALUATION_OUTCOME,
        obligations=(OBLIGATION_NEGATIVE_OUTCOME,),
    ),
    ClaimRule(
        machine=RELEASE_Q_MACHINE,
        state="retired",
        shape=ClaimShape.PROTECTIVE_CLOSURE,
        obligations=(OBLIGATION_CLOSURE_RECORD,),
    ),
    ClaimRule(
        machine=RELEASE_Q_MACHINE,
        state="invalidated",
        shape=ClaimShape.PROTECTIVE_CLOSURE,
        obligations=(OBLIGATION_CLOSURE_RECORD,),
    ),
)

_DONE_STATES = ("published", "release-bound")

_STATE_SETS = (
    StateSet(name="unit.satisfied-dependency", machine=UNIT_MACHINE, states=_DONE_STATES),
    StateSet(name="unit.done", machine=UNIT_MACHINE, states=_DONE_STATES),
    StateSet(name="unit.published", machine=UNIT_MACHINE, states=_DONE_STATES),
    StateSet(
        name="unit.settled",
        machine=UNIT_MACHINE,
        states=("published", "release-bound", "superseded"),
    ),
    StateSet(
        name="unit.marker",
        machine=UNIT_MACHINE,
        states=("invalidated", "re-verifying", "superseded"),
    ),
    StateSet(
        name="unit.frozen",
        machine=UNIT_MACHINE,
        states=(
            "acceptance-frozen",
            "ready",
            "in-progress",
            "done-pending-acceptance",
            "published",
            "release-bound",
            "invalidated",
            "re-verifying",
            "superseded",
        ),
    ),
    StateSet(
        name="release.active.operational",
        machine=RELEASE_O_MACHINE,
        states=("prepared", "deploying", "verifying"),
    ),
    StateSet(
        name="release.active.qualified",
        machine=RELEASE_Q_MACHINE,
        states=("prepared", "verifying"),
    ),
    StateSet(
        name="release.model-terminal.operational",
        machine=RELEASE_O_MACHINE,
        states=("succeeded", "failed-unrecovered", "retired", "invalidated"),
    ),
    StateSet(
        name="release.model-terminal.qualified",
        machine=RELEASE_Q_MACHINE,
        states=("succeeded", "failed-unrecovered", "retired", "invalidated"),
    ),
    StateSet(
        name="release.supersession-no-op.operational",
        machine=RELEASE_O_MACHINE,
        states=("retired", "rolled-back", "failed-unrecovered", "invalidated"),
    ),
    StateSet(
        name="release.supersession-no-op.qualified",
        machine=RELEASE_Q_MACHINE,
        states=("retired", "failed-unrecovered", "invalidated"),
    ),
)

_VOCABULARIES = (
    ClosedVocabulary(
        name="deviation-types",
        values=("pack-defect", "contract-conflict", "plan-gap", "capability-request"),
    ),
    ClosedVocabulary(
        name="supersession-policies",
        values=("retain-releases", "invalidate-releases"),
    ),
    ClosedVocabulary(
        name="closure-trigger-kinds",
        values=("amendment", "operator", "supersession"),
    ),
)


def _required(scope: str = "", detail: str = "") -> TierCell:
    return TierCell(level=RequirementLevel.REQUIRED, scope=scope, detail=detail)


def _risk(scope: str = "", detail: str = "") -> TierCell:
    return TierCell(level=RequirementLevel.RISK_TRIGGERED, scope=scope, detail=detail)


_WAIVED = TierCell(level=RequirementLevel.WAIVED)

_TIER_REQUIREMENTS = (
    TierRequirement(
        control_id="typed-contracts",
        description="Typed contracts, declared classes, purity checks",
        lightweight=_required(),
        standard=_required(),
        high=_required(),
    ),
    TierRequirement(
        control_id="acceptance-frozen-before-implementation",
        description="Acceptance frozen before implementation",
        lightweight=_required(scope="critical-behavior"),
        standard=_required(),
        high=_required(detail="independently reviewed"),
    ),
    TierRequirement(
        control_id="independence-minimum",
        description="Independence (minimum model)",
        lightweight=_required(detail="Model 3"),
        standard=_required(
            scope="critical-behavior",
            detail="Model 1/2 for critical (per-unit override in ledger)",
        ),
        high=_required(detail="Model 1/2 throughout, named human adjudicator"),
    ),
    TierRequirement(
        control_id="harness-recorded-red-green",
        description="Harness-recorded red→green→refactor",
        lightweight=_required(detail="chain optional (ordering only, no causal claim)"),
        standard=_required(detail="chained, CI-signed anchor"),
        high=_required(detail="chained, external append-only anchor"),
    ),
    TierRequirement(
        control_id="real-boundary-tests",
        description="Real-boundary tests (observations and actions)",
        lightweight=_required(),
        standard=_required(),
        high=_required(detail="plus fault injection"),
    ),
    TierRequirement(
        control_id="replay-manifests-content-addressing",
        description="Replay, manifests, content addressing",
        lightweight=_required(scope="transforms-only"),
        standard=_required(),
        high=_required(),
    ),
    TierRequirement(
        control_id="mutation-testing",
        description="Mutation testing",
        lightweight=_WAIVED,
        standard=_risk(scope="critical-logic"),
        high=_required(detail="all required mutants"),
    ),
    TierRequirement(
        control_id="approval-gated-actions",
        description="Approval-gated actions",
        lightweight=_WAIVED,
        standard=_risk(detail="ledger approval_required"),
        high=_required(),
    ),
    TierRequirement(
        control_id="security-evidence",
        description="Security evidence",
        lightweight=_WAIVED,
        standard=_risk(detail="when triggered, the high-tier blocking outcomes apply"),
        high=_required(
            detail=(
                "unresolved high-severity threats, leaked secrets, unbound "
                "dependencies, or missing authorization tests block release"
            )
        ),
    ),
    TierRequirement(
        control_id="system-map-gates",
        description="System map: both gates, full content, owned verifiers",
        lightweight=_required(),
        standard=_required(),
        high=_required(),
    ),
    TierRequirement(
        control_id="release-map-in-bundle",
        description="Release map content-addressed in bundle",
        lightweight=_required(),
        standard=_required(),
        high=_required(),
    ),
    TierRequirement(
        control_id="map-delivered-externally",
        description="Map delivered externally",
        lightweight=_required(detail="per archetype"),
        standard=_required(detail="per archetype"),
        high=_required(detail="per archetype"),
    ),
    TierRequirement(
        control_id="archetype-q-machinery",
        description=(
            "Archetype-Q machinery (sealed procedure, exposure controls, held-out "
            "case variants)"
        ),
        lightweight=_required(detail="bound to archetype, never tier"),
        standard=_required(detail="bound to archetype, never tier"),
        high=_required(detail="bound to archetype, never tier"),
    ),
)

VOUCH_06 = PolicyIr(
    identity=IrIdentity(
        name="vouch-0.6-lifecycle",
        version="0.6.2/kit-5",
        source=(
            "vouch/verification-kit/policy/lifecycle-policy.json (version 1); "
            "vouch/editions/machine.md §9, §11, §13, §14"
        ),
    ),
    machines=(_UNIT, _RELEASE_O, _RELEASE_Q, _DEVIATION),
    claim_rules=_CLAIM_RULES,
    obligations=_OBLIGATIONS,
    tier_requirements=_TIER_REQUIREMENTS,
    state_sets=_STATE_SETS,
    vocabularies=_VOCABULARIES,
)
