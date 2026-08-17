# Vouch mk2 decision log

Records decisions where the goal fixed an outcome but not one unique mechanism, with
the rejected option and why — the 0.6 `DECISIONS.md` discipline, carried over. Entries
cite the 0.6 precedents they uphold or reverse. Append-only; a reversed decision gets a
new entry, never an edit.

## D-1 — Core language: typed Python

**Status:** decided 2026-08-10.

- **Question:** What language is the mk2 core written in?
- **Decision:** Typed Python: Pyrefly strict, frozen dataclasses, closed enums with
  `typing.assert_never` exhaustiveness, real module boundaries. Ground rules (enforced
  by lint/CI from the first commit, not convention):
  - Pyrefly strict everywhere; no `Any`, no `# type: ignore` without a linked defect id.
  - All domain types are frozen dataclasses; all state/claim/verdict enums are closed,
    and every `match` over them ends in `assert_never`.
  - Raw `dict`/JSON never crosses a module boundary — decode to types at the ingress, once.
  - `decide()` and all projections are import-forbidden from doing I/O (enforced with an
    import-linter contract, not review).
  - Stdlib-only for the shipped artifact (dev-time tooling may use pinned dev deps, as
    0.6 did).
- **Rejected:** Rust. The 0.6 defect classes were design defects, not language defects;
  typed Python captures most of the closed-type value at a fraction of the rewrite risk,
  and the contributor/agent ecosystem is Python.
- **Consequence accepted:** the D070 ancestor-symlink TOCTOU stays a *narrowed,
  documented* threat-level-2 residual (descriptor-relative `dir_fd`/`O_NOFOLLOW` where
  the platform provides it), rather than fully closed. See
  `docs/threat-model.md` and 0.6 `verification-kit/DEFECTS.md` D070.

## D-2 — Distribution: one digest-pinned zipapp artifact

**Status:** decided 2026-08-10.

- **Question:** How do consuming projects obtain and trust the tool?
- **Decision:** Vendor exactly one opaque digest-pinned artifact: `vouch.lock` = a
  single-file **zipapp** built from the module tree, tracked by one digest. Keeps the
  founding hermetic-CI constraint (no network, no registry, air-gap safe), collapses
  ownership tracking to one digest, and moves no trust boundary — a hostile lock-editor
  is threat level 3 under either option.
- **Rejected:** registry-retrieved signed release (breaks the no-network constraint,
  adds a registry trust root without moving the hostile-editor boundary).
- **Why the zipapp specifically:** it is the piece that lets mk2 keep "single vendorable
  file" *without* repeating 0.6's god-file — vendoring is a packaging decision, not a
  code-organization decision. Upholds the 0.6 single-file-vendoring precedent
  (0.6 `DECISIONS.md`, "CLI packaging and project-local execution") while reversing its
  code-organization consequence.

## D-3 — Deviation blocking is derived; unblocking and amendment impact are explicit

**Status:** decided 2026-08-11 (Phase 1).

Three places where 0.6's prose (`machine.md` §9/§11) and its implementation
(`bin/vouch` `command_deviation_adjudicate` / `command_deviation_close`) disagree or
drift, resolved for mk2 before `decide()` existed:

- **`blocked_on` is a derived projection, never stored state.** A unit's blocker set
  is *defined* as the deviations bound to its `NAME@VERSION` with status `open` or
  `rejected`. 0.6 stored the array on the unit and recomputed/cleared it in three
  places; the close path literally repairs drift between the array and the deviation
  records. mk2 makes the drift unrepresentable. Rejected: storing the array (the 0.6
  model) — a second authority for one fact.
- **No auto-unblock.** 0.6's `deviation close` on a rejected deviation silently moves
  the unit to `re-verifying` or `specified` as a side effect. mk2 keeps close as
  close; leaving `blocked` is its own gated command (`respecify` or `begin-reverify`,
  legal only when no blocking deviations remain, and `begin-reverify` additionally
  requires a recorded pack — a gate 0.6's ungated set-status edge did not have). Why:
  a transition and its gate are one code path (plan §4 Phase 1); state changes hidden
  inside unrelated commands are how fail-opens breed.
- **Amendment impact invalidates the reflexive transitive consumer closure, except
  `superseded` units.** 0.6's prose says "every published unit downstream"; its code
  invalidates *every* downstream unit regardless of status, including `superseded`
  ones, and clears their blocker arrays. mk2 follows the code's breadth (fail-closed:
  work-in-progress downstream of an amended contract is stale too) but excludes
  `superseded` units (invalidation would resurrect a terminally-dead unit into a
  re-verifiable state). Already-`invalidated` members re-record idempotently so every
  amendment's impact set is complete, and — via the derived blocker set — an
  invalidated unit's own open deviations keep blocking its re-verification instead of
  being orphaned.

Two edge additions over the 0.6 edge set, both consequences of the above (asserted
exactly in `tests/test_mk2_ir.py`):

- **`blocked → blocked` (open-deviation self-loop).** Deviation accumulation on an
  already-blocked unit is table-visible, so any gate the open-deviation edge ever
  gains applies to accumulating units too, instead of being bypassed by a code-level
  carve-out.
- **`invalidated → specified` (respecify escape).** The widened impact closure can
  invalidate a never-frozen unit; without this edge such a unit's only exits would be
  supersession or a fabricated deviation, since `begin-reverify` correctly requires a
  recorded pack. Every non-terminal state keeps a legal recovery path (the escape-
  property discipline, 0.6 invariant I11's spirit).

Also recorded: mk2 has **no operator-initiated block**. 0.6 permitted `set-status →
blocked` with an empty blocker array; in mk2 `blocked` means exactly "has blocking
deviations", so an operational hold must be expressed as a typed deviation (usually
`plan-gap` or `capability-request`), keeping the status and the record in lockstep.

## D-4 — Evidence chains: promotion-only anchors, mode in the snapshot, split trust

