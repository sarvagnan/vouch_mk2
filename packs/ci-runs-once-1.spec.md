# ci-runs-once@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_ci_runs_once.py` or `packs/ci-runs-once-1.effects.json`.
**Decision:** D-43 (user decision 2026-11-20).
**Effects:** none. The change is to `.github/workflows/ci.yml` and `README.md` only.

## Promise

mk2's own CI runs each check once per change. A push runs CI only on `main`; pull requests run it
on every branch. On a default-branch push, `formal` runs once, in the anchor planner's candidate
step; the gates job skips its own `formal` step exactly when the planner runs. README § Gates lists
every command the gates job runs, in CI's order, so a contributor can run the whole gate locally.

## Common rules

- The workflow is read with PyYAML (`yaml.safe_load`), which reads the bare key `on` as `True`.
- A gates "command" is each non-blank, non-comment line of a gates step's `run` script,
  whitespace-collapsed. README lines are taken from the `sh` block under `## Gates`, with a
  trailing shell comment (` # ...`) dropped and whitespace collapsed. A leading `python3 ` counts
  as `python ` on both sides (macOS has no bare `python`).

## Requirements

- **R1 — Push runs only on main.** `test_push_runs_only_on_main`. The `push` trigger is exactly
  `{branches: [main]}`.
- **R2 — Formal once per default-branch run.**
  `test_gates_formal_is_skipped_exactly_when_the_anchor_planner_runs`. The gates job has exactly
  one step whose `run` is `uv run vouch-mk2 formal`, and its `if` is exactly
  `${{ !(<anchor_plan's if>) }}`.
- **R3 — README § Gates lists every gates command in order.**
  `test_readme_gates_lists_every_gates_command_in_order`: the gates commands are a subsequence of
  the README lines. `test_readme_gates_names_the_scratch_directory`: the block's first command sets
  `RUNNER_TEMP=`.
- **R4 — Pull requests still run everywhere.** `# guard:`
  `test_pull_requests_still_run_on_every_branch`: `pull_request` has no filter and the only
  triggers are `push` and `pull_request`.
- **R5 — The planner keeps its formal.** `# guard:`
  `test_the_anchor_planner_still_runs_formal_after_gates`: `anchor_plan` needs `gates`; its
  unconditional candidate step runs the lock's `formal` before `git add -- .vouch/`.
- **R6 — No other gates step becomes conditional.** `# guard:`
  `test_no_other_gates_step_is_conditional`.
- **R7 — README commands stay shell-parsable.** `# guard:`
  `test_readme_gates_lines_are_shell_parsable`.

## Ground rules

- Change only `.github/workflows/ci.yml` (the `on:` block and the gates `formal` step) and
  `README.md` § Gates (its lead sentence and `sh` block). The README test-count line keeps its
  shape (`uv run pytest            # N tests: ...`), which `tests/test_doc_binding.py` reads.
- Do not change the workflow `init` generates (`src/vouch_mk2/init.py`), any other job or step,
  permissions, concurrency, action pins, or `docs/method.md`.
- No source, store or format change; formal sizes and the certificate are unchanged.

## Verification outside pytest (final pass)

After the merge, the owner confirms on GitHub that the pull request ran CI once (no second
`push` run for the branch) and that the main run's gates job skipped `Formal checking` while the
anchor planner ran `formal`.
