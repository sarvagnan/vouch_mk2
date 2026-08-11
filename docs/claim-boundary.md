# Vouch mk2 claim boundary

**Status:** Phase 0 draft, 2026-08-11. This table is the contract every other document
and check cites: what mk2 proves mechanically, and what is permanently `NOT_CHECKED`
because no artifact can prove it. It took 0.6 roughly thirty defects to extract this
boundary (`STATUS.md` §known-limits in the 0.6 repo); mk2 starts with it.

**The one rule:** Vouch checks only what artifacts can prove; where it cannot, it
prints `NOT_CHECKED` rather than offering a button that fakes it. No command may
convert an organizational claim into a mechanical pass — a feature that would do so
(an `attest-independence`, an auto-generated intent-validation record) is rejected at
design time, not reviewed case by case.

Verdicts are `PASS | FAIL | NOT_CHECKED | NOT_APPLICABLE`. Only `PASS` composes to
`PASS`; `NOT_APPLICABLE` must cite an IR applicability rule; there is no silent SKIP.

## Mechanically proved (the `PASS`-capable set)

Each row is backed by the obligation registry — a registered check id mapped to an
evaluator that ran, or the run fails.

| Claim | Mechanism | Threat level |
|---|---|---|
| Snapshot consistency | one authoritative `state.json`, CAS on a monotonic revision; objects written before the swap | 1 |
| Artifact integrity | content-addressed objects; a digest mismatch refuses to load | 1–2 |
| Reference integrity | every cross-artifact reference is a typed `{kind, digest}` field, resolved or failing | 1 |
| Schema conformance | one strict decoder per artifact type; unknown field = error, coercion = error | 1–2 |
| Lifecycle legality | `decide(Snapshot, Command)` refuses every transition the IR does not license; gates are inside the transition | 1 |
| Deviation blocking | a unit with any open deviation cannot advance; `NAME@VERSION` binding | 1 |
| Claim-shape obligations | completion claims re-check units + criteria digest + roster; negative outcomes require criteria + outcome deviations; protective closures require trigger + roster snapshot + `no_completion_claim`, and are never blocked by contaminated state | 1 |
| Evidence-chain validity and ordering | canonical-JSON hash chain; red-before-first-green ordering; anchor monotonicity (prior head remains an ancestor) | 1–2 |
| Projection freshness | regenerate-and-byte-compare in CI; staleness is a FAIL, never a broken commit | 1 |
| Registry completeness | a check declared in the IR without an evaluator fails CI on the mk2 repo itself | 1 |
| Formal invariants over the bounded model | checker consumes the same transition relation the runtime executes; independently authored invariants with private mutation witnesses; `unknown`/timeout is never a pass | 1 |

## Permanently `NOT_CHECKED` (organizational)

These are exclusions, not omissions. Reports on a Vouch-governed project state them.

| Claim | Why no artifact can prove it |
|---|---|
| Acceptance ownership is real | a recorded owner identity proves a string was recorded, not that a person with authority and time validated intent |
| Intent validation | whether the frozen pack tests *the right thing* is human judgment; red proves cases CAN fail, not that they should |
| Independence between real persons | identity comparison is a format heuristic (the 0.6 commit-author lesson), never proof of distinct people |
| Causal red history | an anchored chain proves ordering and anchor timing; no anchor mode proves *why* the red occurred |
| Protective-closure truthfulness | a closure record proves a trigger and roster snapshot were recorded, not that the trigger was real or the response appropriate |
| Semantic test quality | mutation-testing orchestration and statistical-oracle execution are ecosystem-specific and need judgment Vouch cannot supply |
| Behavioral equivalence of model and reality | the formal certificate binds the model run under a finite abstraction, not the implementation's full behavior |
| Forge enforcement | required checks and branch protection are forge-side; a pull request can modify the workflow file; hooks are warn-only fragments |
| Trust-root integrity (threat level 3) | coordinated edits to the vendored `vouch.lock`, workflow, and branch rules defeat repository-local checks; source review and forge governance are the approval boundary |

## Boundary discipline

- Every mechanical row above maps to obligation-registry entries; the mapping is
  itself a checked projection of the IR (Phase 3).
- Moving a row *up* (organizational → mechanical) requires a recorded decision in
  `DECISIONS.md` naming the new artifact that makes it provable.
- The `NOT_CHECKED` set appears verbatim in `vouch check` output for the claims it
  covers — visible honesty, not a footnote.
