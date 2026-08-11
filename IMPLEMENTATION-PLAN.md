# Vouch mk2 — Implementation Plan

**Status:** proposal, 2026-08-10.
**Companion:** [`architecture.html`](architecture.html) — the entity/authority diagram, mutation
flow, and verification flow this plan implements.
**Evidence base:** dual independent rebuild reviews over the Vouch 0.6.2 history — 70 recorded
defects (`vouch/verification-kit/DEFECTS.md`), 31 behavioral hardening commits, decision log
2026-08-03 → 2026-08-09.

---

## 1. Goal and non-goals

Rebuild Vouch as a small typed policy engine with:

- one **authoritative project snapshot** (`state.json`, CAS on a monotonic revision),
- **immutable content-addressed objects** for everything else,
- one **declarative Policy IR** from which every mechanical view is generated,
- a **pure decision core** — `decide(Snapshot, Command) → Events | Violations`,
- **projections** (map, next actions, status facts, agent context) that are never
  transaction participants,
- **thin transports** (CLI, CI, MCP read-only, warn-only hooks) with zero policy.

**Non-goals for the first usable release** (each returns only when a real deployment forces it):

- four prose editions (one precise authority document + kernel suffice at first),
- six adapter families (ship CLI + CI + MCP; hooks as documented fragments),
- HTML map presentation (canonical JSON projection first),
- automatic CI anchor rotation,
- `invalidate-releases` supersession policy (default `retain`; the IR models both,
  only one is exposed),
- scaffold file-tree vendoring and its ownership manifest (replaced by the single
  `vouch.lock` artifact),
- native Windows.

**The conceptual core carries over unchanged:** transforms/observations/actions,
contracts-before-code, frozen acceptance packs, typed deviations, protective vs. completion
claims, tiers with honest `NOT_CHECKED`, red-before-green evidence chains.

## 2. Foundational decisions

| # | Decision | Status | Resolution |
|---|----------|--------|------------|
| D-1 | Core language | **Decided 2026-08-10** | **Typed Python**: Pyrefly strict, frozen dataclasses, closed enums with `typing.assert_never` exhaustiveness, real module boundaries. Rejected: Rust — the defect classes were design defects, not language defects; typed Python captures most of the closed-type value at a fraction of the rewrite risk, and the contributor/agent ecosystem is Python. Consequence accepted: the D070 ancestor-symlink TOCTOU stays a *narrowed, documented* threat-level-2 residual (descriptor-relative `dir_fd`/`O_NOFOLLOW` where the platform provides it), rather than fully closed. |
| D-2 | Distribution | **Decided 2026-08-10** | **Vendor exactly one opaque digest-pinned artifact**: `vouch.lock` = a single-file **zipapp** built from the module tree + one digest. Keeps the founding hermetic-CI constraint (no network, no registry, air-gap safe), collapses ownership tracking to one digest, and moves no trust boundary — a hostile lock-editor is threat level 3 under either option. Rejected: registry-retrieved signed release. The zipapp is the piece that lets mk2 keep "single vendorable file" *without* repeating 0.6's god-file — vendoring is a packaging decision, not a code-organization decision. |

Record both as the first two entries of the mk2 decision log, with the rejected option and
why (the 0.6 `DECISIONS.md` discipline was one of the things that worked).

**Typed-Python ground rules** (the discipline that substitutes for a compiler's closed types —
enforced by lint/CI from the first commit, not convention):

- Pyrefly strict everywhere; no `Any`, no `# type: ignore` without a linked defect ID.
- All domain types are frozen dataclasses; all state/claim/verdict enums are closed, and every
  `match` over them ends in `assert_never`.
- Raw `dict`/JSON never crosses a module boundary — decode to types at the ingress, once.
- `decide()` and all projections are import-forbidden from doing I/O (enforced with an
  import-linter contract, not review).
- Stdlib-only for the shipped artifact (dev-time tooling may use pinned dev deps, as 0.6 did).

## 3. Cross-cutting disciplines (day one, not retrofits)

