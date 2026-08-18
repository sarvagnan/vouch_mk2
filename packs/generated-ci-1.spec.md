# Acceptance pack — unit `generated-ci@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_generated_ci.py`.
External review blockers #1 and #2. Unit #14 fixed the anchor ordering in mk2's OWN ci.yml and
asserted only that; the fragment `init` writes for adopters kept the broken shape. This pack
asserts the GENERATED artifact, which is the thing every adopter actually receives.

## Requirements (each bound to a test)

R1 `vouch_mk2.init.generated_workflow() -> str` returns the fragment text (so it is testable and
   `init` writes exactly it). → used by every test below
R2 Two jobs: a **validation** job running `self-check`, `check --ci … --pending-anchor-ok`, and
   `formal`; and a separate **anchor** job with `needs: <validation>`. Anchoring never shares a job
   with validation. → `test_validation_runs_before_anchoring_and_anchoring_is_a_separate_job`
R3 New `check` flag `--pending-anchor-ok`: exit 0 when the ONLY mechanical non-PASS row is
   `check.evidence-anchors` reporting heads with no workflow run claim; the row is still printed
   (never hidden); any FAIL row, or any other NOT-CHECKED row, still exits 1. Without the flag the
   behavior is unchanged. This is what makes a first CI run possible at standard/high: today the
   adopter's `check` exits 1 and the workflow never reaches the step that would fix it.
   → `test_validation_tolerates_a_pending_anchor_so_the_first_run_is_not_a_deadlock`, `test_pending_anchor_ok_accepts_only_that_row`
R4 Permissions: no job-wide `contents: write`; the validation job declares `contents: read` and
   `persist-credentials: false` on its checkout; only the anchor job declares `contents: write`.
   → `test_validation_job_has_no_write_credentials`
R5 Every `uses:` is pinned to a 40-hex commit SHA (with the human-readable version in a trailing
   comment). → `test_third_party_actions_are_pinned_to_commit_shas`
R6 The anchor job runs only on `push` to the repository's default branch (never on
   `pull_request`, never on forks), and carries NO `[skip ci]` marker: a skipped bot commit can
   strand a required check on a PR head, and on a default-branch push the follow-up run simply
   finds nothing to advance. → `test_anchor_job_only_runs_on_default_branch_pushes_and_never_on_pull_requests`
R7 Top-level `concurrency:` keyed by workflow+ref so two runs cannot race the anchor push.
   → `test_workflow_has_concurrency_control`
R8 The anchor step advances only heads whose current anchor lacks a run claim (read
   `anchor_run_id` from the state), commits nothing when nothing changed.
   → `test_anchor_step_advances_only_heads_that_lack_a_run_claim`
R9 The fragment tells the adopter, in a comment, to add their own test job to the validation
   job's `needs:` — D-18 says anchoring follows the project's tests passing, and the generated
   workflow cannot know that job's name. → `test_fragment_tells_the_adopter_to_wire_their_own_tests`
R10 (guard) mk2's own `.github/workflows/ci.yml` keeps unit #14's property.
   → `test_mk2_own_ci_still_anchors_before_asserting_its_store_is_clean`

## Ground rules
- Do not weaken `check.evidence-anchors` or any other check; `--pending-anchor-ok` is a REPORTING
  tolerance in the CLI's exit code only, and must be visible in the output.
- `docs/commands.md` regenerated; the quickstart's CI section and `docs/claim-boundary.md` mention
  what the generated workflow does and does not grant.
- Do not touch `.vouch/`, README's test-count line, git. Pyrefly strict; import-linter kept; every
  pre-existing test green (`tests/test_init.py` and the operator-docs tests will need updating to
  the new fragment — that is expected and allowed).
- New test functions for anything the pack does not cover (e.g. `init` writing exactly
  `generated_workflow()`).
