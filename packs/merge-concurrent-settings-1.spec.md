# merge-concurrent-settings@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_merge_concurrent_settings.py`, `tests/fixtures/merge-concurrent-settings/` or
`packs/merge-concurrent-settings-1.effects.json`.
**Decision:** D-45 (settles `vouch_mk2-oa2`). **Seams (D-44):** the `merge` and `merge-git-stages`
CLI on stores built by CLI commands, `merge_store_roots` on stores built by `decide()` and
`commit_events` (the R12 property), and `docs/quickstart.md`.
**Effects:** one observation (both stores' verified histories) and one action (the target store
and the Git index), declared in the effects file.

## Promise

A divergent merge refuses with `merge.conflict`, and writes nothing to either store or the Git
index, when both sides changed the same governed key since their common history and their values
differ, or when its replay would leave OURS with a value for a key that differs from the value both
sides already agree on. The governed keys are the CI policy, the anchor mode, the assurance tier,
the recorded formal certificate, and each unit's open-in-CI exception, whose value is its absence or
its exact reason. The refusal names, for each side, the command and the resulting value. Changes
that end with equal values, one-sided changes and changes to different keys merge as before, and a
successful exchange in both directions leaves both stores with equal governed values. `decide()`,
`apply()`, the formal universes, and the state and event formats do not change.

## Common rules

- `_base` initializes a store, specifies `x@1`, freezes it label-only and marks it open in CI with
  reason `shared`; `_fork` copies it. Every CLI command passes `--actor <store directory name>`, so
  the two sides' records are distinct.
- **The refusal detail.** A refused `merge --json` prints `accepted: false` and exactly one
  violation, code `merge.conflict`. Its detail names the key (for an exception, its `NAME@VERSION`)
  and carries the side label `OURS` once, then the side label `THEIRS` once; values are quoted
  verbatim, so an exception reason may itself contain either word (DEV-mcs-01), and the tests use
  reasons that contain neither. The text from the `OURS` label up to the `THEIRS` label names the
  receiving store's command verb and resulting value; the text from the `THEIRS` label on names the
  incoming store's. A value is the setting's value, the full certificate digest, an exception's
  exact reason, or `absent` for no exception. For a replay that would undo an agreed value, OURS
  names the agreed write and THEIRS the incoming write that would undo it.
- "Refused" (`_assert_refused`): the detail as above for that direction, and every file of both
  stores byte-identical before and after. "Refused both ways" checks each direction with the sides
  swapped.
- "Agrees" (`_assert_exchange_agrees`): from untouched copies, both directions exit 0 and the two
  results have equal governed values (`_settings`), compared as literals.
- A recorded certificate is committed with its certificate and tables objects, copied from mk2's
  own store into the fixture directory, as `formal --record` commits them.

## Requirements

Each RED test states why it fails before the change.

- **R1 — CI policy.** `test_concurrent_ci_policies_are_refused`. OURS `strict`, THEIRS `ledger`,
  refused both ways (`set-ci-policy` and the value on each side). *Before:* exits 0 and replays.
- **R2 — Anchor mode.** `test_concurrent_anchor_modes_are_refused`. `ci` against `none`
  (`set-anchor-mode`). *Before:* exits 0.
- **R3 — Tier.** `test_concurrent_tier_promotions_are_refused`. `standard` against `high`
  (`set-tier`). *Before:* exits 0.
- **R4 — Formal certificate.** `test_concurrent_formal_certificates_are_refused`. Two different
  certificates (`record-formal-certificate` and each digest); both setups must succeed.
  *Before:* exits 0.
- **R5 — Clear against clear and re-mark.** `test_a_clear_against_a_clear_and_remark_is_refused`.
  OURS clears `x`; THEIRS clears and re-marks it with `shared`. Sides: `clear-open-in-ci`/`absent`
  against `mark-open-in-ci`/`shared`. *Before:* exits 0 (D-37 skips the clear, the re-mark is
  replayed).
- **R6 — Marks with different reasons.** `test_marks_with_different_reasons_are_refused`. Reasons
  `wait for CI` and `Wait for CI` (differing only in case). *Before:* exits 0 and the incoming
  reason overwrites.
- **R7 — Lifecycle removal against a re-mark.**
  `test_a_lifecycle_removal_against_a_remark_is_refused`. The base adds `y@1` taken to
  done-pending-acceptance and marked `y shared`. OURS publishes `y` (which removes its exception);
  THEIRS re-marks it `theirs reason`. Sides: `publish`/`absent` against
  `mark-open-in-ci`/`theirs reason`. *Before:* OURS<-THEIRS is refused only by `decide()` on the
  replayed mark (no OURS/THEIRS sides), and THEIRS<-OURS exits 0, dropping THEIRS' mark.
