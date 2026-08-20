# Acceptance pack — unit `ci-candidate-validity@1` (pack/1)

**Owner:** Fable under the user's 2026-08-19 method-role override. **Implementer:** Codex — may
NOT edit this file or `tests/test_ci_candidate_validity.py`. This unit closes the first, second,
and fourth findings in the 2026-08-19 external-adoption review. Every non-guard test must fail
against the frozen pre-implementation tree.

## Requirements (each bound to a test)

R1 The generated adopter workflow and mk2's dogfood workflow validate the candidate state after
   `advance-anchor` and `generate`, before `git add`, commit, bundle, upload, or publication. They
   run `check --ci` without `--pending-anchor-ok`, then `formal`; either failure prevents candidate
   construction. The existing pre-anchor validation and read-plan/write-publish split remain.
   → `test_generated_and_dogfood_candidates_are_semantically_validated`

R2 `init --tier standard|high` without any `--test-job` refuses before the first write, exits 2,
   names the requested tier and the missing option, and leaves the project unchanged. It must not
   emit a store that verification will reject for `anchor_mode=none`.
   → `test_non_lightweight_init_without_a_test_gate_refuses_unchanged`

R3 Initialization records a coherent anchor mode. `init_project(..., tier=standard|high)` with
   the default `anchor_mode=none` refuses before writing; its production interface accepts an
   explicit `anchor_mode: AnchorMode`. The CLI resolves a supplied `--test-job` gate to
   `AnchorMode.CI`, at every tier, and records that change through the event log. Lightweight with
   no gate remains `AnchorMode.NONE`. No separate user-facing `--anchor-mode` option is added.
   → `test_init_records_a_tier_coherent_anchor_mode`

R4 Generated and dogfood workflow concurrency groups use a Vouch-specific namespace containing
   `github.repository_id` and `github.ref`; they do not derive their identity from
   `github.workflow`, which is the caller's name in a reusable workflow. Remote-head comparison
   remains the correctness check for stale candidates.
   → `test_concurrency_uses_a_vouch_specific_repository_namespace`

R5 Adoption-facing documentation states that `GITHUB_TOKEN` publication does not trigger a
   follow-up workflow, that the post-anchor `check --ci` and `formal` happen before publication,
   and that ruleset bypass versus GitHub App credentials is an explicit organizational choice
   outside Vouch's mechanical claim.
   → `test_docs_do_not_claim_a_github_token_follow_up_run`

## Ground rules

- Preserve D-20: the sole write-capable publisher executes no checked-out repository code;
  candidate transport remains non-evidence and publisher structure checks remain fail-closed.
- Preserve D-7 and the init publication outcomes. Invalid tier/gate combinations are
  pre-publication refusals; do not add an owned path or edit the adopter's caller workflow.
- Preserve the first-run `--pending-anchor-ok` exception in the pre-anchor validation job. It is
  forbidden only for the post-anchor candidate check.
- Keep immutable action pins, checkout credential hardening, default-branch/fork guards, and
  exact-base publication. Do not add a PAT or GitHub App requirement to the generated workflow.
- GitHub Actions remains the only CI adapter. Do not introduce a provider interface or a second
  provider template in this unit.
- Update existing tests and bound docs as needed, but do not edit this pack, its acceptance test,
  `.vouch/`, README's test-count line, Beads, or git history.
