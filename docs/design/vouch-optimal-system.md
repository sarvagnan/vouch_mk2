# Optimal Vouch system

**Status:** accepted target design; implementation status is identified per capability  
**Date:** 21 August 2026  
**Companion diagram:** [vouch-system-diagram.html](./vouch-system-diagram.html)  
**Preserved current-state diagram:** [vouch-mk2-system-diagram.html](./vouch-mk2-system-diagram.html)

## 1. Purpose

Vouch should make agent-assisted software delivery safer without asking a team to infer its own
adoption method. Mk2 established a strong assurance kernel: typed lifecycle decisions, explicit
gates, append-only evidence, content-addressed artifacts, formal checking, truthful claim limits,
and a narrow delivery seam. Its weakness is that technical installation and strong internals do
not by themselves create sustained governed behavior.

The optimal system therefore has two equally deliberate parts:

1. an **assurance kernel** that decides what can be claimed about governed delivery; and
2. an **adoption module** that helps a project assess fit, integrate, pilot, prove value, expand,
   sustain use, recover, pause, or exit.

The adoption module cannot reinterpret or strengthen an assurance-kernel verdict. The two modules
compose at the project delivery gate while retaining separate records, authorities, and claim
boundaries.

## 2. Status vocabulary

Every system description and interface must use these labels consistently.

| Label | Meaning |
|---|---|
| **Implemented** | Present in the current mk2 code or generated artifacts. |
| **Dogfooded** | Used by the mk2 repository itself; this proves operability in one controlled project, not general adoption. |
| **Target** | Accepted optimal-system design that is not yet implemented. |
| **Adopter-owned** | Must be supplied, configured, or decided by the adopting project or organization. |
| **External** | Depends on an authority or fact Vouch cannot mechanically establish. |
| **External proof pending** | Requires a real independent adopter or delivery environment before the claim can be promoted. |

No target capability may be described as current, and successful initialization may never be
described as adoption.

## 3. System principles

- **One assurance authority.** Policy IR and the pure decision core own lifecycle gates,
  obligations, assurance tiers, and verdict composition.
- **Separate adoption authority.** Adoption state, accountable roles, governed scope, pilot
  outcomes, health, exceptions, support, and exit live in a separate adoption record.
- **Claims never strengthen while crossing modules.** Adoption can block a composed project gate;
  it cannot turn kernel `FAIL` or required `NOT_CHECKED` into `PASS`.
- **Repository-local operation.** Vouch has no telemetry, hosted control plane, or Vouch-initiated
  network transmission. Data leaves only through an explicit user-controlled action or the
  adopter's chosen delivery system.
- **Truthful uncertainty.** Incomplete assessment, missing anchors, external identity, chronology,
  and organizational controls remain explicit rather than becoming green defaults.
- **Fail-closed enforcement.** Unknown scope, missing baselines, unbound inputs, outages, or
  invalid reuse widen verification or block progression.
- **Deep modules, thin adapters.** The assurance kernel and adoption module hide complexity behind
  small interfaces. Delivery adapters translate environments without redefining policy.
- **Evolution, not rewrite.** Preserve the proven mk2 integrity model and introduce target
  capabilities through explicit upgrades with rollback and no invented history.

## 4. Authority architecture

```text
Adopter / agent / delivery authority
                 │
        ┌────────┴────────┐
        │ project actions │
        └────────┬────────┘
                 │
       ┌─────────▼──────────┐          ┌──────────────────────┐
       │   Adoption module   │◄────────►│  Adoption record     │
       │ assess · begin      │          │ stage · health       │
       │ next · advance      │          │ roles · scope        │
       │ status · pause/exit │          │ outcomes · exceptions│
       └─────────┬──────────┘          └──────────────────────┘
                 │ kernel references only
       ┌─────────▼──────────┐          ┌──────────────────────┐
       │  Assurance kernel  │◄────────►│ Assurance record     │
       │ decide · apply     │          │ events · evidence    │
       │ plan · verify      │          │ capsules · checkpoints│
       │ compose · report   │          │ packs · certificates │
       └─────────┬──────────┘          └──────────────────────┘
                 │
       ┌─────────▼──────────┐
       │ Composed project   │
       │ delivery gate      │
       └─────────┬──────────┘
                 │
       Delivery adapter / forge / deployment
```

