# Vouch mk2 decision log

Records decisions where the goal fixed an outcome but not one unique mechanism, with
the rejected option and why — the 0.6 `DECISIONS.md` discipline, carried over. Entries
cite the 0.6 precedents they uphold or reverse.

The log is append-only (D-29). Text already in the log never changes. An amendment,
correction or note is appended to the end of the entry it amends, opened by a bold label that
names its date and source; a reversed decision gets a new entry. After appending, run
`uv run python scripts/decisions_guard.py pin`; the test suite runs `verify`, which fails when
pinned text has changed.

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
- harness-recorded-red-green/lightweight: level=required | coverage=partial | mechanical=record-evidence ordering and outcome gates | residual=the recorded order of Vouch's own events is not proof of chronology relative to implementation; recording a command's exit status is not proof that the bound acceptance tests actually executed
- harness-recorded-red-green/standard: level=required | coverage=partial | mechanical=check.evidence-anchors, check.tier.evidence-required, gate.dod.current-pack-green | residual=the recorded order of Vouch's own events is not proof of chronology relative to implementation; recording a command's exit status is not proof that the bound acceptance tests actually executed
- harness-recorded-red-green/high: level=required | coverage=partial | mechanical=check.evidence-anchors, check.tier.evidence-required, gate.dod.current-pack-green, check.tier.high-attestation | residual=the recorded order of Vouch's own events is not proof of chronology relative to implementation; recording a command's exit status is not proof that the bound acceptance tests actually executed
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

## D-16 — An append-only event log; the snapshot is derived from it and verified against it

**Status:** decided 2026-08-17 (user decision after two independent adoption reviews). Unit `event-log@1`.

- **The gap.** The store held one authoritative snapshot and nothing else: no transition log, no
  timestamps, no actor, no ordering. Chronology lived only in the git history of `.vouch/`
  (destroyed by squash merges), the quickstart's "the recorded order of Vouch's own events
  makes auditable" was untrue, two branches produced unmergeable `state.json` files with no
  safe rule, and an anchor could not say who advanced it or when.
- **Decision.** Every event `apply()` folds becomes a content-addressed **event record**
  (`ArtifactKind.EVENT`): `{event, payload, recorded_at, prev (digest of the previous event
  record), revision, actor claim, run id if present, pack digest for pack-bearing events}`. The
  snapshot carries `event_head` (digest of the latest record). Records are placed objects-first
  in the same commit as the snapshot (the commit is atomic: no snapshot without its records).
  `check.event-log` (mechanical, artifact): the chain from `event_head` back to genesis decodes,
  is unbroken, and **replaying it through `apply()` from the empty snapshot reproduces the
  committed snapshot byte-for-byte** — the snapshot is a derived, verified cache; the log is the
  authority. Actor claim = whatever the caller asserts (`--actor`, defaulting to the OS user)
  plus CI run identity from the environment when present (`GITHUB_RUN_ID` etc.); Vouch records
  these as **claims**, and the check verifies structure and replay, never identity — the
  claim boundary says so.
- **What this enables (each its own unit):** chronology in the store (the quickstart sentence
  becomes true and squash merges stop mattering); anchors that carry the run id that advanced
  them (D-4 gets its CI side: `advance-anchor` in the generated workflow, bot-committed, on
  fork PRs NOT_CHECKED); a semantic `merge`: replay both branches' event chains from their
  common ancestor through `decide()`, refusing (not guessing) on a genuine conflict.
