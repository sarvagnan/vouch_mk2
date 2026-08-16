# Acceptance pack — unit `state-format@1` (pack/1)

**Owner:** acceptance owner (Fable). **Implementer:** Codex — may NOT edit this file,
`tests/test_state_format.py`, or `tests/fixtures/state-format/*`. A finding that this pack is
wrong is a DEVIATION adjudicated by the owner. Decision: DECISIONS.md D-13. Bead: bd4.2.

## Requirements (each bound to a test)

R1 `vouch_mk2.store.CURRENT_STATE_FORMAT == 1`; `state.json` carries `state_format` (canonical
   position by key sort); round-trips. → `test_current_format_is_one_and_the_field_round_trips`
R2 Strict decode: missing `state_format` (format 0) and any older format → `DecodeError` whose
   message names `state_format` and `vouch-mk2 upgrade`; newer → `DecodeError` containing
   "newer" (written by a newer vouch). No decoder-side defaults.
   → `test_missing_state_format_is_refused_naming_upgrade`, `test_older_…`, `test_newer_…`
R3 Drift guard: `tests/fixtures/state-format/format1.golden.json` == `encode_snapshot(Snapshot(revision=0))`.
   The golden is owner-pinned; you may not change it. → `test_canonical_layout_of_format_1_matches_the_golden`
R4 `vouch_mk2.store.upgrade`: `UPGRADERS: Mapping[int, Callable[[dict], dict]]` with keys exactly
   `range(CURRENT_STATE_FORMAT)`; each returns a NEW dict (input not mutated);
   `upgrade_document(doc) -> (doc, steps)` applies them from the document's format
   (`doc.get("state_format", 0)`) to current and returns `steps == [(0, 1), …]`; a document
   already current returns `steps == []`; newer raises. → `test_upgraders_are_contiguous_and_pure_dict_functions`
R5 Upgrader 0→1 accepts the union of pre-versioned shapes (fixtures `format0-unit1-era.json`,
   `format0-unit2-era.json`) with NO invented state: `tier` absent → `"lightweight"`;
   `published_once` absent → `status in {published, release-bound, superseded}`; everything else
   copied verbatim; `state_format` set to 1. Anything it cannot map is left for strict decode
   to refuse (never guessed). → `test_format0_shapes_upgrade_to_the_current_format_without_invented_state`
R6 `vouch-mk2 upgrade --root R [--dry-run]`: reads `state.json` RAW (json), refuses a symlinked
   `state.json` (exit 2), computes the upgraded document, STRICTLY DECODES the result (an
   undecodable result, or ANY exception in an upgrader, → stderr message containing "upgrade",
   exit 2, original bytes untouched, no temp files left), then writes canonical bytes
   atomically under the writer lock (temp + fsync + rename, like commit), regenerates map AND
   context, prints one line per step (`0 -> 1`) and the final format; `revision` unchanged.
   Already current → prints "already at format N", bytes unchanged, exit 0. `--dry-run` prints
   the steps and changes nothing.
   → `test_upgrade_verb_rewrites_a_legacy_store_in_place_and_it_checks_clean`, `test_upgrade_is_idempotent_and_reports_current`,
     `test_upgrade_dry_run_changes_nothing`, `test_upgrade_fails_closed_on_an_undecodable_result`, `test_upgrade_refuses_a_symlinked_state_file`
R7 Read paths on a stale store: `check`, `status`, `next` exit 2 with a stderr message naming
   `upgrade` and no traceback; `Store.open(read_only=True).read_snapshot()` raises a
   `StoreError` naming `upgrade`. → `test_read_verbs_on_a_legacy_store_exit_2_naming_upgrade`, `test_read_only_open_of_a_legacy_store_names_upgrade`
R8 `Store.init` and `migrate` write `state_format == CURRENT_STATE_FORMAT`. → `test_init_and_migrate_write_the_current_format`
R9 The lock exposes `upgrade` (`--dry-run` in its help). → `test_lock_exposes_upgrade`
R10 The codec docstring no longer says DISPOSABLE and describes `state_format` and the upgrade
    path. → `test_codec_docstring_no_longer_calls_the_format_disposable`

## Ground rules

- `upgrade` is NOT a lifecycle command (not reflected from `core.commands`; `LIFECYCLE_VERBS`
  unchanged); it lives beside `generate`. It does not touch the formal certificate (a
  certificate recorded under an older checker fails its own row — that is a separate,
  already-visible fact).
- Do not touch `.vouch/` — the owner runs `upgrade` on the dogfood store (dogfooding the verb).
- MCP read-only server: a stale store yields the same clear error text through the tool result.
- Pyrefly strict; import-linter kept; every pre-existing test green (README count is the owner's).
- Docs: README + `docs/claim-boundary.md` describe the format policy in two sentences.
