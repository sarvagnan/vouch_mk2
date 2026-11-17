# option-safe-identifiers@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_option_safe_identifiers.py` or `packs/option-safe-identifiers-1.effects.json`.
**Decision:** D-35 (settles `vouch_mk2-dww`). **pack/2:** DEV-osi-01 narrows the Promise and the
first ground rule to the D-35 boundary (Astra's re-check); requirements and tests are unchanged.
**Effects:** none. The change is a pure refusal in `decide()` and a sentence in the quickstart.

## Promise

`decide()` refuses `SpecifyUnit` when its unit name or version begins with `-`, with a refusal
code that names the rule, so a newly specified unit cannot give `next` suggested commands that a CLI
option reads as another option. History a store already holds or adopts without re-deciding it (a
fast-forward merge, a raw `Store.commit`, a Vouch 0.6 migration) is outside this guarantee (D-35
note of 2026-11-17).

## Common rules for every scenario

- The refusal code is exactly `identifier.leading-dash`. Its detail contains
  `may not begin with '-'`.
- CLI scenarios build a fresh project with `init` and pass the identifier bound to its option
  (`--name=-x`, `--version=-v1`), the only form argparse can deliver to `decide()`.

## Requirements

- **R1 — `decide()` refuses a dash-led unit name.**
  `test_decide_refuses_a_unit_name_that_begins_with_a_dash`. `SpecifyUnit("-x", "v1")` on an
  empty snapshot is refused with exactly one violation, `identifier.leading-dash`, and its detail
  contains `may not begin with '-'`.
- **R2 — `decide()` refuses a dash-led unit version.**
  `test_decide_refuses_a_unit_version_that_begins_with_a_dash`. `SpecifyUnit("u", "-v1")` is
  refused the same way.
- **R3 — The CLI reports the refusal and records nothing.**
  `test_cli_specify_refuses_a_dash_led_name_and_records_nothing` and
  `test_cli_specify_refuses_a_dash_led_version_as_json`. `specify --name=-x --version v1` exits 2,
  its output contains `identifier.leading-dash` and `may not begin with '-'`, and the store then
  has no units. `specify --name u --version=-v1 --json` exits 2 and prints one JSON object with
  `accepted` false and exactly one violation whose code is `identifier.leading-dash`; the store
  has no units.
- **R4 — Inner and trailing dashes stay legal.** `# guard:`
  `test_inner_and_trailing_dashes_are_still_accepted`. `SpecifyUnit("a-b", "v1-rc")` and
  `SpecifyUnit("x-", "1-")` are accepted, each emitting one `UnitSpecified` with that name and
  version.
- **R5 — The existing identifier rule keeps its code.** `# guard:`
  `test_other_invalid_identifiers_keep_the_identifier_invalid_code`. `SpecifyUnit("", "v1")`,
  `SpecifyUnit("a@b", "v1")` and `SpecifyUnit("-a@b", "v1")` are each refused with exactly
  `identifier.invalid`: when a value breaks the existing rule, that rule is reported and the dash
  rule is not.
- **R6 — The quickstart states it.**
  `test_quickstart_states_that_unit_identifiers_may_not_begin_with_a_dash`. Matching ignores runs
  of whitespace. `docs/quickstart.md` contains `A unit name or version may not begin with` followed
  by `` `-` ``.

## Ground rules

- The rule lives in `decide()`'s handling of `SpecifyUnit`, the only command that introduces a
  unit name and version. Every transport that issues the command, and divergent merge replay
  (which re-decides incoming `UnitSpecified` events), gets the refusal; fast-forward history
  adoption and raw store writes do not re-decide it, and migration is out of scope (D-35).
- `valid_identifier`, `valid_unit_identifier`, the snapshot invariants, the codec, and the event
  and snapshot formats do not change, so stored history stays readable. The formal universes keep
  their sizes.
- Deviation ids, release ids, pack versions and amendment versions are out of scope (D-35). If the
  implementer finds the same defect there, it is reported for a follow-up bead, not fixed here.
