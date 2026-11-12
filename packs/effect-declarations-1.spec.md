# effect-declarations@1 acceptance pack

**Owner:** Claude (Opus 5.5, acceptance owner). **Implementer:** GPT 6.1 Sol. Sol may not edit this
file or `tests/test_effect_declarations.py`. **Decision:** D-31. **Beads:** `vouch_mk2-bd4.8` (unit),
`vouch_mk2-bd4.8.1` (implement).

**Shape reviews.** Fable, Sol and Astra reviewed a draft shape before this pack was written. Their
reviews are summarized in this unit's row in `docs/usage-log.md`. The user decided three
questions on 2026-11-12:
- Units without a declaration are reported, and CI stays green.
- Label-only freezes stay allowed and are reported.
- The requirement is a one-way store switch that `init` sets.

## Promise

A pack can carry an effects declaration. It is embedded in the pack inventory (pack format 3), next
to the inventory entry that binds the raw bytes of the effects file it came from. A store that requires effects refuses to bind a
pack without one. New projects require effects from `init`; existing stores opt in once, with a
recorded command. Vouch reports what each unit declared and never claims that a declaration is true.

## Interface

**Effects file.** Strict JSON, UTF-8, by convention `packs/<unit>-<v>.effects.json`:

```json
{"effects_format": 1, "effects": [
  {"name": "health-connect-read", "kind": "observation",
   "source": "Health Connect on the device", "capture": "one change page as a stored projection"},
  {"name": "portal-delivery", "kind": "action",
   "system": "Portal ingest API", "outcome": "confirmed", "detail": "one HTTP request per attempt"}]}
```

- The top level is an object with exactly `effects_format` (the integer 1, not a boolean) and
  `effects` (a list).
- An `observation` has exactly `name`, `kind`, `source` and `capture`, plus an optional `detail`.
- An `action` has exactly `name`, `kind`, `system` and `outcome`, plus an optional `detail`.
  `outcome` is one of `confirmed`, `compensated` or `irreversible`.
- Required strings are non-blank after stripping. `detail`, when present, is a string.
- `name` matches `^[a-z0-9]+(-[a-z0-9]+)*$` and is unique in the file.
- Duplicate JSON keys, unknown keys, other kinds and non-UTF-8 input are refused.
- `effects: []` declares no effects.

**Model** (`vouch_mk2.core.model`):
- `ObservationEffect(name, source, capture, detail="")`.
- `ActionEffect(name, system, outcome: ActionOutcome, detail="")`, with `ActionOutcome` being
  `CONFIRMED`, `COMPENSATED` or `IRREVERSIBLE`.
- `PackInventory` gains `effects: tuple[ObservationEffect | ActionEffect, ...] | None = None`:
  `None` means undeclared, and `()` means declared with no effects.
- `Snapshot` gains `effects_required: bool = False`.

All are frozen dataclasses, and the effect types are exported like the other model types.

**Parsing and building** (`vouch_mk2.bundle`):
- `parse_effects_file(data: bytes)` returns the effects sorted by `name`, or raises
  `InventoryError`.
- `build_pack_inventory(project, pack_files, pack_test_files=(), effects_file="")`: with an
  `effects_file`, it reads that file once through the existing safe project-relative reader. The
  `effects`-role entry hashes those bytes, and the embedded effects are the parse of the same bytes.
  The result is pack format 3. The path must not collide with a spec or test path.
- Without an `effects_file`, the result is format 1 or 2 exactly as today, byte for byte.

**Pack format 3** (codec):
- The format-3 encoding adds a top-level `effects` list: one object per effect, sorted by name, keys
  sorted, and `detail` omitted when empty. For example:
  `{"kind": "action", "name": "portal-delivery", "outcome": "irreversible", "system": "Portal"}`.
- Format 3 requires exactly one entry with role `effects`, at least one other entry, and `effects`
  not `None`.
- Formats 1 and 2 carry no `effects` key and decode to `effects=None`. Their encodings are
  unchanged.
- Any other combination is a `StoreError` on encode and a decode error on decode.

**Test-set digest:** `pack_test_set_digest` ignores the `effects` entry and the embedded effects. A
format-3 pack's test-set digest equals the digest of the same pack built without the effects file:
the format-2 test-only encoding when it has tests, or the format-1 whole-pack encoding when it does
not. So effects edits never void RED or GREEN (D-17).

**Commands:**
- `RequireEffects()` emits `EffectsRequired`, which sets `effects_required`.
  - It is always accepted, so running it again or replaying it is a no-op on the flag.
  - No command clears the flag.
  - IR id: `CMD_REQUIRE_EFFECTS = "require-effects"`. CLI: `require-effects --root R`.