- **R8 — A replay that would undo an agreed value.**
  `test_a_replay_that_would_undo_an_agreed_value_is_refused`. The fixture
  `split-after-shared-write.json` holds two stores made by the pre-change tree (its `comment` gives
  the recipe): both end `strict` after each merged a third store's explicit `strict`, and THEIRS
  also holds an earlier `ledger`. OURS<-THEIRS is refused with sides `set-ci-policy`/`strict`
  against `set-ci-policy`/`ledger`; THEIRS<-OURS, from a copy, exits 0 and stays `strict`.
  *Before:* OURS<-THEIRS exits 0 and ends `ledger`.
- **R9 — A value carried from a third store.** `test_a_value_carried_from_a_third_store_conflicts`.
  A diverges, then merges C's `ledger`; B sets `strict`; A and B refuse both ways with sides
  `ledger` (A) and `strict` (B). *Before:* exits 0.
- **R10 — Git-stage recovery.**
  `test_git_stage_recovery_refuses_concurrent_policies_and_keeps_the_index`. R1's histories on two
  Git branches with a conflicted `git merge`: `merge-git-stages` exits 2, its output holds
  `merge.conflict` and the OURS (`strict`) and THEIRS (`ledger`) sides, and the index bytes,
  `git ls-files --stage` and the working store are unchanged. *Before:* exits 0 and replays.
- **R11 — Refuse, align, retry.** `test_aligning_after_a_refusal_then_retrying_merges`. R1's
  histories are refused; THEIRS then sets `strict`; the exchange agrees on `strict`.
  *Before:* the first merge exits 0.
- **R12 — Two-way agreement.** `test_any_two_way_exchange_that_succeeds_agrees`. For seeds 0–39,
  three forks receive 3–8 random legal steps (each governed command, a fresh `specify`, or a
  library merge from another fork; refusals ignored), then A<-B and B<-A run from untouched
  copies. Every exchange where both succeed ends with equal governed values, and at least one
  exchange succeeds both ways. *Before:* six seeds (6 and 38 among them) succeed both ways and
  disagree.
- **R13 — The quickstart states it.** `test_quickstart_states_the_concurrent_change_rule`.
  Matching ignores runs of whitespace. `docs/quickstart.md` contains `A merge refuses with
  \`merge.conflict\` when both sides changed the same setting or open-in-CI exception to different
  values` and, beside D-37's sentence, `If OURS cleared it and THEIRS cleared and marked it again,
  the merge refuses with \`merge.conflict\``. *Before:* both absent.

Guards (pass before and after, each for the stated reason):

- **G1** `test_both_sides_clearing_still_merges_and_agrees`: both clear; agree with no exception
  (D-37's skip still applies).
- **G2** `test_same_reason_marks_still_merge`: both mark `same`; agree on `("x@1", "same")`.
- **G3** `test_several_writes_ending_equal_still_merge`: OURS `ledger, strict, ledger`, THEIRS
  `ledger`; agree on `ledger`.
- **G4** `test_a_one_sided_change_still_merges`: only THEIRS changes the tier; agree on `standard`.
- **G5** `test_changes_to_different_keys_still_merge`: policy on one side, anchor mode on the
  other; agree on `("ledger", "ci")`.
- **G6** `test_exceptions_on_different_units_still_merge`: OURS re-marks `x`, THEIRS re-marks a
  second unit `z`; agree on both reasons (exceptions are separate keys per unit).

Equal, one-sided and disjoint changes still go through ordinary replay legality and the
agreed-value check; the guards cover histories where both allow the merge.

## Ground rules

- The check lives in the shared divergent-merge path (`merge` and `merge-git-stages`), runs before
  the write lock is taken, and uses both chains' provenance (D-38, D-41) so a write that already
  reached the other side is not counted twice. Fast-forward and no-op merges are unchanged.
- `decide()`, `apply()`, `formal/`, the codec, and the state and event formats do not change; the
  formal universes keep their sizes and the certificate is unchanged.
- Release criteria, bundle bindings, evidence heads and anchors keep their own rules (out of
  scope). `require-effects` is one-way and cannot conflict.
- Frozen tests of published units that require the old behaviour are adapted by the owner, not the
  implementer: `merge-redundant-clear@1` R1, R2 and R4 (a clear against a clear and re-mark now
  refuses) and `nested-merge-boundary@1` R17 (an independent mark reopening after a concurrent
  clear), each rebound with a stated reason and a fresh GREEN. The implementer reports which of
  them fail and why, and does not edit them.
- The quickstart keeps every pinned phrase, including D-37's
  `An incoming clear of an open-in-CI exception that OURS has already cleared is skipped`, and
  qualifies the re-mark case beside it.

## Verification outside pytest (final pass)

The owner runs the R9 property with 200 further seeds and records the counts of agreed and refused
exchanges, and checks a scratch copy of the pilot project's store merges with itself (no-op) and
that `check` verdicts are unchanged.
