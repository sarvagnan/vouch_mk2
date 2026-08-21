# ci-test-hermeticity@1 acceptance pack

**Owner:** Fable. **Implementer:** Codex — may not edit this file or
`tests/test_ci_test_hermeticity.py`.

## Promise

Vouch's repository tests distinguish a simulated local anchor from a real CI-observed anchor
independently of the environment in which pytest itself runs. Ambient CI-provider run identities
must not silently turn a test's local anchor into a CI-claimed anchor. Production run-identity and
anchor semantics remain unchanged.

## Requirements

- **R1 — Local scenarios are hermetic.** The focused tests that exercise locally advanced anchors
  pass when their pytest process starts with every CI run-ID variable recognized by Vouch set to a
  non-empty synthetic value. The assertion is made through pytest's process-level interface, not
  by inspecting fixture implementation.
- **R2 — The original CI failures are covered.** The focused invocation includes
  `test_ci_anchor_check_names_every_locally_advanced_unit` and
  `test_pending_anchor_ok_accepts_only_that_row`, the two tests that failed in GitHub Actions run
  output for commit `ef0bb62fb6791f68c6bbfba37b789ff63ff1c977`.
- **R3 — Existing local-anchor contracts are covered.** The invocation also includes the existing
  tests for an empty local run claim, local-vs-CI reporting, and a real CI run re-observing a local
  anchor. Portability may not stop at `GITHUB_RUN_ID` while Vouch recognizes other providers.
- **R4 — Intentional CI identity still works.** A focused scenario that explicitly supplies its
  own synthetic CI run ID continues to pass under the hermetic harness. The repair must not change
  `ci_run_id()`, `advance-anchor`, event records, or the meaning of `anchor_run_id`.

## Ground rules

- Repair test isolation at the narrowest shared test-harness seam.
- Do not add a production flag or production API solely for fault injection.
- Do not suppress failures, skip tests, special-case GitHub Actions, or change expected verdicts.
- Do not edit this frozen pack. A contradiction is a deviation, not permission to weaken it.

## Out of scope

- Changing which CI providers Vouch recognizes.
- Strengthening the organizational meaning of a recorded run-ID claim.
- Adding a second CI adapter.

