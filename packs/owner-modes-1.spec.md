# owner-modes@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_owner_modes.py` or `packs/owner-modes-1.effects.json`.
**Decision:** D-58 (`vouch_mk2-vz5`). **Seams (D-44):** the CLI in process on scratch projects,
with the process umask set only around each call (the harness itself runs under `022`); a fresh
interpreter for the umask-at-import check; the same command run under umask `022` on an identically
built project as the oracle for exit codes, the compared output and the multiset of
(path, mode) entries; tables of the intended modes, taken from a `022` run of the code before
this unit; real Git for the conflicted merge; merge-check-upgrade@1's bound format-5 fixture,
read only; the text of `docs/claim-boundary.md`.
**Effects:** declared in `packs/owner-modes-1.effects.json`.

## Promise

Every file and directory Vouch creates gets its intended mode set explicitly right after creation,
so a command leaves the same modes under any umask as under `022`, and a umask that removes the
owner's own bits no longer breaks the store. The intended modes do not change. The sites are the
store (its root, `objects/`, `objects/sha256/`, objects and their temporaries, `state.json` and
its temporaries, the state-format mark, generated-file temporaries, `.lock` when it is created,
merge-transaction directories and their files), `init` (its staging directory and the project
directories it creates), the parent directories `write_atomic_with_parents` creates (`build-lock`),
the workflow directories `refresh-workflow` creates, `check-merge`'s private copy (its scratch
directory, `0700` directories and owner-readable, owner-writable copied files), and
`merge-git-stages` (its staging directory, the stores it materialises, a `.lock` it creates, and
the directories and files it writes into the working tree). The `record-evidence` JUnit scratch
directory already gets `0700` explicitly; the report in it is written by the evidence command.

Vouch does not change the process umask, not even temporarily: the evidence command inherits the
operator's umask, and the umask is as it was when a command returns. A file or directory that
already exists and that the command does not replace keeps its mode (an existing `.lock`, an
existing parent directory); a command that replaces a destination (an atomic rename) publishes a
new file with the writer's intended mode, as before. Commands the generated workflow later runs in
CI are outside this promise.

## Requirements

Expected RED reasons, before the change: under `0477`, `0377` and `0277`, `init` exits 2 because
it cannot open the staging directory it just created without its owner bits, so `.vouch` is never
published and every later lifecycle step exits 2 (`ENOENT` on the store root); `merge` exits 2
opening its merge-transaction directory; `merge-git-stages` exits 2 opening its staging directory;
`check-merge` FAILs reading its private copy's `state-format.json`; `build-lock` cannot create the
lock's parent; `refresh-workflow` cannot open `.github`. Under `077`, `init` and
`refresh-workflow` leave `.github` and `.github/workflows` at `drwx------`, `build-lock` leaves
the new directories at `drwx------`, and `merge-git-stages` leaves the store files it writes at
`-rw-------`, where a `022` run gives `drwxr-xr-x` and `-rw-r--r--`. The docs lack the sentence.
Guards: a permissive umask (`000`) already gives the intended modes; an existing `.lock` (`0640`)
and an existing parent directory (`0750`) already keep their modes; the evidence command already
inherits the umask and Vouch already leaves it unchanged.

- **R1 — Lifecycle.** `test_the_lifecycle_leaves_the_intended_modes_under_any_umask` (`0477`,
  `0377`, `0277`, `077`): `init`, `specify`, `freeze` with a bound pack and effects file,
  `mark-ready`, `start`, `record-evidence` RED, `generate`, `status` and `check --project` exit as
  under `022`; the (path, mode) multiset outside `.git`, the project directory included and object
  digests normalised, equals the `022` run's; every path has the mode in the lifecycle table
  (store directories `drwx------`, store files and every object `-rw-------`, `.github` and
  `.github/workflows` `drwxr-xr-x`, `vouch.yml` `-rw-r--r--`, `vouch.lock` `-rwxr-xr-x`); no
  temporary is left, and `init` leaves nothing beside the project.
  `test_a_permissive_umask_widens_nothing` (guard, `000`): the same codes, multiset and table.
