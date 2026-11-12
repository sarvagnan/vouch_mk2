# Vouch mk2 domain language

- **Initialization refusal** — An initialization attempt stopped before its first
  project-visible publication. It exits 2 and leaves the project unchanged.
- **Initialization suggestion** — Initialization found an existing differing owned file and
  placed a complete `.vouch-suggested` sidecar for operator review. It exits 3.
- **Incomplete publication** — Initialization began project-visible publication but did not
  make the complete store visible. It exits 4, leaves only complete artifacts within the closed
  init-owned path set, and never removes those paths during unwind.
- **Success-equivalent installation** — The complete `.vouch/` store is visible after its lock,
  configuration, and workflow dependencies. It is initialized even if the originating process
  died before reporting success.

## Optimal Vouch system

**Assurance kernel**:
The part of Vouch that records delivery state and evidence, evaluates policy, and produces
bounded release claims.
_Avoid_: Engine, internals

**Project integration**:
The technical work that connects Vouch to a project's repository, test system, delivery
workflow, and external enforcement controls.
_Avoid_: Implementation, adoption, installation

**Governance rollout**:
The change process through which a team begins, expands, and institutionalizes Vouch-governed
delivery practices.
_Avoid_: Deployment, installation

**Adoption operating system**:
The product-supported lifecycle that helps a project assess fit, integrate safely, prove value,
scale governed behavior, sustain use, improve, or exit.
_Avoid_: Documentation, onboarding

**Adoption**:
Sustained use of Vouch-governed behavior in real delivery work. Successful initialization or
project integration alone is not adoption.
_Avoid_: Installation, activation

**Product evolution**:
Work that changes Vouch itself, distinct from integrating Vouch into an adopting project.
_Avoid_: Implementation

**Beachhead adopter**:
A technical lead or maintainer of a small, agent-heavy software team who controls its repository
and delivery workflow and needs trustworthy evidence without enterprise-scale administration.
_Avoid_: End user, generic developer

**Adoption module**:
The part of Vouch that owns project fit, readiness, integration, pilot, rollout, support, and exit
without reinterpreting or strengthening assurance-kernel verdicts.
_Avoid_: Adoption layer, onboarding module

**Greenfield project**:
A project whose product behavior begins after Vouch governance is established.
_Avoid_: New repository, empty repository

**Brownfield project**:
A project with product behavior that predates Vouch governance.
_Avoid_: Legacy project, existing repository

**Legacy baseline**:
The explicitly bounded pre-Vouch behavior and artifacts from which a brownfield project begins its
governance rollout.
_Avoid_: Ungoverned code, old system

**Governed scope**:
The explicitly declared behavior and artifacts to which Vouch governance currently applies.
Anything outside it remains visibly ungoverned.
_Avoid_: Coverage, project scope

**Adoption owner**:
The person accountable for fit, rollout burden, sustained use, and the decision to continue,
pause, or exit Vouch.
_Avoid_: Sponsor, administrator

**Acceptance owner**:
The person accountable for required behavior, acceptance evidence, and amendments independently
of the implementer for the assurance level being claimed.
_Avoid_: Reviewer, approver

**Implementer**:
The person or agent that changes the adopting product within a governed scope.
_Avoid_: Acceptance owner, builder

**Delivery authority**:
The person or organizational role accountable for delivery-workflow enforcement and release
authority.
_Avoid_: Operator, administrator

**Integrated project**:
A project whose technical Vouch connections are verified but whose sustained governed behavior
has not yet been demonstrated.
_Avoid_: Adopted project, installed project

**Adopted project**:
A project that has demonstrated sustained Vouch-governed behavior in real delivery work over a
declared governed scope.
_Avoid_: Integrated project, enabled project

**Exited project**:
A project that has deliberately ended Vouch governance while preserving the historical record and
the limits of claims that remain current.
_Avoid_: Uninstalled project, deleted project

**Bootstrap infrastructure**:
The minimal project and governance scaffolding established before the first governed product
behavior and verified under an explicitly separate bootstrap claim.
_Avoid_: First unit, product behavior