These four each absorb a top defect class from 0.6 and are **standing rules for every phase**,
not phase deliverables:

1. **Defect log + closure witnesses from the first commit.** Every fixed defect gets a
   discriminating witness: it must FAIL on the pre-fix tree with the exact defect ID and PASS
   at HEAD, plus a positive twin. The 0.6 closure harness (`vouch/closure/harness.py`) is the
   reference implementation — port the pattern, keep it dev-repo-only, and never let it grow a
   parallel coverage system (the empty `corpus-manifest.json` is the cautionary example).
2. **No silent SKIP anywhere, ever.** Verdicts are `PASS | FAIL | NOT_CHECKED | NOT_APPLICABLE`;
   only `PASS` composes to `PASS`; `NOT_APPLICABLE` must cite an IR applicability rule.
3. **An authority never supplies its own discriminator.** Any coverage, distinctness, or
   independence claim requires a witness the claimant didn't author (the D023/D040/D042/D043
   lesson — ten defects, the largest class).
4. **Strict decode at every ingress.** Unknown field = error; type coercion = error; every
   cross-artifact reference is a typed `{kind, digest}` field. Hand-written validator mirrors
   are forbidden — there is exactly one decoder per artifact type, generated from or checked
   against the IR.

## 4. Phases

Each phase has deliverables, exit criteria, and the 0.6 defect classes it must close
(cited so the closure witnesses have targets). Phases are sequential; within a phase,
work parallelizes freely.

### Phase 0 — Claim boundary, threat model, and Policy IR schema
*Relative size: small. Everything downstream cites this.*

- Write the **claim boundary**: the table of what mk2 proves mechanically vs. what is
  permanently `NOT_CHECKED` (organizational). Port the honest-limits prose from 0.6
  `STATUS.md` §known-gaps — it took ~30 defects to extract; mk2 starts with it.
- Write the **threat model** with the three explicit levels (accidental / adversarial content /
  hostile tool rewrite) and the D070 TOCTOU residual scoped to level 2.
- Design the **Policy IR schema**: lifecycle states + transition table, claim taxonomy
  (completion / negative / protective), applicability matrix, tier requirements, obligation
  registry format, version identity.
- Record both foundational decisions (D-1 typed Python, D-2 digest-pinned zipapp vendoring)
  as the first entries of the mk2 decision log.

**Exit:** both documents reviewed; IR schema can express the full 0.6 lifecycle (§14 archetype
state machine, deviation blocking, freeze rules) as data — validated by transcribing it and
diffing against 0.6's tables.

### Phase 1 — Typed domain core for one unit lifecycle
*Relative size: medium. The heart.*

- Closed types: `Unit`, `Deviation`, `Command`, `Event`, `Snapshot`, `Verdict`.
- `decide(Snapshot, Command) → Events | Violations`, pure, table-driven from the IR.
- Scope: unit lifecycle end to end (specify → freeze → ready → in-progress →
  done-pending-acceptance → published), deviation open/adjudicate/close with
  `NAME@VERSION` binding and multi-blocker arrays, and the DoR/DoD gate logic *inside*
  `decide` — a transition and its gate are one code path.
- Property tests against the IR: every legal transition reachable, every illegal transition
  refused with a named violation, exhaustive over the state × command matrix.

**Closes by construction:** fail-open class (D046-style ungated publication is
unrepresentable); the deviation identity chain (bare-name → `NAME@VERSION`, scalar
`blocked_on` — both 0.6 remodels are the initial model here).

### Phase 2 — Snapshot store
*Relative size: medium. The only I/O in the system.*

- Capability-rooted storage layer: one root handle, validated relative-path types, symlink
  refusal, descriptor-relative ops (`dir_fd`, `O_NOFOLLOW`) where the platform provides them —
  with the portable-Python residual (D070) documented in the threat model, per D-1.
- `state.json` read-once → strict decode → `Snapshot`; write path = objects first, then
  atomic CAS replace on expected revision; one lock implementation.