- `FreezeAcceptance` and `BindPack` gain `effects_declared: bool = False`. While `effects_required`
  holds, a freeze or bind that carries a pack digest without `effects_declared` is refused with
  `pack.effects-required`.
  - A label-only freeze (no pack digest) is accepted and reported.
  - The CLI and `bundle.bind_pack` set `effects_declared` from the inventory's `effects is not None`.
- `RebindPack`: when the old and new inventories' `effects` differ (including `None` against a
  tuple), the rebind is refused with `rebind.effects-changed`. This check runs before the existing
  path checks.
- `freeze`, `bind-pack` and `rebind-pack` gain `--pack-effects-file PATH`. An invalid effects file,
  or `--pack-effects-file` given without any `--pack-file` or `--pack-test-file`, exits 2 and writes
  nothing.

**Merge:** semantic merge replays `EffectsRequired` as `RequireEffects`. It reconstructs a freeze's or
bind's `effects_declared` from the bound inventory object it carries. So a declared pack replays onto
a store that requires effects, and an undeclared one is refused. In that case the merge exits
non-zero with output naming `pack.effects-required`, and the store is left unchanged.

**`init`** records `EffectsRequired` in every new store.

**Snapshot encoding:** `effects_required` is written only when true. A snapshot without the key
decodes as `False`. `CURRENT_STATE_FORMAT` stays 5. This follows the precedent of `ci_policy`, and it
invents no state (D-13).

**Reports:**
- **`check.effects-declared`** (IR: `CHECK_EFFECTS_DECLARED`, scope `ARTIFACT_CHECK`). Its subjects
  are the active, non-superseded units.
  - It is **FAIL**, naming the unit, when a bound inventory object is missing or does not decode.
  - Otherwise it is **PASS**, with a one-line detail that starts with
    `"<n> declared (<m> with no effects), <k> not declared"`. The undeclared units are named as
    `name@version` (bound but undeclared packs, and label-only freezes).
  - It never fails because a unit is undeclared, so `check --ci` stays green for older units.
  - It does not compare effects files on disk: `check.pack-files-match` already binds their raw
    bytes, scoped by tier.
- **`org.effects-truth`** (IR: `ORG_EFFECTS_TRUTH`, scope `ORGANIZATIONAL`), always NOT_CHECKED. Its
  detail contains "a declaration records what was declared, not that the code has only those
  effects".
- **`show-effects --root R [--json]`**: one line per active unit, sorted by name.
  - Text forms:
    - `u@1: declares no effects`
    - `u@1: action portal-delivery (system: Portal ingest API; declared outcome: confirmed)`
    - `u@1: observation x (source: …; captures: …)`, with several effects joined by ` | `
    - `u@1: effects not declared`
    - `u@1: no pack bound, effects not declared`
    - `u@1: pack inventory unavailable`
  - JSON: `{"effects_required": bool, "units": [{"unit": "u@1", "declaration": "declared" |
    "not-declared" | "no-pack" | "unavailable", "effects": [<canonical effect objects>] | null}]}`.
- **`status --json`** gains `effects_required`. The system map and status facts carry it as an
  optional field, written only when true, so existing maps stay byte-identical. When it is true, the
  agent context gains a line starting "Effect declarations are required".
- **Tier row `typed-contracts`:**
  - Description: "Declared effects (transform, observation, action)".
  - Every tier: `mechanical_checks == (CHECK_EFFECTS_DECLARED,)`, `mechanical_evidence == ()`,
    coverage PARTIAL.
  - Residual: it states that whether declarations are true and complete is human reviewed, and that
    a project's own effect checker is not yet bindable.

**Formal model:**
- `state_fingerprint` includes `effects_required`.
- A new fourth universe, `effects-v1`: from an empty snapshot, the commands are `RequireEffects`, a
  specify, a label-only freeze, and `BindPack` with and without `effects_declared`. It must reach a
  transition labelled `bind-pack` from a required, frozen, unbound state to a required, bound state.
- Its `invariant_ids` are every registered invariant except `M6.no-nonterminal-deadlock`. The
  universe deliberately stops at acceptance-frozen, so M6 would report a deadlock that the universe
  itself manufactured. It does include `M10.one-active-record-per-name`.
- `core-v1` (264,462 / 1,381,350), `history-v1` (62,832 / 219,976) and `tier-v1` (246 / 1,640) do
  not move.
- The owner re-records the certificate.

## Requirements

- **R1** `test_an_effects_file_parses_to_typed_effects_sorted_by_name`.
- **R2** `test_an_invalid_effects_file_is_refused`: 15 refusals.
- **R3** `test_a_format_3_inventory_round_trips_and_legacy_formats_do_not_change`: the encode
  refusals are each isolated, and malformed format-3 or legacy JSON is refused on decode.
