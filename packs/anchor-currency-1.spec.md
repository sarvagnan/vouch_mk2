# anchor-currency@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_anchor_currency.py` or `packs/anchor-currency-1.effects.json`.
**Decision:** D-34 (settles `vouch_mk2-bd4.4.4`, pilot finding 2, and `vouch_mk2-bd4.4.10`).
**Effects:** one observation (the store's evidence heads, read by the check and by both anchor
loops) and one action (the CI anchor loop's advance of the listed heads), under the D-31 note of
2026-11-15.

## Promise

An anchor that CI claimed and that evidence has since moved past is reported as not checked, not
as passing, and both CI anchor loops advance it on their next run. Every row of
`check.evidence-anchors` names the head it is about by its exact `NAME@VERSION`.

## Common rules for every scenario

- Each scenario builds a fresh project with `init`, tier `standard` and anchor mode `ci`. The
  tests clear every CI run-id variable Vouch recognises and set `GITHUB_RUN_ID` only to simulate a
  CI claim, through `advance-anchor --unit-name NAME --unit-version VERSION`.
- The **lagging** fixture: `u@v1` is published, CI claims its anchor at its head under run
  `424242` and `check --ci` exits 0; then a late GREEN is recorded for `u@v1`, so the anchor (still
  claimed by `424242`) differs from the head.
- The anchor loops are exercised by running each workflow's head-listing Python snippet from the
  project root exactly as CI does, in the generated workflow (`init.generated_workflow()`) and in
  mk2's `.github/workflows/ci.yml`.
- The lag phrase is exactly `anchor lags its head`.

## Requirements

- **R1 — A claimed anchor behind its head is not checked.**
  `test_a_claimed_anchor_behind_its_head_is_not_checked` and
  `test_check_json_reports_the_lag_as_not_checked`. On the lagging fixture, `check --ci` exits 1;
  the one mechanical `check.evidence-anchors` row is NOT-CHECKED and contains
  `'u@v1' anchor lags its head`; the composed verdict is NOT-CHECKED. `check --ci --json` reports
  the same verdict (`not-checked`) and detail.
- **R2 — Every row names the exact reference.** `test_rows_name_each_head_by_its_exact_reference`.
  `u@v1` (locally advanced, unclaimed) is superseded by `u@v2` (locally advanced, unclaimed), and
  `w@1` has evidence but was never advanced. The row contains
  `'u@v1' anchor has no workflow run claim`, `'u@v2' anchor has no workflow run claim` and
  `'w@1' remains at GENESIS`, and no `unit 'u' ` or `unit 'w' `. The dangling-anchor and
  unverified-chain rows name `NAME@VERSION` the same way (reviewed, not tested here: neither state
  can be built through ordinary commands).
- **R3 — A lagging anchor is a pending anchor.** `# guard:`
  `test_pending_anchor_ok_tolerates_a_lagging_anchor`. On the lagging fixture,
  `check --ci --pending-anchor-ok` exits 0: the lag is pending CI, like an unclaimed anchor.
- **R4 — The generated workflow advances a lagging claimed head.**
  `test_generated_anchor_step_lists_and_claims_a_lagging_claimed_head`. On the lagging fixture the
  generated loop lists exactly `u@v1` and passes both name and version to `advance-anchor`. After a
  claim under run `515151`, the anchor equals the head with run claim `515151`, `check --ci` exits
  0, and a second listing is empty.
- **R5 — mk2's own workflow does the same.**
  `test_mk2_own_anchor_step_lists_a_lagging_claimed_head`. `ci.yml`'s loop lists exactly `u@v1` on
  the lagging fixture and passes both name and version.
- **R6 — No churn, no loss.** `# guard:`
  - `test_a_current_claimed_head_is_not_listed`: an anchor at its head with a run claim is listed
    by neither loop.
  - `test_an_unclaimed_current_head_is_still_listed`: a locally advanced, unclaimed head is listed
    by both loops, as before.
- **R7 — `next` proposes the advance.** `# guard:`
  `test_next_actions_propose_advancing_a_lagging_claimed_head`. On the lagging fixture,
  `next --json` has exactly one evidence action: `advance-anchor` with `--unit-name u` and
  `--unit-version v1`.
- **R8 — The documents state it.**
  `test_docs_state_that_a_lagging_anchor_is_not_checked_until_ci_advances_it`. Matching ignores
  runs of whitespace. `docs/quickstart.md` § "6. Assurance tier and anchoring" and
  `docs/claim-boundary.md` § "CI anchoring" each contain `evidence recorded after a CI-claimed
  anchor`, `anchor lags its head` and `until the next CI run advances it`.

## Ground rules

- `decide()`, the event and snapshot formats, and `advance-anchor`'s refusals do not change; the
  formal universes keep their sizes.
- The order of `check.evidence-anchors`' per-head outcomes is kept: unverified chain FAIL, still
  at GENESIS NOT-CHECKED, dangling anchor FAIL, no run claim NOT-CHECKED, then the new lag
  NOT-CHECKED, otherwise PASS.
- `pending_anchor_is_only_unchecked` keeps classifying by check id; the lag row needs no new case.
- Both loops keep listing one `NAME@VERSION` per line and calling `advance-anchor` with
  `--unit-name=` and `--unit-version=` bound values (D-26). A head is listed when it has no run
  claim or its anchor differs from its head.
- Existing frozen packs that read the loops (`anchor-unit-ref@1` R1 and R7, `generated-ci`) must
  keep passing unchanged.

## Out of scope

- Updating workflows already generated in adopters' repositories (`vouch_mk2-b6g.14`).
- Leading `-` in identifiers (D-35).
