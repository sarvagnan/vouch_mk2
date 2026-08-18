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
| Evidence-chain validity and ordering | canonical-JSON hash chain keyed by `NAME@VERSION`; each new record binds the current acceptance pack's test-set digest (the whole inventory digest for legacy pack format 1); red-before-first-green ordering except that a recorded post-publication rebound requires a fresh GREEN without inventing a RED; anchor monotonicity (prior head remains an ancestor) | 1–2 |
| Tier-scoped evidence requirements | the authoritative snapshot tier selects IR-bound chain, anchor, and high-attestation checks; no config authority exists | 1–2 |
| Projection freshness | `check.map-freshness` and `check.context-freshness` regenerate and byte-compare in CI; staleness is a FAIL, never a broken commit | 1 |
| Registry completeness | a check declared in the IR without an evaluator fails CI on the mk2 repo itself | 1 |
| Recorded formal-certificate integrity | whenever a certificate is recorded, ordinary `check` verifies that the certificate object resolves at its recorded CAS digest and decodes strictly, its checker identity is current, its recorded policy tables resolve and equal freshly emitted tables, and its all-true invariant/witness rows cover the current registries; that narrowed obligation is PASS-capable | 1 |
| Formal invariants over the bounded model | `vouch-mk2 formal` exhaustively regenerates the bounded runtime relations, evaluates M1–M11, and runs the private mutation witnesses; the generated workflow runs it after `check --ci`; the D-7 read-only MCP `formal` summary performs the same exploration; CLI `formal --record` stores that run's tables, relation digests and counts, and certificate | 1 |

Lifecycle writes use the reflected CLI verbs (`vouch-mk2 specify`,
`vouch-mk2 record-evidence`, and the rest of the typed command set). Each opens
one store and holds its writer lock across `decide` → `apply` → commit,
including `record-formal-certificate` through the revision it reports. Writers
wait on a blocking exclusive lock. Read-only handles never acquire that lock.
Standalone projection generation reads unlocked and holds the writer lock only
while replacing generated files; upgrade holds its outer writer lock through
regeneration. Orphan-temp cleanup is opportunistic and skipped when a writer
holds the lock. The MCP transport remains read-only; these verbs do not add a
remote mutation path.

Format 1 and later declare `state_format`; absence denotes legacy format 0.
Strict reads accept exactly the current format. Older stores require the explicit,
lock-protected `vouch-mk2 upgrade` path; newer stores are refused as written by a
newer Vouch.

For `record-evidence`, the exact mk2 claim is: "the recorded outcome is the exit
status of a process this verb spawned, at the time it spawned it". The child
inherits the caller's environment and working directory. This is not a claim that
the argv names a test runner, that the runner is honest, or that its exit status
truthfully represents a test suite. Recording before acceptance is frozen is
refused. At `standard` and `high`, `gate.dod.current-pack-green` refuses publication
unless the current pack's test-set digest has RED followed by GREEN and the latest
evidence for that test set is green; a later refactor preserves that result, while a
later RED invalidates it. Legacy pack format 1 uses its whole inventory digest, so its
behavior is unchanged. Legacy pre-D-17 objects remain immutable and readable as having
no pack digest, and are reported by name rather than treated as current-pack GREEN.

Before spawning, the CLI warns on stderr when argv looks secret-bearing. The warning
does not redact, rewrite, or refuse the command: the exact command line is still stored
verbatim in the immutable evidence object.

`check --ci` evaluates the recorded `ci_policy`. `strict` requires every unit that
has left `specified` to be published, release-bound, or superseded, unless a recorded
`mark-open-in-ci` exception names the exact current `NAME@VERSION` and its stated reason. It is
cleared when that exact record becomes terminal or ceases to be the active record, so it cannot
exempt a later version. `ledger` retains the
pre-D-18 coherence-only behavior. The check row always names the policy used and lists
recorded exceptions with their reasons; Vouch verifies that the facts were recorded,
not that an exception's reason is true or adequate.

## Formal certificate boundary

An ordinary `check` never explores the bounded model. Whenever a formal certificate is
recorded, at any tier, its cheap certificate path verifies that the content-addressed
certificate object resolves at its recorded digest and decodes strictly, the checker
identity is current, the recorded policy tables resolve and equal freshly emitted tables,
and the invariant/witness rows cover the current registries and are all true. A successful
comparison reports `PASS` for that narrowed, mechanical obligation. Missing or corrupt
objects, stale checker identity, mismatched tables, incomplete registries, and false rows
remain `FAIL` at every tier.

That `PASS` does **not** claim that `check` regenerated the reachable relations, recomputed
their recorded digests or counts, executed M1–M11 over them, or reran the private witness
mutations. `vouch-mk2 formal` performs that exhaustive re-execution, and the generated
workflow runs `python vouch.lock formal` after `check --ci` on every CI run. Only a passing
`vouch-mk2 formal --record --root ROOT` records fresh formal results. D-7 also binds the
read-only MCP `formal` summary to the explorer; ordinary MCP `check`, CLI `check`, and hooks
never explore.
When no certificate is recorded, the formal-certificate row is `NOT_APPLICABLE` by its
`subjects-present: formal-certificate` IR rule. High-tier publication separately requires a
recorded certificate through `gate.dod.tier-evidence`; that requirement is not an applicability
gate on verifying a certificate that already exists.

`rebind-pack` is restricted to published or release-bound units. Vouch records the old
and new inventory digests, the old and new test-set digests, the evidence-head sequence,
and records the stated reason as a claim while preserving the pack label, unit status,
and evidence chain. Mechanically, no previously bound path may be removed and no
executable entry may be demoted to a non-executable role. Vouch does not verify the reason's truth
and does not verify the adequacy of the new test content. It marks the
binding as rebound; `check.evidence-chains` requires a fresh GREEN recorded after the
rebound boundary for the new test-set digest at every tier (no RED is required), and
`check.pack-bound` names rebound units and enforces that GREEN at high tier.

For acceptance-pack binding, the pack's EXECUTION is not verified — runner
configuration (conftest.py, pytest.ini, pyproject testpaths) can deselect a bound
test; bind those files too. A format-2 inventory distinguishes executable
`--pack-test-file` entries from `--pack-file` specs for evidence validity, but both roles
remain byte-bound and re-verifiable.

## Permanently `NOT_CHECKED` (organizational)

These are exclusions, not omissions. Reports on a Vouch-governed project state them.

| Claim | Why no artifact can prove it |
|---|---|
| Acceptance ownership is real | a recorded owner identity proves a string was recorded, not that a person with authority and time validated intent |
| Intent validation | whether the frozen pack tests *the right thing* is human judgment; red proves cases CAN fail, not that they should |
| Independence between real persons | identity comparison is a format heuristic (the 0.6 commit-author lesson), never proof of distinct people |
| Causal red history | an anchored chain proves ordering and anchor timing; no anchor mode proves *why* the red occurred |
| Protective-closure truthfulness | a closure record proves a trigger and roster snapshot were recorded, not that the trigger was real or the response appropriate |
| Residual assurance-tier controls | `org.tier-controls` lists active cells without full coverage, distinguishes partial from unchecked residuals, and reports waived/no-obligation cells separately |
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
