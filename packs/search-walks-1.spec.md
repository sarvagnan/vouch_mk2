# search-walks@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_search_walks.py` or `packs/search-walks-1.effects.json`.
**Decision:** D-63 (`vouch_mk2-867`). **Seams (D-44):** the CLI (`main()` in process) on scratch
projects and directories under `tmp_path`, with directory modes set by the test. **Effects:**
declared in `packs/search-walks-1.effects.json` (none: the same files are written with the same
modes; only which existing directories can be passed through changes).

## Promise

`refresh-workflow`, `check`'s `check.workflow-current` row and `build-lock --output` walk to their
target one directory descriptor at a time. A directory the walk only passes through, to reach a
deeper directory or a file it reads, needs search permission only, on a platform with a
search-only directory open (`O_SEARCH` or `O_PATH`; the `O_RDONLY` fallback still needs read
permission): an existing directory at mode
`0111` (search only) or `0311` (write and search) on the way no longer makes the command fail with
`Permission denied`. This covers the project root and `.github` for `refresh-workflow`; the
project root, `.github` and `.github/workflows` for `check.workflow-current`, which only reads;
and every ancestor of the lock's parent for `build-lock --output`, including a directory reached
through a symlink. Creating a missing directory inside a write-and-search-only directory works
too.

The directory a file is written into (`.github/workflows` for `refresh-workflow`, the lock's
parent for `build-lock`) is still opened for reading, because the atomic write syncs that
directory after the rename and the sync needs a readable descriptor. When it is not readable, the
command fails as before with `Permission denied` and leaves the directory as it was.

What the commands write, and the modes they set, are unchanged: the workflow `refresh-workflow`
writes and the lock `build-lock` writes are byte-for-byte the files the same command writes where
no directory is restricted, and a directory the command creates is `0755`. The directories the
test restricted, and an existing lock parent, keep their modes. The text of the
`check.workflow-current` row is the same as for the same project with nothing restricted, and a
directory the check cannot search is still reported in that row as `Permission denied`. The refusals of symlinked or non-directory components, and of
creating a directory through a symlinked ancestor, are unchanged.

## Requirements

Expected RED reasons, before the change: every directory on the walk is opened for reading, so
each non-guard test gets `Permission denied` from the first restricted directory:
`refresh-workflow` and `build-lock` exit 2 (`build-lock` reporting the parent as "not a real
directory", or the symlink as not resolving to an existing directory), and
`check.workflow-current` is FAIL with `Permission denied` in its line. Guards pass before and
after: the R2 guard because a directory without search permission fails either way, and the R4
guards because the directory written into is opened for reading both times.

- **R1 — refresh-workflow.** `test_refresh_workflow_passes_through_search_only_directories`: with
  the project root, `.github`, or both at `0111` and a stale workflow, `refresh-workflow` exits 0
  and the workflow is the bytes `init` writes in a fresh project.
  `test_refresh_workflow_creates_workflows_inside_a_write_and_search_only_github`: with
  `.github/workflows` removed and `.github` at `0311`, `refresh-workflow` exits 0, creates
  `.github/workflows` at `0755` and writes those bytes.
- **R2 — check.workflow-current.** `test_check_reads_the_workflow_through_search_only_directories`:
  with the project root, `.github`, `.github/workflows`, or all three at `0111`, the row is PASS
  and `check --ci --project` exits 0.
  `test_check_compares_a_stale_workflow_through_search_only_directories`: with the project root
  and `.github` at `0111` and a stale workflow, the row is FAIL, names `refresh-workflow` and does
  not contain `Permission denied`, its line is identical to the row for the same stale project
  before any directory was restricted, and `check` exits 1; after `refresh-workflow` (with the same
  modes) the row is PASS and `check` exits 0.
  `test_check_still_reports_a_workflows_directory_it_cannot_search` (guard): with
  `.github/workflows` at `000`, the row is FAIL with `Permission denied` and `check` exits 1.
- **R3 — build-lock.** `test_build_lock_writes_through_a_search_only_ancestor`: with an existing
  `0700` parent below a `0111` ancestor, or a new parent below a `0311` ancestor, `build-lock
  --output` exits 0, prints the digest of a lock built with nothing restricted and writes the same
  bytes; the existing parent stays `0700` and the new one is `0755`. `test_build_lock_follows_a_symlink_to_a_search_only_directory`: an output
  path through a symlink to a `0111` directory writes the same bytes into the target.
- **R4 — The directory written into is still read.** Guards.
  `test_build_lock_still_needs_to_read_the_directory_it_writes_into`: a lock parent at `0311`,
  named directly or through a symlink to it, makes `build-lock` exit 2 with `Permission denied`
  and leaves the parent empty.
  `test_refresh_workflow_still_needs_to_read_the_workflows_directory`: `.github/workflows` at
  `0311` makes `refresh-workflow` exit 2 with `Permission denied` and leaves the stale workflow as
  the directory's only entry.

Every test is skipped when run as root (root ignores directory permissions) and on a platform with
neither `O_SEARCH` nor `O_PATH`. CI runs them on Linux (`O_PATH`); locally they run on macOS
(`O_SEARCH`).

## Ground rules

- Open a directory the walk only passes through with search permission only, as `init` already
  does (`_SEARCH_ONLY` in `src/vouch_mk2/init.py`: `O_SEARCH`, else `O_PATH`, else `O_RDONLY`);
  share one definition rather than copying it. Open the directory a file is written into for
  reading, whether it is reached by an ordinary open or through the symlink-follow branch.
- The tests cannot write at the host's root, so review checks three `build-lock` boundaries by
  reading the code: an output of exactly `/` keeps its existing refusal (no filename); for `/name`
  the component list is empty and the first descriptor, opened on `/`, is the directory written
  into, so it is opened for reading; for `/dir/name` the first child is the directory written into.
- On Linux, `O_PATH | O_NOFOLLOW` without `O_DIRECTORY` opens a symlink itself: keep
  `O_DIRECTORY` on every search-only open, so a symlinked component still fails the no-follow
  open and reaches the existing symlink handling.
- No change to what any command writes, the modes it sets, its refusals and messages for symlinked
  or non-directory components, the `init` walk, `write_atomic`, the store, the lock format, or any
  frozen pack or test file. `check.workflow-current`'s verdicts and the text of its FAIL line are
  unchanged apart from no longer failing on a search-only directory.
- The full suite must pass, on macOS locally and on Linux in CI.
- **Out of scope.** Writing into a directory that is not readable (a sync without a readable
  descriptor); other path-based walks; Windows.

## Limits (convergence bound, set before review)

A review blocks only on: a test-restricted search-only directory on one of these walks still
needing read permission; a directory written into that is not opened for reading (so its sync can
be skipped or fail after the rename); a symlinked or non-directory component accepted where it was
refused, or a directory created through a symlinked ancestor; a change in the bytes or modes a
command writes; a change to a frozen file, `init`'s walk or the store; or a Linux-only behaviour
(`O_PATH`) that differs from macOS on these tests. Other findings are filed as beads.