**Status:** decided 2026-08-13 (Phase 5).

- **Anchor advance is promotion-only.** `advance-anchor` sets the anchored digest to
  the chain's *current head*, never to a caller-chosen digest. Monotonicity ("prior
  head must remain an ancestor") and refusal of backward rotation are then true by
  construction — chains are append-only (parent-must-equal-head at record time), so
  every past anchor is an ancestor of every later one. 0.6 verified ancestry at
  rotation time and redesigned the machinery three times; mk2 makes the illegal
  rotations unrepresentable instead. Rejected: caller-supplied anchor targets with
  ancestry verification (requires object reads inside policy, and is exactly the
  surface 0.6 kept getting wrong).
- **Anchor mode lives in the snapshot, not a config sidecar.** 0.6 put `anchor.mode`
  in `.vouch/config.json` ("project configuration, not the ledger"). mk2 has one
  authoritative snapshot precisely so facts cannot drift between authorities; the
  mode is recorded state (`set-anchor-mode` command), reversing the 0.6 precedent.
- **Stagnant-GENESIS is NOT_CHECKED under mode `ci`, NOT_APPLICABLE under `none`.**
  0.6 printed a warning and never failed. mk2's verdict lattice has no warning; under
  mode `ci` a non-empty chain that was never anchored means the project's own claim
  ("CI anchors our chains") is mechanically unproven — NOT_CHECKED, which gates the
  CI exit. Under mode `none` the check is NOT_APPLICABLE citing the IR applicability
  rule. This deliberately tightens 0.6: opting into `ci` is opting into the check.
