# Acceptance pack — unit `init-recovery@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_init_recovery.py`.
D-19; external review finding #4 (high).

## The defect

`init_project`'s docstring promises that "every precondition ... is checked before the first
write, so a refusal never leaves a half-initialized project" — but preflight cannot see every
failure. Reproduced: with `.github/workflows` present and non-writable, the store, its config and
the lock are written first, the workflow write fails, `init` exits 2, and the retry refuses with
"store already exists" — an unrecoverable half-created project whose only repair is `rm -rf`.
Separately, an existing-but-different `vouch.lock` causes `init` to write
`vouch.lock.vouch-suggested` and **exit 0**, while binding `.vouch/config.json` to the digest of
the suggested lock rather than the one actually installed — so `self-check` fails immediately on a
"successful" init.

## Requirements (each bound to a test)

R1 A refused `init` leaves the project exactly as it found it: no `.vouch/`, no config, no lock
   written by this run, no `.github/workflows/vouch.yml`. Achieve this by staging every artifact
   and moving them into place only once all writes can succeed, or by unwinding what this run
   created on failure — the choice is yours; say which in DONE and why it is safe against a crash
   mid-unwind. A retry after the cause is fixed must succeed.
   → `test_a_failed_init_leaves_no_partial_project_and_retry_succeeds`
R2 (guard) A successful `init` always binds `.vouch/config.json` to the digest of the lock
   installed in the project, so `self-check --expect @.vouch/config.json` passes.
   → `test_a_successful_init_always_leaves_self_check_passing`
R3 A lock collision (an existing `vouch.lock` whose bytes differ) is NOT success: exit **3**,
   distinct from 0 (initialised) and 2 (refused, nothing written). The output names the suggested
   file, says what to review/replace, and `config.json` — if written at all — is never bound to a
   lock that is not the installed one. → `test_a_lock_collision_does_not_report_success`
R4 (guard) An identical lock is not a collision; an existing real store is still refused with
   "already exists". → `test_an_identical_lock_is_not_a_collision`, `test_an_existing_store_is_still_refused`
R5 `docs/commands.md`'s `init` section documents the three outcomes (0 initialised, 2 refused with
   nothing written, 3 suggestions written and action required). → `test_init_exit_codes_are_documented`

## Ground rules
- Same treatment for a colliding `.github/workflows/vouch.yml`: suggestions, exit 3, never a
  silent 0.
- Do not weaken `_require_fresh_store` or the symlink/ownership preflight.
- Do not touch `.vouch/`, README's test-count line, git. Pyrefly strict; import-linter kept; every
  pre-existing test green (`tests/test_init.py` asserts the current behaviour — update it).
- New test functions for what the pack does not cover: a partially-populated `.vouch/` directory
  that is not a real store, a read-only project root, and a crash between staging and placement.
