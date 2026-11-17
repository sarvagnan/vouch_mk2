# nested-merge-boundary@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_nested_merge_boundary.py`, `tests/fixtures/nested-merge/legacy-untranslated.json` or
`packs/nested-merge-boundary-1.effects.json`.
**Decision:** D-38 (settles `vouch_mk2-bd4.4.11`), from Sol's design `design-bd4.4.11`. Extends D-33.
**Effects:** in `packs/nested-merge-boundary-1.effects.json`: the translated boundary a replayed
merge writes, and the diagnostic for a legacy record (D-31 note of 2026-11-15).

## Promise

When merge replay writes an incoming `MergeRecorded`, its `ours_head` names the record on the
receiving chain where its replayed block begins, and its `theirs_head` keeps the source value, so
merges of merges keep replaying in any order without duplicating or dropping work. A store that
already holds an untranslated record keeps its bytes and stays readable; a merge that needs that
record's boundary refuses atomically and names the record.

## Common rules for every scenario

- Stores are built with `Store.init` and the CLI, each command run with `--actor` set to the
  store's name (A, B, C, D). Each store starts as a copy of one holding `shared@1`, then specifies
  its own unit (`a@1`, `b@1`, …). "X<-Y" is `merge --root X --other Y`.
- No CI run-id variable is set.
- The legacy fixture holds stores B and C as the pre-change code wrote them after A<-B and C<-A:
  C's chain holds exactly one `MergeRecorded` whose `ours_head` is not an earlier record of C's
  chain ("the offending record").

## Requirements

- **R1 — Three nested merges replay.** `test_three_store_nested_merges_all_replay`. A<-B, C<-A
  and B<-C all exit 0 as replays. B then holds `a@1`, `b@1`, `c@1` and `shared@1`, and its chain
  holds exactly four `UnitSpecified` records.
- **R2 — The replayed marker's boundary is local.**
  `test_a_replayed_merge_record_names_a_boundary_on_the_receiving_chain`. After A<-B and C<-A, the
  one `MergeRecorded` that C<-A replayed from A has A's `theirs_head`, an `ours_head` that is an
  earlier record of C's chain, and A's actor claim (D-33).
- **R3 — Later work and a fourth store.** `test_later_work_and_a_fourth_store_merge_without_duplicates`.
  After R1's merges, A specifies `a2@1` and C specifies `c2@1`; then C<-A, D<-C, B<-D and A<-B all
  exit 0, and A and B each hold exactly the seven units, each specified once.
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

## Ground rules

- The rule lives in merge replay (`vouch_mk2.store.merge`), so `merge` and `merge-git-stages`
  share it. Where the provenance filter skipped the source boundary record, `ours_head` is the
  staging head at that boundary (D-38). The merge's own final record still names the two actual
  input heads.
- Claims are matched on the source fact before the boundary is translated (D-33), so a replayed
  marker keeps its source actor, run claim and recorded time.
- No event, log, snapshot or state format change. Stored records are never rewritten; no boundary
  is guessed and no interval is widened to skip an unlocated one. Formal universe sizes do not
  change.
- D-33's frozen merge-replay-provenance pack, the branch-merge, git-merge-recovery and
  merge-open-in-ci-replay packs, and merge-redundant-clear's skip all keep passing unchanged.
- `docs/claim-boundary.md` § Merging stores states the same rule.