**Adoption record**:
The repository-local history of a project's adoption lifecycle, accountable roles, declared
governed scope, and adoption decisions. It cannot strengthen an assurance-kernel verdict.
_Avoid_: Adoption log, assurance ledger

**Governed change**:
A real product change delivered within a governed scope through the applicable Vouch lifecycle and
enforcement path.
_Avoid_: Vouch change, compliant change

**Proven project**:
A project that has completed one real governed change through its declared hard-gated pilot and met
the pilot's predeclared success criteria.
_Avoid_: Adopted project, integrated project

**Observation window**:
The project-declared period or delivery cadence over which sustained governance must be observed
before the project can be considered adopted.
_Avoid_: Trial, waiting period

**Governance exception**:
An explicit, expiring authorization to interrupt an otherwise required governance control, with a
recorded reconciliation obligation.
_Avoid_: Bypass, waiver

**Whole-project assurance**:
An assurance claim whose declared governed scope genuinely covers all relevant product behavior in
the project.
_Avoid_: Adoption, full coverage

**Candidate project**:
A project being assessed for Vouch fit before its adoption prerequisites and commitments are
settled.
_Avoid_: Prospect, unready project

**Ready project**:
A candidate project whose pilot, accountabilities, authority, assurance level, constraints, and
success and exit criteria are sufficiently settled to begin project integration.
_Avoid_: Integrated project, approved project

**Exploration area**:
An explicitly ungoverned place for disposable discovery work whose contents cannot silently become
governed product behavior.
_Avoid_: Prototype scope, sandbox

**Characterized scope**:
A governed scope whose behavior predates Vouch and is represented by post-hoc characterization
evidence rather than a causal built-under-Vouch claim.
_Avoid_: Legacy unit, retroactive unit

**Candidate map**:
An automatically proposed description of project structure and possible governed scopes that has
not yet received human confirmation of meaning or intent.
_Avoid_: System map, governed-scope map

**Governance interruption**:
An observed break in required Vouch enforcement that makes affected current claims stale until its
impact is reconciled.
_Avoid_: Governance exception, bypass

**Adoption scorecard**:
The predeclared measures used to decide whether a project should continue, expand, pause, or exit
its governance rollout.
_Avoid_: Usage analytics, test metrics

**Support owner**:
The person accountable for resolving adoption friction and escalating defects without silently
changing policy or adoption state.
_Avoid_: Adoption owner, help desk

**Support bundle**:
A secret-safe diagnostic artifact that preserves enough project and adoption context to investigate
a problem without becoming an authority over project state.
_Avoid_: Debug dump, adoption record

**Adoption plan**:
A reviewable proposal for how a candidate project will integrate Vouch, run its pilot, measure
success, recover, expand, pause, or exit.
_Avoid_: Installation plan, rollout checklist

**Integration probe**:
An executable demonstration that a project's real delivery workflow enforces Vouch correctly on
both refusal and success paths.
_Avoid_: Smoke test, installed-file check

**Delivery adapter**:
A Vouch-owned integration with a particular delivery and forge environment that enforces recorded
policy without redefining it.
_Avoid_: Provider plug-in, CI script

**Pinned Vouch artifact**:
The exact version and digest of the self-contained Vouch executable selected by a project.
_Avoid_: Installed version, latest version

**Portfolio view**:
A user-operated, read-only aggregation of explicitly imported project exports that never becomes
an authority over any project.
_Avoid_: Control plane, telemetry dashboard

**Local-only operation**:
The rule that Vouch never transmits project or adoption data, including anonymous or opt-in
telemetry. Any export or aggregation is an explicit user-controlled local action.
_Avoid_: Privacy mode, opt-out telemetry

**Adoption stage**:
A project's position in the adoption lifecycle, independent of the strength of assurance it has
chosen.
_Avoid_: Assurance level, maturity score

**Assurance tier**:
The declared strength of evidence and independence required for governed delivery, independent of
how long or broadly a project has adopted Vouch.
_Avoid_: Adoption stage, maturity level

