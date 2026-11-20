# merge-redundant-clear@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_merge_redundant_clear.py` or `packs/merge-redundant-clear-1.effects.json`.
**Decision:** D-37 (settles `vouch_mk2-bd4.4.3`). Amends D-25 for merge replay only.
**Effects:** one store action, in `packs/merge-redundant-clear-1.effects.json`: a replayed merge
writes no record for a redundant incoming clear and reports it (D-31 note of 2026-11-15).
**Pack/2** (DEV-mrc-01): pack/1's `_event_kinds` helper read the `GENESIS` sentinel as an object,
and R2 called `replayed_records` the records written from THEIRS, which conflicts with the
existing counter's meaning (THEIRS' source records replayed, D-33 companions included) and with
unchanged output when nothing is skipped.

## Promise

During a replayed merge (`merge` and `merge-git-stages`), an incoming `OpenInCiCleared` whose
exact `NAME@VERSION` has no open-in-CI exception on OURS, and which would replay onto that same
reference, writes no record and is reported as skipped; the rest of THEIRS' history replays as
before. A clear that would land on another unit version is still a `merge.conflict`, and a direct
`clear-open-in-ci` of an absent exception is still refused.

## Common rules for every scenario

- Shared history: a fresh store with unit `u@1` specified, frozen as `u-pack/1` against one
  `--pack-file`, and marked open in CI with reason `shared reason`. THEIRS is a copy of it.
- OURS diverges first by specifying `ours-only@1`, so its clear is a different record from
  THEIRS' clear and the merge is a replay, not a fast-forward or a no-op.
- "The redundant clear" is THEIRS' `clear-open-in-ci --name u`, made after OURS cleared the same
  exception.

## Requirements

- **R1 — A directory merge skips the clear and keeps THEIRS' re-mark.**
  `test_directory_merge_skips_a_clear_ours_already_made_and_keeps_the_remark`. OURS clears; THEIRS
  clears and marks again (`re-marked on theirs`). `merge --other` exits 0, its output contains
  `skipped 1 redundant open-in-CI clear(s)`, OURS' only exception is `u@1` with THEIRS' re-mark
  reason, OURS' chain holds exactly one `OpenInCiCleared`, and its last three events are OURS'
  clear, the replayed `OpenInCiMarked` and the `MergeRecorded`.
- **R2 — JSON reports it.** `test_json_merge_reports_the_skipped_clear`. The same merge with
  `--json` prints `accepted` true, `mode` `replayed`, `replayed_records` 1 (THEIRS' source records
  replayed, as before; the skipped clear is not counted) and `skipped_records` 1.
- **R3 — Both sides only clear.** `test_a_clear_on_both_sides_merges_with_no_exception`. THEIRS
  only clears. The merge exits 0, reports the skip, leaves no exception, and OURS' chain holds
  exactly one `OpenInCiCleared`.
- **R4 — Git-stage recovery does the same.** `test_git_stage_recovery_skips_the_redundant_clear`.
  R1's histories on two Git branches with a conflicted `git merge`: `merge-git-stages` exits 0,
  reports the skip, and leaves THEIRS' re-mark as the only exception.
- **R5 — A clear aimed at another version is still a conflict.** `# guard:`
  `test_a_clear_aimed_at_another_unit_version_is_still_refused`. OURS clears, opens a deviation,
  supersedes `u@1` and specifies `u@2`; THEIRS only clears `u@1`. The merge exits 2, its output
  contains `merge.conflict` and `u@1`, and OURS is unchanged.
- **R6 — `decide()` is unchanged.** `# guard:`
  `test_a_direct_clear_of_an_absent_exception_is_still_refused`. A second direct
  `clear-open-in-ci --name u` exits 2 with `ci-policy.exception-missing` and changes nothing.
- **R7 — The quickstart states it.**
  `test_quickstart_states_that_a_redundant_clear_is_skipped`. Matching ignores runs of
  whitespace. `docs/quickstart.md` contains `An incoming clear of an open-in-CI exception that OURS
  has already cleared is skipped`.

## Ground rules

- The skip lives in merge replay (`vouch_mk2.store.merge`), so `merge` and `merge-git-stages`
  share it. `decide()` and its refusal of a clear of an absent exception are unchanged (R6).
- The skip applies only to `OpenInCiCleared`, only when OURS has no exception for the event's
  exact recorded reference at that point in the replay, and only when the clear would otherwise
  replay onto that same reference (D-25, D-33). Any other refusal of any other replayed command is
  still a `merge.conflict`, and a refused merge writes nothing.
- The skipped record is not written and gets no claims. Nothing else about the merge changes: the
  other replayed records keep their source claims (D-33) and the merge record names both heads.
  When nothing is skipped, the CLI output is unchanged apart from `skipped_records` 0 in JSON.
- No event, snapshot or state format change. Formal universe sizes do not change. Fast-forward and
  no-op merges never skip, since they replay nothing.
- `docs/claim-boundary.md`'s statement that each divergent THEIRS command is replayed through
  `decide()` names this exception.

## Amended by D-45 (2026-11-21, owner rebind)

A merge now refuses when both sides changed the same open-in-CI exception to different values
(`merge-concurrent-settings@1`). R1, R2 and R4's history (OURS clears; THEIRS clears and re-marks)
ends with the exception absent on OURS and present on THEIRS, so `merge`, `merge --json` and
`merge-git-stages` now exit 2 with one `merge.conflict` and leave OURS, and for R4 the index,
unchanged. R3 (both sides only clear), R5, R6 and R7 are unchanged: an incoming clear that OURS has
already made is still skipped.
