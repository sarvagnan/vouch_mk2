# tier-summary@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_tier_summary.py` or `packs/tier-summary-1.effects.json`.
**Decision:** D-61 (`vouch_mk2-k76`). **Seams (D-44):** the CLI in process (`check`, `check
--verbose`, `check --json`, `check --help`, `set-anchor-mode`, `set-tier`) on fresh stores at each
tier and on mk2's own store read-only; a freshly built lock run as a subprocess; `run_checks()` on the stored snapshot as the oracle for the per-control lines and
the JSON detail; the IR's tier matrix (`mk2.MK2.tier_requirements`) as the independent oracle for
the counts; the text of `docs/claim-boundary.md`.
**Effects:** declared in `packs/tier-summary-1.effects.json` (none: the change is output only).

## Promise

In text output, `check` prints the `org.tier-controls` row as exactly one line, in its place among
the organizational rows:

`NOT-CHECKED    org.tier-controls  <tier>: <n> controls — <counts> (run check --verbose to list them)`

`<n>` is the number of tier controls. `<counts>` lists, in this order and separated by `, `, each of
`<k> fully checked`, `<k> partially checked`, `<k> not checked` and `<k> waived` whose `<k>` is not
zero. Each tier control counts once. A control is waived when its cell at the tier is waived (no
obligation), whatever its coverage; otherwise it is fully, partially or not checked by its
coverage, and a risk-triggered cell counts by its coverage whether or not its trigger applies. With
today's IR, `standard` reads `standard: 13 controls — 3 partially checked, 10 not checked`; no tier
has a fully checked control yet.

`check --verbose` prints the per-control lines exactly as `check` printed them before this unit (one
line for each line of the row's detail). `--verbose` changes nothing else in text output: the exit
status, standard error, every other line and their order, and the composed verdict are those of the
same `check` without it, with or without `--ci`. `check --json` is byte-identical with and without
`--verbose`, and its `org.tier-controls` detail still lists every control (the detail
`run_checks()` returns). `check --help` describes the flag.

The built lock accepts `--verbose` the same way. `docs/claim-boundary.md` states the summary in its
**Residual tier controls** row.

## Requirements

Expected RED reasons, before the change: R1 and R6 see 23 or 26 tier-control lines where one is
expected; R2–R5 and R8 fail because `--verbose` is not an option (argparse exits 2, or the help
lacks it), and R8's plain run also sees the per-control lines; R7 fails on the missing sentence.
There are no guards.

The fresh stores set anchor mode `ci` and then the tier, so `check` exits 0 at `lightweight` and
`standard` and 1 at `high` (no formal certificate). The failing variant also runs `generate` and
then `specify`, leaving stale generated files, so `check --ci` exits 1.

- **R1 — One summary line.** `test_check_prints_one_summary_line_for_the_tier_controls`
  (`lightweight`, `standard`, `high`): on a fresh store set to the tier, the only line of `check`
  containing `org.tier-controls` equals the summary, with counts taken from the IR's tier matrix.
- **R2 — `--verbose` lists every control.** `test_check_verbose_lists_every_control` (each tier):
  the `org.tier-controls` lines of `check --verbose` equal the row's detail from `run_checks()`,
  one line each, rendered as before (`NOT-CHECKED` padded to 14 columns, the check id, two spaces,
  the line).
- **R3 — Nothing else changes.** `test_verbose_changes_nothing_but_the_tier_control_lines`
  (`plain`, `ci`, `ci-failing`, all at `standard`): with and without `--verbose` the exit status
  (0, 0 and 1) and standard error are equal. The `--verbose` lines containing `org.tier-controls`
  are contiguous and equal R2's lines, and the plain output is exactly the `--verbose` output with
  that block replaced by the summary line. The block sits between the lines of its neighbours in
  `run_checks()`'s organizational order (or the heading, or the `composed:` line).
- **R4 — JSON unchanged.** `test_json_is_unchanged_by_verbose_and_lists_every_control`: `check
  --json` exits 0 and prints the same bytes with and without `--verbose`, with empty standard error;
  the output is `json.dumps(report, sort_keys=True)` and a newline; its revision and organizational
  rows (check id, verdict, detail) equal `run_checks()`'s; the `org.tier-controls` detail has more
  than one line.
- **R5 — Help.** `test_check_help_names_verbose`: whitespace normalised, the help text after
  `--verbose` in `check --help` mentions `tier control` and `text output`.
- **R6 — mk2's own store.** `test_the_dogfood_store_prints_one_summary_line`: `check` on mk2's
  `.vouch` (read-only; exit status not asserted) prints the summary for the store's own tier.
- **R7 — Claim boundary.** `test_claim_boundary_states_the_summary`: the one table row of
  `docs/claim-boundary.md` starting `| Residual tier controls |`, whitespace normalised, keeps
  "`org.tier-controls` lists active cells without full coverage" and contains "In `check`'s text
  output the row is one summary line with a count of each kind present; `check --verbose` and
  `check --json` list every control."
- **R8 — The lock.** `test_the_built_lock_accepts_verbose`: a lock built from `src/`, run as a
  subprocess on a fresh `standard` store, exits 0 for `check` and `check --verbose`, printing the
  summary and R2's lines respectively.

## Ground rules

- The change is in `check`'s text rendering and argument parsing. `run_checks()`, every
  `CheckResult` (including the row's `detail` and verdict), the agent context's tier-control text
  (`legacy_projection`), the IR, every gate, the formal universes, the state and event formats and
  the generated workflow do not change. `docs/commands.md` is regenerated, not hand-edited.
- The summary's counts come from the same tier matrix the row's detail is built from
  (`mk2.MK2.tier_requirements` and each requirement's cell at the snapshot's tier), at the time the
  summary is printed. They are not parsed out of the detail text and not tabulated per tier. The
  agent context's grouped text (`legacy_projection`) is not on the summary's path. No tier has a
  fully checked control today, so no test reaches that count; the review checks it by reading the
  code.
- Existing tests that count tier-control lines in text output may be adapted to the summary (list
  them in the DONE file). `tests/test_cli.py`'s dogfood test is expected to change to one tier row,
  keeping its honesty and CI checks; adoption-seam@1's frozen line-length test stays as it is. Keep every pinned phrase in the docs; the claim-boundary row keeps its
  current sentence and gains R7's.
- **Out of scope.** The six other organizational rows; other commands' output; a `--verbose` for
  any other row.

## Limits (convergence bound, set before review)

A review blocks only on: a summary line that differs from the Promise at any tier; counts that do
not come from the tier matrix as the ground rules say, even when the visible output is right;
`--verbose` output that differs from the pre-unit lines; any other change to text output, exit
status, standard error or JSON; a change to `run_checks()`, a `CheckResult` (including the row's
detail and the evaluator in `checks/tier.py`), the IR, a gate or a format; a lock that does not
accept `--verbose`; a missing help text or claim-boundary sentence, or a false statement in a
changed doc; or a pinned phrase removed. Other findings are filed as beads.
