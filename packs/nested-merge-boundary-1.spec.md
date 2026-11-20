# nested-merge-boundary@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_nested_merge_boundary.py`, `tests/fixtures/nested-merge/legacy-untranslated.json`,
`tests/fixtures/nested-merge/legacy-v1-merges.json` or `packs/nested-merge-boundary-1.effects.json`.
**Decisions:** D-38 (settles `vouch_mk2-bd4.4.11`), from Sol's design `design-bd4.4.11`, extending
D-33; and D-41 with its 2026-11-18 note (settles `vouch_mk2-bd4.4.18`), Fable's option O.
**Effects:** in `packs/nested-merge-boundary-1.effects.json`: the translated boundary a replayed
merge writes, version-2 envelopes with `origin` and `satisfied`, state format 7, and the ancestry
reads and refusals (D-31 note of 2026-11-15).
**Pack/2** (DEV-nmb-01): pack/1's R3 needed ancestry across replay copies, which D-38 does not
decide; R3 became a three-store round and the Promise was narrowed.
**Pack/4** (DEV-nmb-03): from Sol's implementation report, a generated companion cannot carry its
own digest as `origin`, so it is flagged `generated` instead (D-41 note), pinned by R31; R27 counts
only `dep`'s invalidation (x's own was already present); R28 creates its scratch parent directory.
**Pack/3** (DEV-nmb-02): Astra's review showed that, with D-38 alone, a merge can silently re-apply
a copy of a command the receiver already merged and undid, and Fable showed the same on `main`
without a nested merge. The user decided D-41 and that it ships in this unit. Pack/3 restores pack/1's
four-store case (R15), widens the Promise, and adds R8 to R30, revised after Astra's pre-freeze
review (`nmb-pack3-prefreeze.OUT.md`) and Fable's audit of the pilot (R25).

## Promise

When merge replay writes an incoming `MergeRecorded`, its `ours_head` names the record on the
receiving chain where its replayed block begins, and its `theirs_head` keeps the source value (D-38).
Every record merge replay writes carries the origin of its fact, and a replayed merge drops each
source command batch whose origin OURS already holds (D-41). For history written by this tool, merges
of merges in any order, including copies of copies, then neither duplicate nor drop work, and a copy
cannot restore, remove or redo something the receiver already merged; a batch whose replay generated
a companion OURS does not show refuses instead. Where history written before this change cannot prove
what OURS already holds, the merge refuses with `merge.ancestry-unproven` before any lock and writes
nothing. `origin` and `satisfied` are claims written by the merging tool (D-41's trust level). A store that already holds an untranslated record keeps its bytes and stays readable; a
merge that needs that record's boundary refuses atomically and names the record.

## Common rules for every scenario

- CLI scenarios (R1 to R7, R15, R26) build stores with `Store.init` and the CLI, each command run with
  `--actor` set to the store's name (A, B, C, D). Each store starts as a copy of one holding
  `shared@1`, then specifies its own unit (`a@1`, `b@1`, …). "X<-Y" is `merge --root X --other Y`.
- Library scenarios (R8 to R14, R16 to R19, R27 to R31) start from store A holding `x@1` specified and frozen
  label-only, copied once per other store. Commands go through `decide()` and `commit_events` with
  the store's name as actor, an empty run claim and the fixed recorded time
  `2026-11-18T00:00:00+00:00`, so a copy and its original agree on every claim. Merges go through
  `merge_store_roots`; "replays" means it returns a `MergeOutcome` with mode `replayed`.
- No CI run-id variable is set.
- Fixtures are written by the released code (state format 6, version-1 envelopes) and upgraded with
  `upgrade` before use. `legacy-untranslated.json` holds stores B and C after A<-B and C<-A: C's chain
  holds exactly one `MergeRecorded` whose `ours_head` is not an earlier record of C's chain ("the
  offending record"). `legacy-v1-merges.json` holds A (x@1 frozen; then A specified `a@1`, and A<-B
  brought in `b@1`: one version-1 merge block), C (forked before that merge, then specified `c@1`),
  M and U (forked from A after the merge, then specifying `m2@1` and `u@1`), and P (forked from A
  just before the merge).
- Wire format pinned by this pack: a version-2 event object is the same canonical JSON as today plus
  `"event_format": 2`; a replayed record adds `"origin"` (a digest) and `"origin_format"` (1 or 2); the
  merge's own final record adds `"satisfied"` (a list of digests); a companion merge replay
  generates, having no source occurrence, carries `"generated": true` and no `origin` (its root is its
  own digest), and a copy of it carries `origin`, `origin_format` 2 and `"generated": true`; a directly
  authored record has none of `origin`, `satisfied` or `generated`. `EventRecord` exposes them as `origin` (empty when absent) and
  `satisfied` (a tuple). R30 edits a stored object as a hand edit would: it re-encodes the JSON with
  `encode_document` and re-chains every later record.