- **Digest trust is split, and said plainly.** The pure core cannot hash (the purity
  contract bans `hashlib` — the digest registry is the store's). `decide()` validates
  evidence *structure* (parent = head, sequence, version binding, kind/outcome
  coherence); content addressing (the recorded digest actually names those bytes, the
  chain walks to GENESIS) is verified by the artifact checks in CI. A lying digest
  passes `decide()` and fails `vouch-mk2 check`.

## D-5 — Formal checking: the model is decide() itself

**Status:** decided 2026-08-13 (Phase 6).

- **The transition relation is emitted by executing `decide()`/`apply()`** over a
  canonical bounded command universe, breadth-first from the empty snapshot. There is
  no separately-maintained model: model↔implementation conformance is an identity,
  not a correspondence 0.6 had to re-verify with a conformance harness. What the
  checker explores IS what the runtime executes. Rejected: a hand-written model plus
  conformance scenarios (0.6's design — the gap between them produced the D040/D042
  class).
- **Invariants are hand-authored; generating them is forbidden.** An invariant
  derived from the IR or from `decide()` would certify the implementation against
  itself (the D023/D043 discriminator rule at the formal layer). The catalog cites
  each 0.6 invariant it descends from.
- **Every counted invariant carries a private mutation witness over the relation** —
  a named perturbation of the explored relation that makes that invariant's check
  fail. Privacy means the one-witness-per-invariant ownership mapping — a missing
  witness, a witness whose owned invariant still passes, or two invariants sharing
  one witness ("borrowing") is a build failure, not a report line. Privacy does NOT
  mean behavioral exclusivity: a mutation may legitimately trip other invariants too
  (0.6's applicability lesson — "applicability affects reporting, not behavioral
  exclusivity"). Witnesses mutate the *relation*, never the invariant or the checker.
- **Dummy digests are sound inside the formal universe.** decide() is structural by
  D-4, so the universe records evidence with literal placeholder digests; the formal
  layer stays pure (no hashing) and the evidence-content checks remain CI's job.
- **Bounded exhaustive exploration; `unknown` cannot exist silently.** The universe
  is finite and exploration runs to closure; if a state cap were ever hit the run
  reports UNKNOWN and the certificate build fails — never a pass (0.6 D057 lesson).
- **Certificates are recomputed by consumers.** The content-addressed certificate
  binds the emitted-table digest, relation digest, per-invariant verdicts, and
  witness verdicts; `vouch-mk2 check` re-runs the exploration and compares — a
  recorded certificate that no longer matches the source is a FAIL, not a stale pass.

## D-6 — Projections: pure, allowlisted on disk, freshness-checked, docs bound

**Status:** decided 2026-08-14 (Phase 7).

- **Projections are pure functions of the snapshot and never transaction
  participants** (plan §1). The `projections` package joins both purity contracts;
  emission to disk is transport work in the store/CLI layer.
- **Generated files live in the store root under a closed allowlist** (`map.json`,
  `vouch-context.md`), written atomically with the store's descriptor-relative
  discipline. No caller-supplied generated-file paths exist — the 0.6 scaffold
  ownership-manifest machinery is unnecessary when the name set is closed.
- **Freshness is an artifact check, opt-in by presence.** A committed `map.json` is
  regenerated into memory and byte-compared on every `vouch-mk2 check`; a mismatch
  is a FAIL naming the file, never a repaired or rewritten commit. An absent map is
  PASS (generating is opt-in until the first-release DoD requires it). Next actions
  and status facts ride inside the map document, so their freshness is the map's.
- **Agent-context emission keeps 0.6's suggestion-sidecar pattern** (it worked): the
  generated context targets a caller path only via `--target`; an existing,
  differing target is never overwritten — the emission goes to
  `<target>.vouch-suggested` instead.
- **mk2's docs bind their cited mechanical facts via tests** (0.6's `doc_binding.py`
  lesson, applied from the start): check ids, invariant/witness ids, command labels,
  and stated counts appearing in README/DECISIONS/docs must exist in (or equal) the
  artifacts they cite, enforced by a doc-binding test — a document stating a fact the
  code no longer backs fails CI.

The Phase 0 transcription (`ir/vouch_06.py`) keeps the prose-faithful reading, since
0.6's own precedence rule gives prose authority absent a recorded discrepancy; this
entry is mk2's recorded discrepancy for its own semantics.

## D-7 — Transports: what the lock binds, what init owns, what MCP exposes

**Status:** decided 2026-08-14 (Phase 8).

- **`vouch.lock` is a reproducible zipapp of `src/vouch_mk2` and nothing else.** Built
  with fixed zip entry order, zeroed timestamps, and no compiled bytecode, so two builds
  of the same tree are byte-identical; its identity is one digest
  (`digest_of(ArtifactKind.LOCK, bytes)`). The lock carries a `--self-check` that
  recomputes and prints its own digest. Vendoring the lock moves no trust boundary
  (D-2): a hostile lock editor is threat level 3 and stays there.
- **`vouch-mk2 init` writes exactly four things** into a target project:
  `.vouch/` (a fresh store), `vouch.lock`, `.vouch/config.json` (the lock digest and
  the store path — nothing policy-bearing; anchor mode lives in the snapshot per D-4),
  and `.github/workflows/vouch.yml` (runs `python vouch.lock check --ci`). Nothing else,
  ever — no CLAUDE.md, no adapters, no skills. An existing, differing file at any of
  those paths is never overwritten: init writes `<path>.vouch-suggested` (the D-6
  sidecar rule) and reports it. Rejected: 0.6's ownership manifest — unnecessary when
  the owned set is closed and sidecars handle collisions.
- **MCP is read/verify-only, permanently, stateless, structured.** Tools: `check`
  (returns the CheckReport as data — never parsed text), `formal` (pure run summary),
  `next`, `status`, `map` (projections). No mutation tool will ever be added (0.6
  STATUS.md "deliberately not built"). One store handle per request; no caching across
  requests. Speaks the current MCP protocol only via a minimal stdlib JSON-RPC-over-stdio
  server — no SDK dependency (D-1 stdlib-only for the shipped artifact).
- **Hooks are documented fragments, warn-only, root-resolved from the edit target.** A
  hook receives the path being edited, walks up from THAT path (never cwd — the 0.6
  `be7ea9a` lesson) to the nearest `.vouch/`, runs `check`, and prints the composed
  verdict as advice. It never blocks and never mutates. Shipped as `hooks/` text
  fragments the user pastes into their client's settings, plus one `vouch-mk2 hook`
  subcommand that does the resolution so the fragment stays a one-liner.

**D-7 amendments (Phase 8 adversarial review, 2026-08-15):**

- **MCP root is pinned at server start** (`vouch-mk2 mcp --root R`); tools take no
  `root` argument. Every other transport gets its root from a human's CLI invocation,
  but MCP tool arguments are chosen by a model — i.e. by repository content under
  threat-model level 2 — so a caller-supplied root made every directory on disk
  client-reachable and turned the read-only tools into a store-mutating primitive
  (the orphan-temp sweep runs under an exclusive lock on open). Read/verify transports
  (MCP, hook) now open stores `read_only=True`: no sweep, no exclusive lock, commit
  refused — "read-only" and "never blocks" are mechanisms, not conventions.
- **The hook's root walk stops at the first repository boundary** (a `.git`) crossed
  without a store. A `.vouch` planted higher (`$HOME`, `/tmp`) must never be picked up
  for an edit inside a project that has none.
- **`init` proves every precondition before its first write**, and refuses any owned
  path whose components include a symlink or non-directory (`os.makedirs` follows
  symlinked components — a repo shipping `.github -> /elsewhere` had made init write
  outside the project root while reporting an in-root write). A refusal never leaves a
  half-initialized project.
- **The lock digest is compared, not merely printed.** `self-check --expect <digest|@config.json>`
  exits 1 on mismatch; the generated project workflow pins `vouch.lock` against the
  digest `init` recorded in `.vouch/config.json`, and mk2's own CI builds twice,
  `cmp`s, and pins against the build digest. `self-check` attests the archive the
  executing module was imported from (`__file__`), with `sys.argv[0]` only as a
  fallback — argv is caller-controlled and can name a decoy.
- **The lock binds every shipped package file, not only `*.py`** (data files, `py.typed`),
  and refuses symlinked package files/directories, so two different trees cannot share
  a digest; only bytecode caches and dotfile droppings are excluded by name.
- **Known residual, recorded:** byte-identical rebuilds rest on zlib deflate output
  being stable across builds; CI verifies it on the runner, but a zlib/zlib-ng version
  change could alter compressed bytes for identical sources. Revisit (`ZIP_STORED` at
  ~3x size) if a real reproducibility break is ever observed.

## D-8 — vouch-migrate: field mapping from the 0.6 ledger, and what is lossy

**Status:** decided 2026-08-15 (Phase 9).

- **Input** is a 0.6 `ledger.yaml.json` (top-level `{"ledger": {...}}`); the mk2
  runtime never reads it — only the separate `vouch-migrate` tool does, and mk2 stays
  free of `_migrate_v0x` accretion (plan §4 Phase 9).
- **Units:** `name`, `contract_version → version`, `status` (same 11-value vocabulary),
  `depends_on`, `acceptance_pack_version`. **Dropped by design:** `blocked_on` (mk2
  derives blocking from deviation records — D-3; the migrator VERIFIES the stored array
  equals the derived set and reports each divergence as a warning), `class`,
  `criticality`, `approval_required`, `definition_path`, `artifact_digest`,
  `gate_results` (unit-definition/map concerns outside the mk2 core snapshot; recorded
  in the migration report, not silently lost).
- **Deviations:** `id`, `type`, `unit` (`NAME@VERSION` string → typed `UnitRef`; a bare
  name is REFUSED — 0.6 kit-3 already refuses ambiguous histories), `status`,
  `amendment_version`, `impact_set` split into unit names and `release:`-prefixed ids
  (→ `release_impact_set`, D-3's typed field). Dropped: `description`, `frozen_artifact`.
- **Releases:** operational archetype maps in full (`id`, `state`, `includes`,
  criteria fields → `LaunchCriteria`, `closure_record` → `ClosureRecord` with
  `roster[].{name,version?,status}` → exact-ref `roster_snapshot`; a present version
  maps directly, while an absent version is taken from the single matching include by
  name, with an ambiguous or absent match REFUSED as "closure roster row cannot be
  bound to an include"; `bundle_inventory_digest`). **Qualified releases are refused**
  unless `--drop-qualified` is given, in which case each is
  reported and omitted — Q is a plan §1 non-goal and mk2 cannot represent it honestly.
  Dropped: `criteria_digest` (mk2 recomputes), map digests / `frozen_at` /
  `map_generator_identity` (0.6 map machinery), the `retroactive` closure flag (reported).
  A 0.6 `invalidated` release maps as-is (same state).
- **Project block:** `anchor.mode` comes from `.vouch/config.json` if present (else
  `none`) → snapshot `anchor_mode` (D-4). D-12 supersedes this decision for tier:
  ledger `assurance_tier` maps to snapshot `tier`, and a supplied config tier must
  agree or migration is refused. Everything else (`claim_version`,
  independence model, runner/orchestration identities, `methodology_events`,
  `next_actions`) is outside the mk2 core snapshot: `next_actions` are re-derived
  (never migrated), the rest are carried in the migration report only.
- **Evidence chains are not migrated** (0.6's chain format is not mk2's
  content-addressed objects); the report says so per unit. Formal certificates likewise.
- **Procedure:** parse strictly (unknown ledger fields are warnings, listed) → build a
  candidate `Snapshot` → run the mk2 decoder round-trip, `run_checks`, and the standing
  checks on it → print a diff (before/after summary + every warning) → write NOTHING
  unless `--approve`, in which case a fresh store is initialized and the candidate
  committed as revision 1. Re-running against an already-migrated target is refused.
- **DoD target:** the 0.6 repo has no store of its own; "round-trip the 0.6 vouch repo's
  own ledger" means the kit fixture (`verification-kit/fixture/ledger.yaml.json`) plus
  the 8-case negative corpus (`corpus-ledger/01`–`08`; the directory's ninth entry is
  its README), vendored into `tests/data/`. Corpus cases
  that are illegal in 0.6 must be refused by the migrator, not repaired.

## D-9 — Superseded unit history may share a name; one ACTIVE record per name

**Status:** decided 2026-08-15 (surfaced by Phase 9; user decision).

- **The situation.** Amendment adjudication mints a new contract version: `NAME@1.0.0`
  is superseded and `NAME@1.1.0` becomes current. 0.6 keeps BOTH records — that is why
  its deviations bind `NAME@VERSION` (kit-3 made bindings version-exact precisely
  because a bare name becomes ambiguous once history exists), and why 0.6's fixture
  ledger contains a superseded `legacy-score-export@1.0.0` with a closed deviation
  still bound to it. mk2 Phases 1–8 modeled supersession as a terminal status on ONE
  record and enforced "one record per name" (codec, `Snapshot.unit`, fold), which was
  correct only because no mk2 command yet mints versions.
- **Decision.** Adopt the truthful model: a snapshot may hold several records for one
  name provided **at most one is non-superseded**. Name-keyed lookups
  (`Snapshot.unit(name)`, `Publish(name)`, gates keyed by name) mean *the current
  record*, falling back to the newest superseded record only when no active one
  exists; exact bindings (`Snapshot.unit_ref(NAME@VERSION)` — deviations, rosters,
  evidence heads) resolve history precisely; the fold replaces by exact ref so a
  superseded twin is never overwritten. Opening a deviation against a superseded
  version is refused (no edge from `superseded`), so history is inspectable but inert.
- **Invariant, enforced three ways:** "at most one non-superseded record per name" is
  refused by the codec at ingress, FAILed by a standing check at rest, and (this
  epic) checked as formal invariant M10 with a private witness.
- **Rejected:** dropping superseded duplicates at migration. It would leave mk2 unable
  to represent the ordinary outcome of its own amendment protocol, and would dangle
  every version-exact binding to history — the exact ambiguity kit-3 was built to end.
- **Reachability caveat, stated plainly:** no `decide()` command mints a new version
  yet, so a two-version state is currently reachable ONLY through migration. The
  property tests and the formal universe therefore SEED such a state (they cannot
  reach it), and that seeding is labelled as such. When amendment-driven
  re-versioning arrives (a `ReVersion` command is the natural next step), the seeded
  state becomes reachable and the seed is retired.

## D-10 — Dogfooding: mk2's own store carries mk2's own formal certificate

**Status:** decided 2026-08-15 (DoD §7).

- The plan's DoD says `vouch check --ci` must pass "on the mk2 repo itself" with zero
  mechanical `NOT_CHECKED` and every organizational claim visibly `NOT_CHECKED`. mk2
  does not (yet) model its own modules as units, and inventing a fake unit lifecycle to
  make the sentence true would be exactly the vacuous self-certification this rebuild
  exists to end. The honest dogfood is the one artifact mk2 genuinely produces about
  itself: its **formal certificate** — recomputable evidence over its own `decide()`.
- Therefore the repo commits a real store at `.vouch/` whose snapshot records mk2's own
  formal certificate (`vouch-mk2 formal --record`) and its own generated system map, and
  CI runs `check --ci` against it **from the vendored lock**, not from `uv`. Every
  mechanical row is then a true statement about this repository (registry completeness,
  certificate recompute, map freshness, empty-but-audited chains and bundles), and the
  six organizational rows are printed `NOT_CHECKED` verbatim.
- The store's `state.json`, objects, `map.json`, and `vouch-context.md` are committed
  and re-verified on every push; the certificate must be re-recorded whenever
  `formal/` changes (the checker version bumps), and map freshness FAILS if the
  snapshot changes without regeneration — the dogfood is a live gate, not a fixture.
- Modelling mk2's own modules as units was deferred here, but two versions must be kept
  apart. RETROACTIVE self-hosting (declaring existing modules units with packs never
  frozen and red evidence never recorded) is rejected outright: fabricated history, the
  post-hoc-plausible-chain problem anchoring exists to prevent. PROSPECTIVE self-hosting
  (running mk2's method on mk2's own next increments, packs frozen before
  implementation, evidence recorded live) is a genuine option that D-10 did NOT settle;
  it is proposed as **D-11** (bead `vouch_mk2-dvz`, user decision pending) with D045 as
  the pilot unit.

## D-11 — Adopt prospective self-hosting: mk2 governs its own forward development

**Status:** decided 2026-08-15 (user decision; pilot).

- **The inconsistency, stated plainly.** Vouch's thesis is that a system is built as units
  with contracts frozen before code and evidence recorded live. The plan itself says the
  Phase 1 tests are written "against the IR", demands closure witnesses "from the first
  commit", and makes `check --ci` "on the mk2 repo itself" a definition-of-done item. Yet
  the tool embodying the method was built through nine phases without a single unit in a
  ledger. That was not a foundational design choice recorded anywhere; the plan enumerated
  WHAT to build precisely and HOW to build it only as review disciplines, and the
  build followed its letter. D-10 rationalized the deferral after the fact. This entry
  corrects the record: the omission was a gap, and this decision closes it going forward.
- **Decision.** From this point on, every increment to mk2 goes through mk2's own unit
  lifecycle in the dogfood store (`.vouch/`), per `docs/self-hosting-tradeoff.md`:
  pack (spec + acceptance tests) frozen BEFORE implementation; red evidence recorded from
  the failing acceptance tests before any implementation; the implementer (Codex) cannot
  edit the pack (Model 2 asymmetry, mechanical); review findings that contradict the pack
  are DEVIATIONS adjudicated by the user; publication through the real DoD gates; mk2
  versions released with launch criteria. Retroactive self-hosting stays rejected (D-10).
- **Scope: a pilot with a stop rule.** The first unit is NOT D045 as originally proposed
  but the external-review remediation `ingress-invariants@1` (below) — more urgent, and a
  purer test of the method on correctness work. D045 follows. Wholesale adoption is
  decided after the pilot shows what the friction costs on this repo. **Stop rule:** if a
  unit cannot reach `published` through the real gates without hand-editing the store or
  bending a gate, that is a finding about the method or about mk2 — record it and rule.
- **What stays NOT_CHECKED:** independence between the pack author (Fable) and the
  implementer (Codex) is a format heuristic (two models, two vendors), not proof of
  distinct persons; the claim boundary already says so and self-hosting does not change it.
- **Infrastructure the pilot forces, accepted as in-scope:** re-versioning through
  `decide()` (retiring D-9's seeded state), and a pytest→`record_evidence` adapter so red
  and green evidence is recorded by the harness, never hand-typed.

## D-12 — Assurance tier lives in the snapshot and has exactly one mechanical consumer

**Status:** decided 2026-08-16 (pilot unit #2, D045).

- **The 0.6 defect.** D045: config and ledger each carried a tier and could disagree; the
  ledger's tier silently won, so a "high" project got lightweight enforcement. Two
  authorities for one fact — the same class D-4 closed for anchor mode.
- **Decision.** `Snapshot.tier: Tier` (closed enum lightweight|standard|high; default
  `lightweight`), set by one command `set-tier` (any direction, recorded, table-visible;
  raising is free, LOWERING is refused while any unit is beyond `specified` — budget
  never lowers tier below the risk already committed to, machine.md §1). One authority;
  no config field.
- **What the tier mechanically GOVERNS — and only this.** The 0.6 §13 matrix has 13
  controls; most are organizational or concern surfaces mk2 does not have (mutation
  testing, security evidence, external map delivery). mk2 binds the tier to the checks it
  can actually run, and states the rest as NOT_CHECKED rows scoped by tier:
  - `standard`+: evidence anchoring required — `check.evidence-anchors` under mode `none`
    is FAIL (not NOT_APPLICABLE) and a published unit with no evidence chain is FAIL
    (`check.tier.evidence-required`); at `lightweight` chains are optional (ordering-only,
    no causal claim — the matrix's own words).
  - `high`: additionally every published unit's chain must be anchored (not merely
    present) and a formal certificate must be recorded (`check.tier.high-attestation`).
  - `gate.dod.tier-evidence` enforces those same IR cells before either publication
    edge is accepted; `published_once` preserves the obligation after later lifecycle
    status changes.
  - Active cells without full coverage appear in `org.tier-controls`: partial rows name
    their mechanical evidence and exact residual, unchecked rows name the remaining
    requirement, and waived cells are reported separately as no-obligation clauses.
- **The tier requirements themselves move from the 0.6 transcription into the mk2 IR** as
  first-class data (`TierRequirement` rows already exist in the schema); the mk2 checks
  read the matrix rather than hardcoding tier→control, so the doc-binding test can bind
  the matrix to the checks that consume it. The mechanical row is
  `harness-recorded-red-green`, whose per-tier cells bind
  `check.evidence-anchors`, `check.tier.evidence-required`, and (at high)
  `check.tier.high-attestation`; every other row is surfaced by
  `org.tier-controls`.

The matrix coverage metadata is bound below. `mechanical=none` means mk2 has no
mechanical coverage for that cell; `residual=none` means the cell is either fully
covered or waived.

- typed-contracts/lightweight: level=required | coverage=partial | mechanical=pyrefly strict, import-linter, check.registry-complete | residual=declared classes are human reviewed
- typed-contracts/standard: level=required | coverage=partial | mechanical=pyrefly strict, import-linter, check.registry-complete | residual=declared classes are human reviewed
- typed-contracts/high: level=required | coverage=partial | mechanical=pyrefly strict, import-linter, check.registry-complete | residual=declared classes are human reviewed
- acceptance-frozen-before-implementation/lightweight: level=required | coverage=partial | mechanical=acceptance pack digest bound at freeze | residual=frozen BEFORE implementation is human attested
- acceptance-frozen-before-implementation/standard: level=required | coverage=partial | mechanical=acceptance pack digest bound at freeze | residual=frozen BEFORE implementation is human attested
- acceptance-frozen-before-implementation/high: level=required | coverage=partial | mechanical=acceptance pack digest bound at freeze | residual=frozen BEFORE implementation is human attested
- independence-minimum/lightweight: level=required | coverage=none | mechanical=none | residual=the required independence model is human attested
- independence-minimum/standard: level=required | coverage=none | mechanical=none | residual=the required independence model is human attested
- independence-minimum/high: level=required | coverage=none | mechanical=none | residual=the required independence model is human attested
- harness-recorded-red-green/lightweight: level=required | coverage=full | mechanical=record-evidence ordering and outcome gates | residual=none
- harness-recorded-red-green/standard: level=required | coverage=full | mechanical=check.evidence-anchors, check.tier.evidence-required | residual=none
- harness-recorded-red-green/high: level=required | coverage=full | mechanical=check.evidence-anchors, check.tier.evidence-required, check.tier.high-attestation | residual=none
- real-boundary-tests/lightweight: level=required | coverage=none | mechanical=none | residual=boundary selection and test adequacy are human reviewed
- real-boundary-tests/standard: level=required | coverage=none | mechanical=none | residual=boundary selection and test adequacy are human reviewed
- real-boundary-tests/high: level=required | coverage=none | mechanical=none | residual=boundary selection, test adequacy, and fault injection are human reviewed
- replay-manifests-content-addressing/lightweight: level=required | coverage=none | mechanical=none | residual=replay completeness and content-addressing coverage are human reviewed
- replay-manifests-content-addressing/standard: level=required | coverage=none | mechanical=none | residual=replay completeness and content-addressing coverage are human reviewed
- replay-manifests-content-addressing/high: level=required | coverage=none | mechanical=none | residual=replay completeness and content-addressing coverage are human reviewed
- mutation-testing/lightweight: level=waived | coverage=none | mechanical=none | residual=none
- mutation-testing/standard: level=risk-triggered | coverage=none | mechanical=none | residual=mutation scope and surviving-mutant adjudication are human reviewed
- mutation-testing/high: level=required | coverage=none | mechanical=none | residual=mutation scope and surviving-mutant adjudication are human reviewed
- approval-gated-actions/lightweight: level=waived | coverage=none | mechanical=none | residual=none
- approval-gated-actions/standard: level=risk-triggered | coverage=none | mechanical=none | residual=approval triggers and authorization policy are human reviewed
- approval-gated-actions/high: level=required | coverage=none | mechanical=none | residual=approval triggers and authorization policy are human reviewed
- security-evidence/lightweight: level=waived | coverage=none | mechanical=none | residual=none
- security-evidence/standard: level=risk-triggered | coverage=none | mechanical=none | residual=security triggers and required blocking outcomes are human reviewed
- security-evidence/high: level=required | coverage=none | mechanical=none | residual=security evidence and required blocking outcomes are human reviewed
- system-map-gates/lightweight: level=required | coverage=none | mechanical=none | residual=gate completeness, content adequacy, and verifier ownership are human reviewed
- system-map-gates/standard: level=required | coverage=none | mechanical=none | residual=gate completeness, content adequacy, and verifier ownership are human reviewed
- system-map-gates/high: level=required | coverage=none | mechanical=none | residual=gate completeness, content adequacy, and verifier ownership are human reviewed
- release-map-in-bundle/lightweight: level=required | coverage=none | mechanical=none | residual=release-map bundle inclusion is human reviewed
- release-map-in-bundle/standard: level=required | coverage=none | mechanical=none | residual=release-map bundle inclusion is human reviewed
- release-map-in-bundle/high: level=required | coverage=none | mechanical=none | residual=release-map bundle inclusion is human reviewed
- map-delivered-externally/lightweight: level=required | coverage=none | mechanical=none | residual=external delivery is human attested
- map-delivered-externally/standard: level=required | coverage=none | mechanical=none | residual=external delivery is human attested
- map-delivered-externally/high: level=required | coverage=none | mechanical=none | residual=external delivery is human attested
- archetype-q-machinery/lightweight: level=required | coverage=none | mechanical=none | residual=sealed procedure, exposure controls, and held-out variants are human reviewed
- archetype-q-machinery/standard: level=required | coverage=none | mechanical=none | residual=sealed procedure, exposure controls, and held-out variants are human reviewed
- archetype-q-machinery/high: level=required | coverage=none | mechanical=none | residual=sealed procedure, exposure controls, and held-out variants are human reviewed
- **Formal binding.** M11 `M11.tier-never-lowers-after-commitment` checks the runtime
  relation, and private witness W11 `W11.tier-lowering` forges exactly the forbidden
  lowering edge.
- **Rejected:** a tier in `.vouch/config.json` (0.6's shape — the D045 defect itself);
  and enforcing the full 13-row matrix mechanically (would require faking checks mk2
  cannot perform — the vacuous-coverage class).

## D-13 — The state format is versioned from format 1; upgrades are explicit, recorded, and invent no state

**Status:** decided 2026-08-16 (unit `state-format@1`, bead bd4.2).

- **The problem.** The codec declared the state format "pre-1.0 and DISPOSABLE": every field
  addition made every existing store undecodable, and the store owner hand re-encoded the
  dogfood store twice during unit #2. That is acceptable for the tool's own store and
  disqualifying for a user's. The codec's own docstring said stability "begins at the first
  usable release" — this is that line.
- **Decision.**
  - `state.json` carries `state_format: int`. `CURRENT_STATE_FORMAT = 1` names the layout as
    of unit #2 (tier + published_once) plus the field itself. Strict decode accepts exactly
    the current format; a missing field is format 0 ("pre-versioned"), an older format is
    refused with a message naming `vouch-mk2 upgrade`, a newer format is refused as "written
    by a newer vouch" — never silently read either way.
  - `vouch-mk2 upgrade --root R [--dry-run]` is the ONE path from format N to the current
    format: pure upgraders `UPGRADERS[N]: dict → dict` (one per bump, contiguous keys
    0..CURRENT-1) applied in order to the raw document, then strict decode of the result
    (an upgrader that produces an undecodable document fails closed, original bytes kept),
    written atomically under the writer lock, then projections regenerated. `revision` is
    unchanged — a format upgrade is not a lifecycle event.
  - **No invented state**: an upgrader's defaults are the pre-field semantics — `tier` absent
    → `lightweight` (0.6's own meaning of "no tier"), `published_once` absent → derived from
    status (published/release-bound/superseded → true), `state_format` absent → 0. Any real
    value is a later explicit recorded command (`set-tier`), as unit #2 did by hand.
  - The upgrader for 0→1 accepts the *union* of pre-versioned shapes the tool ever wrote
    (unit-#1 era: no tier/no published_once; unit-#2 era: tier, no published_once), each kept
    as a fixture. From format 1 on, **changing the canonical layout without bumping the
    format is a test failure** (a golden of format 1's canonical bytes), and every bump ships
    its upgrader + fixture.
  - Read paths (`check`, `status`, MCP read-only) refuse a stale format with the same clear
    message, exit 2, no traceback; `init` and `migrate` write the current format.
- **Rejected:** decoder-side compatibility defaults ("read old files by filling in the new
  field") — a silent default on read is the fail-open class the ingress work closed; and
  recording the upgrade as a snapshot event — it would make the upgrade itself require a
  decode of the old format by the new core.

**D-13 correction (2026-08-16, unit `state-format@1` DEV-sf-02):** the bullet "`published_once`
absent → derived from status (published/release-bound/superseded → true)" was wrong and
contradicted D-13's own "no invented state" rule. `published_once` is a latch: only
`published`/`release-bound` are unambiguous; every other status is reachable both with and
without a prior publication. The upgrader therefore REFUSES ambiguous units unless the
operator asserts each (`--was-published` / `--never-published`), and prints what it applied.
The dogfood store's own upgrade (run before this correction) produced correct values by
inspection: three published units → true, one in-progress unit that had never been published
→ false; recorded here so that fact is not merely luck.

## D-14 — Acceptance packs are byte-bound: the pack's files are stored, digested, and re-verifiable

**Status:** decided 2026-08-16 (unit `pack-binding@1`, bead bd4.6).

- **The gap.** `freeze` records an opaque `pack_version` string. Vouch never reads or hashes
  the pack, so "the goalposts cannot move" was a convention (unit #5's accuracy review, findings
  1/7) — and the IR tier matrix already listed "acceptance pack digest bound at freeze" as
  mechanical evidence for `acceptance-frozen-before-implementation`, which was an overclaim.
- **Decision.**
  - A **pack inventory** object (`ArtifactKind.ACCEPTANCE_PACK`, canonical JSON: `pack_format`,
    entries of relative `path` + per-file `sha256` + `size`, sorted by path) is placed in the
    CAS store atomically with the freeze commit. `Unit.acceptance_pack_digest` (64-hex, `""` =
    unbound) names it. **State format 2**; upgrader 1→2 sets `""` — the truthful pre-field
    fact: nothing was bound.
  - `freeze --pack-version V --pack-file PATH…` (repeatable, paths relative to `--project`,
    default cwd) builds and binds; `freeze` without files stays legal (label-only) at
    `lightweight`. `bind-pack --name U --pack-file …` binds a unit's *current* pack version
    after the fact (recorded event `AcceptancePackBound`; legal while the unit's pack version
    is unbound; refused once bound — binding is write-once per pack version). `respecify`
    clears the digest with the version. Amendment (`adjudicate-amend`) is unchanged: it names
    the next version; the next `freeze` binds it.
  - Two checks, tier-scoped through the D-12 machinery on the `acceptance-frozen-before-
    implementation` control: `check.pack-bound` (mechanical, standing+artifact: every unit
    with a pack version at `standard`+ must be bound and its object must decode; at
    `lightweight` unbound is NOT_APPLICABLE "label-only pack") and `check.pack-files-match`
    (artifact check, only when `check --project DIR` is given: every bound unit's files in the
    working tree hash to the inventory; missing/changed → FAIL naming paths; without
    `--project` → NOT_APPLICABLE "no project root"). The IR cell's coverage becomes honest:
    PARTIAL, mechanical = pack bytes bound + re-verifiable, residual = frozen BEFORE
    implementation is human-attested.
  - mk2's own store (tier `standard`) must satisfy this: the owner binds the five published
    units' current packs with `bind-pack` (their files are unchanged since each final freeze,
    which `check --project .` will now prove every run), and the quickstart freezes with
    `--pack-file`, so its sentence about goalposts becomes mechanism.
- **Rejected:** hashing pack bytes into the `pack_version` string (unverifiable without the
  bytes; the string stays a human label); making binding mandatory at `lightweight` (0.6's
  lightweight tier is explicitly "ordering only"); a chronology claim ("frozen before
  implementation") — Vouch still records order of *its own* events only.

**D-14 IR matrix binding (state format 2):** D-14 supersedes D-12's three original
`acceptance-frozen-before-implementation` binding rows as follows. Lightweight permits a
label-only pack and therefore claims no mechanical coverage; standard and high bind and
re-verify the canonical pack inventory while leaving chronology human-attested.

- acceptance-frozen-before-implementation/lightweight: level=required | coverage=none | mechanical=none | residual=frozen BEFORE implementation is human attested; the pack's EXECUTION is not verified — runner configuration (conftest.py, pytest.ini, pyproject testpaths) can deselect a bound test; bind those files too
- acceptance-frozen-before-implementation/standard: level=required | coverage=partial | mechanical=check.pack-bound, check.pack-files-match | residual=frozen BEFORE implementation is human attested; the pack's EXECUTION is not verified — runner configuration (conftest.py, pytest.ini, pyproject testpaths) can deselect a bound test; bind those files too
- acceptance-frozen-before-implementation/high: level=required | coverage=partial | mechanical=check.pack-bound, check.pack-files-match | residual=frozen BEFORE implementation is human attested; the pack's EXECUTION is not verified — runner configuration (conftest.py, pytest.ini, pyproject testpaths) can deselect a bound test; bind those files too

**D-14 correction (2026-08-16, unit `pack-binding@1` DEV-pb-01 + adversarial review):**
(i) `bind-pack` names the unit's ACTIVE record and is refused on superseded history (D-9/M10);
(ii) `Unit.pack_history` remembers retired labels so `freeze` refuses `pack.version-reused` —
"write-once per pack version" is now enforceable; (iii) late binding (after publication) stays
legal at `standard` but is durably recorded (`Unit.pack_binding: "" | at-freeze | late`),
listed by name in `check.pack-bound`'s detail and in status, and is FAIL at `high` — a green
row can never hide that the bytes were bound after the code shipped (mk2's own four
published units were bound late on 2026-08-16 and say so); (iv) what binding does NOT prove is
stated in the IR residual and the docs: the pack's *execution* — a `conftest.py` can deselect
a bound test — so the quickstart advises binding runner configuration too; (v) `check
--project` defaults to the store's parent when the store is `<project>/.vouch`, and at
`standard`+ its absence makes `check.pack-files-match` NOT_CHECKED, never a clean
NOT_APPLICABLE; (vi) an empty inventory is refused everywhere; superseded units are out of
both checks' scope; a pack path must appear verbatim in its directory listing (case).

## D-15 — Human legibility of the governed system is the project's responsibility, not mk2's map (parked)

**Status:** decided 2026-08-16 (user decision; parked, revisit only when a real deployment forces it).

- **The narrowing.** 0.6 required a rich, human-legible system map per part (purpose, inputs,
  outputs, failure modes, authority, effects) with legibility acceptance tests. mk2 carried over
  *internal* legibility (one snapshot, pure freshness-checked projections, typed modules,
  recorded decisions, executable docs) and a checked-in human-readable context
  (`vouch-context.md`: revision, tier, binding state, next actions, the not-provable list) — but
  mk2 units carry lifecycle facts only, its map is a *delivery* map not a *system* map, and there
  is no legibility acceptance test. Until now this narrowing was undecided.
- **Decision.** Park it. Descriptive fields (`purpose` etc.), a human system-map projection, and
  legibility tests are NOT added now. System legibility is the responsibility of the users and
  system designers as part of project design; mk2 remains a policy engine over what artifacts can
  prove. Rationale: the ledger would start asserting facts about code that Vouch cannot verify
  (the D068 stale-docs class through the front door); a third state format in a week; a standing
  owner cost per unit; second-order asks (purpose per release/deviation) with no natural fence —
  for a benefit not yet demanded by any real deployment.
- **Revisit trigger:** a real project (bd4.4) whose users cannot answer "what is this unit for /
  what depends on it / what blocks it" from `vouch-context.md` + `status` + the map. If that
  happens, the minimal shape is recorded here: `purpose` only, immutable after publication, one
  Markdown human map, presence tests + an honest organizational NOT_CHECKED row for legibility
  itself; not 0.6's full field set.
- The two `defect-closure.md` rows classified (c) for purpose rendering / DOM-order verification
  stand, now with this decision as their rationale.

**D-11 status change (2026-08-17): ADOPTED.** The pilot ran eight units through mk2's own
lifecycle (`ingress-invariants`, `assurance-tier`, `lifecycle-cli`, `state-format`,
`operator-docs`, `pack-binding`, `cli-completeness`, `defect-dispositions`; `state-format`
re-published after a genuine cross-unit contract conflict) — twelve deviations recorded and
adjudicated as store facts, and the stop rule never fired. From this date every forward change
to mk2 goes through a unit: pack frozen and byte-bound before implementation, RED recorded
through the harness, review, publish through the real DoD gates, at tier `standard`;
`docs/method.md` is binding, not descriptive. **Named exceptions** (changes that are not units):
(i) `DECISIONS.md` entries themselves and their dated corrections; (ii) prose edits with no
test binding and no behavioral claim (typo/wording), which still go through the doc-binding and
accuracy tests; (iii) housekeeping artifacts — `docs/usage-log.md`, `docs/method.md`, the beads
export, README's test-count re-pin; (iv) the dogfood store's own lifecycle commands (they are the
method running, not a change to it). Anything else that changes what mk2 does, checks, stores,
or claims is a unit. **The stop rule stands** as a standing check, not a pilot condition: if a
gate must be bent to publish mk2's own unit, adoption stops and that fact is recorded here.

**D-8 addendum (2026-08-17): plan §7 bullet 5 withdrawn.** "`vouch-migrate` round-trips the 0.6
repo's own ledger without warnings" cannot be met: no 0.6-governed project ever existed (0.6
never self-hosted, and Vouch was not used on any project before mk2). `migrate` remains as
D-8 specifies, proven over its synthetic fixtures (13 tests: field mapping, refusal-not-repair,
config/ledger tier disagreement, structural refusal before touching the target). It is not
deprecated; it is simply unexercised by real data. Revisit trigger: a real 0.6 ledger appears.
The remaining "first usable release" work (bd4.4: a second real project) waits for the user to
run Vouch on a real project first, then returns as a unit with that project's frictions as its
pack.