### 4.1 Assurance kernel

The kernel owns:

- typed commands, accepted events, and structured violations;
- state-machine transitions and their gates;
- evidence, acceptance-pack, dependency, release, and exception integrity;
- assurance-tier requirements and core obligations;
- scope impact planning, verification-capsule validity, checkpoint validity, and verdict
  composition;
- formal-policy identity and explicit claim boundaries.

The target kernel interface remains small: lifecycle decisions, verification planning, receipt
recording, composition, full verification, historical scrubbing, status, and next actions. The
implementation may contain internal seams, but transports never learn enough policy to recreate a
second assurance engine.

### 4.2 Adoption module

The adoption module owns:

- project assessment and readiness;
- adoption stage and adoption health;
- accountable roles and separation requirements;
- candidate maps, confirmed scope, pilot hypotheses, scorecards, and stop rules;
- integration probes, rollout cohorts, support, interruptions, recovery, pause, and exit;
- kernel references that bind an adoption fact to the exact assurance revision supporting it.

Its external interface is deliberately narrow:

```text
assess → begin → next → advance → status
                         ├──────→ pause → resume
                         └──────→ exit
```

### 4.3 Delivery adapters

A delivery adapter:

- obtains an exact base and candidate identity from a forge or local repository;
- supplies a normalized change manifest;
- executes immutable verification-plan entries locally or through the adopter's delivery system;
- returns plan-bound execution receipts;
- renders local annotations or prepares a constrained publication candidate.

It cannot declare a scope unaffected, change assurance policy, or reinterpret a verdict. GitHub is
the first real adapter; another adapter is added only when a second delivery environment is being
implemented, not to preserve a speculative plug-in surface.

## 5. Records and composition

The assurance record and adoption record are repository-local, append-only histories with separate
namespaces and integrity roots. A cross-record transition does not pretend to be an atomic database
transaction. Instead, the adoption event stores an immutable **kernel reference** to the exact
kernel revision and artifact digests on which it depends. Missing or mismatched references fail
closed.

The composed project gate combines three independent inputs:

1. the assurance-kernel verdict;
2. adoption health and required adoption-stage obligations; and
3. named project- or organization-selected additional checks.

Core integrity, history, honesty, and claim-limit obligations cannot be disabled. Additional checks
may strengthen local governance but never weaken a core obligation. A failed kernel verdict remains
failed; an adoption interruption or required unchecked control blocks hard-gated progression.

## 6. Adoption operating system

### 6.1 Lifecycle

```text
candidate → ready → integrating → piloting → proven → adopted → expanding
    │          │           │           │         │          │
    └──────────┴───────────┴───────────┴─────────┴──────────┴──→ paused / exited
```

- **Candidate:** fit is being assessed within declared count, byte, depth, and time budgets.
- **Ready:** pilot, authorities, assurance tier, constraints, success criteria, and exit criteria
  are sufficiently settled.
- **Integrating:** repository, acceptance, delivery, and enforcement connections are being made and
  proven with executable success and refusal probes.
- **Piloting:** one bounded real product change uses the actual hard-gated delivery path.
- **Proven:** that pilot completed and satisfied its predeclared scorecard.
- **Adopted:** a later independent governed change and observation window demonstrate sustained use.
- **Expanding:** governed scope or rollout cohorts grow through an explicit ratchet.
- **Paused / exited:** progression stops without deleting history or silently preserving stale
  current claims.

