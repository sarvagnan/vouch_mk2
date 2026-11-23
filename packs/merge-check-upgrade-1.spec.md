# merge-check-upgrade@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_merge_check_upgrade.py`, `tests/fixtures/merge-check-upgrade/*.json` or
`packs/merge-check-upgrade-1.effects.json`.
**Decision:** D-53 (`vouch_mk2-bd4.4.25`). **Seams (D-44):** the CLI in process (`upgrade`,
`record-evidence`, `check-merge`); store fixtures written by the code of their time
(`tests/fixtures/merge-check-upgrade/format-N-store.json` for formats 3–6, each naming its
source commit: units `a@v1` published after RED and GREEN and `b@v1` in progress after RED);
`tempfile.tempdir` set to a scratch directory; the text of `docs/claim-boundary.md`.
**Effects:** declared in `packs/merge-check-upgrade-1.effects.json`.

## Promise

**Older BASE or HEAD.** Before reading the three stores as today, `check-merge` reads the raw
state format of BASE and of HEAD. For each one (BASE first) whose format is 4 or more but below
the running Vouch's `CURRENT_STATE_FORMAT`, it copies the store into its private temporary
directory and upgrades the copy exactly as `vouch-mk2 upgrade` would with no assertions, then
uses the upgraded copy in place of the input for everything that follows (reading, replay,
comparison). Everything else is as before.

**Output.** The first line is the verdict, unchanged in wording (`PASS check-merge: ...` or
`FAIL check-merge: ...`). Right after it, for each input that was upgraded, in the order BASE then
HEAD, comes one line exactly `  upgraded a private copy of <BASE|HEAD> from format <N> to <M>`
(two leading spaces; `M` is `CURRENT_STATE_FORMAT`); these lines appear whatever the verdict.
Any detail lines the verdict already had follow them.

**Before the event log.** If BASE or HEAD is at format 3 or older (BASE checked first), the check
fails without upgrading or replaying: exit 1, a first line starting `FAIL check-merge` that names
the side (`BASE` or `HEAD`) and contains `before the event log`.

**MERGED.** MERGED is never upgraded. If it is at an older format than the running Vouch, the
check fails: exit 1, a first line starting `FAIL check-merge` that contains `MERGED` and
`upgrade`. A store newer than the running Vouch fails as today.

**Nothing written.** No file under BASE, HEAD or MERGED changes (a writable open's housekeeping
must not touch them either), and the private temporary directory is removed in every case.

## Requirements

Each test fails before the change because `check-merge` refuses an input older than the running
format before comparing anything (`FAIL check-merge: state_format: format N is older than
supported format 7; run vouch-mk2 upgrade`), so the expected verdict, upgrade line or named side
is absent, or because the docs lack the text, except the guards.

- **R1 — An upgrade pull request passes.** `test_an_upgrade_pull_request_passes` (formats 4, 5,
  6): BASE is the fixture, HEAD its upgrade, MERGED a copy of HEAD: exit 0, first line
  `PASS check-merge: replay matches MERGED (no-op)`, the BASE upgrade line, no HEAD line.
- **R2 — With new evidence.** `test_an_upgrade_with_new_evidence_passes`: HEAD also records a
  GREEN for `b@v1`: `PASS ... (fast-forward)` and the BASE line.
- **R3 — An older HEAD.** `test_an_older_head_is_upgraded_too`: BASE is the upgrade plus a GREEN,
  HEAD the format-5 fixture, MERGED a copy of BASE: `PASS ... (no-op)`, the HEAD line, no BASE
  line. `test_both_older_inputs_are_upgraded_in_order`: BASE at 5 and HEAD at 6 (unrelated
  stores): both lines, BASE first, whatever the verdict.
- **R4 — Tampering still fails.** `test_a_merged_store_missing_the_heads_work_still_fails`: first
  line `FAIL check-merge: replay differs from MERGED`, the BASE line, a line naming `b@v1`.
  `test_a_hand_edited_upgraded_head_still_fails`: a unit status edited by hand in the upgraded
  HEAD (and MERGED): `FAIL`, `merge.head-history-invalid`, the BASE line.
- **R5 — Nothing written.** `test_inputs_are_never_written_and_no_temporary_directory_is_left`:
  with a leftover temporary file seeded in each input, a passing, a failing and a reversed run
  leave every input byte-for-byte unchanged and the scratch temporary directory empty.
- **R6 — An older MERGED.** `test_an_older_merged_store_still_fails_and_is_named`.
- **R7 — Before the event log.** `test_a_store_from_before_the_event_log_fails_with_its_reason`
  (BASE, HEAD).
- **R8 — Guards.** `test_a_store_newer_than_vouch_still_fails` (passes today and must keep
  passing); `test_the_fixtures_are_the_formats_they_name` (fixture integrity).
- **R9 — Docs.** `test_claim_boundary_states_the_upgrade`: `docs/claim-boundary.md` contains,
  whitespace normalised, "When BASE or HEAD is at an older state format than the running Vouch,
  `check-merge` upgrades a private copy of it first, as `upgrade` would, so a pull request that
  upgrades the store is checked too." and "MERGED is never upgraded, and a store older than
  format 4, from before the event log, fails the check."

## Ground rules

- **Code.** The change is confined to `check-merge` (and helpers it needs); it reuses the store
  upgrade path rather than a second upgrader. No change to `upgrade`, `merge`, `decide`, gates,
  state or event formats, the generated workflow or mk2's CI. `pr-merge-check@1`'s frozen tests
  must keep passing unchanged.
- **Docs.** The R9 sentences in `docs/claim-boundary.md`'s "Pull-request merge check" paragraph;
  keep every pinned phrase; regenerate `docs/commands.md` only if help text changes.
- **Out of scope.** Upgrading MERGED; refreshing an adopter's workflow after a re-pin
  (`vouch_mk2-bd4.4.26`); `merge-git-stages` with an older THEIRS store; upgrades that need
  `--was-published`/`--never-published` answers (format 0, covered by the format-3 rule).

## Limits (convergence bound, set before review)

A review blocks only on: a verdict, exit code or upgrade line that differs from the Promise for a
store an honest Vouch release wrote; an input file changed or a temporary directory left; MERGED
upgraded; a format-3-or-older input replayed; a change outside `check-merge`'s behaviour; a false
statement in a changed doc; or a pinned phrase removed. Hand-edited stores remain `vouch_mk2-22a`'s
boundary (D-50). Wording beyond what the tests pin is a note.
