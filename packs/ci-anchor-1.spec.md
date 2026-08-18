# Acceptance pack — unit `ci-anchor@1` (pack/1)

**Owner:** the acceptance owner. **Implementer:** Codex — may NOT edit this file or
`tests/test_ci_anchor.py`. Decision: DECISIONS.md **D-18 B5 (option a)**, which depends on D-16's event
log for the run-id claim. Bead: `vouch_mk2-179.5`.

## Requirements (each bound to a test)

R1 **The generated workflow anchors after the project's own tests pass.** `init`'s workflow gains a step
   that, after the check step succeeds, runs `advance-anchor` for every unit that has an evidence head,
   regenerates the projections, and commits `.vouch/` back as a bot commit whose message contains
   `[skip ci]` so it does not retrigger. → `test_workflow_anchors_after_checks_and_commits_with_skip_ci`

R2 **Fork pull requests do nothing.** The anchoring step is conditioned so that a `pull_request` from a
   fork neither anchors nor pushes. The condition must be visible in the workflow file itself (a
   `if:` guard comparing the head repository to the base repository), not merely described in prose.
   → `test_workflow_does_not_anchor_on_fork_pull_requests`

R3 **The anchor records which run advanced it.** When `advance-anchor` runs with a CI run id in the
   environment, the event record it appends carries that run id (D-16's claim), so the store can say
   which workflow run advanced an anchor. Locally, with no CI environment, the run id is empty.
   → `test_advance_anchor_records_the_run_id_claim`, `test_local_anchor_records_no_run_id`

R4 **The claim is displayed as a claim.** Where anchors are reported (`status`, the generated context),
   an anchor advanced by a run is shown as `advanced by workflow run N`; one advanced locally is shown
   without any run claim rather than with a blank or invented one.
   → `test_anchor_run_claim_is_displayed_when_present_and_absent_when_not`

R5 **Honest NOT_CHECKED at anchor mode `ci`.** At anchor mode `ci`, a unit whose anchor carries NO run
   claim must not be reported as if CI had observed it: the anchor row names those units and stays
   NOT_CHECKED for them rather than PASS. → `test_ci_anchor_mode_does_not_claim_ci_for_locally_anchored_units`

R6 **Docs.** `docs/claim-boundary.md` states that the run id is a CLAIM the caller's environment
   asserted, verifiable by a human against the run URL and never verified by Vouch; the quickstart
   explains the bot commit and the fork-PR behaviour. → `test_docs_state_the_run_id_is_a_claim`

## Ground rules
- Formal: any new refusal appears in the universes if reachable; M1–M11 hold; the certificate is
  recomputed; every count change explained in DONE.
- Do not touch `.vouch/` (the owner's store), the README test-count line, or git.
- Every pre-existing test stays green — adjusted coherently, NEVER by weakening an assertion.
- New test functions for non-pack behaviour; pyrefly 0; import-linter contracts kept.
- `check --ci --project .` on the dogfood store must exit 0 for the owner at the end, from the working
  tree AND from the lock.