Adoption stage, assurance tier, and adoption health are orthogonal. A project can be early-stage at
high assurance, adopted at standard assurance, or historically adopted but currently interrupted.

### 6.2 Roles

| Role | Accountable for |
|---|---|
| **Adoption owner** | Fit, rollout burden, sustained use, scorecard, pause, and exit. |
| **Acceptance owner** | Required behavior, acceptance evidence, and amendments. |
| **Implementer** | Product changes inside governed scope; may be a person or agent. |
| **Delivery authority** | Delivery-workflow enforcement and release authority. |
| **Support owner** | Adoption friction and defect escalation without changing policy or state silently. |

At standard and high assurance, the implementer and acceptance owner are separate for the claim
being made. Identity is recorded unless an external authority authenticates it; Vouch must not
claim more.

### 6.3 Assessment and readiness

Assessment is bounded and produces one of three honest outcomes: a candidate map, a refusal, or an
incomplete assessment. Automation may propose repository structure and potential scopes, but a
human confirms meaning, authority, exclusions, and the pilot. Budget exhaustion is incomplete, not
partial success.

A ready project records:

- the trust gap Vouch is intended to address;
- governance root and initial governed scope;
- adoption, acceptance, implementation, delivery, and support responsibilities;
- assurance tier and enforcement path;
- pilot hypothesis, scorecard, stop rule, and exit criteria;
- integration probes and recovery plan;
- whether the project enters through the greenfield or brownfield path.

## 7. Greenfield and brownfield paths

### 7.1 Greenfield fast lane

A greenfield project is defined by behavior, not repository age: product behavior begins after
Vouch governance is established.

1. Establish the governance root, roles, assurance tier, delivery authority, and scope manifest.
2. Create minimal bootstrap infrastructure under an explicitly separate bootstrap claim.
3. Keep disposable discovery in an exploration area that cannot silently become governed product
   behavior.
4. Establish the verified baseline before the first governed product behavior.
5. Deliver the first real behavior through the normal acceptance, evidence, and delivery path.

If a prototype is retained as product behavior before governance, that scope becomes brownfield
and must be characterized; it cannot be relabeled as built-under-Vouch.

### 7.2 Brownfield-first path

1. Bound the legacy baseline and make ungoverned scope visible.
2. Generate a candidate map within declared assessment budgets.
3. Have accountable humans confirm scope meaning, exclusions, dependencies, and authorities.
4. Characterize pre-Vouch behavior without making causal built-under-Vouch claims.
5. Integrate diagnostic checks, then validation, then a bounded hard-gated pilot.
6. Ratchet governed scope only after the pilot proves value at acceptable cost.
7. Reconcile exceptions and interruptions before current claims are restored.

Vouch never requires an all-at-once retrofit. Whole-project assurance is available only when the
declared governed scope genuinely covers all relevant behavior.

## 8. Scope model

The human-confirmed **scope manifest** assigns every relevant path exactly one primary
classification:

- governed leaf scope;
- shared scope;
- integration scope;
- generated artifact belonging to one scope;
- exploration area; or
- explicit exclusion that cannot feed governed behavior.

A scope may own many paths; one path does not have multiple owners. Cross-scope impact is expressed
through dependency contracts, not overlapping ownership. Path/file ownership is the portable core
granularity. If independently governed behavior shares a file, the file belongs to a containing
scope or the project splits it; the kernel does not depend on language-specific symbol parsing.

Pure versus effectful implementation does not change ownership. A project may bind mechanically
produced effect information into dependency contracts, but Vouch does not infer purity. Effectful
behavior that crosses scopes or external seams belongs in explicit integration scopes.

Scope-manifest changes are governed. Impact is calculated under both the old and proposed mapping,
then unioned. Moves invalidate at least the source and destination scopes. Unclassified or
ambiguous paths are global invalidators.

## 9. Sound incremental verification

### 9.1 Current mk2 gap