- **R4** `test_building_hashes_and_parses_the_same_effects_bytes`:
  - the `effects` entry's sha256 is the sha256 of the file's raw bytes;
  - rewriting the same path with differently formatted JSON keeps the effects but changes the hash;
  - a missing file is refused, and so is valid JSON at a path that collides with a spec or test path.
- **R5** `test_effects_never_change_the_test_set_digest`.
- **R6** `test_freeze_and_bind_pack_accept_an_effects_file`: refusals leave every file in the store
  byte-identical, not just `state.json`.
- **R7** `test_require_effects_is_recorded_and_one_way`.
- **R8** `test_a_store_that_requires_effects_refuses_an_undeclared_pack`.
- **R9** `test_the_decision_and_the_library_enforce_the_requirement`.
- **R10** `test_rebind_keeps_the_declaration_exactly`.
- **R11** `test_init_requires_effects_for_new_projects`.
- **R12** `test_a_merge_replays_declared_packs_onto_a_store_that_requires_effects` and
  `test_a_merge_refuses_an_undeclared_pack_onto_a_store_that_requires_effects`.
- **R13** `test_check_reports_declared_and_undeclared_units_without_failing` and
  `test_an_unreadable_declared_inventory_fails_the_effects_row`.
- **R14** `test_show_effects_lists_each_active_unit`.
- **R15** `test_status_and_agent_context_state_the_requirement`.
- **R16** `test_the_tier_row_claims_declared_effects_only`.
- **R17** `test_the_formal_model_covers_the_requirement_without_moving_other_universes`.
- **R18** `test_the_docs_show_effect_declarations`:
  - `docs/quickstart.md` freezes with `--pack-effects-file` and shows an `.effects.json` file.
  - `docs/claim-boundary.md` states the effects claim, including the phrase above and how
    `require-effects` works.
  - `docs/commands.md` is regenerated, with `## require-effects` and `## show-effects`.
  - `scripts/walkthrough.py` freezes with `--pack-effects-file`, because `init` now requires it.
- **R19** (`# guard:`) `test_d31_records_the_decision`.

## Prerequisite at publication (owner)

Two published units' bound tests build their fixture with `init` and then freeze with pack files
and no effects file:
- `tests/test_anchor_unit_ref.py` (`anchor-unit-ref@1`);
- `tests/test_ci_anchor.py` (`ci-anchor@1`).

Once `init` requires effects, those fixtures are refused. Sol does not edit them. Sol reports the
failures in the DONE file and confirms each one comes only from `pack.effects-required`.

After implementation, the owner adapts each fixture so its freezes pass an empty declaration
(`--pack-effects-file` with `{"effects_format": 1, "effects": []}`). The assertions stay unchanged.
The owner then runs `rebind-pack` for each of the two units with a stated reason (the D-14
amendment, shared-interface adaptation) and records fresh GREEN for each, before this unit
publishes.

## Ground rules

- **Do not edit test files bound in another unit's pack** (the `--pack-test-file` and `--pack-file`
  paths listed in `packs/*.spec.md` and frozen in `.vouch/`). If one must change, stop and write
  `effect-declarations.DEVIATION.md`.
- **Unbound tests may be updated** where they pin behaviour this pack supersedes. For example:
  - `tests/test_projections.py` pins the old tier-row text;
  - `tests/test_formal.py` and `tests/test_cli.py` pin the universe list;
  - `tests/test_init.py` pins `init`'s event objects;
  - `tests/test_doc_binding.py` pins tier-row text that must come from the D-31 row, never from
    edits to D-12's text.

  Say which ones you changed, and why, in the DONE file.
- **The quickstart's sh blocks are executed by `tests/test_operator_docs.py`.** Keep every phrase
  that other packs pin (`docs/quickstart.md` §8, the formal-claim sentence) and keep the walkthrough
  green.
- **Leave alone:** `.vouch/`, the README count, `DECISIONS.md` and git.
- **The owner handles:** the formal certificate (`formal --record`) and switching on `require-effects`
  in mk2's own store.
- **What "required" means.** The CLI, `bundle.bind_pack` and `decide()` all refuse an undeclared bind
  in a store that requires effects. `decide()` trusts the adapter-supplied `effects_declared`, as it
  trusts `test_set_digest`. The claim boundary says so.

## Out of scope

- Binding a project's own effect checker's run as evidence (D-31: after `release@1`).
- Declaring existing units without a new pack version (`declare-effects` was deferred by all three
  shape reviews). Units declare at their next version, and rebind can neither add nor change a
  declaration.
- Showing per-unit effects in the system map and agent context. Only the requirement line is in
  scope; per-unit lists are in `show-effects`.
- Comparing an effects file's parse on disk against the embedded copy: `check.pack-files-match`
  binds the raw bytes.
