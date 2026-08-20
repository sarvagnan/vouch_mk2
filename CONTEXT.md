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