- "Refused before lock" means the merge exits 2 with the named code, `Store.write_locked` is never
  entered, and both stores' files are unchanged (ignoring the lock file).

## Requirements

- **R1 — Three nested merges replay.** `test_three_store_nested_merges_all_replay`. A<-B, C<-A
  and B<-C all exit 0 as replays. B then holds `a@1`, `b@1`, `c@1` and `shared@1`, and its chain
  holds exactly four `UnitSpecified` records.
- **R2 — The replayed marker's boundary is exact.**
  `test_a_replayed_merge_record_names_a_boundary_on_the_receiving_chain`. After A<-B, A's marker's
  `ours_head` is A's `UnitSpecified a`. After C<-A, the `MergeRecorded` C replayed from A has A's
  `theirs_head`, an `ours_head` equal to the digest of C's replayed copy of `a`, and A's actor claim
  (D-33); C's own final marker names C's pre-merge head and A's head.
- **R3 — Later work merges again.** `test_later_work_on_every_store_merges_again_without_duplicates`.
  After R1's merges, A, B and C each specify their own second unit (`a2@1`, `b2@1`, `c2@1`); then
  C<-A, B<-C, A<-B and C<-B all exit 0, and A, B and C each hold exactly the seven units, each
  specified once.
- **R4 — A repeat merge is a no-op.** `test_a_fully_incorporated_repeat_merge_is_a_byte_stable_no_op`.
  After R1, B<-C again exits 0 as `merge: no-op` and leaves B's files byte-identical (ignoring the
  lock file).
- **R5 — A legacy record stays readable.** `# guard:`
  `test_a_legacy_untranslated_record_stays_readable`. The fixture's C opens, holds the four units,
  and `check` reports `check.event-log` PASS.
- **R6 — A merge needing a legacy boundary refuses and names the record.**
  `test_a_merge_needing_a_legacy_boundary_refuses_naming_the_record`. On the fixture, B<-C exits 2
  with `merge.theirs-history-invalid`, its output contains the first 16 hex characters of the
  offending record's digest, `Store.write_locked` is never entered, and both stores' files are
  unchanged.
- **R7 — The quickstart states it.** `test_quickstart_states_the_replayed_merge_boundary`.
  Matching ignores runs of whitespace. `docs/quickstart.md` contains `A replayed merge record names,
  as its OURS head, the record on the receiving chain where its replayed block begins`.
