# Acceptance pack — unit `cli-completeness@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_cli_completeness.py`.
Beads bd4.5 (bundle-inventory) + bd4.7 (next completeness, record-evidence help). Depends on
`pack-binding@1` landing first (both touch cli.py); implement on top of it.

R1 `vouch-mk2 bundle-inventory --root R --tree DIR [--exclude PATTERN…] [--json]`: builds the
   inventory with `bundle.build_inventory(tree, exclusions)`, refuses a missing tree or a tree
   path that is a symlink (exit 2, nothing written), places the object under `store.write_locked()`
   via the store's object-placement path (objects-first, CAS), prints `bundle inventory <digest>`
   (text) or `{"accepted": true, "inventory_digest": …}` (json). Not a lifecycle verb (not
   reflected; `LIFECYCLE_VERBS` unchanged); ships in the lock; listed in docs/commands.md.
   → `test_bundle_inventory_builds_places_and_prints_the_digest`, `test_bundle_inventory_json_and_then_bind_bundle_round_trip`,
     `test_bundle_inventory_refuses_a_missing_or_symlinked_tree_and_writes_nothing`, `test_bundle_inventory_is_read_only_safe` (guard)
R2 `next` prints complete invocations: `vouch_mk2.cli.invocation_for(action, root) -> str` renders
   `vouch-mk2 <verb> --root <root> <subject flag> <value>` plus, for every OTHER required field of
   the reflected command (no default), `--flag <FLAG_NAME>` placeholders (uppercase snake of the
   field name); values shlex-quoted; the reason after `  # `. `next --json` prints
   `{"actions": [{"command", "subject_kind", "subject_id", "reason", "argv": [...], "placeholders": ["--flag", …]}]}`.
   → `test_next_invocations_include_every_required_argument_as_value_or_placeholder`, `test_next_placeholders_cover_all_reflected_required_fields`, `test_next_json_carries_structured_invocations`
R3 `record-evidence --help` shows the `-- <argv...>` tail in usage and an epilog stating the exact
   claim ("the recorded outcome is the exit status of a process this verb spawned"). Regenerate
   `docs/commands.md` (it must include `## bundle-inventory` and the new help).
   → `test_record_evidence_help_shows_the_argv_tail`, `test_commands_reference_reflects_the_new_help_and_verb`
R4 Quickstart: replace the bundle parenthetical with a `bundle-inventory` + `bind-bundle` block
   in the release section (owner writes the prose; implementer may add the two command lines
   inside a new ```sh block and delete the parenthetical), and delete the sentence "its help does
   not show that tail yet". The quickstart's executable test must stay green.
   → `test_quickstart_bundle_parenthetical_now_uses_the_verb`

Ground rules: no changes to decide/model (bind-bundle already exists); pyrefly/lint-imports/full
pytest green; new test functions for non-pack behavior; do not touch .vouch/, README count, git.