- `objects/sha256/…` store with a **digest registry**: artifact kind → canonicalization →
  domain separator. All digest computation goes through it (0.6 had 43 scattered call sites).
- **Fault-injection test rig**: kill the process at every step boundary, inject short
  writes/fsync failures/permission flips, run concurrent CAS contention — assert the two
  invariant outcomes (before swap: unreferenced objects only; after swap: complete state).

**Closes:** coupled-writes/atomicity class (D024, D029, D047, D052 analogues); most of the
path-handling class (D055, D058, D059, D067); narrows D070 to the documented platform limit.

### Phase 3 — Structured checks and the CI command
*Relative size: medium.*

- Verdict type + composition rule; obligation registry evaluated as: every registered check ID
  must map to an evaluator that ran, or the run fails.
- `vouch check --ci`: one snapshot load, registry dispatch, structured result tree, one
  presentation layer (text + exit code + machine-readable JSON on request).
- Registry completeness is itself a checked projection of the IR (a check added to the IR
  without an evaluator fails CI on the mk2 repo itself).

**Closes:** vacuous-coverage class mechanics (D023, D051, D069 analogues) and the
"CI didn't run the lifecycle gates" hole (0.6 commit `b48ea46`).

### Phase 4 — Releases: one archetype, full claim taxonomy
*Relative size: medium-large. The subtlest domain logic.*

- `Release` entity, operational archetype only; prepare / set-criteria / advance under the IR
  state machine; criteria are ledger-native fields from day one (never sidecars).
- Claim taxonomy in full: completion claims re-check every included unit + criteria digest +
  roster; negative evaluation outcomes require criteria + `outcome_deviations` without roster
  health; protective closures require trigger + roster snapshot + `no_completion_claim`, and
  are **never blocked by contaminated state** (the I3 inversion is a named regression test).
- Every active release must have a legal protective escape (escape-property check in CI).

**Closes:** the terminal-semantics decision chain (0.6 relitigated it four times); the
criteria-sidecar drift chain.

### Phase 5 — Content-addressed evidence and release bundles
*Relative size: medium.*

- Evidence events as immutable objects; heads in `state.json` (git-refs pattern); canonical-JSON
  hash chain; red-before-first-green ordering check; scope inventories with the
  0.6 exclusion-glob semantics (D-pruning lesson: `a/*/**` excludes descendants).
- Anchor modes `none` and `ci` as a **monotonic state machine from day one**: prior head must
  remain an ancestor, backward rotation refused, stagnant-GENESIS warning (0.6 redesigned this
  three times; mk2 starts at the end state). `command` mode deferred.
- Release bundles: digest-addressed, frozen regeneration inputs, inventory digest bound in
  `state.json`.

**Closes:** evidence-chain and anchor holes (`f461654`, `580abdd`, D047 analogues).

### Phase 6 — Formal checking over the emitted transition relation
*Relative size: medium, sharply bounded.*

- The model checker consumes the **same transition relation the runtime executes** (emitted
  from the IR + `decide` tables) — model↔implementation conformance is an identity, not a
  correspondence to maintain.
- Invariants **independently authored** (never generated); each counted invariant carries a
  **private raw mutation witness**; witness borrowing is a build failure.
- Content-addressed certificate binding IR digest, model digest, per-invariant verdicts,
  witnesses. `unknown`/timeout is never a pass. Certificate consumers recompute in source
  checkouts and enforce verdict/coherence/non-vacuity in bundles (the 0.6 per-mode-authority
  lesson, D057).

**Closes:** formal self-certification class (D025, D037, D040, D042, D043, D057).

### Phase 7 — Projections
*Relative size: small-medium.*

- Canonical **JSON system map** keyed on a complete dependency digest; `vouch generate`
  regenerates, CI regenerates into memory and byte-compares (freshness FAIL, never a broken
  commit). HTML presentation is a later, separate layer with its own DOM-order test (D062).
- Next actions derived and **binding** from the first commit. Status facts and agent-context
  emission (kernel → `AGENTS.md`/`CLAUDE.md` suggestions, `.vouch-suggested` sidecar pattern
  kept — it worked).
