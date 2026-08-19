# Acceptance pack — unit `ci-anchor-closure@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_ci_anchor_closure.py`.
Closes the loop batch A opened: D-18 B5 gave adopters a CI anchor job and made mk2's own store depend
on CI-signed anchors, but mk2's own `ci.yml` never gained the job, and the no-network walkthrough (a
plan §7 DoD probe) began exiting 1 unrecorded.

## Requirements (each bound to a test)

R1 (guard) A locally advanced anchor is NOT_CHECKED ("no workflow run claim"); the same head advanced
   with a CI run identity in the environment records `EvidenceHead.anchor_run_id` and the row PASSes.
   Do not change this mechanism. → `test_a_locally_advanced_anchor_is_not_checked_and_a_ci_run_resolves_it`
R2 `.github/workflows/ci.yml` (mk2's OWN workflow, hand-maintained — not the fragment `init` writes)
   gains an anchor step: `permissions: contents: write`; runs only when the event is not a fork pull
   request (same guard shape as the generated fragment); advances every unit whose head lacks a run
   claim; commits `.vouch/` (state + regenerated projections) with `[skip ci]` and pushes to the
   branch; positioned AFTER the test gates and BEFORE the dogfood step, so the dogfood assertion sees
   anchors that carry run identities. **pack/2 (DEV-cac-01):** because `uv run pytest` is itself one
   of those earlier gates, the two dogfood tests in `tests/test_cli.py` must accept a store whose
   ONLY mechanical NOT-CHECKED row is `check.evidence-anchors` "no workflow run claim", and must
   still refuse any FAIL row or any other NOT-CHECKED row — otherwise CI goes red at the test gate
   and never reaches the job that resolves it. This is D-10's promise restated against the anchor
   rule, not a weakening of it.
   → `test_mk2_ci_advances_anchors_before_asserting_its_own_store_is_clean`, `test_the_suite_can_pass_before_ci_has_anchored`
R3 (guard) The dogfood step keeps its strict assertion — no mechanical NOT-CHECKED row in mk2's own
   store — unweakened. → `test_mk2_ci_still_refuses_any_mechanical_not_checked_row`
R4 `scripts/walkthrough.py` demonstrates BOTH states of the anchor rule on its scratch project:
   first advance an anchor locally and show `composed: NOT-CHECKED` with exactly one mechanical row
   (`check.evidence-anchors`, "no workflow run claim") — printing something containing "anchor
   pending"; then simulate a CI run by advancing again with `GITHUB_RUN_ID` set in the child
   environment and show `composed: PASS` with zero mechanical NOT_CHECKED. The script exits 0 only
   when both hold, and its final line still reports "composed PASS". `--assert-no-network` keeps
   working (nothing fetched). → `test_walkthrough_demonstrates_the_pending_anchor_and_then_a_simulated_ci_run`,
     `test_walkthrough_from_the_lock_ends_clean_again`
R5 DECISIONS.md D-18 gains a dated note recording the constraint the implementation revealed: at
   anchor mode `ci`, no project — including mk2 — composes PASS until a real CI run has advanced an
   anchor; a locally advanced anchor is honestly NOT_CHECKED, and lowering the anchor mode to hide
   that is the gate-bending D-11 forbids. → `test_d18_records_that_ci_mode_cannot_compose_pass_before_a_real_run`
R6 `docs/quickstart.md` (§6, where anchoring is introduced) and `docs/claim-boundary.md` state the
   same constraint in operator terms — what they will see, why, and that one CI run resolves it.
   → `test_docs_state_the_constraint_where_an_operator_meets_it`

## Ground rules
- The mechanism is batch A's and stays: do not weaken `check.evidence-anchors`, do not add a local
  "pretend this was CI" flag, do not lower mk2's anchor mode.
- mk2's own store is NOT to be anchored locally with a forged run id — it stays NOT_CHECKED until its
  real CI runs. The owner does not fake the thing this unit exists to make real.
- Pyrefly strict; import-linter kept; every pre-existing test green; README count is the owner's.
- `pytest.ini`/marker for `slow` if the walkthrough test needs one — it must still run by default in CI.

## Amendment pack/3 (DEV-cac-03, pack-defect, adjudicated amend)

pack/2's test asserted mk2's LIVE store had no FAIL row at the moment it ran. That made it a
trap rather than a check: any transient store condition broke it, and repairing the pack
required a `rebind-pack` whose own "awaiting a fresh GREEN" row was exactly such a condition —
an unresolvable cycle. The test now asserts the tolerance PROPERTY (the dogfood tests tolerate
the pending-anchor row and nothing else); the store's health is asserted by those dogfood tests
themselves. The unit's promise is unchanged.
