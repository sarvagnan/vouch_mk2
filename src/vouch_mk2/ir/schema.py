"""Policy IR schema — Phase 0.

The Policy IR is the single declarative artifact from which every mechanical view
(the ``decide`` tables, gates, checks, projections, the model checker's transition
relation) is generated. This module defines the schema — the closed, frozen types an
IR document instantiates — and pure structural validation.

It holds exactly what IMPLEMENTATION-PLAN.md §4 Phase 0 lists: lifecycle state
machines, claim taxonomy, applicability, tier requirements, obligation registry
format, and version identity. Invariants, witnesses, and prose rationale live
deliberately outside it (plan §6 risk 3).

Purity: no I/O and no I/O-capable imports — enforced by the import-linter contract
in pyproject.toml, not convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(Enum):
    """The only verdicts a check may return. There is no silent SKIP."""

    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKED = "not-checked"
    NOT_APPLICABLE = "not-applicable"


class ObligationScope(Enum):
    """The closed set of places where an obligation may be evaluated."""

    TRANSITION_GATE = "transition-gate"
    CLAIM = "claim"
    STANDING_CHECK = "standing-check"
    ARTIFACT_CHECK = "artifact-check"
    ORGANIZATIONAL = "organizational"


class ClaimShape(Enum):
    """What entering a claim-bearing state asserts (plan §4 Phase 4)."""

    COMPLETION_CLAIM = "completion-claim"
    NEGATIVE_EVALUATION_OUTCOME = "negative-evaluation-outcome"
    PROTECTIVE_CLOSURE = "protective-closure"


class Tier(Enum):
    LIGHTWEIGHT = "lightweight"
    STANDARD = "standard"
    HIGH = "high"


class RequirementLevel(Enum):
    REQUIRED = "required"
    RISK_TRIGGERED = "risk-triggered"
    WAIVED = "waived"


class ConditionKind(Enum):
    """Closed set of predicates an applicability rule may cite.

    ``NOT_APPLICABLE`` verdicts must cite an IR applicability rule; these are the
    only conditions such a rule may express.
    """

    SUPERSESSION_POLICY_IS = "supersession-policy-is"
    ARCHETYPE_IS = "archetype-is"
    TIER_AT_LEAST = "tier-at-least"
    ANCHOR_MODE_IS = "anchor-mode-is"


@dataclass(frozen=True, slots=True)
class Applicability:
    kind: ConditionKind
    value: str


@dataclass(frozen=True, slots=True)
class Transition:
    """One licensed edge: ``source`` may move to ``target`` under ``command``.

    ``gates`` are obligation ids evaluated inside the transition — a transition and
    its gate are one code path (plan §4 Phase 1).
    """

    source: str
    command: str
    target: str
    gates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdjudicationEntry:
    """An out-of-band entry edge driven by human adjudication, not a command.

    Example: amendment adjudication sets a release to ``invalidated`` from any state.
    ``sources`` lists the states it may fire from; ``excluded_sources`` carves
    exceptions out of that list (states where the entry is a no-op).
    """

    trigger: str
    target: str
    sources: tuple[str, ...]
    excluded_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StateMachine:
    name: str
    states: tuple[str, ...]
    initial: str
    command_transitions: tuple[Transition, ...]
    adjudication_entries: tuple[AdjudicationEntry, ...] = ()

    def command_targets(self, source: str) -> tuple[str, ...]:
        return tuple(
            sorted({t.target for t in self.command_transitions if t.source == source})
        )

    def command_predecessors(self, target: str) -> tuple[str, ...]:
        return tuple(
            sorted({t.source for t in self.command_transitions if t.target == target})
        )

    def command_terminal_states(self) -> tuple[str, ...]:
        """States with no outgoing command transition (adjudication entries aside)."""
        sources = {t.source for t in self.command_transitions}
        return tuple(s for s in self.states if s not in sources)


@dataclass(frozen=True, slots=True)
class ClaimRule:
    """Entering ``state`` on ``machine`` asserts ``shape`` and owes ``obligations``."""

    machine: str
    state: str
    shape: ClaimShape
    obligations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Obligation:
    """Registry entry: every registered id must map to an evaluator that ran.

    Empty ``applicability`` means always applicable; otherwise all conditions must
    hold, and a run where one fails reports ``NOT_APPLICABLE`` citing this rule.
    """

    obligation_id: str
    scope: ObligationScope
    description: str
    applicability: tuple[Applicability, ...] = ()


@dataclass(frozen=True, slots=True)
class TierCell:
    """One conformance-matrix cell. ``scope`` narrows the control ('transforms only',
    'critical behavior'); empty scope means full scope. ``detail`` carries a value the
    level alone cannot ('Model 3', 'chained, CI-signed anchor')."""

    level: RequirementLevel
    scope: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TierRequirement:
    control_id: str
    description: str
    lightweight: TierCell
    standard: TierCell
    high: TierCell

    def cell(self, tier: Tier) -> TierCell:
        match tier:
            case Tier.LIGHTWEIGHT:
                return self.lightweight
            case Tier.STANDARD:
                return self.standard
            case Tier.HIGH:
                return self.high


@dataclass(frozen=True, slots=True)
class StateSet:
    """A named classification of states within one machine (e.g. 'frozen', 'done')."""

    name: str
    machine: str
    states: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClosedVocabulary:
    """A closed value list that is not a state machine (deviation types, policies)."""

    name: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IrIdentity:
    name: str
    version: str
    source: str


@dataclass(frozen=True, slots=True)
class PolicyIr:
    identity: IrIdentity
    machines: tuple[StateMachine, ...]
    claim_rules: tuple[ClaimRule, ...] = ()
    obligations: tuple[Obligation, ...] = ()
    tier_requirements: tuple[TierRequirement, ...] = ()
    state_sets: tuple[StateSet, ...] = ()
    vocabularies: tuple[ClosedVocabulary, ...] = ()

    def machine(self, name: str) -> StateMachine:
        for m in self.machines:
            if m.name == name:
                return m
        raise KeyError(name)

    def state_set(self, name: str) -> StateSet:
        for s in self.state_sets:
            if s.name == name:
                return s
        raise KeyError(name)

    def vocabulary(self, name: str) -> ClosedVocabulary:
        for v in self.vocabularies:
            if v.name == name:
                return v
        raise KeyError(name)

    def claim_rule(self, machine: str, state: str) -> ClaimRule | None:
        for r in self.claim_rules:
            if r.machine == machine and r.state == state:
                return r
        return None


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class _Collector:
    found: list[Violation] = field(default_factory=list)

    def add(self, code: str, detail: str) -> None:
        self.found.append(Violation(code=code, detail=detail))


def _validate_machine(machine: StateMachine, out: _Collector) -> None:
    prefix = f"machine {machine.name!r}"
    if len(set(machine.states)) != len(machine.states):
        out.add("machine.duplicate-state", f"{prefix}: duplicate state names")
    if machine.initial not in machine.states:
        out.add("machine.unknown-initial", f"{prefix}: initial {machine.initial!r}")
    states = set(machine.states)
    seen_edges: set[tuple[str, str, str]] = set()
    command_targets: dict[tuple[str, str], str] = {}
    for t in machine.command_transitions:
        edge = (t.source, t.command, t.target)
        if edge in seen_edges:
            out.add("transition.duplicate", f"{prefix}: {edge!r}")
        seen_edges.add(edge)
        command_key = (t.source, t.command)
        previous_target = command_targets.get(command_key)
        if previous_target is not None and previous_target != t.target:
            out.add(
                "transition.ambiguous-command",
                f"{prefix}: source/command {command_key!r} targets both "
                f"{previous_target!r} and {t.target!r}",
            )
        command_targets[command_key] = t.target
        if not t.command:
            out.add("transition.empty-command", f"{prefix}: {t.source!r}->{t.target!r}")
        for endpoint in (t.source, t.target):
            if endpoint not in states:
                out.add("transition.unknown-state", f"{prefix}: {endpoint!r}")
    for entry in machine.adjudication_entries:
        if entry.target not in states:
            out.add("adjudication.unknown-state", f"{prefix}: {entry.target!r}")
        if not entry.sources:
            out.add(
                "adjudication.empty-sources",
                f"{prefix}: {entry.trigger!r} lists no source states",
            )
        for s in entry.sources + entry.excluded_sources:
            if s not in states:
                out.add("adjudication.unknown-state", f"{prefix}: {s!r}")
        for s in entry.excluded_sources:
            if s not in entry.sources:
                out.add(
                    "adjudication.exclusion-not-in-sources",
                    f"{prefix}: {entry.trigger!r} excludes non-source {s!r}",
                )


def validate(ir: PolicyIr) -> tuple[Violation, ...]:
    """Structural closed-world validation. Pure; behavioral checks belong to decide()."""
    out = _Collector()
    machine_names = [m.name for m in ir.machines]
    if len(set(machine_names)) != len(machine_names):
        out.add("ir.duplicate-machine", "duplicate machine names")
    machines = {m.name: m for m in ir.machines}
    for machine in ir.machines:
        _validate_machine(machine, out)

    obligation_ids = [o.obligation_id for o in ir.obligations]
    if len(set(obligation_ids)) != len(obligation_ids):
        out.add("obligation.duplicate-id", "duplicate obligation ids")
    known_obligations = set(obligation_ids)
    obligations_by_id = {o.obligation_id: o for o in ir.obligations}

    seen_claims: set[tuple[str, str]] = set()
    for rule in ir.claim_rules:
        key = (rule.machine, rule.state)
        if key in seen_claims:
            out.add("claim.duplicate", f"{key!r}")
        seen_claims.add(key)
        machine = machines.get(rule.machine)
        if machine is None:
            out.add("claim.unknown-machine", f"{rule.machine!r}")
        elif rule.state not in machine.states:
            out.add("claim.unknown-state", f"{rule.machine!r}: {rule.state!r}")
        for obligation_id in rule.obligations:
            if obligation_id not in known_obligations:
                out.add("claim.unknown-obligation", f"{key!r}: {obligation_id!r}")

    for machine in ir.machines:
        for t in machine.command_transitions:
            for gate in t.gates:
                if gate not in known_obligations:
                    out.add(
                        "transition.unknown-gate",
                        f"machine {machine.name!r}: {t.source!r}->{t.target!r}: {gate!r}",
                    )
                    continue
                obligation = obligations_by_id[gate]
                if obligation.scope in {
                    ObligationScope.STANDING_CHECK,
                    ObligationScope.ARTIFACT_CHECK,
                    ObligationScope.ORGANIZATIONAL,
                }:
                    out.add(
                        "obligation.scope-misuse",
                        f"obligation {gate!r} with scope {obligation.scope.value!r} "
                        f"was found in machine {machine.name!r} transition "
                        f"{t.source!r}->{t.target!r}",
                    )

    set_names = [s.name for s in ir.state_sets]
    if len(set(set_names)) != len(set_names):
        out.add("state-set.duplicate-name", "duplicate state-set names")
    for state_set in ir.state_sets:
        machine = machines.get(state_set.machine)
        if machine is None:
            out.add("state-set.unknown-machine", f"{state_set.name!r}")
            continue
        for state in state_set.states:
            if state not in machine.states:
                out.add("state-set.unknown-state", f"{state_set.name!r}: {state!r}")

    vocab_names = [v.name for v in ir.vocabularies]
    if len(set(vocab_names)) != len(vocab_names):
        out.add("vocabulary.duplicate-name", "duplicate vocabulary names")
    for vocab in ir.vocabularies:
        if len(set(vocab.values)) != len(vocab.values):
            out.add("vocabulary.duplicate-value", f"{vocab.name!r}")

    control_ids = [t.control_id for t in ir.tier_requirements]
    if len(set(control_ids)) != len(control_ids):
        out.add("tier.duplicate-control", "duplicate control ids")

    return tuple(out.found)