- Doc-binding: any figure or citation in mk2's own docs is generated or parse-validated
  (0.6's `doc_binding.py` pattern, applied from the start rather than after D068).

**Closes:** stale-generated-artifact class (D031, D044, D050, D056, D064, D068).

### Phase 8 — Transports
*Relative size: small.*

- MCP server: read/verify-only (permanent), stateless, current protocol only; consumes the
  structured result — never parses text.
- Hook fragments for Claude Code/Codex-class clients: warn-only, documented honestly as
  fragments the user wires up; root resolution from the **edit target**, not cwd
  (the `be7ea9a` lesson).
- `vouch.lock` single-artifact vendoring + `vouch init` that writes only state, config, lock,
  and CI workflow.

### Phase 9 — `vouch-migrate` from 0.6.x
*Relative size: medium. Separate tool, final workstream.*

- Reads a 0.6 project, emits a candidate mk2 `state.json` + objects, validates the candidate
  through the mk2 decoder and full check surface, presents a diff, writes nothing until
  approved. Appends one `tooling-upgrade` methodology event, idempotently.
- The mk2 runtime understands **only the current format** — no `_migrate_v0x` accretion.

## 5. Reuse from the 0.6 repo

| Asset | How mk2 uses it |
|---|---|
| `verification-kit/DEFECTS.md` (70 defects) | Acceptance list: every defect whose class mk2 claims to close gets a mk2 regression test or an "unrepresentable — decoder/type rejects it" note. This is the rebuild's definition of done. |
| `tests/test_vouch.py` (7,210 lines) | Mine for behavioral cases, especially lifecycle-edge and claim-taxonomy tests; do not port wholesale. |
| `closure/harness.py` | Reference implementation for the mk2 witness harness. |
| `editions/precise.html` + `kernel.md` | The prose authority and session kernel carry over with edits for mk2 mechanics. |
| `formal/` invariant catalog + negative controls | Seed the independently-authored invariant set. |
| `DECISIONS.md` | Precedent log; mk2 decisions cite 0.6 entries they uphold or reverse. |

## 6. Risks

1. **Typed-Python discipline erodes under velocity** — `Any` creeps in, dicts start crossing
   module boundaries, and mk2 drifts back toward the 0.6 god-file. Mitigation: the §2 ground
   rules are CI lint gates from the first commit (Pyrefly strict, import-linter purity
   contracts, `assert_never` exhaustiveness), not review conventions — a check that exists
   only as prose reliably gets skipped (the 0.6 lesson, verbatim).
2. **Reimplementation misreads 0.6's policy subtlety.** Mitigation: Phase 1's exhaustive
   state × command property tests are written against the IR, not the code, and 0.6's test
   corpus is the oracle for disagreements.
3. **The IR becomes a second god-artifact.** Mitigation: the IR holds only what §4 Phase 0
   lists; invariants, witnesses, and prose rationale are deliberately outside it.
4. **Scope creep back toward 0.6's surface.** Mitigation: the §1 non-goals list is a standing
   gate — each item returns only with a recorded decision naming the forcing deployment.
5. **The meta-layer grows again** (harness, registries, coverage systems). Mitigation:
   discipline 1 caps it — one witness harness, one registry, both checked for non-vacuity by
   the same composition rule as everything else.

## 7. Definition of done for the first usable release

- All Phase 0–8 exit criteria green; fault-injection rig green under CI.
- Every closed 0.6 defect in a claimed class has its mk2 regression test or
  unrepresentability note (§5 row 1).
- `vouch check --ci` on the mk2 repo itself passes with zero `NOT_CHECKED` in the mechanical
  set and every organizational claim visibly `NOT_CHECKED`.
- A scratch project can run the full walkthrough (init → unit lifecycle → evidence →
  release → attest) from the vendored `vouch.lock` artifact with no network.
- `vouch-migrate` round-trips the 0.6 `vouch` repo's own ledger without warnings.
