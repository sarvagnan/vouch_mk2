# Acceptance pack — unit `branch-merge@1` (pack/1)

**Owner:** the acceptance owner. **Implementer:** Codex — may NOT edit this file or
`tests/test_branch_merge.py`. Decision: DECISIONS.md **D-16** ("a semantic `merge`: replay both
branches' event chains from their common ancestor through `decide()`, refusing (not guessing) on a
genuine conflict"). Depends on `event-log@1`. Bead: `vouch_mk2-179.4`.

## Requirements (each bound to a test)

R1 **`merge` verb.** `vouch-mk2 merge --root OURS --other THEIRS [--actor A]` merges the other store's
   branch into ours by REPLAY, never by comparing snapshots: it finds the common ancestor of the two
   event chains, takes the events THEIRS has that OURS lacks, and applies each through `decide()`
   against the evolving merged snapshot. Accepted events become new records appended to OUR chain, so
   the merged store's log remains a single append-only chain.
   → `test_merge_replays_the_other_branch_onto_ours`

R2 **Refuse, never guess.** If any divergent event is REFUSED by `decide()` against the merged state,
   the whole merge is refused (`merge.conflict`), naming the event, the revision it came from, and the
   refusal code from `decide()`. Nothing is written: OURS is byte-identical afterwards.
   → `test_a_genuine_conflict_refuses_the_whole_merge_and_writes_nothing`

R3 **Common ancestor.** The ancestor is the deepest record digest present in BOTH chains. Two stores
   with no shared record are refused (`merge.unrelated-histories`) rather than merged from genesis.
   → `test_unrelated_histories_are_refused`

R4 **Fast-forward.** If OUR chain is a prefix of THEIRS, the merge is a fast-forward: our snapshot
   becomes theirs and no event is re-recorded (the record digests are unchanged, not rebuilt).
   → `test_fast_forward_when_our_chain_is_a_prefix`

R5 **A no-op merge is a no-op.** If THEIRS is a prefix of OURS (or the chains are identical), the merge
   succeeds and changes nothing — no new records, same `event_head`.
   → `test_merging_an_ancestor_changes_nothing`

R6 **The merged store verifies itself.** After a successful merge, `check.event-log` passes on OURS:
   the appended chain still replays to the committed snapshot. A merge that could not produce a
   replay-verifiable store must refuse instead of committing.
   → `test_merged_store_still_replays_to_its_snapshot`

R7 **The merge is a recorded fact.** The merge appends a record naming both branch heads it merged and
   the actor CLAIM that performed it, so the history shows a merge happened and from where. Actor
   remains a claim (D-16): recorded, never verified.
   → `test_merge_records_both_heads_and_the_actor_claim`

R8 **Docs.** `docs/quickstart.md` and `docs/claim-boundary.md` stop describing a single-writer
   constraint as the only answer to concurrent work and describe `merge` and what it refuses; the
   claim boundary states that a successful merge proves the merged log replays to the merged snapshot,
   NOT that the two branches were semantically compatible in any wider sense.
   → `test_docs_describe_merge_and_what_it_does_not_prove`

## Ground rules
- Formal: any new refusal path appears in the universes if reachable there; M1–M11 hold; the
  certificate is recomputed; every count change is explained in DONE.
- Do not touch `.vouch/` (the owner's store), the README test-count line, or git.
- Every pre-existing test stays green — adjusted coherently, NEVER by weakening an assertion.
- New test functions for non-pack behaviour; pyrefly 0; import-linter contracts kept.
- `check --ci --project .` on the dogfood store must exit 0 for the owner at the end, from the working
  tree AND from the lock.
