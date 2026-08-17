# Acceptance pack — unit `adoption-seam@1` (pack/1)

**Owner:** the acceptance owner. **Implementer:** Codex — may NOT edit this file or
`tests/test_adoption_seam.py`. Decisions: D-18 (B2 ci policy, B4 Python 3.14), plus adoption reviews
A3–A8. Bead: `vouch_mk2-179.3` under epic `vouch_mk2-179`.

Every requirement below was probed against the tree before freezing; the ones that already hold are
marked `# guard:` in the tests and must keep holding.

## Requirements (each bound to a test)

R1 `init` is collision-safe and takes `--tier`. Guard: an existing `.vouch` is refused
   (`store already exists; refusing re-initialization`) and an existing
   `.github/workflows/vouch.yml` is never overwritten — it is reported as `suggested` and its bytes
   are left alone. New: `init --tier lightweight|standard|high` sets the store's tier at creation
   (default `lightweight`, today's value), so adoption does not require a second command.
   → `test_init_refuses_an_existing_store_and_never_clobbers_a_workflow` (guard),
     `test_init_tier_sets_the_store_tier_at_creation`

R2 `check` must not re-explore the bounded model. Today `_check_formal_certificate` calls
   `run_formal()` on every run, re-exploring ~264k states, which is why `check` costs minutes.
   The recorded certificate is verified by comparing it against the store's recorded formal tables
   and its own digest — cheaply, with no exploration. `vouch-mk2 formal` is the ONLY entry point that
   re-explores; `hook` and `mcp` never do. The verdict must stay honest: if the cheap comparison
   cannot decide, the row is NOT_CHECKED naming what was not verified, never a silent PASS.
   → `test_check_does_not_re_explore_the_bounded_model`, `test_formal_verb_still_explores`,
     `test_hook_and_mcp_never_explore`

R3 The generated CI workflow pins Python 3.14 and checks the project. It contains an
   `actions/setup-python` step with `python-version: '3.14'`, and its check step passes
   `--project .` so `check.pack-files-match` is a real row instead of NOT_CHECKED.
   → `test_generated_workflow_pins_python_314_and_passes_project`

R4 Emitted command strings use the real invocation. Every command string a projection emits
   (`next`, `next --json`, context) is built from the running program name (`argv[0]` / the parser's
   prog), so a store driven through `python vouch.lock ...` does not print `vouch-mk2 ...`.
   → `test_emitted_commands_use_the_real_invocation_name`

R5 `next` bootstraps an empty store. On a store with no units it emits at least one actionable
   bootstrap action (specify the first unit; set the tier if still default) instead of
   `no next actions`. → `test_next_emits_bootstrap_actions_on_an_empty_store`

R6 CI policy (D-18 B2). A recorded per-store `ci_policy` of `ledger | strict`, set by
   `set-ci-policy --policy …`, defaulting to **strict** for stores created by `init`. `check --ci`
   names the policy it enforced. Under `strict`, `check.ci-policy` FAILs naming every unit that has
   left `specified` and is neither terminal (published / release-bound / superseded / retired) nor
   carrying a recorded exception; `mark-open-in-ci --name U --reason …` records an exception
   (empty reason refused `ci-policy.reason-required`) and `clear-open-in-ci --name U` removes it;
   exceptions are listed by name in the check's detail. Under `ledger`, today's behaviour, and the
   report says so. → `test_strict_ci_policy_fails_a_nonterminal_unit_and_an_exception_clears_it`,
     `test_ledger_policy_is_todays_behaviour_and_is_named`, `test_init_defaults_to_strict`

R7 `status --json` emits one structured object with the same facts as the text form.
   → `test_status_json_matches_the_text_facts`

R8 No vacuous PASS. A check that verified nothing reports `NOT_APPLICABLE` citing an IR
   applicability rule (or `NOT_CHECKED`), never `PASS` with an empty subject set.
   → `test_a_check_with_nothing_to_verify_is_not_a_vacuous_pass`

R9 `org.tier-controls` is legible: one row per control rather than a single line concatenating every
   control, so the report can be read. → `test_org_tier_controls_renders_one_row_per_control`

R10 `build-lock --output` creates missing parent directories instead of failing with `Errno 2`.
   → `test_build_lock_creates_missing_parent_directories`

R11 Every reflected verb has real help text — no verb's help is a dataclass repr such as
   `AmendDeviation(deviation_id: 'str', amendment_version: 'str')`.
   → `test_every_verb_has_human_help_text`

R12 `record-evidence` warns (on stderr, non-fatal) when the recorded command line looks like it
   carries a secret (`--token`, `--password`, `AWS_SECRET…`, a long opaque value), because the
   command line is stored verbatim in an immutable object.
   → `test_record_evidence_warns_about_a_secret_looking_command_line`

R13 Python 3.14 is stated wherever a reader starts: README, `docs/quickstart.md`, `init`'s output,
   the generated workflow, and the lock's bootstrap message (which must say "Vouch requires Python
   3.14+" rather than raising `SyntaxError` on an older interpreter).
   → `test_python_314_is_stated_everywhere`

R14 Bundles: `next` recommends `bind-bundle` for a release whose bundle is unbound.
   → `test_next_recommends_bind_bundle_for_an_unbound_release`

## Ground rules
- Formal: universes gain any new refusal paths; M1–M11 hold; certificate recomputed; counts explained
  in DONE (a shrink must be explained).
- Do not touch `.vouch/` (the owner's store), the README test-count line, or git.
- New test functions for non-pack behaviour; every pre-existing test stays green (adjust coherently,
  never by weakening an assertion); pyrefly 0; import-linter contracts kept.
- `check --ci` on the dogfood store must still exit 0 when the owner runs it.
