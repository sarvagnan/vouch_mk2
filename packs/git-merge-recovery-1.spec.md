# Acceptance pack — unit `git-merge-recovery@1` (pack/1)

**Owner:** the acceptance owner. **Implementer:** Codex — may NOT edit this file or
`tests/test_git_merge_recovery.py`. Decision: DECISIONS.md **D-21** ("Git conflict recovery gains a
Git-index-stage adapter around the existing semantic merge, not a second merge algorithm"). Depends
on `strict-types@1` and the published `branch-merge@1` semantic merge. Bead:
`vouch_mk2-b6g.13`.

## Public seam

`vouch-mk2 merge-git-stages --root ROOT [--actor ACTOR]`

The command is a Git working-tree adapter. It reads the current index without changing it,
materializes complete OURS and THEIRS stores from stage 0 plus paired stages 2 and 3, delegates the
only semantic decision to the existing store merge, regenerates canonical projections, and writes a
resolved ROOT into the working tree. The operator remains responsible for reviewing and explicitly
staging the result.

## Requirements

R1 **Real conflicted-index recovery.** In a real repository whose two branches share Vouch history
and then record compatible divergent events, `merge-git-stages` succeeds. The resolved working store
contains both branches' events, `check.event-log` passes, and `map.json` plus `vouch-context.md` are
already canonical. The command must use the current index stages; it does not ask the adopter to
materialize temporary stores.
→ `test_real_git_conflict_recovers_through_the_public_cli`

R2 **The index is an explicit operator gate.** Success does not run `git add`, `git checkout`,
`git reset`, or otherwise mutate the index. Its byte-for-byte stage listing is unchanged and the
unmerged paths remain visible until the operator executes the printed `git add -- ROOT` next step.
→ `test_real_git_conflict_recovers_through_the_public_cli`

R3 **Semantic conflicts still refuse.** If the staged branches record incompatible lifecycle events,
the existing semantic merge refusal is returned as `merge.conflict`. The working ROOT and the Git
index are unchanged; the adapter never guesses or resolves by snapshot/text preference.
→ `test_semantic_conflict_preserves_the_worktree_and_index`

R4 **A conflict is required.** A clean index, or an index with no unmerged path under ROOT, refuses as
`git.index-no-conflict` and changes nothing. This command is recovery, not a second ordinary merge
entry point.
→ `test_clean_index_refuses_without_touching_the_store`

R5 **Index shapes fail closed.** Every path used to materialize the stores is repository-relative and
under ROOT. Stage-0 entries feed both stores; each unmerged entry must have paired stages 2 and 3.
Delete/modify or otherwise incomplete stage sets refuse as `git.index-stages-incomplete`. Only
regular non-executable blob mode `100644` is accepted; symlink, executable, gitlink, and unknown
modes refuse as `git.index-mode-unsafe`. Refusal leaves working files and the index unchanged.
→ `test_incomplete_index_stages_fail_closed`
→ `test_unsafe_git_mode_fails_closed`

R6 **Filesystem capability stays no-follow.** The repository, ROOT, and every existing publication
ancestor are resolved without following symlinks. A symlinked ROOT refuses as `git.path-unsafe`
before reading or writing the target; both the target and the staged index remain unchanged.
→ `test_symlinked_store_root_fails_closed`

R7 **Operator documentation.** The quickstart gives concrete merge and rebase recovery commands,
states Git's stage-2/stage-3 meaning (including the rebase perspective), requires review plus
`git add -- .vouch` before `git merge --continue` or `git rebase --continue`, and documents all
fail-closed conditions above. The claim boundary says this adapter trusts neither branch names nor
organizational intent and proves only what the delegated semantic merge and regenerated store prove.
The generated command reference contains `merge-git-stages`.
→ `test_docs_cover_merge_and_rebase_recovery_without_overclaiming`

## Ground rules

- The frozen spec and test file are owner-controlled and byte-identical through implementation.
- No second merge algorithm and no public provider abstraction: Git plumbing is local to this one
  adapter; all lifecycle acceptance/refusal remains in `merge_store_roots`/`decide()`.
- No shell command execution, conflict-marker parsing, branch-name guessing, or index mutation.
- Refusal before the publication boundary leaves ROOT byte-identical. Publication uses the existing
  descriptor-rooted/no-follow store discipline and never writes outside ROOT.
- Pyrefly configured strict and explicit strict remain at 0 errors; import contracts remain kept;
  all existing tests and formal invariants/witnesses remain green.
- Do not touch the frozen pack, `.vouch/`, Beads, README, usage log, decisions, or git history during
  delegated implementation.