**Kernel reference**:
An immutable reference from an adoption fact to the exact assurance-kernel revision and artifacts
on which that fact depends.
_Avoid_: Shared state, kernel snapshot

**Governance root**:
The repository-local authority under which one or more governed scopes share an adoption record
and assurance kernel.
_Avoid_: Repository, project root

**Rollout cohort**:
A set of governance roots coordinated through the same organization-managed adoption wave while
retaining independent project authority and outcomes.
_Avoid_: Organization adoption, rollout batch

**Adoption stop rule**:
A predeclared condition under which adoption should be reconsidered because its burden, failures,
or unmet value exceed the project's acceptable limits.
_Avoid_: Mechanical exit, failure threshold

**Trust gap**:
A concrete inability to justify confidence in an upcoming change or release that makes evaluating
Vouch potentially worthwhile.
_Avoid_: Compliance need, generic risk

**Pilot hypothesis**:
A predeclared expectation that applying Vouch to a bounded real change will produce specified value
at an acceptable cost.
_Avoid_: Adoption goal, success metric

**Feedback report**:
A locally generated, user-reviewed account of adoption experience that Vouch never transmits on the
user's behalf.
_Avoid_: Telemetry, support bundle

**Adoption health**:
A project's current governance condition—healthy, attention, blocked, or interrupted—reported
independently of its historical adoption stage and assurance tier.
_Avoid_: Adoption stage, project status

**Adoption episode**:
One continuous period from beginning an adoption plan through continued use, pause, or exit. A
project returning after exit begins a linked new episode.
_Avoid_: Installation, rollout attempt

**Recovery event**:
A recorded restoration from an exact previously valid adoption history that identifies the lost or
untrusted interval and requires affected claims to be re-evaluated.
_Avoid_: Repair, rollback

**Core obligation**:
A Vouch integrity, honesty, history, or claim-limit requirement that project policy cannot disable.
_Avoid_: Default check, mandatory setting

**Additional check**:
A project- or organization-selected named requirement that strengthens local governance without
weakening a core obligation.
_Avoid_: Custom policy code, plug-in gate

**Portfolio export**:
A minimal, digest-bound project summary generated only through explicit user action for import into
a user-operated local portfolio view.
_Avoid_: Telemetry event, support bundle

**Pilot outcome**:
The preserved result of evaluating a pilot hypothesis, including failure to demonstrate value and
the reason classified for the adoption owner's decision.
_Avoid_: Pilot status, adoption result

**Composed project gate**:
The delivery-workflow decision that combines assurance-kernel verification, adoption health, and
project-owned checks without merging their authorities.
_Avoid_: Kernel gate, Vouch check

**Time claim**:
A recorded wall-clock value whose chronology is not mechanically established unless it is supported
by a separately verifiable external timestamp.
_Avoid_: Observation evidence, event order

**Assessment budget**:
The declared count, byte, depth, and time limits within which a project assessment may claim to be
complete.
_Avoid_: Timeout, scan limit

**Incomplete assessment**:
An assessment that exhausted a declared budget or prerequisite and therefore cannot support a
candidate map or readiness decision.
_Avoid_: Partial success, failed assessment

**Local notification report**:
A deterministic local summary of blockers, drift, expiring exceptions, and next actions that a
user-owned adapter may consume without Vouch transmitting it.
_Avoid_: Notification, webhook payload

**Enforcement outage**:
The unavailability of the external delivery system required for hard governance, during which local
work may continue but hard-gated progression cannot silently become advisory.
_Avoid_: Governance interruption, CI failure

**Incremental verification**:
An assurance run that re-verifies the affected closure from an exact verified baseline while
reusing only unaffected results whose complete validity conditions remain unchanged.
_Avoid_: Partial verification, changed-file check

**Affected closure**:
The changed governed scopes together with every dependent, shared, or integration scope whose
required inputs or claims may have been invalidated by the change.
_Avoid_: Changed units, touched files