- **R2 — Store merge.** `test_a_store_merge_leaves_the_intended_modes_under_an_owner_stripping_umask`
  (`0477`): `merge` of two diverged stores exits 0 like the `022` run, with the same multiset, the
  store table, no temporary, and `check` passing on the result.
- **R3 — Git stage merge.** `test_merge_git_stages_leaves_the_intended_modes_under_any_umask`
  (`0477`, `077`): a real Git conflict in `.vouch/`, with `.lock` removed so the command creates
  it; `merge-git-stages` exits 0 with the `022` run's multiset and the Git-stage table (store
  directories and the new `.lock` owner-only, published store files `-rw-r--r--`, as Git checks
  them out), no temporary, and nothing left beside the project.
  `test_merge_git_stages_keeps_an_existing_locks_mode` (guard, `077`): an existing `0640` lock
  stays `0640`.
- **R4 — Merge check.** `test_check_merge_upgrades_its_private_copy_under_an_owner_stripping_umask`
  (`0477`): a format-5 BASE, its upgrade as HEAD and MERGED; the exit code and output equal the
  `022` run's (PASS, with the BASE upgrade line); the inputs' multisets, roots included, are
  unchanged by either run; no scratch directory is left beside them.
- **R5 — New directories.** `test_build_lock_creates_its_directories_with_the_intended_modes`
  (`0477`, `077`): exit 0, the same output, the `022` multiset and the lock table (every new
  directory `drwxr-xr-x`, the lock `-rwxr-xr-x`).
  `test_build_lock_keeps_an_existing_directorys_mode` (guard, `077`): an existing `0750` parent
  stays `0750`.
  `test_refresh_workflow_creates_its_directories_with_the_intended_modes` (`0477`, `077`): exit 0,
  the `022` multiset, and `.github`, `.github/workflows` and `vouch.yml` at their table modes.
- **R6 — The umask is left alone.**
  `test_the_operators_umask_reaches_the_evidence_command_and_survives` (guard, `027`): in a fresh
  interpreter started under `027` before Vouch is imported, `record-evidence` exits 0, the evidence
  command sees `027`, and the umask is `027` after `main` returns. Every in-process call also
  asserts the umask is unchanged when `main` returns.
- **R7 — Docs.** `test_the_claim_boundary_states_the_modes`: `docs/claim-boundary.md` contains,
  whitespace normalised, "Vouch sets the mode of each file and directory it creates, so a command
  leaves the same modes under any umask. A file or directory that already exists keeps its mode,
  unless the command replaces it."

## Ground rules

- **Code.** Apply the intended mode with `fchmod` on the new descriptor, or `chmod` (through the
  directory descriptor where the site already uses one) on a new directory, at each creation site
  named in the Promise, before the new path is used; on an open with `O_CREAT` but without
  `O_EXCL` (an existing `.lock`), apply the mode only when this call created the file. Do not set
  the process umask, not even temporarily. Keep each site's intended mode. Helpers shared by the store, `init` and the merge adapters are fine. No change to
  `decide`, any gate, the state or event formats, formal universes, the generated workflow or any
  output text.
- **Docs.** The R7 sentence goes in `docs/claim-boundary.md` § Store writes; keep every pinned
  phrase.
- **Out of scope.** Repairing modes of existing stores or files; the modes of files the evidence
  command or Git writes; commands the generated workflow runs in CI; changing any intended mode.

## Limits (convergence bound, set before review)

A review blocks only on: a creation site named in the Promise whose mode still depends on the
umask; an intended mode that changed; Vouch changing the process umask; a temporary left behind; a
change to `decide`, a gate, a format, the generated workflow or output text; a false statement in
a changed doc; or a pinned phrase removed. Sites outside the Promise that a reviewer finds are
filed as beads, not blockers.