- **R8 — A copied mark does not restore a cleared exception.**
  `test_a_copied_mark_does_not_restore_a_cleared_exception` (Astra's case). A, B, D specify `a`, `b`,
  `d`; C marks x open in CI (`old`); A<-B, C<-A, B<-C replay; B clears; C specifies `c2`; D<-C and
  B<-D replay. B has no exception, holds `a`, `b`, `c2` and `d`, and its chain holds exactly one
  `OpenInCiMarked`.
- **R9 — Without a nested merge too.** `test_three_stores_without_a_nested_merge_keep_the_clear`
  (V4). B, D specify `b`, `d`; C marks; B<-C; B clears; C specifies `c2`; D<-C and B<-D replay. B has
  no exception and holds `b`, `c2` and `d`.
- **R10 — A receiver that never merged.** `test_a_receiver_that_never_merged_keeps_its_clear` (V7).
  U1 copies A and specifies `u1`; A marks; U2 copies A and clears; A specifies `m2`; U1<-A and U2<-U1
  replay. U2 has no exception and holds `m2` and `u1`.
- **R11 — A copied clear does not remove a later mark.**
  `test_a_copied_clear_does_not_remove_a_later_mark` (V2). A marks `shared` before the copies; C
  clears; B<-C; B marks `new-on-B`; C specifies `c2`; D<-C and B<-D replay. B's only exception is
  `x@1` with `new-on-B`.
- **R12 — A skipped redundant clear does not return as a copy.**
  `test_a_skipped_redundant_clear_does_not_return_as_a_copy` (V3, D-37). As R11, but B also clears
  before B<-C, which reports one skipped record; B then marks `new-on-B`. B keeps that mark.
- **R13 — A source holding the original directly.**
  `test_a_source_holding_the_original_directly_is_recognised` (V6). C marks; D copies C; C and D
  specify `c1` and `d`; B<-C; B clears; B<-D replays. B has no exception and holds `d`.
- **R14 — A copied setting does not undo a later setting.**
  `test_a_copied_setting_does_not_undo_a_later_setting`. C sets the CI policy to `ledger`; B<-C; B sets
  it back to `strict`; C specifies `c2`; D<-C and B<-D replay. B's policy is `strict`.
- **R15 — pack/1's four-store case.**
  `test_a_fourth_store_with_copies_of_an_advanced_history_merges`. After R1's merges, A specifies
  `a2@1` and C `c2@1`; then C<-A, D<-C, B<-D and A<-B all exit 0, and A and B each hold exactly the
  seven units, each specified once.
- **R16 — Five generations, then a no-op.**
  `test_five_generations_of_copies_keep_the_clear_and_repeat_as_a_no_op`. C marks; B<-C; B clears; C
  specifies `c2`; D<-C, E<-D, F<-E, G<-F, then B<-G replay. B has no exception and holds `b`, `c2` and
  `d` to `g`. B<-D is then a no-op that leaves B's files byte-identical.
- **R17 — An independent identical mark still reopens.** `# guard:`
  `test_an_independent_identical_mark_still_reopens`. As R9, but instead of copying C's mark, D
  records its own mark of x with reason `old`, actor `C` and the same recorded time. B<-D replays and
  B's exception is `x@1` with `old`.
- **R18 — A skipped block's generated companion must already hold on OURS.**
  `test_skipping_a_block_whose_replay_generated_a_companion_refuses` (Fable's H3). A specifies `dep`
  depending on x and freezes, readies and starts it; C copies A then; D copies the base and specifies
  `d`; B opens deviation `dev1` on `x@1` and amends it to `x-pack/2`; A<-B replays, generating one
  `UnitInvalidated` for `dep` with A's actor; D<-A replays. B<-A (block excluded by exact digest) and
  B<-D (batch excluded by origin) are each refused before lock with `merge.conflict`, and the output
  names `UnitInvalidated` and `dep`.
- **R19 — Replayed records carry their origin on the wire.**
  `test_replayed_records_carry_their_origin_on_the_wire`. C's own mark is a version-2 object with no
  `origin` or `satisfied`. After D<-C and E<-D, the one `OpenInCiMarked` on D and on E each has a
  digest different from C's record, wire `origin` equal to C's record's digest and `origin_format` 2,
  and `EventRecord.origin` equal to it; the merge record E replayed from D carries an `origin`.
- **R20 — Version-1 rule (a).** `test_a_source_batch_inside_a_version_1_merge_block_is_unproven`. On
  the upgraded `legacy-v1-merges` fixture, C<-A is refused before lock with `merge.ancestry-unproven`,
  and C does not hold `b`.
- **R21 — Version-1 rule (b).**
  `test_a_version_1_source_after_a_version_1_merge_on_ours_is_unproven`. A<-C is refused before lock
  with `merge.ancestry-unproven`, and A does not hold `c`.
- **R22 — Version-1 merges in the shared prefix still merge.** `# guard:`
  `test_version_1_merges_in_the_shared_prefix_still_merge`. U<-M and M<-U replay, and each then holds
  `a`, `b`, `m2` and `u`.
- **R23 — `upgrade` reports version-1 merges and changes only the format.**
  `test_upgrade_reports_version_1_merge_markers_and_changes_only_the_format`. Upgrading the fixture's A
  prints the line `version-1 merge markers: 1`, and C the line `version-1 merge markers: 0`. Each
  `state.json` then has `state_format` 7 and is otherwise equal to before, and every object under
  `objects/sha256` is byte-identical.
- **R24 — The docs state the rule and its trust level.**
  `test_the_docs_state_the_origin_rule_and_its_trust_level`. Matching ignores runs of whitespace.
  `docs/quickstart.md` contains `A merge skips a replayed copy of a record OURS already holds` and
  `` `upgrade` reports how many version-1 merge markers a store holds ``, and `docs/claim-boundary.md`
  contains `` `origin` and `satisfied` are claims written by the merging tool ``.
- **R25 — A fast-forward through a version-1 merge is unaffected.** `# guard:`
  `test_a_fast_forward_through_a_version_1_merge_is_unaffected` (D-41 note of 2026-11-18, from the
  pilot audit). The fixture's P (A just before its merge) merging A fast-forwards: P then holds `a`
  and `b`, and its chain's digests equal A's; merging A again is a no-op.
- **R26 — A filtered source boundary translates to the staging head.**
  `test_a_filtered_source_boundary_translates_to_the_staging_head`. C<-A first copies A's `a`; then
  A<-B makes A's marker name A's `a` as its boundary; then C<-A again replays that marker with
  `ours_head` equal to C's head before this merge.
- **R27 — A skipped companion whose effect OURS already has.**
  `test_a_skipped_companion_whose_effect_ours_already_has_merges`. In R18's stores, C<-B replays and
  leaves `dep` invalidated on C; then C<-D replays: C gains `d`, `dep` stays invalidated, and C's chain
  holds exactly one `DeviationAmended` and exactly one `UnitInvalidated` for `dep`.
- **R28 — The merge record lists exactly the facts it satisfied.**
  `test_the_merge_record_lists_exactly_the_facts_it_satisfied`. A marks x once (`shared`) before the
  copies. OURS specifies `ours-only`; OURS and THEIRS each clear; OURS<-THEIRS skips THEIRS' clear,
  and OURS' final record's `satisfied` is exactly THEIRS' clear's digest. THIRD (which specified
  `third-only`) merges OURS, then marks x afresh (`fresh-on-third`); CARRIER copies THEIRS; THIRD<-CARRIER
  replays and THIRD keeps `fresh-on-third`. Separately, when OURS and THEIRS each open their own
  deviation on `x@1`, OURS<-THEIRS's final record's `satisfied` is exactly THEIRS' `UnitBlocked` digest
  (the companion replay no longer produces).
- **R29 — A version-2 copy of a version-1 root is still unproven.**
  `test_a_version_2_copy_of_a_version_1_root_is_still_unproven`. P2 copies the fixture's P and
  specifies `p2`; P<-C replays C's version-1 `c` as a copy with `origin_format` 1. A<-P is refused before
  lock with `merge.ancestry-unproven`; A<-P2 replays, and A holds `p2` but not `c`.
- **R30 — A misplaced or disagreeing origin is refused.**
  `test_a_misplaced_or_disagreeing_origin_is_refused`. THEIRS' directly authored `t` record is edited
  to carry an `origin`: OURS<-THEIRS is refused before lock with `merge.theirs-history-invalid`, and
  `check` on THEIRS reports `check.event-log` FAIL. Separately, A marks x, CARRIER copies it, and the
  copy's actor is edited: A<-CARRIER is refused before lock with `merge.theirs-history-invalid`.
- **R31 — A generated companion is flagged, and its copies name it.**
  `test_a_generated_companion_is_flagged_and_its_copies_name_it`. In R18's stores, A's generated
  `UnitInvalidated` for `dep` has `generated` true and no `origin`; D's copy of it has `generated` true,
  `origin` equal to A's record's digest and `origin_format` 2.

## Ground rules

- Both rules live in merge replay (`vouch_mk2.store.merge`), so `merge` and `merge-git-stages` share
  them. Where the provenance filter skipped the source boundary record, `ours_head` is the staging
  head at that boundary (D-38). The merge's own final record still names the two actual input heads.
- Claims are matched on the source fact before the boundary is translated (D-33), so a replayed
  marker keeps its source actor, run claim and recorded time.
- D-41's mechanism is binding: `origin` (root of the first-authored occurrence, inherited unchanged by
  a copy of a copy) and the origin's envelope version on every record replay writes; `satisfied` on
  the merge's own final record; coverage is every digest, `origin` and `satisfied` entry on OURS'
  chain; selection is per source command batch, never a prefix cut; a batch excluded as already
  incorporated, by exact digest, D-38 block or origin, that holds a `generated` record or a copy
  of one requires OURS to already show that companion's effect, else `merge.conflict`. Every THEIRS record
  after the exact shared cut is validated, whether replayed or excluded: a malformed or misplaced
  `origin` (on a record outside a version-2 merge block) refuses with `merge.theirs-history-invalid`
  and fails `check.event-log` on that store; an `origin` disagreeing with OURS' counterpart on kind,
  subject, actor, run claim or recorded time refuses the merge the same way (a standalone `check` has
  no counterpart to compare). The
  version-1 rules (a) and (b) are exactly D-41's. No retained source objects, receipts or new artifact
  kinds; the `MergeRecorded` payload and the snapshot do not change.
- D-41's selection and version-1 rules govern replay only; a fast-forward and an exact no-op are
  unaffected (D-41 note of 2026-11-18, R25).
- State format 7 (D-13): the 6 -> 7 upgrader changes only the format number and reports the store's
  version-1 merge markers. Stored records are never rewritten; no boundary is guessed and no interval
  is widened. Formal universe sizes do not change (merges are outside the formal command union).
- The branch-merge, git-merge-recovery, merge-open-in-ci-replay and merge-redundant-clear packs keep
  passing unchanged. D-33's merge-replay-provenance R12 is adapted to D-38 and rebound (pack/2). The
  owner adapts, rebinds and re-GREENs the published units whose frozen tests pin state format 6, its
  upgrader steps or its golden, and any whose fixtures the version-2 envelope breaks.
- `docs/claim-boundary.md` § Merging stores states both rules and the trust level of `origin` and
  `satisfied`; `upgrade`'s documentation names the version-1 merge count.

## Amended by D-45 (2026-11-21, owner rebind)

R17's B clears `x` after receiving C's mark, and D marks `x` with the same reason independently.
Under D-45 the two sides' values for `x@1` differ (absent against `old`), so B<-D now refuses with
one `merge.conflict` and B keeps no exception. D's mark is still treated as independent rather than
as a copy of C's, which is what R17 guards.
