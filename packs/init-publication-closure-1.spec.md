# Acceptance pack — unit `init-publication-closure@1` (pack/1)

**Owner:** Fable under the user's 2026-08-19 method-role override. **Implementer:** Codex — may
NOT edit this file or `tests/test_init_publication_closure.py`. This unit closes D-7's remaining
publication race and hard-death coverage gap. Every non-guard test must fail against the frozen
pre-implementation tree.

## Requirements (each bound to a test)

R1 File placement is an atomic **create-if-absent** operation. If a different file appears at
   the final pathname at the publication boundary, `init` refuses and preserves that file byte
   for byte; it never uses an overwrite-capable final rename. Existing identical files retain
   the established `UNCHANGED` behavior. → `test_publication_race_never_overwrites_the_winner`

R2 Once project-visible publication begins, unwind never unlinks or removes a project-visible
   pathname. A path can be replaced after any identity comparison but before deletion, so
   `stat`/`unlink` is not identity-safe. A later failure leaves only complete, retryable owned
   artifacts and must preserve any concurrent replacement. →
   `test_rollback_does_not_unlink_a_concurrent_replacement`

R3 Hard process death is exercised after the first and second owned-file placements, not merely
   before placement. Every exposed file is complete (never truncated); no `.vouch` store is
   visible before its dependencies; after death before store publication, rerunning the public
   `init_project` operation succeeds and produces a self-checking installation. →
   `test_hard_death_after_each_file_placement_is_complete_and_retryable`

R4 A hard death immediately after atomic store publication leaves either no store or the full
   store with both dependency files present and exact. A full store is success-equivalent even
   if the original process never returned; it passes lock self-check and generated projections
   are coherent. → `test_hard_death_at_store_publication_never_exposes_an_invalid_install`

R5 The production `_init_step` no-op disappears. Fault injection patches real private
   operations from the test harness and does not widen `init_project`'s public signature.
   → `test_init_has_no_production_only_fault_hook`

R6 (guard) Ordinary failure, collisions, identical-file handling, modes, no-follow policy, and
   D-7's exactly-four-path ownership remain as specified by `init-recovery@1` and the existing
   init suite. A pre-publication refusal remains exit 2 with no writes; collision suggestions
   remain exit 3. → `test_successful_init_still_owns_only_the_closed_path_set`

R7 The public outcome distinguishes an **incomplete publication** from a refusal. If a caught
   error occurs after the first project-visible publication but before the complete store is
   visible, the CLI exits 4, names complete retryable remnants, and a rerun after the cause is
   fixed succeeds. Exit 2 remains reserved for pre-publication refusal with nothing written. →
   `test_post_publication_failure_is_exit_4_and_retryable`

## Ground rules

- The intended regular-file primitive is a same-filesystem hard link from a complete staged
  file directly to the absent final pathname; `EEXIST` is a refusal. Equivalent primitives are
  acceptable only if they provide atomic no-replace semantics on supported platforms.
- Store publication must also be no-replace; a preflight `lstat` followed by ordinary
  overwrite-capable `rename` is not sufficient.
- Directory durability (`fsync`) stays explicit. After project-visible publication starts,
  unwind removes no project-visible file or directory; identity comparison is insufficient.
- Subprocess crash tests may patch private operations in their harness; no production callback
  or no-op hook may exist solely for fault injection.
- Do not add parameters or test hooks to `init_project`, weaken no-follow checks, silently accept
  a genuine existing store, or write outside D-7's closed set. One production incomplete-
  publication outcome is required by R7. Do not edit this pack, its acceptance test, `.vouch/`,
  README's test-count line, Beads, or git.

## Amendment pack/2 (DEV-ipc-01, contract conflict)

Pack/1 accepted `stat(st_dev, st_ino)` followed by pathname `unlink` as identity-safe. It is not:
a concurrent writer can replace the entry between those operations. Portable supported
filesystems provide no conditional unlink by inode. Pack/2 preserves exit 2's existing
pre-publication “nothing written” promise and introduces exit 4 after publication begins; complete
owned remnants are left for retry instead of being deleted. This is the D-7/D-19 amendment's
explicit recovery outcome, not a relaxation of no-clobber publication.
