# option-safe-labels@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_option_safe_labels.py` or `packs/option-safe-labels-1.effects.json`.
**Decision:** D-40 (settles `vouch_mk2-rkh`). Extends D-35.
**Effects:** none. The change is a pure refusal in `decide()` and a sentence in the quickstart.

## Promise

`decide()` refuses a deviation id (`OpenDeviation`), release id (`PrepareRelease`), pack version
(`FreezeAcceptance`) or amendment version (`AmendDeviation`) that begins with `-`, with D-35's
refusal code, in the command that introduces it. With D-35, no identifier or label an operator gives
Vouch may begin with `-`. History a store already holds, or adopts through a fast-forward merge or
a raw `Store.commit`, is not re-judged (D-33, D-35).

## Common rules for every scenario

- The refusal code is exactly `identifier.leading-dash`. Its detail contains
  `may not begin with '-'`.
- Pure scenarios decide each command on a snapshot where the same command with a plain label is
  accepted (each test checks that first), so the dash is the only reason for the refusal.
- CLI scenarios build a fresh project with `init`, specify `u@1`, and pass the label bound to its
  option (`--pack-version=-p`), the only form argparse can deliver to `decide()`.

## Requirements

- **R1 — Pack version.** `test_decide_refuses_a_pack_version_that_begins_with_a_dash`.
  `FreezeAcceptance("u", "-p", …)` on a specified unit is refused with exactly one violation,
  `identifier.leading-dash`, whose detail contains `may not begin with '-'`.
- **R2 — Deviation id.** `test_decide_refuses_a_deviation_id_that_begins_with_a_dash`.
  `OpenDeviation("-d", pack-defect, u@1)` on an in-progress unit is refused the same way.
- **R3 — Amendment version.** `test_decide_refuses_an_amendment_version_that_begins_with_a_dash`.
  `AmendDeviation("D1", "-a")` on an open pack-defect deviation is refused the same way.
- **R4 — Release id.** `test_decide_refuses_a_release_id_that_begins_with_a_dash`.
  `PrepareRelease("-r", ("u",))` with `u@1` published is refused the same way.
- **R5 — The CLI reports the refusal and records nothing.**
  `test_cli_freeze_refuses_a_dash_led_pack_version_and_records_nothing`,
  `test_cli_open_deviation_refuses_a_dash_led_id_as_json` and
  `test_cli_adjudicate_amend_refuses_a_dash_led_amendment_version`. `freeze --pack-version=-p`,
  `open-deviation --deviation-id=-d --json` (after a label-only freeze) and
  `adjudicate-amend --amendment-version=-a` (after opening `D1`) each exit 2 with the code in their
  output (as the single JSON violation for `--json`), and the store's revision is unchanged.
- **R6 — Inner and trailing dashes stay legal.** `# guard:`
  `test_inner_and_trailing_dashes_are_still_accepted`. Pack versions `u-pack/1-rc` and `p-`,
  deviation ids `DEV-osl-01` and `d-`, amendment version `u-pack/2-`, and release ids `r-1` and
  `r-` are accepted.
- **R7 — The existing identifier rule keeps its code.** `# guard:`
  `test_other_invalid_labels_keep_the_identifier_invalid_code`. Where a label already has a
  validity rule (deviation id, release id, pack version), a value that breaks it and also begins
  with `-` or whitespace (`" -d"`, `"-d\n"`, and the empty release id) is refused with exactly
  `identifier.invalid`.
- **R8 — mk2's store holds no such label.** `# guard:` `test_mk2_store_holds_no_dash_led_label`.
  No deviation id, amendment version, release id, current pack version or pack-history entry in
  mk2's own `.vouch` begins with `-`.
- **R9 — The quickstart states it.** `test_quickstart_states_the_rule_for_all_four_labels`.
  Matching ignores runs of whitespace. `docs/quickstart.md` contains `A deviation id, release id,
  pack version or amendment version may not begin with` followed by `` `-` either``.

## Ground rules

- Each refusal lives in `decide()`'s handling of the command that introduces the label. Every
  transport that issues the command, and divergent merge replay (which re-decides incoming
  events), gets the refusal; fast-forward adoption and raw store writes do not re-decide it.
- Where a label has an `identifier.invalid` check, that check runs first and is reported alone
  (R7). The amendment version has no such check today, and this unit adds none.
- `valid_identifier`, `valid_unit_identifier`, the snapshot invariants, the codec, and the event
  and snapshot formats do not change, so stored history stays readable. The formal universes keep
  their sizes and the certificate is unchanged; if either moves, stop and write
  `option-safe-labels.DEVIATION.md`.
- The quickstart's D-35 paragraph keeps every pinned phrase; R9's sentence is added beside it.

## Verification outside pytest (final pass)

The owner checks a scratch copy of the pilot project's store (never the pilot itself) for any
dash-led deviation id, amendment version, release id or pack version, and records the result.
