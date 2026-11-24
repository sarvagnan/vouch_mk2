# merge-rerun@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_merge_rerun.py` or `packs/merge-rerun-1.effects.json`.
**Decision:** D-59 (`vouch_mk2-d7k`). **Seams (D-44):** the CLI in process on scratch Git
repositories; real Git for the conflicts (a merge and a rebase), for restoring a conflict and for
taking one side; the store read back through `Store.open(read_only=True)`; byte trees of `.vouch`
(without `.lock`) and `git ls-files --stage` as the oracles for "writes nothing"; `shlex.join` of
the supplied root (under a path with a space) as the oracle for the printed commands; the named
sections of `docs/quickstart.md` and `docs/claim-boundary.md`.
**Effects:** declared in `packs/merge-rerun-1.effects.json`.

## Promise

`merge-git-stages` no longer replaces a divergent merge it already wrote. Under the publication lock,
before its first write, it reads the working store. If that store opens as a valid store with a
verified chain, its history incorporates both index-stage heads, and its head is neither of them,
recovery refuses with `git.already-recovered`, exits 2 and writes nothing: no store payload and no
index entry changes (taking the lock may create `.lock`, as before). "Incorporates" means reachable
from the working head through predecessors and merge parents (the OURS and THEIRS heads a
`MergeRecorded` names), nested merges included; the stage heads are the event heads of the OURS
and THEIRS stores as materialised from the index, captured before the directory merge changes its
scratch OURS. A rebase swaps which branch is OURS, but not this two-head test.

The message names the root and both ways forward, each as `shlex.join` of the supplied root:
`git add -- <root>` (stage the result) and `git checkout --conflict=merge -- <root>` (restore the
conflict to merge again, which discards changes made to the working store since).

The rule sees the store, not who wrote it or when. A working store that does not open or does not
verify (Git's conflict markers, a broken chain), whose head is one of the stage heads (a side taken,
a fast-forward or no-op recovery, a `state.json` Git merged cleanly), or whose history incorporates
only one stage head (a side taken plus later work) is replaced as before. A working store that
incorporates both heads by other means is refused too. A semantic refusal of the merge itself
(`merge.conflict` and the other existing reasons) is reported as before; which refusal wins when
both apply is not promised.

## Requirements

Expected RED reasons, before the change: in R1 and R2 the second `merge-git-stages` exits 0
("replayed; revision 4") where 2 is expected, after attempting a fresh merge over the working store;
R7 fails on the first missing sentence (quickstart). Guards (R3–R6): each exercises a path
that already recovers successfully and must still do so.

- **R1 — Re-run refuses.** `test_a_rerun_before_staging_refuses_and_keeps_the_first_result`
  (`merge-other-actor`, `merge-same-actor`, `rebase`): after a successful recovery of a real
  conflict, a second run exits 2 with `git.already-recovered` and both full quoted commands in its
  output; the `.vouch` byte tree equals the first result's; the index equals the conflicted index;
  the store holds the three units.
- **R2 — Later work is kept.** `test_a_rerun_keeps_work_recorded_on_the_recovered_store`: after a
  recovery and a `specify` of a fourth unit on the working store (so the head is no longer the merge
  record), a second run refuses the same way and leaves the byte tree, the index and the four units
  as they were.
- **R3 — Restored conflict.** `test_a_restored_conflict_recovers_again` (guard): after a recovery
  and `git checkout --conflict=merge -- .vouch`, a second run exits 0 with the three units and the
  conflicted index.
- **R4 — One side taken.** `test_a_working_store_that_took_one_side_is_replaced` (guard; `--ours`,
  `--theirs`): with the working store checked out from one stage (two units), recovery exits 0 with
  the three units and the conflicted index.
- **R5 — One side plus later work.** `test_one_side_with_later_work_is_replaced` (guard; `--ours`,
  `--theirs`): with the working store checked out from one stage and a further unit specified on
  it, so its head is neither stage head, recovery exits 0 with the three merged units and the
  conflicted index. Together with R4 this catches a check of head inequality alone or of one head
  only.
- **R6 — Unverifiable working store.** `test_a_working_store_with_a_broken_chain_is_replaced`
  (guard): after a recovery, the predecessor of the working head is deleted, leaving a readable
  snapshot whose chain does not verify; a second run exits 0 with the three units and the
  conflicted index.
- **R7 — Docs.** `test_the_docs_state_the_refusal`: whitespace normalised, the paragraph of
  `docs/quickstart.md` starting "Recovery refuses if any of the following hold" contains "The
  working store already incorporates both stages, as after an earlier divergent recovery
  (`git.already-recovered`): review and stage it, or run `git checkout --conflict=merge -- .vouch`
  to merge again, which discards any changes made since" (a bullet in that list), and the
  **Git conflict recovery (`merge-git-stages`):** paragraph of `docs/claim-boundary.md` contains
  "Recovery refuses with `git.already-recovered` and writes nothing when the working store is a
  valid store whose history incorporates both stage heads and whose head is neither, so a second
  run cannot replace an earlier divergent recovery. It cannot tell who wrote that store. A
  fast-forward or no-op recovery leaves a stage head in place and can run again."

## Ground rules

- **Code.** The check lives in `src/vouch_mk2/git_merge.py`, inside the existing publication lock
  and before the first write; reading the store read-only takes no lock and sweeps nothing. Read the
  working store at the directory the publication descriptor holds (confirm with `os.path.samestat`
  against `os.fstat(root_fd)` that the path opened is that directory; if it is not, raise
  `git.concurrent-update`). Verify its chain with the existing event-log readers and walk merge
  parents with the existing replay-provenance machinery (chain verification, not replay-claim
  validation, which the directory merge does separately). Any failure to open, decode or verify the
  working store means "not a recovered store", and recovery continues as before; keep those errors
  local to the probe. Raise the refusal as the adapter's other `git.*` reasons are raised (a
  `StoreError`, exit 2 through the CLI). Keep owner-modes@1's modes. No change to
  `merge_store_roots`, `decide`, any gate, the state or event formats, formal universes, the
  generated workflow, or any existing output text.
- **Docs.** The two R7 sentences in the places named; keep every pinned phrase.
- **Out of scope.** Directory `merge`; staging or checking the index for the operator; telling who
  produced a working store or when.

## Limits (convergence bound, set before review)

A review blocks only on: a re-run that replaces a working store whose verified history incorporates
both stage heads and whose head is neither; a refusal with another reason, exit status or without
both full quoted commands; a refusal that changes a store payload or index entry; a working store
outside the rule (one that does not open or verify, one at a stage head, one incorporating only one
stage head) being refused; the check made before taking the publication lock, after a write, or on
a directory other than the one the lock holds; a change to `merge_store_roots`, `decide`, a gate, a
format, the generated workflow or existing output text; a false statement in a changed doc; or a
pinned phrase removed. The lock scope has no concurrent test, so the review checks it by reading the
code. Other findings are filed as beads, not blockers.
