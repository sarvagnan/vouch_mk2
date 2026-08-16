# Acceptance pack — unit `operator-docs@1` (pack/1)

**Owner:** Fable (also writes the quickstart prose — user-facing copy stays Claude-side per the
delegation policy). **Implementer (mechanical parts):** Codex — the generator script, the
generated reference, README head. Nobody edits `tests/test_operator_docs.py`. Bead: bd4.3.

## Requirements (each bound to a test)

R1 `docs/quickstart.md` walks a scratch project end to end using ONLY `python3 vouch.lock <verb>`
   (the single-artifact transport, D-2): obtain/build the lock → `init` → `specify` → write a
   real failing acceptance test → `freeze` (pack digest computed in-shell) → `mark-ready` →
   `start` → `record-evidence --kind red -- python3 -m pytest …` (RED for real) → implement →
   `record-evidence --kind green` → `mark-done` → `publish` → one deviation raised, amended,
   closed, respecified, re-frozen → `prepare-release`/`set-criteria` (four flags) →
   `start-deploy` → `start-verification` → `complete` → `check --ci` → `generate` → `upgrade
   --dry-run` (shows "already at format 1"). ≥ 10 ```sh blocks; at least one block tagged
   ```sh refuses that demonstrates a refusal (exit 2, violation code shown).
   → `test_quickstart_has_a_full_lifecycle_worth_of_blocks`, `test_every_quickstart_block_runs_in_order_with_the_expected_outcome`,
     `test_quickstart_ends_with_a_published_unit_a_succeeded_release_and_a_clean_check`, `test_quickstart_shows_at_least_one_refusal_and_states_the_evidence_claim`
R2 Every ```sh block runs under `sh -e` in a scratch directory containing `vouch.lock`, with
   `python3` on PATH (the harness shims it to the test interpreter so pytest exists) — blocks
   are executed IN ORDER by the pack, so they must be self-contained and idempotent-free
   (no `alias`, no interactive steps, no network).
R3 The quickstart states mk2's exact claim for `record-evidence` ("the recorded outcome is the
   exit status of a process this verb spawned…") and, in the upgrade section, the no-invented-
   state rule and the `--was-published/--never-published` assertions.
R4 Every `vouch.lock <verb>` named in the quickstart is a real subcommand.
   → `test_every_verb_the_quickstart_names_is_a_real_subcommand`
R5 `scripts/gen_commands_doc.py [--check]` renders `docs/commands.md`: one `## <verb>` section
   per subcommand (all of `_parser()`'s subcommands, lifecycle and non-lifecycle) containing
   that verb's `--help` text verbatim in a fenced block, deterministic; `--check` exits 0 iff
   the file equals fresh output (this is the drift binding). → `test_commands_reference_is_generated_and_fresh`
R6 README's first 12 lines point to `docs/quickstart.md` and `docs/commands.md` ("Start here").
   → `test_readme_starts_with_the_quickstart`

## Ground rules
- No new runtime code paths; docs + one script + README head only. Existing tests green.
- The quickstart must run in CI's no-network walkthrough environment (nothing fetched).
