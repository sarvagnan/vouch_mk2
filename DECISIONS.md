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

The Phase 0 transcription (`ir/vouch_06.py`) keeps the prose-faithful reading, since
0.6's own precedence rule gives prose authority absent a recorded discrepancy; this
entry is mk2's recorded discrepancy for its own semantics.
