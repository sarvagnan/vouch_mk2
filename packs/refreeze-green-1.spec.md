# refreeze-green@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_refreeze_green.py` or `packs/refreeze-green-1.effects.json`.
**Decision:** D-36 and its note of 2026-11-17 (settles `vouch_mk2-bd4.4.5`, pilot finding 3).
**Effects:** none. The change is a stricter publication gate and a sentence in the quickstart.

## Promise

When a unit is re-frozen with a pack whose inventory digest differs from its previous freeze,
`publish` is refused with `gate.dod.current-pack-green` until a GREEN for the current pack is
recorded after that re-freeze, and `next` does not offer `publish` before then. A re-freeze of
identical bytes keeps the earlier GREEN, and RED stays keyed to the test-set digest (D-17).

## Common rules for every scenario

- Every scenario runs through the CLI at tier `standard` on a fresh `init` project. Unit `u@v1`
  binds `packs/u.spec.md` with `--pack-file`, `tests/t.py` with `--pack-test-file`, and an empty
  effects file. It is frozen as `u-pack/1`, gets RED then GREEN, and is marked done. Then
  deviation `DEV-1` (`pack-defect`) is opened, the pack files are rewritten, the deviation is
  amended to `u-pack/2` and closed, and the unit is respecified, frozen as `u-pack/2`, readied,
  started and marked done again, with no evidence recorded after the re-freeze.
- A refusal's gate codes are the `gate.…` tokens in the CLI output, in order. The refusal detail
  is matched after collapsing runs of whitespace.

## Requirements

- **R1 — A spec-only re-freeze needs a fresh GREEN.**
  `test_publish_after_a_spec_only_refreeze_requires_a_fresh_green`. The re-freeze changes only
  `packs/u.spec.md`, so the test-set digest is unchanged and the inventory digest is not.
  `publish` exits 2, the only gate code in its output is `gate.dod.current-pack-green`, the
  output contains `after the latest freeze`, and the snapshot is unchanged.
- **R2 — `next` agrees with the gate, and a fresh GREEN satisfies it.**
  `test_next_offers_publish_only_after_the_fresh_green`. In R1's state, `next_actions` offers no
  `publish` for the unit. After one passing `green` record, it offers `publish`, and `publish`
  succeeds.
- **R3 — A re-freeze of identical bytes keeps the earlier GREEN.** `# guard:`
  `test_a_refreeze_of_identical_bytes_keeps_the_earlier_green`. The re-freeze binds the same
  bytes under the new label `u-pack/2`. `next` offers `publish` and `publish` succeeds with no
  new evidence.
- **R4 — A re-freeze that changes the tests still needs RED then GREEN.** `# guard:`
  `test_a_refreeze_that_changes_the_tests_still_needs_red_then_green`. The re-freeze changes
  `tests/t.py`. `publish` is refused with only `gate.dod.current-pack-green`; after a lone
  `green` it is still refused; after `red` then `green` it succeeds.
- **R5 — The quickstart states it.**
  `test_quickstart_states_that_a_refreeze_needs_a_fresh_green`. Matching ignores runs of
  whitespace. `docs/quickstart.md` contains `A re-freeze that changes any bound pack file needs a
  GREEN recorded after it`.

## Ground rules

- "Changes the inventory digest" compares the re-freeze's inventory digest with the one the unit
  had at its previous freeze, before the respecify cleared it. A unit's first freeze is unaffected.
  Effects files are part of the inventory.
- The rule lives in `gate.dod.current-pack-green` and in the predicate `next` uses for it
  (`current_pack_evidence_is_green` today), so the two cannot disagree. The gate's refusal detail
  names the reason. The IR obligation's description states the new requirement.
- RED is unchanged: `red_seen` stays keyed to the test-set digest (D-17), so R1 and R2 need no new
  RED. `rebind-pack` and its fresh-GREEN rule (D-14, D-31) are unchanged.
- Units already published or release-bound are not re-judged: `check` keeps its verdicts on them,
  including mk2's own store, which the dogfood tests check.
- Prefer no event or state format change. If the snapshot must record where the latest freeze sits
  in the evidence chain, that is a state-format bump under D-13: the format number, an upgrader
  that invents no state (a unit upgraded from the old format keeps the old rule until it is next
  re-frozen), a fixture, and the canonical-bytes golden. The owner upgrades mk2's own store at the
  final pass. Report any change in the formal universes' sizes.
- Whether a `refactor` record after the re-freeze satisfies the rule is not pinned. `mark-done`
  stays ungated.