- **State format 3** (`event_head`); upgrader 2→3 sets a genesis marker and records, as its own
  first event, that history before revision N is not in the log ("pre-log revisions 1..N: git
  history only") — no invented state. Store size grows by one small object per event; the
  bench script is re-run and the number recorded.
- **Rejected:** a merge driver over snapshots alone (no safe rule without event order); making
  the log the *only* store (every read would replay; the derived snapshot is the cache that
  keeps reads O(1)).

**D-16 correction (2026-08-17, recorded by the batch-A driver; append-only — the text above is left
as decided).** `event_head` becomes **state format 4**, not 3. Unit `evidence-semantics@1` (D-17)
landed first and needed format 3 for `EvidenceHead.unit_version` plus the per-pack-digest evidence
summaries that D-17's pure publication gate reads (`EvidenceHead.pack_summaries`); `decide()` is a
pure function of the snapshot and cannot open stored objects, so that summary has to live in the
snapshot. D-16's substance is unchanged — an append-only event log, the snapshot derived from it and
verified by replay — only the format number moves, and the 3→4 upgrader carries the same "no invented
state" obligation D-16 already states.

## D-17 — Publication requires current-pack GREEN; evidence binds the pack it ran against

**Status:** decided 2026-08-17 (both adoption reviews' first finding). Unit `evidence-semantics@1`.

- **The gap.** `gate.dod.tier-evidence` required only that an evidence head *exist*: a unit with
  a single RED published at `standard` and `check --ci` composed PASS. `record-evidence` accepted
  any status, so a green on a merely-specified unit poisoned its chain forever. `EvidenceEvent`
  bound unit, command line, outcome, and time — not the pack it ran against, so evidence for
  pack/1 counted for pack/3.
- **Decision.** `EvidenceEvent.pack_digest` (the unit's bound pack at recording; `""` for
  label-only packs at lightweight); `record-evidence` is refused before `acceptance-frozen`
  (`evidence.no-pack`) and allowed in every later status incl. published (late evidence is
  legitimate and displayed as such); at `standard`+ publication requires, for the CURRENT pack
  digest, a RED followed by a GREEN with the latest record GREEN (`gate.dod.current-pack-green`);
  `check.evidence-chains` reports a chain whose latest current-pack record is not GREEN as FAIL
  on published units at `standard`+. Re-versioning: `specify NAME --version V2` is accepted when
  every existing record for NAME is superseded (D-9's stated intent), creating the new active
  record with an empty chain — which is also the sanctioned escape from a poisoned chain.
- **Rejected:** a `reset-evidence` command (an append-only chain must not be rewritten; the new
  version IS the reset, and it is visible).

## D-18 — CI policy, tier granularity, and CI anchoring (adoption reviews, batch B)

**Status:** decided 2026-08-17 (user adopted the recommendations).

- **`--ci` semantics (B2 → option iv).** A recorded per-store setting `ci_policy: ledger | strict`
  set by one command (`set-ci-policy`), default **strict** for new stores (`init`); `check --ci`
  prints which policy it enforced. `strict`: every unit that has left `specified` must be terminal
  (published / release-bound / superseded / retired) or carry an explicit, recorded, table-visible
  exception (`mark-open-in-ci --name U --reason …`, cleared by `clear-open-in-ci`); otherwise
  `check.ci-policy` FAILs naming the units. `ledger`: today's behavior, and the report says so.
  Change-scoping (only units touched by a diff) is deferred until the event log can attribute
  events to a branch. Rejected: silent ledger-only (green would keep meaning "coherent", never
  "done"); scoping on pack files alone (blind to implementation-only changes).
- **Tier granularity (B3 → keep global, measure first).** The tier stays a single store-level
  fact through the first real project (bd4.4). If that project shows the ceremony cost the reviews
  predict, the recorded next step is a **raise-only** per-unit `critical` mark (global tier as the
  floor; never lowers a unit below it, so D-12's lowering rule survives), not a per-unit tier.
  Rejected now: per-unit tiers ("non-critical" is itself an organizational claim; every check
  grows a per-unit branch; reports mean less).
- **CI anchoring (B5 → option a).** The generated workflow, after the project's own tests pass,
  runs `advance-anchor` for every unit whose head CI observed and pushes a bot commit of
  `.vouch/` (state + regenerated projections) with `[skip ci]`, recording the workflow run id
  (from the D-16 event log's run-id claim); on pull requests from forks it does nothing and the
  anchor rows stay honestly NOT_CHECKED until merge. What an anchor then claims: "advanced by
  workflow run N" — verifiable against the run URL, not asserted. Rejected: an observation
  artifact imported later (a second trust root); dropping the CI meaning (would make `ci` mode a
  rename of today's convention).

**CI-anchor closure note (2026-08-18, unit `ci-anchor-closure@1`).** At anchor mode `ci`,
no project composes PASS until a real CI run has advanced its current evidence anchor, including
mk2 itself. A locally advanced anchor has no workflow-run claim and is therefore honestly
`NOT_CHECKED`; lowering the anchor mode to conceal that state would bend the gate, which D-11
forbids. The first eligible CI run records its run-ID claim and resolves the row.

- **Python (B4).** 3.14 stays; it is stated in README, the quickstart, `init`'s output, the
  generated workflow (`actions/setup-python` with 3.14), and a bootstrap message in the lock that
  says "Vouch requires Python 3.14+" instead of a `SyntaxError` on older interpreters.

**D-17 amendment (2026-08-17, batch-A ruling O-1, user decision):** evidence binds the digest of
the pack's **executable entries** (the test files an implementer's harness runs), not the whole
inventory. The spec stays byte-bound in the inventory for tamper visibility, but a prose-only
amendment does not void RED: `gate.dod.current-pack-green` and `check.evidence-chains` compare
against the current pack's *test-set digest*; any change to a test file still voids prior
evidence for it. Rationale: RED proves the tests can fail against pre-implementation code — a
fact about the tests, not the prose. Rejected: re-recording RED after every amendment (forces
REDs that cannot honestly exist once the tree is green); leaving the unit unpublished (every
dependent inherits the failure).

**D-14 amendment (2026-08-17, batch-A ruling O-3, user decision):** `rebind-pack` — for a
published or release-bound unit whose bound pack files must change **only to adapt to a changed
shared interface** (behavior unchanged; e.g. a record type gained a field), a recorded event
that replaces the unit's pack inventory (old digest → new digest, with a stated reason), carries
the evidence chain forward, requires a fresh **GREEN** on the new test-set digest before
`check.evidence-chains` passes for the unit (a RED is not required — it would be meaningless for
an adaptation), and is listed by name in `check.pack-bound`'s detail ("rebound after publication:
…", like late binding); FAIL at `high` unless the rebound unit has that GREEN. Not legal below
`published`. Rejected: narrowing what is bound (weakens D-14 for every project to solve a
self-hosting problem); a standing exception (permanent and growing).

**Method rule (batch-A note O-2):** a pack amendment edits the requirement text in place *and*
records the amendment note; it never appends a contradiction to a requirement that still reads
the old way.

## D-19 — Batch B: the adoption seam (external review, 2026-08-18)

**Status:** decided 2026-08-18 (user). A third independent review found the core close to release
quality and the distribution/CI seam not. Findings reproduced by the owner before acceptance:
the generated workflow runs `check --ci` before it advances anchors (so any adopter at standard/
high with anchor mode `ci` is permanently red and never reaches the step that would resolve it —
unit #14 fixed this for mk2's OWN workflow and asserted only that, missing the class);
job-wide `contents: write` granted before the checked-out lock executes, with `persist-credentials`
left default; `record-evidence` reads the pack digest AFTER the child exits, so a concurrent
freeze/rebind attributes an exit status to a pack that was not current while the tests ran;
`init` can leave `.vouch/` and `vouch.lock` behind on failure and then refuse to retry; and there
is no licence, tag, release artifact, or version command.

- **Licence: MIT** (user's choice), with metadata in `pyproject.toml` and a `LICENSE` file.
- **Order:** B1 `generated-ci@1` (blockers #1/#2), B2 `evidence-atomicity@1` (#3 and the unbounded
  `--json` buffering), B3 `init-recovery@1` (#4) now; B4 `release@1` (licence, tagged `vouch.lock`
  with digest and provenance, `--version`, lock upgrade/rollback) and B5 `adoption-docs@1`
  (`docs/adoption.md`, the `vouch-mk2` vs `python3 vouch.lock` strings, teaching
  `--pack-test-file`) immediately after — they are load-bearing only once the artifact leaves
  this machine.
- **Standing rule from #1:** when a defect is found in an instance mk2 maintains for itself, the
  acceptance pack must assert the property on the *generated* artifact adopters receive, not on
  mk2's own copy. Fixing the instance is not fixing the class.

**D-14 amendment note (2026-08-18, DEV-ca-01):** `rebind-pack` also covers the case where a
published unit's pack asserted a *detail it over-specified* which a later recorded decision
superseded, provided the unit's own promise is unchanged and a fresh RED would be artificial.
First instance: `ci-anchor@1`'s pack froze `[skip ci]` and "anchoring must come after the check
step" — the second of which was the external review's blocker, specified rather than merely
implemented. D-19 supersedes both; the generated workflow's shape is now owned by
`generated-ci@1`'s pack, `ci-anchor@1` keeps its real promise (CI advances anchors and commits
the result), and the rebind's reason names the superseding decision. A changed *promise* still
requires the full deviation → amend → respecify → re-freeze path; this is narrower than that.

## D-20 — CI trust boundary: callable gate, read-plan/write-publish (2026-08-19)

**Status:** decided 2026-08-19 (user). The second B1 review showed that naming project jobs in
the generated workflow cannot gate anything: GitHub Actions `needs` resolves only jobs in the
same workflow run. It also showed that removing persisted checkout credentials does not make it
safe to execute checked-out repository code in a job whose `GITHUB_TOKEN` has `contents: write`.

- **Project-test gate.** The generated `.github/workflows/vouch.yml` is a reusable workflow as
  well as a direct read-only validation workflow. `init --test-job NAME` does not put `NAME` in
  the generated workflow's `needs`; it prints a caller job for the adopter to place in the
  workflow that actually owns those jobs. That caller has the real `needs: [NAME, ...]` edge and
  invokes the reusable Vouch workflow with anchoring explicitly enabled. Direct `push` and
  `pull_request` runs validate only and can never anchor. `init` continues to own exactly D-7's
  four paths: it neither edits an existing adopter workflow nor creates a fifth file.
- **Privilege split.** Validation and anchor planning run with `contents: read`. Planning may
  execute the checked-out, digest-verified `vouch.lock` and produces a candidate commit based on
  the exact checked-out revision. A separate publisher job alone receives `contents: write`; it
  executes no checked-out repository code. Before pushing, fixed workflow code verifies the
  candidate's parent, allowed path set and modes, and confirms that the remote default-branch
  head still equals the planned base. A stale or malformed candidate fails closed. Concurrency
  remains an additional guard, not the correctness mechanism.
- **Trust-root clarification.** The same-run candidate commit/bundle is transport between two
  jobs under the already-recorded forge/runner organizational boundary. It is not evidence, is
  never imported through a Vouch command, cannot advance an anchor on its own, and is discarded
  if its structural checks fail. It therefore does not create the later-imported observation
  artifact or second trust root rejected by D-18. The offline, stdlib-only `vouch.lock` contract
  is unchanged; forge artifact transfer belongs to the CI adapter, not Vouch distribution.
- **Self-hosting.** mk2's own workflow uses the same read-plan/write-publish boundary. A real
  GitHub Actions adoption probe is required before B4/B5: adopter tests must genuinely gate the
  reusable call, a fresh GENESIS anchor must advance, a stale candidate must fail, and the
  publisher must not execute repository-controlled code.

Rejected: dangling cross-workflow `needs`; a write-capable job that runs `vouch.lock` even with
`persist-credentials: false`; automatically editing an adopter's existing workflow; a privileged
`workflow_run` that checks out an untrusted revision; and a later command that imports an
observation artifact as evidence.

**D-7/D-19 amendment — init publication outcomes (2026-08-19, DEV-ipc-01).** A filesystem has
no portable atomic operation meaning “unlink this pathname only if it still names inode X”;
`stat` followed by `unlink` retains the same replacement race it was meant to close. The init
interface therefore distinguishes the side of its publication seam instead of claiming an
impossible rollback:

- **refused** (exit 2): initialization stopped before its first project-visible publication;
  the project is unchanged;
- **suggested** (exit 3): the established collision sidecars were written and require operator
  action;
- **incomplete publication** (exit 4): project-visible publication began but did not complete;
  every remaining owned artifact is complete, no project-visible pathname is removed during
  unwind, and the output gives retry/verification guidance;
- **initialized** (exit 0): the complete store is visible. Because `.vouch/` is published last,
  a complete visible store is success-equivalent even if the process dies before returning.

This preserves D-7's “a refusal never leaves a half-initialized project” literally: exit 2 is
reserved for the pre-publication state. It supersedes D-19's broader “every failed init leaves
nothing” wording only after publication has started; the real adoption requirement is that no
failure leaves an unrecoverable project. Rejected: `stat`/`unlink` identity checks (still racy),
an advisory lock (a concurrent writer need not cooperate), a transaction marker or ownership
manifest (would expand D-7's closed path set), and deleting complete retryable remnants.

**D-20 amendment note — reusable-workflow event context (2026-08-19, DEV-ctb-01).** A called
GitHub Actions workflow retains the caller's event context, so a caller triggered by `push` has
`github.event_name == 'push'`, not `'workflow_call'`. The frozen pack/1 test incorrectly required
that comparison. D-20's decision is unchanged: the default-false `inputs.anchoring` value is the
call-only capability, while the caller and the called workflow both retain default-branch and
repository guards. Direct `push`/`pull_request` runs cannot set that input true.

## D-21 — External-adoption closure remains GitHub-first (2026-08-19)

**Status:** decided 2026-08-19 (user: proceed with the reproduced findings; defer a second CI
adapter). The core remains forge-neutral, but GitHub Actions is the only adoption adapter for the
first release. A public provider interface with one adapter would be speculative; portability is
revisited only when a second provider is commissioned. The GitHub template may be made more local
internally, but no generic adapter contract is added merely in anticipation.

- **Published candidate validity.** Anchor planning runs the full Vouch semantic checks on the
  post-`advance-anchor`, post-`generate` candidate before it is committed or transported:
  `check --ci` without the pending-anchor exception, then `formal`. Publisher structure checks
  remain necessary but are not semantic proof. A push authenticated by `GITHUB_TOKEN` is not
  assumed to trigger another workflow. The first-release policy is an explicitly documented,
  scoped ruleset/bypass for the already validated `.vouch/**` bot commit; an organization that
  requires independent forge checks on that SHA must replace publication credentials with a
  GitHub App token. Vouch does not claim that either organizational policy is mechanically
  verified.
- **Coherent initialization.** `standard` and `high` stores are not created with
  `anchor_mode=none`. They require at least one `--test-job`, initialize CI anchoring as a
  recorded store fact, and print the same-workflow caller. Without that gate, initialization
  refuses before publication with an exact remediation. `lightweight` may remain validation-only
  when no test job is supplied; supplying a gate initializes CI anchoring there too. No separate
  `--anchor-mode` knob is added: it would allow combinations the generated adoption path cannot
  honestly support.
- **GitHub concurrency.** Generated and dogfood workflows use a Vouch-specific repository/ref
  concurrency namespace rather than the caller-derived `${{ github.workflow }}` key. Remote-head
  comparison remains the stale-candidate correctness mechanism.
- **Evidence execution safety.** The rendered command is validated against the authoritative
  evidence invariant before the child starts. A repeatable, explicit argument-position redaction
  option replaces selected stored arguments with a visible `<redacted>` marker; the executable
  itself cannot be redacted and no digest of the secret is retained. Environment-based secret
  injection remains the preferred documented route. The warning and claim boundary stay honest.
- **Assurance and operations.** D-1 is enforced by the actual Pyrefly strict preset. Git conflict
  recovery gains a Git-index-stage adapter around the existing semantic merge, not a second merge
  algorithm. D-19's `release@1` supplies MIT metadata, version identity, reproducible lock release
  metadata/provenance, and a recoverable lock/config/workflow update plus rollback procedure;
  `adoption-docs@1` then supplies the durable integration and compatibility guide.

**Order:** `ci-candidate-validity@1`, `evidence-command-safety@1`, `strict-types@1`,
`git-merge-recovery@1`, `release@1`, `adoption-docs@1`, then the already-open real GitHub
adoption probe. Each is its own D-11 unit and is published before the next pack is frozen. A
second provider adapter is outside this batch.

## D-22 — CI simulations are hermetic; shipped GitHub actions use Node 24 (2026-08-21)

**Status:** decided 2026-08-21 (user: repair the CI failure and its Node 20 warnings together).

- **Simulated locality is explicit.** A repository test that claims to exercise a local anchor
  clears every CI-provider run-identity variable recognized by Vouch before it invokes the public
  CLI. Production `ci_run_id()` and `advance-anchor` behavior do not change: when a real CI run
  identity is present, recording it remains the intended D-18 claim. Tests that intentionally
  exercise a CI-observed anchor continue to set an explicit synthetic run identity.
- **Both workflow surfaces move together.** The generated adopter workflow and mk2's checked-in
  dogfood workflow use reviewed, full-commit action identities. Updating only mk2's instance would
  violate D-19's standing generated-artifact rule; updating only the generator would leave the
  dogfood path on a deprecated runtime.
- **Node 24 action set.** The reviewed identities are
  `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1),
  `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` (v9.0.0),
  `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (v7.0.1), and
  `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` (v8.0.1).
  Their published manifests declare `node24`. The existing
  `actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1` (v6) identity already
  declares `node24` and stays pinned.
- **No silent cache-policy change.** setup-uv v9 changed the default for `prune-cache` from true to
  false. mk2 records `prune-cache: true` explicitly so this dependency refresh does not alter the
  existing cache-retention choice.
- **Compatibility boundary.** The generated adapter remains GitHub-hosted (`ubuntu-latest`) and
  therefore has a Node-24-capable runner. A future self-hosted-runner option must require Actions
  Runner 2.327.1 or newer; this repair does not add that option or a second CI adapter.

The repair is split into `ci-test-hermeticity@1` and `ci-action-node24@1`: one preserves the
meaning of the run-identity claim while making the test harness deterministic, and the other owns
the externally executed workflow dependencies. Local acceptance proves the exact workflow bytes
and synthetic-environment behavior; a real GitHub run remains the final check that the warning is
absent and the candidate transport still works.

## D-23 — Projection anchor claims follow the authoritative unit-ref order (2026-08-21)

**Status:** decided 2026-08-21 after GitHub Actions run `32468660968` exposed the defect while
closing D-22.

- **One canonical identity order.** Evidence heads and every projection derived from them use the
  core's existing `(unit_name, unit_version)` order. A rendered `name@version` label is
  presentation, not an alternative ordering key: lexicographic label order disagrees when one
  name is a prefix of another, such as `ci-anchor` and `ci-anchor-closure`.
- **Strict projection integrity remains.** `map.status_facts.anchor_run_claims` must still equal
  the map's evidence-head claims exactly and canonically. The decoder does not become
  order-insensitive and does not repair malformed artifacts; generation emits the one accepted
  order and tampered claims remain refused.
- **Narrow repair.** Align the status-facts projection with the already-authoritative evidence-head
  order. Do not change evidence events, anchor semantics, the generated workflow, or the external
  meaning of a run-ID claim.
- **External closure.** Local round-trip acceptance is necessary but not sufficient. The same
  GitHub candidate-planning path that found the defect must advance the pending heads, validate and
  transfer the candidate, and complete the write-only publisher before D-22 and this repair can be
  published.

The repair is `projection-anchor-ordering@1`, a separate D-11 unit discovered during final
verification of the two D-22 units.

## D-24 — A real-project pilot gates the first release (2026-08-21)

**Status:** decided 2026-08-21 (user: release remains paused until Vouch has governed a real
product change outside Vouch itself).

- **Corrected order.** The first-release path is `GitHub technical adoption probe` →
  `real-project pilot` → `release@1` → `adoption-docs@1`. This supersedes D-21's closing order,
  which placed the GitHub probe after release and documentation, and resolves its conflict with
  D-20's requirement that the probe precede B4/B5.
- **The probe is necessary but not adoption.** `vouch_mk2-b6g.9` exercises the exact generated
  workflow in a scratch GitHub repository: project-test gating, first-run GENESIS anchoring,
  pull-request privilege separation, scoped publication, stale/concurrent refusal, and an
  idempotent rerun. Passing it validates the delivery adapter's real forge behavior; it does not
  show that a project has used Vouch to deliver product behavior.
- **The pilot is a real governed change.** Reactivated `vouch_mk2-bd4.4` requires a project that
  is not Vouch itself to complete at least one genuine product change through specify, frozen
  acceptance, red/green evidence, done, CI anchoring, and publication. Merely installing Vouch,
  initializing a store, or running the scratch probe does not satisfy the pilot. The pilot records
  operator friction, recovery experience, the standing stop-rule result, and an explicit release
  go/no-go decision. Any Vouch behavior change discovered by the pilot becomes its own frozen D-11
  unit rather than silently expanding the pilot.
- **Bootstrap without circularity.** Because `release@1` is deliberately gated by the pilot, the
  probe and pilot use a reproducibly built `vouch.lock` tied to an exact reviewed source revision
  and recorded digest. They do not require a release artifact whose production they are meant to
  authorize. `release@1` begins only after the pilot records a go decision.
- **The 0.6 half remains withdrawn.** No real 0.6 ledger ever existed, so D-8's addendum still
  governs migration: synthetic refusal/round-trip fixtures remain the available evidence, and a
  real 0.6 migration is revisited only if such a ledger independently appears. It is not part of
  `bd4.4` or a first-release prerequisite.

The GitHub-first scope from D-21 is unchanged; a second delivery adapter remains parked.

## D-25 — Semantic merge replays open-in-CI exceptions by exact unit reference (2026-10-13)

**Status:** decided 2026-10-13 (user: Vouch defects found by the activity_tracker_portal pilot are
fixed in Vouch, as their own frozen units, before the pilot works around them).

- **The finding.** The first divergent `.vouch` merge in the pilot (`bd4.4`) was refused:
  `event OpenInCiMarked name='operational-store@1' … was refused by decide(): unit.unknown`. Since
  the exact-reference fix, `OpenInCiMarked` and `OpenInCiCleared` record a `NAME@VERSION` label,
  but semantic merge recovers their commands by passing that label as the bare unit name. Every
  divergent merge in which the incoming branch marked or cleared a strict-CI exception is
  therefore refused, by both `merge --other` and `merge-git-stages`.
- **Replay by name, then require the same reference.** The recovered command resolves the unit
  name parsed from the recorded label. The replay is accepted only when the decision's event
  carries exactly the recorded label. If the current unit with that name now has another version
  (the incoming exception was recorded for `X@1` and ours has superseded it with `X@2`), the merge
  is refused as `merge.conflict`, naming both references. An exception is never silently
  transferred to another version, and nothing is written.
- **Narrow repair.** Do not change the event encoding, the `CiException` field, the strict CI
  policy, `decide()` for these commands, or how other events are replayed. A bare name from the
  short-lived pre-fix encoder cannot match an exact reference, so replaying one is refused as
  `merge.conflict`. Such events exist only in history that predates the fix (none in mk2's own
  store), so a merge never needs to replay one.
- **Observation, not decided here.** Lifecycle events such as `UnitMarkedReady` record a bare
  name, so their replay targets whichever version is current. Whether replay should also refuse
  when the current version differs from the source branch's is a separate question
  (`vouch_mk2-bd4.4` follow-up), not part of this unit.

The repair is `merge-open-in-ci-replay@1`, the first D-11 unit discovered by the D-24 pilot.

## D-26 — Anchor advances name the exact unit reference (2026-10-30)

**Status:** decided 2026-10-30 by the user (Operator), after pilot finding 4
(`vouch_mk2-bd4.4.7`): the design below was chosen by the user; it is recorded here, not re-asked.

- **The finding.** The activity_tracker_portal pilot rebound the published
  `manage-sender-enrollment@1` (`rebind-pack` plus a fresh GREEN), then specified, froze and
  GREENed its successor `manage-sender-enrollment@2`, and merged both in one step. Main's store
  then held two evidence heads named `manage-sender-enrollment`, both without a run claim:
  version 1 at head 5 and version 2 at head 2. The generated CI anchor step loops over unclaimed
  heads by `unit_name` and runs `advance-anchor --unit-name <name>`. `AdvanceAnchor` and
  `AnchorAdvanced` carry only the name, and both `_decide_advance_anchor` and the fold resolve
  `snapshot.evidence_head(unit_name)`, the CURRENT version's head. Both iterations claimed
  version 2; version 1's head could never be claimed; `check.evidence-anchors` stayed
  `NOT-CHECKED` ("anchor has no workflow run claim"), composed `NOT-CHECKED`, and the anchor
  plan job exited 1 without committing (Project CI run 41036402336). Nothing on the pilot's
  main could be anchored, so nothing could reach acceptance.
- **Advances name the reference.** `AdvanceAnchor` and `AnchorAdvanced` name the unit
  reference: `unit_name` and `unit_version`. The decision and the fold resolve the head by
  reference (`Snapshot.evidence_head_ref`), so a superseded version's head can be claimed. The
  `advance-anchor` verb gains an optional `--unit-version`; without it the command means the
  version that is current for that name, exactly as before, so every existing caller and every
  existing store behaves as it did. The event an accepted advance records always carries the
  resolved exact version. D-4 is untouched: the event still carries no target; the fold still
  derives the anchored digest from the named head.
- **The generated CI step claims each head.** The anchor step emits `(unit_name, unit_version)`
  for every head without a run claim and runs `advance-anchor --unit-name NAME --unit-version
  VERSION` for each pair. mk2's own `ci.yml` does the same.
- **Stores keep replaying; no invented state.** An `AnchorAdvanced` record written before this
  unit has no `unit_version`. The decoder accepts that shape and the fold keeps its recorded
  meaning: the version current for that name at that point of the replay, which is the version
  that was current when it was recorded. The event log is content-addressed and is never
  rewritten; the snapshot format does not change (`EvidenceHead` already carries
  `unit_version`). A record that carries an empty `unit_version` is refused by the decoder: no
  encoder of this unit writes one, so it cannot be legacy and cannot be exact.
- **Everything that names the advance follows.** Semantic merge recovers `AdvanceAnchor` with
  the recorded version. The next-actions projection proposes an advance for every lagging head,
  including a superseded version's, and names the exact reference. The formal relation
  witnesses claiming a superseded version's head in `history-v1` (a seeded unanchored head for
  `u@v1`, one exact-reference command); `core-v1` and `tier-v1` do not change, so the 400k
  state bound is not approached. Every count change is explained in the DONE file.
- **Rejected.** Re-encoding history (forbidden by D-16); a decoder-side default that guesses a
  version (D-13's "no invented state"); and one advance that claims every head of the name (it
  hides which reference a run observed, and an event that claims several chains is
  unrepresentable under D-4's payload-free promotion).
- **Not decided here.** Finding 2 (`bd4.4.4`: heads whose anchor lags their head after a
  claim) and finding 3 (`bd4.4.6`: merge replay dropping a run claim) stay separate units.

The repair is `anchor-unit-ref@1`, the fourth D-11 unit discovered by the D-24 pilot.

## D-27 — The working method's roles move to the current models (2026-10-30)

**Status:** decided 2026-10-30 by the user: "You act as the planner, pack author, and
publisher, implementer will be Sol, then reviewers will be Astra."

- **The roles.** The acceptance owner and planner is Claude (Opus 5.5, the main session): plan,
  beads, decision records, the pack, adjudication of deviations, the final pass, publish, commit.
  The implementer is GPT 6.1 Sol through a T3 `delegate_task` (provider `codex`, model
  `gpt-6.1-sol`, reasoning effort high). The reviewer is GPT 6 Astra through a T3 `delegate_task`
  (provider `codex`, model `gpt-6-astra`); it replaces both the Codex mechanical review and the
  Opus adversarial review. `docs/method.md`, `CLAUDE.md` and `AGENTS.md` say so. The separation
  is unchanged: the planner never implements, the implementer never edits a pack, and the
  reviewer is neither.
- **Why.** The method named an earlier model list: Fable planning, gpt-5.6-sol through a
  detached `codex exec`, and Opus reviewing adversarially. The D-24 pilot already runs Sol and
  Astra as implementer and approver through T3, and mk2's own units now use the same agents.
- **No detached launches.** On 2026-10-30 the planner of `anchor-unit-ref@1` was replaced
  mid-unit, and the `nohup codex exec` (gpt-5.6-sol) it had started kept editing the tree after
  the planner was cancelled. The user had it stopped. Its uncommitted diff was set aside
  unreviewed, and Sol implements the unit from the frozen pack. Implementation and review now run
  only as delegated tasks, which are cancelled with the task, never as detached processes.
- **The hand-over of `anchor-unit-ref@1`.** Its pack (`anchor-unit-ref-pack/1@b2cff3363106a90f`),
  RED #1 and D-26 were written by the previous planner and stand unchanged. Claude reviewed the
  frozen pack against the tree before implementation and owns it from here; for example, unit
  identifiers cannot contain `@` (`valid_unit_identifier`), so the anchor step's `NAME@VERSION`
  split is exact. The pack's header still names the previous roles; it is frozen, so this record
  is the hand-over. A duplicate draft was withdrawn: a second D-26 entry naming
  `anchor-by-unit-ref@1`, and beads `bd4.4.9` and `bd4.4.9.1`.
- **Unchanged.** The lifecycle, the gates, the deviation procedure, and the user's role as
  decider: D-numbered decisions, contract conflicts, `git push`, `bd sync`.

## D-28 — Rewording test-bound docs is exception (ii); the one claim correction is a unit (2026-11-02)

**Status:** decided 2026-11-02 by the user, choosing option 2 of three: a ruling alone, a ruling
plus a unit for the one claim correction, or a full unit for the whole rewrite.

- **The ruling.** D-11 exception (ii) covers a rewording of a test-bound document when it adds,
  removes and changes no statement about Vouch's behavior, guarantees, limits or non-claims,
  keeps every test-pinned phrase, and passes the full suite. Statements may move between
  documents. An independent reviewer checks claim preservation before the commit. A sentence
  whose meaning must change is not rewording; it goes through a unit.
- **Why.** The 2026-11-02 plain-prose rewrite of `README.md`, `docs/quickstart.md`,
  `docs/claim-boundary.md`, `docs/threat-model.md` and `docs/performance.md`
  (`vouch_mk2-c6i`) was much larger than the typo-level edits exception (ii) was written for.
  Running it as a unit afterwards would have recorded a freeze after the implementation it
  governs, with the planner as implementer; the method forbids both. So the ruling covers the
  rewording, and the one real claim change is split out.
- **The unit `quickstart-formal-claim@1`** (`vouch_mk2-4zh.1`). The quickstart says that at
  `high`, `check` "reports `NOT_CHECKED` for invariant execution and mutation-witness
  discrimination". The code reports one row, `check.formal-certificate`: `PASS` for the narrowed
  obligation at every tier where a certificate is recorded, with the unexplored properties named
  in its detail (`src/vouch_mk2/verify.py`). The claim boundary already says so. The rewording
  commit keeps the original sentence verbatim, and the unit's pack binds the quickstart to the
  verdict the code produces: R1, the quickstart names `check.formal-certificate` with `PASS`,
  and the code yields `PASS` at every tier with the residual in the detail; R2, no quickstart
  sentence attaches a `NOT_CHECKED` verdict to formal exploration; R3, certificate verification
  is not limited to `high`; R4 (guard), the quickstart still shows `formal --record`.
- **Unchanged.** D-11's other exceptions and the stop rule.

## D-29 — The decision log is append-only within each entry, and a digest guard enforces it (2026-11-12)

**Status:** decided 2026-11-12 by the user, choosing "append within an entry" of three rules: append
within an entry, strict (every amendment is a new entry), or a grace window that freezes an entry
when its first implementing unit publishes. Unit `decisions-append-only@1`.

- **The gap.** The header said "Append-only; a reversed decision gets a new entry, never an edit",
  and nothing checked it. Git shows seven rewrites of existing entry text: D-8 (`0ab81e2`,
  `2d44410`), D-10 (`2b60a56`, `2d44410`), D-12 (`2d44410`, `7efb526`), D-18 (`26bc697`, a note
  inserted mid-entry) and D-26 (`8d7ed57`, a withdrawn duplicate draft). D-10's is a reversal made
  by editing: its deferral of self-hosting was rewritten in place when D-11 was proposed. Six other
  changes only appended text, and the D-14 and D-17 amendments were written as dated notes later in
  the file. The text those rewrites replaced survives only in git history; this entry records them
  instead of restoring it.
- **The rule.** Text already in the log never changes. An amendment, correction or note is appended
  to the end of the entry it amends, opened by a bold label naming its date and source, the form
  the D-14 and D-17 amendments already use. A reversed decision is a new entry. The header was
  rewritten once, in the same change as this entry and before the first pin, to state this rule.
- **The guard.** `scripts/decisions_guard.py` splits the log into the header and entries `D-1` to
  `D-N`, and `DECISIONS.pins.json` records a SHA-256 digest of each section's pinned bytes.
  `verify`, run by the test suite, fails when pinned bytes change, when an entry is missing, out of
  order or misnumbered, or when text is not yet pinned. `pin` extends the pins to the current text
  and refuses, writing nothing, if any pinned text has changed. 0.6 used the same technique for its
  defect log (a digest-frozen D001–D049 prefix, 2026-08-07).
- **What it does not claim.** The guard catches accidental and unreviewed edits. Someone who edits
  an entry and hand-edits its digest in the pins file gets past it; that edit is visible only in
  the diff, and reviewing the diff is the control. This is the same boundary as 0.6's
  "repository-local checks cover accidental drift".
- **Rejected.** Strict new-entry-only amendments: they separate an amendment from what it amends,
  and history shows amendments kept next to their entry. A grace window: the guard would need to
  know which unit implements which entry, which nothing records.

**D-29 note (2026-11-12, unit `decisions-append-only@1`):** As built (pack/3), a heading inside
top-level fenced code is content of the entry that quotes it, an unclosed fence is a problem, and
`pin` locks the directory that holds the pins file, because an editor's atomic save gives the log a
new inode. The guard does not render Markdown. A heading-like line deliberately written so that the
guard and a renderer disagree can create a section boundary inside an entry; like a hand-edited pins
file, that is visible only in the diff and review is the control. An unsupported container, such as a
fence inside a list item or block quote, can also make the guard refuse; that fails closed. See the
pack's **Limits** section.

## D-30 — The optimal-system design is the accepted direction after the first release, not current work (2026-11-12)

**Status:** decided 2026-11-12 by the user.

- **What exists.** On 2026-08-20 and 08-21 (bead `vouch_mk2-pv4`, nine grilling rounds in a Codex
  thread), the user accepted a target design: `docs/design/vouch-optimal-system.md` with its
  diagrams `vouch-system-diagram.html` and `vouch-mk2-system-diagram.html`, ADRs `0001`–`0004` in
  `docs/adr/`, and an adoption glossary in `CONTEXT.md`. None of it was committed until 2026-11-12
  (`1be2af4`), when the design documents also moved here from the parent `project_blueprint/` folder. It adds a separate adoption module and record beside the assurance kernel,
  repository-local operation with no telemetry, distinct greenfield and brownfield adoption paths,
  and sound fail-closed incremental verification. Until this entry, no decision referred to it.
- **Decision.** It is the accepted direction for work after `release@1`. It does not change D-24's
  order (GitHub probe, real-project pilot, `release@1`, `adoption-docs@1`). Nothing in it describes
  current behaviour, and its own status labels ("Target", "External proof pending") still apply.
  Each capability enters only through its own `D-n` entry and unit, citing the ADR it implements.
  The ADRs record the design's reasoning; this log stays the only binding decision record.
- **Q90.** Its answer on purity and unit kinds (`docs/design/vouch-optimal-system.md` §8) is superseded by D-31.
  The rest of §8, one primary scope per path and propagation through dependency contracts, stands as
  part of the accepted direction.
- **Revisit** when the pilot records its release go/no-go (D-24).
- **Rejected.** Building it now: the pilot has not yet shown Vouch governing a real change, and the
  design itself says adoption claims need external proof. Dropping it: it is the only worked answer
  to the adoption weakness this log keeps returning to.

## D-31 — Every frozen pack declares its unit's effects; Q90 is superseded (2026-11-12)

**Status:** decided 2026-11-12 by the user, after independent opinions from Fable, Sol and Astra.
Unit `effect-declarations@1` (bead `vouch_mk2-bd4.8`), before `release@1`.

- **The gap.** The blueprint (v2, 2026-08-01) and Vouch 0.6 (`kernel.md` rule 1, the unit-definition
  schema's required `class`) made every unit exactly one of transform, observation or action, with
  its effects disclosed. mk2's plan said the three kinds "carry over unchanged"
  (`IMPLEMENTATION-PLAN.md` §1), but the mk2 `Unit` has no kind, and D-8 dropped `class` as a concern
  "outside the mk2 core snapshot" that nothing else picked up. The optimal-system design's Q90
  (2026-08-21) then said Vouch should not treat pure and effectful as universal unit types; it was
  accepted in a batch and was not presented as a reversal. The tier report meanwhile prints
  "Typed contracts, declared classes, purity checks" as PARTIAL, citing mk2's own pyrefly and
  import-linter, with the residual "declared classes are human reviewed", although nothing can be
  declared. The real-project pilot kept the principle on its own: each unit contract under
  `activity_tracker_portal/governance/scopes/` lists its `observable_effects`.
- **Decision.** Every frozen acceptance pack carries a required effect declaration, byte-bound with
  the pack. It lists the effects of the behaviour the unit introduces or changes. Each effect is an
  **observation**, which names the one external source it reads and captures what it reads as an
  immutable artifact, or an **action**, which names the one external system it changes and whether
  the change is confirmed, compensated or declared irreversible. An empty list declares no effects:
  the unit's behaviour is a **transform**. A unit may declare several effects. This keeps the three
  kinds and their disclosures and replaces the earlier rule that a unit is exactly one kind.
- **What Vouch checks.** Mechanically: the declaration is present, strictly well-formed, consistent
  with each kind's disclosures, and bound into the frozen pack. `NOT_CHECKED`: that the declaration is
  true or complete, that a transform has no undeclared effect, that a compensation works. A project
  may bind its own effect checker (an import contract, a lint rule, a type-level effect system); Vouch
  records the run as evidence and reports "checker X passed rule Y over scope Z", never "pure".
  Vouch does not infer purity; that part of Q90 stands.
- **Existing units.** No upgrade invents a declaration (D-13). Units frozen before the change report
  "effects not declared"; a declaration arrives only with a new pack version or an explicit recorded
  amendment, never by inference from names or code layout.
- **mk2's own units.** A self-hosted unit is a change, so it declares the effects of the behaviour it
  adds or modifies. A change with no runtime behaviour, such as a docs or process change, declares an
  empty list, and that claim is reviewed, not checked.
- **The tier row** is corrected in the same unit: it stops citing mk2's own tools as evidence about a
  governed project, and its residual names the effect declaration.
- **Sequencing.** The declaration, any state-format change and the tier-row correction land before
  `release@1`; D-24's order is otherwise unchanged. Binding a project's effect checker as evidence may
  follow the release.
- **Rejected.** A single `kind` field on the unit (Fable's recommendation): the pilot's
  `send-outbox-message` declares three effects, so one kind would force splitting units or hiding
  effects. Optional, project-level effect contracts only (Astra's recommendation): an optional
  declaration is how the principle was lost. Keeping Q90 as written: it answered inference, not
  declaration.
- **Note (2026-11-13): mk2's own store opted in.** The user approved `require-effects` on mk2's
  dogfood store, after a trial on a scratch copy (`check --ci` exit 0). From revision 922 a freeze or
  late binding of pack bytes must carry a declaration. Opting in does not reach rebinds: a rebind
  keeps the declaration status it inherits and refuses to change it (`rebind.effects-changed`), so
  the 33 units bound before it rebind without a declaration and gain one only through an amendment
  and re-freeze. Label-only freezes stay allowed. Fable, Sol and Astra each pointed this out after
  the planner had described the switch as covering every rebind.

- **Note (2026-11-15): what counts as an effect for mk2's own units.** The boundary is the one
  mk2's import contracts already draw: `ir`, `core`, `checks`, `formal` and `projections` may not
  import I/O or the store. A unit that changes only code inside that boundary declares an empty
  list. A unit that adds or changes what the store layer, the CLI, git, a spawned process or the
  network reads or writes declares each such observation or action, even when the input or output
  already existed, since this entry covers behaviour a unit introduces or changes. A store counts
  as an external system: the store a command writes is usually a user's, not mk2's own. The user
  chose this convention (Fable's proposal) over treating the state Vouch manages as inside the
  boundary (Sol's view, and Astra's on conditions), which would have let most store and CLI units
  declare nothing. `merge-replay-provenance@1` (D-33) is the first unit it applies to.

## D-32 — Units land on `main` through pull requests; freezing happens on a branch (2026-11-13)

**Status:** decided 2026-11-13 by the user, after opinions from Fable, Sol and Astra on the red
run that every unit's frozen-pack commit put on `main`.

- **The problem.** `docs/method.md` §1 step 6 committed the frozen pack and RED to `main`, and
  "CI stays red until GREEN". That red shows less than it seems. The gates job stops at its first
  failing step, usually the type check on names the frozen tests use, so the later gates never ran
  on that commit and an unrelated regression would look the same as the expected RED (Astra, Fable).
  It is also not evidence: `anchor_plan` needs a green `gates` job, so RED and GREEN were always
  anchored together by the GREEN run (Fable).
- **Decision.** Each unit runs on a branch `unit/<unit>-<v>` cut from the latest `main`, and reaches
  `main` through a pull request that the user merges with a merge commit, not a squash, so the
  frozen-pack commit stays in `main`'s history ahead of the implementation. After the merge, CI on
  `main` anchors the evidence as before, and the publish commit follows on a short
  `publish/<unit>-<v>` branch and pull request. Anchoring is unchanged: it still runs only on a green
  push to the default branch (Sol confirmed the existing workflow needs no change for this).
- **What a pull request's CI shows.** Only the gates job runs on a pull request; the dogfood check
  and the lock walkthrough run after the merge, so the planner runs them locally before asking for
  the merge (§6 step 3).
- **Merging `.vouch/`.** GitHub cannot merge the store. If `main` moved while a branch was open, the
  planner merges `main` into the branch locally and recovers any `.vouch/` conflict with
  `merge-git-stages`. One unit in flight at a time keeps this rare.
- **Rejected.** Keeping the red run on `main` with only an explanatory sentence (Fable's
  recommendation; the sentence is in §1 step 6). Teaching CI to treat a recorded RED as an expected
  failure (all three advisers): a recorded RED cannot show that every failure in a checkout is
  expected. A CI-witnessed RED, where the gates require everything outside the in-progress pack to
  pass and that pack's tests to fail, stays a possible later unit (Fable).
- **Note (2026-11-15): integrate on a branch cut from `main`.** Merging `main` into a unit branch
  makes `main`'s records the replayed side of the store merge, so they are renumbered after the
  branch's records and lose their claims, and a revision cited from `main` (such as D-31's
  "revision 922") stops naming the same record. When `main` moved while a branch was open, the
  planner instead cuts `integrate/<unit>-<v>` from the latest `main`, `git merge`s the unit branch
  into it, recovers `.vouch/` with `merge-git-stages` there, and opens the pull request from that
  branch. `main`'s chain stays a prefix and only the unit's records are replayed. This replaces
  "merges `main` into the branch locally" above; Fable proposed it while reviewing D-33. If the
  store merge is refused, D-33's recovery applies.

## D-33 — A merge replays each command onto what its source acted on, keeping the source's claims (2026-11-15)

**Status:** decided 2026-11-15 by the user, after Fable, Sol and Astra reviewed a first draft
(Astra: rework; Fable and Sol: approve with changes; this text takes their changes). Settles
`vouch_mk2-bd4.4.2` (decision) and `vouch_mk2-bd4.4.6` (pilot finding 3) in one unit,
`merge-replay-provenance@1`.

- **The findings.** A divergent merge recovers each incoming command from its recorded event and
  runs it through `decide()` against ours; a fast-forward copies the source records byte for byte and
  is not affected. Most lifecycle events carry a bare unit name, so replay acts on whichever version
  is current on ours. If ours superseded `X@1` with `X@2` while theirs froze, readied, finished or
  published `X@1`, those events can land on `X@2` when it is in a state that accepts them, including
  `X@1`'s pack and, since D-31, its effect declaration. Companion facts are recomputed on ours, so an
  amendment that invalidated `X@1` on theirs can invalidate `X@2` (reproduced by Sol). Release
  preparation records exact references, but the recovered command passes names. A legacy anchor
  advance without a version resolves to ours' current version, not the one D-26 says it meant. And
  every replayed record is re-enveloped with the merge's actor, run id and time, so a replayed CI
  anchor loses its run claim (`bd4.4.6`) and the next CI run re-anchors it under a later run.
- **Identity.** For each replayed command, and for each unit-bearing companion fact of its source
  batch (`UnitBlocked`, `UnitInvalidated`, `UnitReleaseBound`), ours must resolve the same unit
  reference the source did. The source reference is: for a fact that records only a name, the version
  current for that name in the source's state immediately before that fact; for a fact that records
  an exact reference, that reference; for a creation (`UnitSpecified`), the reference it creates.
  Ours is resolved step by step during the replay, since the incoming sequence may itself supersede
  and specify. Release preparation's regenerated exact roster must equal the recorded one. A legacy
  anchor advance is recovered as an exact-reference command using the source-derived version. D-25's
  refusal of bare-name legacy CI exceptions is unchanged. Identity covers the targets of operations
  and companion facts; it does not version-pin by-name dependencies or gate lookups.
- **Anchors.** An anchor advance also requires that ours' evidence head for its exact reference is
  the digest the source promoted, immediately before the advance; an ancestor of it is not enough. A
  superseded version's head can still be claimed (D-26). This refuses merging a CI anchor into a
  branch that has since added evidence for the same reference. That is deliberate: replaying it would
  claim the source run for evidence the run never saw.
- **Claims.** A replayed fact that corresponds to a source fact keeps that source record's actor, run
  claim (an empty claim stays empty) and recorded time. Correspondence is by event kind and subject,
  never by position. A companion that `decide()` generates on ours with no source counterpart carries
  the merge's claims; a source companion with no replayed counterpart produces no record. An incoming
  `MergeRecorded` keeps its source claims; the record of this merge carries the merge's own. Chain
  links, revisions and content digests are rebuilt, and `decide()` recomputes derived fields such as
  gate records and evidence-head sequence numbers. Recorded times are therefore not monotonic along a
  merged chain; nothing orders them (the codec requires only a non-empty string).
- **Source context.** The source branch's full verified history, folded record by record, including
  pre-log baselines and records that provenance filtering leaves out of the replayed suffix. Command
  batch boundaries come from the source chain before filtering.
- **Refusal.** `merge.conflict` names the event, its source revision and both references, and for an
  anchor both head digests. Nothing is written and no lock is created.
- **No format change.** The event encoding, the event log and the snapshot format are unchanged, and
  no history is rewritten; existing stores, including the pilot's, merge as they are. This repair
  cannot show that an earlier merge never misattributed an event.
- **Recovery after a refusal.** The usual causes are that one branch superseded a version the other
  kept working on, or added evidence for a unit the other anchored. Try the other direction. Under
  D-32 the planner integrates on a branch cut from `main` (D-32 note of 2026-11-15), so the other
  direction is to merge `main` into the unit branch with `merge-git-stages` and open the pull request
  from the unit branch. This may give an honest order; it is not guaranteed to. A refusal in both
  directions means this replay cannot reconcile the histories automatically, not that the work is
  incompatible. Where the other direction leaves a claimed anchor lagging its head, anchoring it
  waits on `bd4.4.4`.
- **Scope.** This preserves unit-version identity, not acceptance-pack epoch identity within one
  version: same-version divergence stays with the existing `decide()` checks, which refuse pack-version
  reuse and stale rebinds, but another lifecycle command can still replay under a different pack of
  the same version (reproduced by Sol). It does not repair nested `MergeRecorded` block boundaries
  (`bd4.4.11`), so some existing merge topologies can still be refused.
- **Effects.** Under the D-31 note of 2026-11-15, the unit changes what merge reads from the source
  store and writes to the target store. It declares one observation, the source store's event chain,
  captured as the verified head the merge record names (`theirs_head`), and one action on the target
  store, confirmed by one atomic commit, with nothing written on refusal.
- **Rejected.** Re-mapping onto a matching active version (option B): D-9 allows one current version
  per name, so it reduces to refusal. Keeping by-name replay and documenting it (option C): contradicts
  "refuses, never guesses". Matching companions by position or requiring identical batch lengths:
  wrongly refuses legitimate differences, such as a redundant block another deviation already caused,
  or an amendment whose impact on ours reaches a dependent theirs did not have. Replaying anchors
  without their claim, as now: honest, but erases provenance and makes merge direction matter.
  **Deferred, not rejected:** exact references in every new event, at a later format bump (D-30); this
  derivation stays for history.
- **Note (2026-11-15): the docs' provenance sentence is narrowed to this decision.** As published,
  the R16 sentence in `docs/quickstart.md` and `docs/claim-boundary.md` said every replayed record
  keeps its source claims. Astra's claim check found that it omitted this decision's exceptions: a
  companion the replay generates with no matching source record carries the merge's claims, and a
  source companion the replay no longer produces is not written (R7, R12 test both). Both sections
  now say so, keeping R16's pinned phrases. The behaviour and its tests are unchanged, so this is a
  correction of the docs to this entry, reviewed under D-28, not a new unit.

## D-34 — An anchor that lags its evidence head is not checked, and CI advances it (2026-11-15)

**Status:** decided 2026-11-15 by the user. Settles `vouch_mk2-bd4.4.4` (pilot finding 2) and
`vouch_mk2-bd4.4.10` in one unit, `anchor-currency@1`.

- **The finding.** The anchor loop in mk2's `ci.yml` and in the workflow `init` generates advances
  only evidence heads whose `anchor_run_id` is empty. Once CI has claimed an anchor, evidence
  recorded after it is never anchored. `check.evidence-anchors` returns PASS whenever the anchor is in
  the chain and carries a run claim, even when the head has moved past it, so that unanchored
  evidence is reported as anchored (the pilot, 2026-10-13: three refactors after run 38297556382).
  mk2 avoided it only because the owner advances the anchor locally after every record. The check's
  messages also name a head by its unit name alone, so two versions' heads read as duplicates
  (`bd4.4.10`, pilot 2026-10-30).
- **Decision.** A head whose claimed anchor differs from its head is NOT-CHECKED, with a detail naming
  the exact reference and saying the anchor lags its head, the same verdict as a head still at
  GENESIS or a locally advanced anchor with no run claim. Dangling anchors and unverified chains stay
  FAIL. Both anchor loops advance every head that has no run claim or whose anchor differs from its
  head; `advance-anchor` already accepts that (an anchor is refused only when current and unclaimed
  by a run). Every message of the check names the head by `NAME@VERSION`.
- **Adopters.** Workflows already generated keep the old loop until they are regenerated or edited;
  the new check makes the gap visible as NOT-CHECKED instead of PASS. Versioned workflow upgrades
  are `vouch_mk2-b6g.14`.
- **Rejected.** FAIL for a lagging anchor: nothing is wrong yet, only waiting for CI, and every
  local `check --ci` after new evidence would fail. Fixing only the loops: the check would stay
  blind whenever a loop is skipped or broken, which is the false reassurance the pilot hit.

## D-35 — Unit names and versions may not begin with "-" (2026-11-15)

**Status:** decided 2026-11-15 by the user. Settles `vouch_mk2-dww` in one unit,
`option-safe-identifiers@1`.

- **The finding** (Astra, reviewing `anchor-unit-ref@1`). `valid_unit_identifier` accepts a leading
  `-`, but every CLI option reads a separate `-v1` token as an option, so `specify --name -x` and
  `advance-anchor --unit-version -v1` exit 2, and the commands `next` suggests for such a unit, in
  text and as structured argv, cannot run.
- **Decision.** `decide()` refuses a unit name or version that begins with `-`, with a refusal code
  that names the rule, for every command that introduces one. Neither mk2's store nor the pilot's
  has such an identifier (checked 2026-11-15), so no stored history is invalidated. Other
  identifiers (deviation ids, release names, pack versions) are out of scope; if the unit finds the
  same defect there, it is filed as a follow-up for its own decision.
- **Rejected.** Rendering every option as `--flag=value` and keeping the identifiers legal: every
  suggested invocation and JSON argv would change, frozen packs that pin the separate-token shape
  (`anchor-unit-ref@1` R8) would need rebinding, and hand-typed commands would still fail.

- **Note (2026-11-17, Astra's review of `option-safe-identifiers@1`).** Two other paths can bring
  in a dash-led unit, and the rule deliberately does not reach either. (1) A fast-forward merge
  (and `merge-git-stages`, which uses it) copies the source's records byte for byte, and a raw
  `Store.commit` writes events without `decide()`. Both carry recorded history, which no
  `decide()` rule re-judges (D-33). (2) Migration from a Vouch 0.6 ledger builds units directly,
  but no 0.6 project exists (D-11 note), so the user ruled it out of scope.

## D-36 — A re-freeze that changes the pack needs a fresh GREEN (2026-11-15)

**Status:** decided 2026-11-15 by the user. Settles `vouch_mk2-bd4.4.5` (pilot finding 3) in one
unit, `refreeze-green@1`. Amends D-17.

- **The finding.** D-17 (amended by O-1) binds evidence to the digest of the pack's executable
  entries. The pilot amended a pack by changing only files bound with `--pack-file`: a generator,
  41 JSON schemas and the vectors its bound tests load. The test-set digest did not change, so
  after the re-freeze `mark-done` accepted the earlier GREEN and `check --ci` passed, with no run
  against the amended vectors. The pilot re-recorded GREEN by hand.
- **Decision.** When a re-freeze changes the pack's inventory digest, `gate.dod.current-pack-green`
  also requires that the latest current-pack GREEN was recorded after that re-freeze. RED is
  unchanged: it stays keyed to the test-set digest, so a re-freeze that leaves the tests alone needs
  no new RED, as D-17's rationale requires. The gate applies to every later `mark-done` and
  `publish`; units already published are not re-judged. The unit chooses the mechanism, preferring
  no event or state format change; if the snapshot must record where the latest freeze sits in the
  evidence chain, that is a state-format bump under D-13.
- **Rejected.** Binding executed data files as test files and carrying `red_seen` forward when the
  implementation is unchanged: it changes D-17's digest rules and asks adopters to classify every
  data file. Accepting the gap as a residual: the pilot showed it lets an untested pack publish.

- **Note (2026-11-17, owner, writing `refreeze-green@1`).** `mark-done` has no gate; only
  `publish` (from `done-pending-acceptance` and from `re-verifying`) evaluates
  `gate.dod.current-pack-green`. "Every later `mark-done` and `publish`" above therefore means
  every later `publish`; `next` offers `publish` only when the gate would pass.

## D-37 — A merge skips an incoming clear of an exception ours already cleared (2026-11-15)

**Status:** decided 2026-11-15 by the user. Settles `vouch_mk2-bd4.4.3` in one unit,
`merge-redundant-clear@1`. Amends D-25 for merge replay only.

- **The finding** (`merge-open-in-ci-replay@1` review). Shared history holds an open-in-CI
  exception; ours clears it; theirs clears it and marks it again. Replay refuses theirs' clear with
  `ci-policy.exception-missing`, because `decide()` refuses a clear of an absent exception, so the
  merge fails although both sides agree the exception was cleared.
- **Decision.** During merge replay only, an incoming `OpenInCiCleared` whose exact unit reference
  has no exception on ours, and which D-25 and D-33 would otherwise replay onto the same reference,
  is satisfied: it writes no record, and the merge reports it as skipped. The rest of the incoming
  history replays as before, so theirs' later re-mark leaves the exception open. A clear whose unit
  reference differs is still refused (D-25, D-33). `decide()` is unchanged, so a direct
  `clear-open-in-ci` of an absent exception is still refused.
- **Rejected.** Accepting a redundant clear in `decide()`: it removes the error that catches a clear
  aimed at the wrong unit. Keeping the refusal and documenting a manual recovery: two branches that
  each closed the same exception could not merge.

## D-38 — A replayed merge record names its block's start on the receiving chain (2026-11-16)

**Status:** decided 2026-11-16 by the user, from Sol's design (`design-bd4.4.11`). Settles
`vouch_mk2-bd4.4.11` in one unit, `nested-merge-boundary@1`. Extends D-33.

- **The finding** (Sol and Fable, reviewing D-33; reproduced by Sol). A←B and C←A replay, then
  B←C refuses `merge.theirs-history-invalid`. An incoming `MergeRecorded` in a replayed suffix is
  replayed with its source `ours_head` and `theirs_head` unchanged, but replay re-envelopes the
  records its block bounded, so its `ours_head` names a digest that is not on the receiving chain.
  The provenance filter requires that boundary to be an earlier record of the chain it filters, and
  refuses.
- **Decision.** When merge replay writes an incoming `MergeRecorded`, its `ours_head` is the digest
  on the receiving chain at which its replayed block begins: the replayed counterpart of the source
  boundary record, or, where the provenance filter skipped that record, the staging head at that
  boundary. `theirs_head` keeps the source value, so a reverse merge still recognises the
  incorporated work. Claims are matched on the source fact before the translation (D-33). No event,
  log or snapshot format change. Stores that already hold an untranslated record keep their bytes; a
  merge that needs its boundary still refuses atomically, with a diagnostic naming the record. No
  boundary is guessed, no event is rewritten and no interval is widened to skip it.
- **Rejected.** A new local-boundary field beside both original heads: an event-format change for
  what the existing field can carry. Resolving the original boundary from source provenance on read:
  replay does not copy source event objects, so the boundary often exists in neither store.

- **Note (2026-11-18, Astra's review of `nested-merge-boundary@1`).** With the boundary rule in
  place, a merge can silently re-apply work a store already merged and then undid: B merges C's
  open-in-CI mark and clears it; D replays C's later history; B<-D replays D's copy of the old mark
  and restores the exception. B knows C's older head, D names C's newer one, and neither chain links
  them, so the provenance filter cannot tell D's copies from new work. Before D-38 the first nested
  merge in that chain was refused. The user paused the unit: ancestry that survives replay copies
  (`vouch_mk2-bd4.4.18`) is decided first, and both ship as one unit.

## D-39 — Rebind continuity is checked within a pack generation, from history (2026-11-16)

**Status:** decided 2026-11-16 by the user, from Sol's design (`design-0tc`). Settles
`vouch_mk2-0tc` in one unit, `rebind-generations@1`. Amends D-14's continuity invariant.

- **The finding** (Astra, effect-declarations@1 final check; reproduced by Sol). publish, rebind,
  amend, respecify, re-freeze with different bytes, publish, rebind: the second rebind is decided
  but the commit refuses with `rebind.history-disconnected`, because the snapshot invariant requires
  every rebind row's old inventory to equal the previous row's new inventory, across the re-freeze
  that legitimately broke the chain.
- **Decision.** A pack generation runs from a freeze to the next respecify. Each
  `AcceptancePackRebound` must name, as its old inventory and old test set, that unit's current
  values immediately before the event; that is validated when events are folded, when the event log
  is verified, before an event extension is committed, and on merge source chains. The unconditional
  adjacent-row comparison is removed from the snapshot invariant, except for pre-log baseline rows,
  which keep the contiguous-chain requirement because no event witnesses a generation break there.
  Every rebind row stays in the snapshot; D-31's effects equality, `rebind.stale-pack` and D-14's
  fresh GREEN after each rebind are unchanged. No event, log or snapshot format change. mk2's own
  store (34 published units, 33 rebind rows, no rebind after a respecify) keeps its verdicts.
- **Rejected.** Generation markers in the snapshot: a D-13 state-format upgrade for information the
  event log already holds. Clearing the rebind list at respecify: the snapshot would lose adaptations
  that evidence verification still reads.

## D-40 — Deviation ids, release ids, pack versions and amendment versions may not begin with "-" (2026-11-17)

**Status:** decided 2026-11-17 by the user. Settles `vouch_mk2-rkh` in one unit,
`option-safe-labels@1`, run after D-36 to D-39.

- **The finding** (Sol, implementing `option-safe-identifiers@1`). `decide()` accepts
  `OpenDeviation` with a deviation id `-d`, `PrepareRelease` with a release id `-r`,
  `FreezeAcceptance` with a pack version `-p` and `AmendDeviation` with an amendment version `-a`.
  Only the bound form (`--deviation-id=-d`) can deliver such a value, and every later command that
  passes it as a separate token exits 2. Deviation and release ids also appear as separate tokens
  in the commands `next` suggests, so Vouch would suggest commands that cannot run (the D-35
  defect).
- **Decision.** `decide()` refuses each of the four when it begins with `-`, with D-35's refusal
  code `identifier.leading-dash`, in the command that introduces it. With D-35 the rule is uniform:
  no identifier or label an operator gives Vouch may begin with `-`. D-35's boundaries carry over:
  history a store already holds, or adopts through a fast-forward merge or a raw `Store.commit`, is
  not re-judged (D-33). The unit checks that mk2's store and the pilot's hold no such value.
- **Rejected.** Fixing only deviation and release ids, the two that appear in suggested commands:
  the rule would differ by identifier and the other two would still be stored and then unusable.
  Recording the gap as won't-fix: Vouch would keep accepting values it cannot then use. Rendering
  options as `--flag=value`: rejected in D-35 for the same reasons.

**Implemented 2026-11-20 (`option-safe-labels@1`).** The four handlers refuse a dash-led label
after any `identifier.invalid` check. Astra's review approved with two LOW findings, both accepted
without a pack change. (1) "Only the bound form can deliver such a value" (above, and the pack's
common rules) is too strong: argparse also passes a separate token that looks like a negative
number (`--pack-version -1`) as a value, and `decide()` refuses it the same way. The rule is
unaffected; the claim is narrowed here. (2) No frozen test covers a dash-led label written before
this rule: Astra reproduced that a fast-forward merge adopts one and a divergent merge replay
refuses its introduction (`merge.conflict` naming `identifier.leading-dash`), as stated above.
A regression fixture is filed as `vouch_mk2-0jc`. A scratch copy of the pilot store (revision
421) holds no dash-led deviation id, amendment version, release id or pack version.

## D-41 — A replayed record carries its origin, and a merge skips work it already holds by origin (2026-11-18)

**Status:** decided 2026-11-18 by the user, choosing Fable's option O over Sol's receipt design
(`design-bd4.4.18`) after independent design reviews by Astra and Fable. Settles
`vouch_mk2-bd4.4.18`. Ships with D-38 in one unit, `nested-merge-boundary@1`.

- **The finding.** Replay re-envelopes records, so a copy has a new digest, and a merge cannot tell
  a copy of a record it already holds from new work. Any command `decide()` accepts twice then
  replays a second time: an open-in-CI mark restores an exception the receiver cleared, a copied
  clear removes a later mark, and settings and repeated lifecycle steps are exposed the same way.
  Fable reproduced this on `main` without a nested merge (three stores, and an ordinary two-branch
  flow whose receiver had never merged), so it is a defect of the released merge, not of D-38 alone.
  A related defect: a merge that skips a block whose source replay generated a companion leaves the
  two stores disagreeing (one unit `invalidated`, the other `in-progress`).
- **Decision.**
  - New event envelopes are version 2. A record written by merge replay carries `origin`: the EVENT
    digest of the first-authored occurrence of that fact; a copy of a copy inherits it unchanged. A
    record authored directly has no `origin`. The merge's own final record carries `satisfied`: the
    origins of source facts the merge accounted for without writing a record (D-37's skipped clears,
    omitted D-33 companions), plus those inherited from the source chain. The `MergeRecorded`
    payload, the snapshot and the artifact kinds do not change; nothing extra is stored or retained.
  - A chain's coverage is every record digest, every `origin` and every `satisfied` entry on it.
    After the existing exact-digest and D-38 exclusions, a merge drops each source command batch
    whose primary record's root (its `origin`, else its own digest) is in OURS' coverage. The
    decision is per batch, never a prefix cut, so earlier independent work on THEIRS still replays.
  - When a batch dropped this way carried a companion that THEIRS' replay generated, OURS must
    already show that companion's effect once the preceding work is staged; otherwise the merge
    refuses with `merge.conflict` naming the fact.
  - `origin` must be a well-formed digest and appear only on records inside a version-2 merge block;
    where OURS holds its own counterpart of an origin, the two must agree on kind, subject, actor,
    run claim and recorded time. A violation refuses with `merge.theirs-history-invalid`, and
    `check.event-log` reports it.
  - **History written before the change (version 1).** After the exact-digest exclusions, a merge
    refuses with `merge.ancestry-unproven`, before any lock and writing nothing, if (a) any remaining
    source batch lies inside a version-1 merge block, or (b) any remaining source batch has a
    version-1 root and OURS has a version-1 merge marker after the exact shared cut. A copy records
    whether its origin was a version-1 or version-2 record, so (b) needs no source object. A store
    with no version-1 merges, or whose version-1 merges all lie in the shared prefix, merges as
    before. The refusal is final in this unit: the refused branch's work is re-recorded on the merged
    side. An operator assertion to override it is a separate later decision. `upgrade` reports how
    many version-1 merge markers a store holds.
  - The state format moves from 6 to 7 (D-13), so an older binary gives a clear "run upgrade"
    refusal instead of appending version-1 records to a version-2 chain. The upgrader changes only
    the format number.
  - Trust: `origin` and `satisfied` are claims written by the merging tool, at the same level as the
    `theirs_head` a merge already trusts. A hand-edited store could label new work as a copy and have
    a receiver skip it, as it can already forge a merge record or omit work. `docs/claim-boundary.md`
    states this.
- **Rejected.** Sol's option A (a receipt per merge, retained source records and a verifier that
  re-runs historical merges): the verifier re-judges history with today's `decide()`, which
  reproduces only 646 of mk2's 759 recorded command batches; two correct receipts can give identical
  replay bytes different origins (Astra); and it adds a new artifact kind, archive, sweep and
  verification path. Astra's option B (an immediate source pointer plus the archive and a replay
  check): the same verifier problem and machinery, for protection only against hand-edited stores.
  Refusing on suspicion alone: it would leave legitimate four-store merges failing.
- **Note (2026-11-18, owner, writing `nested-merge-boundary@1` pack/3).** The companion rule above
  says "a batch dropped this way", meaning by origin. Fable's reproduction drops the block through
  the existing provenance filter, by exact digest. The rule applies to every source batch a merge
  excludes as already incorporated, whether by exact digest, a D-38 block or origin.

- **Note (2026-11-18, owner, from Fable's audit of the pilot's merges).** D-41's selection rules and
  its version-1 rules (a) and (b) govern replay only. A fast-forward, where OURS' head is an ancestor
  of THEIRS' and OURS adopts THEIRS' verified chain verbatim, replays nothing and so cannot apply a
  fact twice; it is unaffected, including when THEIRS' suffix holds version-1 merge blocks. Exact
  no-ops are unaffected too. The audit found the pilot's history clean: no replayed record copied a
  command its receiver already held, and its next ordinary merge passes rules (a) and (b).

- **Note (2026-11-18, owner, from Sol's implementation report).** A companion that merge replay
  generates has no source occurrence, and it cannot name itself as `origin`, since an object cannot
  contain its own digest. Such a record carries `"generated": true` and no `origin`; its root is its
  own digest. A copy of it carries `origin` (that digest), `origin_format` 2 and `generated: true`,
  inherited unchanged by further copies. `generated` is valid only inside a version-2 merge block. The
  companion rule then applies to every excluded batch holding a `generated` record, with no inference
  from claims. Correction to the state-format bullet: an older binary refuses a format-7 store with
  D-13's "written by a newer vouch" message; the "run upgrade" message is what a format-7 binary
  prints for a format-6 store.

- **Note (2026-11-18, owner, after Astra's re-check of `nested-merge-boundary@1`): the validation
  bound.** Every `origin` (and `satisfied` entry) that resolves to a record on either chain, OURS' or
  THEIRS' own, is validated against that record: it must be a first occurrence (carry no `origin`
  itself), agree on kind and subject, and agree on actor, run claim and recorded time; `check.event-log`
  applies the same rule to origins resolving within a single store. An `origin` naming a digest held by
  neither store is accepted as a claim, at D-41's trust level. A hand edit that invents such a digest,
  or forges a whole consistent history, is outside what this rule detects and is not a defect of it;
  a locally disprovable `origin` is.

- **Note (2026-11-18, owner, after Astra's round-3 re-check): position is part of the bound.** A
  `satisfied` entry that resolves on a chain must name a record at or before the `theirs_head` of the
  merge that lists it (the source history that merge accounted for), and a resolving `origin` must name a
  record at or before the `theirs_head` of the merge block that holds the copy. A resolving entry
  authored later than that head is locally disprovable and refuses like any other.

- **Note (2026-11-18, user decision after Astra's round-4 re-check): position across stores.** The
  position rule also applies when the entry and the merge's `theirs_head` resolve on different chains.
  If the chain holding a resolving `satisfied` entry (or the record an `origin` names) had already
  incorporated that `theirs_head`, through its own merge provenance, before the entry was authored, the
  head's history cannot contain the entry, and the claim refuses like any other. Order is judged only
  through recorded provenance (exact digests and merge markers), never by comparing revision numbers or
  times across stores.

- **Note (2026-11-18, user decision after Astra's round-5 re-check): the general rule, and when review
  stops.** The position notes above are special cases of one rule. When the source history through a
  merge's `theirs_head` is fully available (on either chain, through exact digests and merge
  provenance), every resolving `satisfied` entry the merge lists, and every resolving `origin` on a
  copy in its block, must be found in that history: as the record itself, as a copy's `origin`, or as
  an inherited `satisfied` entry. Otherwise the claim refuses before any lock, and `check.event-log`
  reports it when one store suffices. Where the history is not fully available, D-41's trust level
  holds. **Stop rule for this unit's reviews:** from here, a review finding blocks only if it drops or
  duplicates honest work, or shows actual loss caused by a forged claim. Other forged-provenance findings
  go to a follow-up bead.

- **Note (2026-11-18, user decision, from Astra's and Fable's opinions on the recurring findings,
  `d41-forgery-loop_*.OUT.md`).** Two changes to the note above. (1) When the source history through a
  merge's `theirs_head` is fully available, every `satisfied` entry and every `origin` in its block must be
  found in it, whether or not the entry resolves on a chain. (2) The stop rule is reworded: a finding
  blocks only if (a) histories written only by the tool, with no hand edit, are dropped, duplicated,
  wrongly refused or left disagreeing, or (b) the implementation accepts a claim the general rule, as
  written, requires it to refuse. Loss that needs a forged claim the general rule does not cover (for
  example a merge record whose `theirs_head` is rewritten to a later source head, or an entry naming a
  record only a third store holds) is D-41's stated trust limit and goes to the follow-up bead
  `vouch_mk2-22a` on the threat model for hand-edited stores, not to a unit.

- **Note (2026-11-18, user decision, after Astra's final review of `nested-merge-boundary@1`).** Stop
  rule (a) covers behaviour this unit introduces or changes. Astra's randomized test found honest stores
  left disagreeing after two-way merges when both sides changed the same CI policy or exception; the
  released merge on `main` does the same, so it predates D-38 and D-41. It is decided and fixed separately
  under `vouch_mk2-oa2`, and does not block this unit.

## D-42 — A formal certificate hashes each relation as a stream, and the explorer keeps one string per state (2026-11-19)

**Status:** decided 2026-11-19 by the planner, on the user's instruction to do `vouch_mk2-7dr`
after D-41 and on the Fable and Astra reviews it asked for. Settles `vouch_mk2-7dr` in one unit,
`formal-memory@1`.

- **The finding.** A full test run peaks above 8 GB, so it cannot run on an 8 GB machine without
  swap, and takes 15 to 39 minutes. About 7 GB of the peak is `build_certificate` serialising each
  explored relation to one JSON blob (2.4 GB for core-v1, held about three times over while
  encoding and framing) only to hash it. The blob is never stored. Most of the rest is duplicate
  copies of state fingerprint strings: each transition's target is a fresh string equal to one the
  explorer already holds. One test outside every pack builds two core-v1 blobs at once.
- **Decision.** The certificate's relation digest is computed by feeding the same canonical bytes
  to SHA-256 in pieces, so no digest, count, verdict or certificate changes and `CHECKER_VERSION`
  stays. The explorer reuses the state string it already holds for each transition target.
  A formal run carries each relation's digest, computed once, and a certificate build uses it. The
  test that built two blobs compares encodings only for the two small universes and checks every
  relation through its streamed digest.
- **Rejected.** Replacing fingerprints with hashes or integer ids in a `Relation`: the strings are
  what the relation digest covers, and frozen tests read them. Compacting `Snapshot`
  representatives (about 5% of the memory), releasing or reshaping the `run_formal` cache, and
  parallel exploration: small gains for frozen-shape risk. Symmetry or partial-order reduction:
  they change the counts and the digest. An on-disk exploration cache: a certificate would then
  attest a reuse, not a fresh exploration.
- **Later.** The witness re-sorts, a `formal` pytest marker so the non-formal suite (about 90
  seconds) runs alone, and a CI split are a follow-up unit once this one's measured suite time is
  known.

**2026-11-19 note: the digest is remembered in the verification layer, not carried on the run
(DEV-fm-01).** Pack/1 had `run_formal` attach each relation's digest to its `UniverseRun`. The formal
layer is pure (D-1: its import contract forbids `hashlib`, `json` and the store), so Sol could only
do it by adding import-contract exceptions. Rather than weaken D-1, the verification layer's
`formal_relation_digest` remembers the digests of the last few relation objects it hashed (keyed by
identity, holding a reference), so a run's relations are still hashed once per process and the
formal layer and both import contracts are unchanged.

**2026-11-19 note: the memo's promise is bounded (DEV-fm-02).** Astra's review: a relation pushed
out of the 8-entry memo is hashed again, so the claim is "not hashed again while it is among the
last 8 relation objects hashed", not "once per process". The memo supports relations whose tuples
yield the same content on every read, which every explored relation does; a tuple subclass that
changes its content or calls back into the digest is outside it, and the digest does not iterate a
relation while holding a lock. Pack/3 also pins the whole recorded certificate digest and checks
that the memo releases its oldest relation.


## D-43 — mk2's CI runs each check once per change, and README § Gates lists every gates command (2026-11-20)

**Status:** decided 2026-11-20 by the user, from the owner's report on what CI runs (changes 1, 2
and 4 of five). One unit, `ci-runs-once@1`.

- **The finding.** (1) `ci.yml` ran on `push` to every branch and on `pull_request`, so each
  pull-request commit ran the gates job (about 17 minutes) twice. (2) On a default-branch push,
  `formal` (about 3.5 minutes) ran in the gates job and again in the anchor planner's candidate
  step. (3) README § Gates said "Run what CI runs" but listed four of the gates job's commands: it
  lacked the generated-command-reference check, the scratch-store checks and the lock steps, and
  ran `uv sync` without `--locked`.
- **Decision.** (1) `push` runs only on `main`; `pull_request` is unchanged. A branch pushed
  without a pull request gets no CI. (2) The gates job's `formal` step is skipped exactly when the
  anchor planner runs: its condition is the negation of the planner's. The planner's candidate
  `formal` stays, because generated and dogfood candidates are semantically validated before they
  are staged (`tests/test_ci_candidate_validity.py`). Every run still runs `formal` once, and pull
  requests are unchanged. (3) README § Gates lists every gates command in CI's order, with
  `RUNNER_TEMP` set to a scratch directory, so a contributor can run all of it locally.
- **Not changed.** The workflow `init` generates for adopters (its `push`, `pull_request` and
  `workflow_call` triggers are pinned by the trust-boundary tests, and an adopter's CI is the
  adopter's), anchor semantics, permissions, and the rule that pull-request CI runs only the gates
  job (`docs/method.md` §1).
- **Rejected.** Dropping `formal` from the anchor planner: it contradicts the candidate-validation
  rule above. Dropping `push` entirely: anchoring needs default-branch pushes. Two report items
  (pull requests skip the walkthrough and dogfood check; the anchor bot's push triggers no run)
  stay as documented.

**Implemented 2026-11-20 (`ci-runs-once@1`).** Astra's review approved with fixes. MEDIUM, fixed in
README § Gates: run as a script, the block did not stop at a failing command, and a pipeline hid a
failing `check --ci --json`; the lead now says to run it with `bash -eo pipefail`, as CI does. Two
LOW findings accepted without a pack change. (1) "Once per change" (the title, the decision and the
pack's Promise) means: an ordinary feature-branch pull request no longer gets a second, push-run
gates job, and a push to `main` runs `formal` once. GitHub can still run CI again for the same
commit (a reopened pull request, a re-run) or once for several pushed commits (one `synchronize`
event for the tip), a pull request from `main` into another branch gets both runs, and retrying only
a failed publish job runs `formal` zero times in that attempt. (2) The frozen tests pin command text
and order, not execution: an extra README invocation or a commented-out planner `formal` would pass
them; review of the diff covers that. The `push` filter names `main` literally; renaming the
default branch needs this filter updated or anchoring stops.

## D-44 — Test-quality rules from the `tdd` skill apply inside the frozen-pack boundary (2026-11-21)

**Status:** decided 2026-11-21 by the user, after independent takes from Fable, Astra and Neon
(all ADOPT WITH CHANGES; `tdd-proposal_*.OUT.md`). Amends the procedure in `docs/method.md`; the
roles of D-27 and the freeze of D-11 and D-17 are unchanged.

- **The proposal.** Gemini suggested using Matt Pocock's `tdd` skill (`mattpocock/skills`) in each
  stage. The skill's rules: test at public seams agreed up front; no implementation-coupled or
  tautological tests; vertical slices (one test, minimal code, next); refactoring belongs to review.
- **Decision.** Adopt the test-quality rules, not the loop. mk2 freezes the whole acceptance pack
  before implementation and binds evidence to that test set, so vertical slicing does not apply to
  the pack, and the reviewers judged one-test-at-a-time drafting and implementing to be ceremony.
  1. **Standing seams.** `docs/method.md` lists mk2's seams. A pack tests at them; the owner asks
     the user only when a pack adds a seam, crosses a purity boundary (D-1) or tests an internal.
     The user is asked behaviour questions (what Vouch should promise), not where to test.
  2. **Test quality before the freeze.** Each pack test states, in the spec, the reason it fails
     before the change (and each guard why it passes); the owner checks each RED and guard result
     is for that reason. Expected values come from an independent source; no mocks of mk2's own
     modules; no pin on a global fact (state format, universe sizes, certificate) unless the unit
     changes it. A non-trivial pack is probed before freezing: a throwaway draft implementation
     run against it and reverted, or a pre-freeze Astra read of the pack.
  3. **Implementation.** Sol works in any order and may group tests that share a fix. A test Sol
     adds must sit at a pack seam; Sol lists every added and changed test in the done file.
  4. **Review.** Astra applies the anti-patterns to the pack and to Sol's added and changed tests,
     and names at least one plausible defect that would pass every test. Refactors are
     non-blocking suggestions.
- **Rejected.** Vertical slicing of the acceptance pack (conflicts with the freeze); per-unit seam
  sign-off by the user (seam mistakes caused two of the last ten deviations, and a seam list would
  not have caught DEV-fm-01); the skill's "you can't test everything" economy (most review findings
  in a policy engine are untested interactions); recording this as housekeeping only, since it
  gives the user, Sol and Astra new obligations.

## D-45 — A merge refuses concurrent changes to the same setting or exception that end with different values (2026-11-21)

**Status:** decided 2026-11-21 by the user (three answers: refuse only when the values differ;
refuse a replay that would undo an agreed value; include the formal certificate). Settles
`vouch_mk2-oa2` in one unit, `merge-concurrent-settings@1`. Design memo: `design-oa2.OUT.md`
(Sol).

- **The finding** (Astra's randomized review of nested-merge-boundary@1). Two stores that each
  changed the same store-wide setting or the same unit's open-in-CI exception merged both ways
  without refusal and ended with opposite values that later merges preserve: replay applies the
  incoming change after one's own. Reproduced on the pre-change tree for the CI policy (A `strict`,
  B `ledger`: A ends `ledger`, B `strict`) and for an exception (A clears; B clears and re-marks:
  only B keeps it). Sol found a third case: two stores that both end `strict` after each merged a
  third store's explicit `strict` split again when one replays the other's older `ledger`.
- **Decision.** A divergent merge refuses with `merge.conflict`, writing nothing to either store or
  the Git index, when (1) both sides changed the same governed key since their common history and
  their resulting values differ, or (2) its replay would leave OURS with a value for a key other
  than the one both sides already agree on. Governed keys: CI policy, anchor mode, assurance tier,
  the recorded formal certificate, and each `NAME@VERSION` open-in-CI exception, whose value is its
  absence or its exact reason. An explicit write of the same value counts as a change; equal
  results merge. A write that reached the other side through an earlier merge is shared, not
  independent (D-38, D-41 provenance). The refusal names each side's command and resulting value;
  the operator aligns one side with an ordinary command and merges again. `decide()` and `apply()`
  do not change; the check lives in the shared divergent-merge path, so `merge` and
  `merge-git-stages` both get it; fast-forward and no-op merges are unchanged.
- **Consequences.** D-37's re-mark promise narrows: a clear against a clear and re-mark now refuses
  (absent against present), while two clears still merge with the incoming clear skipped. D-41's
  R17 (an independent mark reopening after a concurrent clear) refuses likewise. Their frozen tests
  are adapted by the owner and rebound. Two branches that each run `formal --record` on different
  code refuse until one side records the other's certificate or both re-record.
- **Rejected.** A last-writer or fixed-winner rule (silent: one side's change is dropped).
  Refusing whenever both sides changed a key even to equal values (needless refusals). Automatic
  reconciliation of the split case (needs an ordering policy for history already written).
  Release criteria, bundle bindings and evidence anchors stay under their own rules.

## D-46 — Formal explores D-36's fresh-GREEN rule in a new universe under a new invariant, M12 (2026-11-21)

**Status:** decided 2026-11-21 by the user (two answers: a new small universe; M12 checks set,
honour and clear). Settles `vouch_mk2-7kc` in one unit, `formal-fresh-green@1`. Follows
`refreeze-green@1` (D-36).

- **The finding** (Fable's review of `refreeze-green@1`, Finding 2). The fresh-GREEN requirement
  is on in no state of any formal universe. `history-v1` runs at `lightweight`, where the
  current-pack GREEN gate is off, and only binds one inventory for `u`. `tier-v1` runs at `high`
  but never re-freezes with a changed pack. No invariant checks any publish evidence gate, so a
  `decide()` that dropped the requirement would still pass `formal`.
- **Decision.** Add a focused universe, `refreeze-v1`, at `standard`: one unit is bound, recorded
  RED and GREEN, published, blocked by a deviation, respecified, re-frozen and bound to a different
  inventory, then may publish only after a further GREEN. It runs every invariant except M6, as
  `effects-v1` does, because a unit marked done without evidence has no exit once the universe's
  deviation ids are used up: a bound of the universe, not of Vouch. Add M12 over every universe
  that runs all invariants: (set) a transition, other than an owner rebind, that binds a unit to an
  inventory different from its last bound inventory leaves the requirement on; (honour) at
  `standard` or `high`, no unit enters `published` while its requirement is on; (clear) the
  requirement turns off only on a transition that records a GREEN for the unit's current test set.
  The witness registry keeps one private mutation per invariant (W12); the acceptance tests mutate
  `refreeze-v1`'s real relation once for each of M12's three parts. The docs name M1–M12.
- **Consequences.** The certificate changes (a fifth universe and M12 rows); mk2's store re-records
  it. `CHECKER_VERSION` stays. `history-v1`, `core-v1`, `tier-v1` and `effects-v1` keep their
  relations. Two published units pin global facts this unit changes and are adapted by the owner
  and rebound: `effect-declarations@1` (the list of universe names) and `formal-memory@1` (the set
  of relation digests and the certificate digest).
- **Rejected.** Extending `history-v1`: it would need a tier change and grow the largest universe.
  Extending `tier-v1`: it moves pinned counts in three published units. An invariant that checks
  only the publish refusal: a `decide()` that never turned the requirement on would pass it.