Current mk2 lifecycle mutations and evidence capture can target one named unit, but `check` and
`check --ci` are store-wide. They read the complete snapshot, run all evaluators, walk every
evidence head, replay the event log, and verify every applicable bound pack. The generated workflow
also performs full bounded formal exploration on each validation. Diff-based unit scoping was
explicitly deferred in [D-18](../../DECISIONS.md#d-18--ci-policy-tier-granularity-and-ci-anchoring-adoption-reviews-batch-b).

That design is coherent but is not the target behavior for large projects.

### 9.2 Ordinary change flow

```text
exact verified baseline
          +
normalized change manifest
          ↓
scope ownership + dependency contracts
          ↓
affected closure
          ↓
immutable verification plan
          ↓
parallel local / forge execution
          ↓
plan-bound execution receipts
          ↓
fresh capsules + reusable valid capsules
          ↓
global composition checks
          ↓
PASS (incremental composition) / FAIL / NOT_CHECKED
```

The **affected closure** contains directly changed scopes plus reverse dependents, shared scopes,
and integration scopes whose required inputs or claims may have changed.

- A mechanically bound dependency contract that remains unchanged permits downstream capsule
  reuse.
- An opaque dependency propagates every relevant producer change.
- A volatile input forces fresh execution whenever its scope participates.
- An explicitly pinned dependency from another governance root changes the consumer only when the
  published dependency reference changes. Unpinned runtime dependencies are opaque or volatile.

### 9.3 Verification capsule

A capsule is a reusable per-scope result bound to:

- owned source and generated-input identities;
- acceptance pack and test-set identity;
- evidence head and required attempt history;
- dependency contracts and published dependency references;
- exact verification-plan entry and command;
- runner, toolchain, and relevant environment identity;
- policy, assurance tier, schema, formal identity, and pinned Vouch artifact;
- result and claim provenance.

Secrets are never stored or hashed. A runner may attest a non-secret secret-version or issuer
handle; otherwise that input is volatile. Effectful scopes are reusable only when relevant effect
inputs are fixed, simulated, snapshotted, or mechanically version-bound.

### 9.4 Three verification modes

| Mode | Purpose | Scope |
|---|---|---|
| **Incremental composition** | Ordinary changes and eligible releases | Freshly verify affected closure; reuse only exact valid capsules; check global composition. |
| **Full verification** | Re-evaluate current behavior | Execute every active governed scope and current-state global obligation without change-based exclusion. |
| **Historical scrub** | Re-establish history trust | Replay and verify authoritative event and evidence history from genesis to head. |

A release requires complete composition across its exact roster, not necessarily full verification.
Full verification is triggered by missing trust, global invalidators, recovery, incompatible
upgrades, explicit audit, or assurance-policy cadence. A longer cadence and history-specific
events trigger a historical scrub. Cadences are measured in governed changes or releases rather
than relying solely on wall-clock chronology.

### 9.5 Checkpoints and soundness

A verified checkpoint binds the exact history roots, derived state, schema, policy, verifier, and
hard-gated repository revision. A later check may validate and replay only the suffix if the
checkpoint ancestry and verifier identity still match. Missing or corrupt ancestry requires a
historical scrub.

The non-negotiable invariant is **incremental soundness**:

> For the same supported project state and change, incremental composition must never pass when
> full verification would fail.

The bounded formal model and differential/property tests must cover scope exclusion, contract
propagation, capsule reuse, checkpoint suffixes, invalidators, branch merges, and recovery. A
soundness mismatch blocks promotion of incremental verification.

### 9.6 Branches, parallelism, and retries

Branches start from an exact common verified ancestor. A merge combines change manifests,
evaluates the merged tree, recomputes the affected closure, and reuses only identities that match
that result; it never unions green outcomes blindly.

The kernel emits an immutable verification plan. Local and delivery runner adapters can execute
independent entries concurrently. Every receipt is append-only. Successful entries from a partial
plan may be reused only while the full plan, tree, inputs, environment, and policy remain identical.
Retries are explicit and bounded; failure followed by success remains visible. A replacement
capsule appears only after every required entry for that scope passes.

### 9.7 Bootstrap and migration

- **Greenfield:** confirm scope, perform initial full verification and historical scrub, then create
  the first reusable checkpoint before governed product behavior.
- **Existing mk2 store:** remain full-only after upgrade. Do not synthesize retroactive capsules.
  Incrementality begins only after human scope confirmation and explicit baseline establishment.
- **Always:** retain force-full operation and explicit rollback. Old capsules remain historical and
  are never reinterpreted under a new schema or verifier.

Performance acceptance is based on scaling shape: with a fixed affected closure, ordinary
verification must not grow linearly with total project units or history. Phase timings and budgets
remain local. Budget exhaustion produces incomplete/`NOT_CHECKED`, never a reduced green result.

## 10. Assurance, release, and enforcement

The kernel continues to preserve mk2's unit and release integrity principles: frozen acceptance,
explicit evidence, dependency closure, deviations, release rosters, protective rollback, and
truthful residual claims. The target adds scope-aware planning and evidence reuse; it does not
weaken transition gates.

The composed hard gate is:

```text
project-owned tests and checks
        +
assurance-kernel composition
        +
required adoption health
        +
protected delivery dependency
        ↓
merge / release / deployment authority
```

Forge rules, branch protection, authenticated identity, deployment authority, workflow protection,
and external services remain adopter-owned or external. If the delivery system is unavailable,
local work and evidence may continue, but hard-gated progression cannot silently become advisory.
A complete, expiring governance exception may authorize bounded interruption and always carries a
reconciliation obligation.

## 11. Local-only operation and support

Vouch runs through local CLI, CI invocations, deterministic reports, and an optional local
dashboard. It has no daemon and no telemetry of any kind.

- A **local notification report** lists blockers, drift, expiring exceptions, and next actions for
  user-owned scripts or CI annotations.
- A **support bundle** is secret-safe, user-generated, and never authoritative over project state.
- A **feedback report** is generated and reviewed locally; Vouch never sends it.
- A **portfolio export** is an explicit, minimal, digest-bound project summary.
- A **portfolio view** imports those exports locally and remains read-only; it is never a control
  plane over contributing projects.

## 12. Recovery, upgrades, and exit

- Record events before derived views; keep recovery fail-closed.
- Merge adoption histories semantically rather than concatenating records.
- Bind every recovery to an exact prior valid history and identify the lost or untrusted interval.
- Stage upgrades, validate both kernel and adoption migrations, and retain explicit rollback.
- Preserve referenced adoption and assurance history after pause or exit.
- Garbage-collect only proven-unreferenced immutable objects and recreatable or user-created
  non-authoritative artifacts.
- An exited project begins a linked new adoption episode if it later returns.

## 13. Claim boundary

Vouch can establish repository-local structural and execution facts relative to its pinned
identities. It cannot establish:

- adequacy or honesty of a project test runner merely from an exit code;
- human intent, role independence, or actor identity without external authentication;
- real chronology from wall-clock values without a separately verifiable timestamp;
- forge configuration, deployment wiring, or enforcement durability outside an integration probe
  and external authority;
- truth of a stated exception reason or pilot value judgment;
- safety against an authority controlling the repository, pinned Vouch executable, delivery policy,
  runner, and required credentials together.

Checkpoints and capsules preserve integrity relative to their trusted baseline and identities; they
do not manufacture independent authority.

## 14. Capability status

| Capability | Status | Evidence or limitation |
|---|---|---|
| Typed assurance kernel, event history, packs, evidence, tiers, release gates | **Implemented · Dogfooded** | Current mk2 source, tests, self-hosted store, and decision record. |
| Self-contained pinned lock and GitHub read-plan/write-publish adapter | **Implemented · Dogfooded** | Generated workflow and mk2 GitHub use. |
| Independent real-project adoption proof | **External proof pending** | Current decision log deliberately gates release on a real-project pilot. |
| Full adoption module and separate adoption record | **Target** | Accepted design; not current mk2 behavior. |
| Greenfield fast lane and brownfield governance ratchet | **Target** | Accepted design; requires adoption implementation and pilots. |
| Sound incremental verification, capsules, scope manifests, checkpoints | **Target** | Current mk2 check remains global. |
| Forge protection, authenticated identity, deployment authority | **Adopter-owned · External** | Vouch can probe and report but cannot own the authority. |
| Local portfolio, support, and feedback tools | **Target** | Must remain explicit and local-only. |
| No Vouch telemetry or hosted control plane | **Target constraint** | Non-negotiable for every implementation wave. |

## 15. Roadmap and promotion gates

### Wave 0 — truthful design baseline

- Preserve the current mk2 diagram.
- Publish this target design, status taxonomy, focused ADRs, and optimal diagram.
- Keep current and target claims visually distinct.

### Wave 1 — adoption foundations on reliable full verification

- Separate adoption record and kernel references.
- Assessment budgets, candidate maps, confirmed scope, roles, plans, scorecards, health, exceptions,
  recovery, pause, and exit.
- Greenfield and brownfield workflows.
- Executable GitHub success and refusal probes.

### Wave 2 — opt-in incremental dogfood

- Scope and change manifests, dependency contracts, integration scopes.
- Verification plans, execution receipts, capsules, checkpoints, full and historical modes.
- Formal and differential soundness proof.
- Explicit migration, baseline establishment, force-full, and rollback.

### Wave 3 — independent pilot and operational proof

- Use one real brownfield project and one real greenfield project if available.
- Measure adoption burden, affected-closure scaling, recovery, false invalidation, and support cost
  locally.
- Require at least one real hard-gated pilot, one later independent governed change, and the agreed
  observation window before claiming adoption.

### Wave 4 — default and scale

- Make incremental verification default only after soundness, migration, recovery, and performance
  gates pass.
- Retain force-full permanently.
- Add explicit local portfolio exports and organization-managed rollout cohorts without creating a
  central authority.

## 16. Promotion evidence

A target capability moves to **Implemented** only with code, acceptance tests, claim-boundary text,
migration behavior, and failure-path coverage. It moves to **Dogfooded** only after the mk2 project
uses its real path. It clears **External proof pending** only after an independent project exercises
the actual delivery authority, refusal path, recovery path, and sustained adoption criteria.

Exact latency defaults, cadence counts, and tier thresholds are empirical policy inputs to settle
through dogfood and pilots. Their measurement remains local; their absence must not be hidden by a
false precision in this design.

## 17. Decision records and sources

Focused ADRs:

1. [Separate adoption module and record](../../docs/adr/0001-separate-adoption-module-and-record.md)
2. [Repository-local operation with no telemetry](../../docs/adr/0002-repository-local-no-telemetry.md)
3. [Distinct greenfield and brownfield adoption paths](../../docs/adr/0003-greenfield-and-brownfield-adoption.md)
4. [Sound fail-closed incremental verification](../../docs/adr/0004-sound-fail-closed-incremental-verification.md)

Current implementation sources:

- [Domain language](../../CONTEXT.md)
- [Mk2 decision log](../../DECISIONS.md)
- [Claim boundary](../../docs/claim-boundary.md)
- [Threat model](../../docs/threat-model.md)
- [Performance measurements](../../docs/performance.md)
- [Quickstart and current integration](../../docs/quickstart.md)
- [Policy IR](../../src/vouch_mk2/ir/mk2.py)
- [Pure lifecycle decisions](../../src/vouch_mk2/core/decide.py)
- [Current global artifact verification](../../src/vouch_mk2/verify.py)
- [Current GitHub adapter](../../src/vouch_mk2/init.py)