**Verified baseline**:
The exact previously accepted project state against which an incremental verification may justify
that particular assurance results remain reusable.
_Avoid_: Main branch, last green build

**Full verification**:
An assurance run that re-evaluates every active governed scope and current-state global integrity
obligation required by its declared claim rather than relying on change-based scope exclusion.
_Avoid_: Full test suite, whole-project assurance

**Verification capsule**:
A reusable assurance result for one governed scope whose validity is limited to its exact declared
inputs, dependencies, verification environment, and Vouch identity.
_Avoid_: Verification certificate, cache entry, previous green

**Scope manifest**:
The human-confirmed declaration that assigns each relevant project artifact one primary scope or
explicit exclusion and records the dependencies needed to determine an affected closure.
_Avoid_: Candidate map, file ownership list

**Change manifest**:
The normalized description of a candidate change supplied by a delivery adapter for assurance-
kernel impact planning; it does not itself decide which scopes are unaffected.
_Avoid_: Diff, affected units

**Incremental composition**:
A project assurance result formed from freshly verified affected scopes, still-valid verification
capsules for unaffected scopes, and successful global integrity checks.
_Avoid_: Partial pass, cached pass

**Global invalidator**:
A change or unresolved uncertainty that prevents safe scope exclusion and therefore requires
verification to widen to every potentially affected scope or to full verification.
_Avoid_: Large change, cache miss

**Release composition**:
The assurance decision for an exact release roster formed from valid scope results and global
obligations; it may use incremental composition without implying full verification occurred.
_Avoid_: Release test, full verification

**Dependency contract**:
The mechanically identified behavior a governed scope exposes to dependent scopes and against
which their verification capsules are bound.
_Avoid_: Source digest, public API

**Opaque dependency**:
A declared dependency without a sufficient dependency contract, requiring any relevant producer
change to invalidate dependent verification capsules.
_Avoid_: Dynamic dependency, unknown dependency

**Integration scope**:
A first-class governed scope for behavior that crosses primary scopes or an effectful seam, with
its own acceptance evidence and dependency relationships.
_Avoid_: Global tests, shared scope

**Verification plan**:
The assurance-kernel decision that names the exact scopes and executions required for a candidate
change before any runner performs them.
_Avoid_: Test plan, CI matrix

**Execution receipt**:
A result bound to an exact verification-plan entry that preserves whether execution was observed
locally or attested by an external delivery environment.
_Avoid_: Test result, evidence event

**Verification cadence**:
The assurance-policy limit on how many governed changes or releases may occur before a specified
full verification obligation becomes due.
_Avoid_: Schedule, expiration period

**Historical scrub**:
A genesis-to-head re-evaluation of the complete authoritative event and evidence history,
distinct from verifying every currently active governed scope.
_Avoid_: Full verification, audit log check

**Verified checkpoint**:
An exact previously verified history prefix from which later verification may validate and replay
only the appended suffix without weakening the resulting claim; its authority is limited to its
bound verifier identity and hard-gated project revision.
_Avoid_: Snapshot, cache, last green build

**Volatile input**:
A verification-relevant input whose exact identity cannot be safely bound for later reuse and
therefore requires fresh execution whenever its scope participates in composition.
_Avoid_: Side effect, nondeterminism, secret

**Volatile scope**:
A governed scope with at least one volatile input, preventing its verification capsule from being
reused even when its repository-owned artifacts are unchanged.
_Avoid_: Effectful scope, unstable unit

**Published dependency reference**:
A consumer-governance-root binding to an exact dependency contract published by a different
governance root, without transferring authority over either root.
_Avoid_: Cross-project state, shared capsule

**Incremental soundness**:
The requirement that incremental composition must never pass for a supported project state and
change when full verification of that same state and change would fail.
_Avoid_: Cache correctness, performance equivalence

**Baseline establishment**:
The explicit transition from full-only operation to incremental verification after scope
confirmation, full verification, and the required historical scrub have succeeded.
_Avoid_: Migration, cache warm-up
