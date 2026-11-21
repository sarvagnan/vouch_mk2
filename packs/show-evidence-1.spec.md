# show-evidence@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_show_evidence.py` or `packs/show-evidence-1.effects.json`.
**Decision:** D-49 (`vouch_mk2-inv.2`). **Seams (D-44):** the CLI on a scratch store (including
`record-evidence --json` receipts as the oracle for recorded values, and `upgrade` of a historical
store built with the strict codec); the MCP server
(`vouch_mk2.mcp.serve`); the text of `docs/*.md`; `Store` read-only, for the bound test-set digest.
**Effects:** none. The verb and the tool only read the store, opened read-only.

## Promise

`vouch-mk2 show-evidence [--root ROOT] [--json] [--unit NAME@VERSION]` opens the store read-only
and changes no byte. It lists every unit that is not superseded, sorted by name then version, or
only the exact `NAME@VERSION` given (a superseded version included). For each unit it walks the
evidence chain of that exact version and lists every record oldest first: sequence, kind, outcome,
the test-set digest it was recorded for, the stored command line (redacted positions stay
`<redacted>`), its recorded time, and its evidence object digest, all as recorded.

`current_pack_green` is the answer of the evidence predicate the `standard` and `high` publication
gate uses (`current_pack_evidence_is_green`): the current pack's RED-then-GREEN result, including
D-36's fresh-GREEN rule after a changed re-freeze and the rebind rules. It is one evidence
predicate, independent of the tier and of publication's other checks; at `lightweight`,
publication does not use it. When it is true and the chain was read in full, the view marks its
witnesses: the GREEN that counts is the latest explicit GREEN for the current test-set digest, and
the RED that counts is the latest RED for that digest before it, if any (on a rebound binding a
RED is not required, and a marked RED may predate the rebind). When it is false, nothing is
marked. REFACTOR records are never marked. Records from before test-set digests existed (evidence
format 1) and records of a label-only freeze have an empty test set and are never marked.

When both marks exist and their stored command lines differ, the unit carries one note,
`GREEN ran a different command from RED`. The note compares stored text only: two different
invocations can store the same line. It never refuses and changes no gate.

A chain that cannot be read in full gives that unit a non-empty `chain_error`, no records, no
notes and no marks (its `current_pack_green` still reports the snapshot's predicate); the
document is still printed and the command exits 1. An unknown `--unit` exits 2 and names it on
stderr. The text form shows each unit's label and, for each record, one line holding its kind and
command, with `counts` on a marked record's line, and the note.

The read-only MCP server gains an `evidence` tool that takes no arguments and returns the same
object as `show-evidence --json` as `structuredContent`, with `isError: true` when any unit has a
`chain_error`. The docs name the verb and its limits.

## JSON shape

```json
{"revision": 9, "units": [{"unit": "u@v1", "status": "in-progress",
  "current_test_set": "<64 hex or empty>", "current_pack_green": true,
  "records": [
    {"sequence": 1, "kind": "red", "outcome": "tests-failed", "test_set": "<64 hex>",
     "command": "python3 -c 'raise SystemExit(1)'", "recorded_at": "<as recorded>",
     "digest": "<64 hex>", "counts": "red"},
    {"sequence": 2, "kind": "green", "outcome": "tests-passed", "test_set": "<64 hex>",
     "command": "python3 -c pass", "recorded_at": "<as recorded>",
     "digest": "<64 hex>", "counts": "green"}],
  "notes": ["GREEN ran a different command from RED"], "chain_error": ""}]}
```

`counts` is `"red"`, `"green"` or `""`; `notes` is a list of strings; `chain_error` is `""` when
the chain was read in full.

## Requirements

Each test fails before the change because `show-evidence` is not a subcommand (argparse exits 2),
the MCP server lists no `evidence` tool, or the docs lack the text, except the guard.

- **R1 — Every record as recorded.** `test_show_evidence_lists_every_record_as_recorded`. RED,
  GREEN, REFACTOR on a bound pack: exactly the keys above; `revision`, and each record's sequence,
  kind, outcome, test set, recorded time and digest, equal the `record-evidence --json` receipts;
  the commands equal the literal lines; `current_test_set` equals the bound digest from the store.
- **R2 — The latest GREEN and the RED before it.** `test_the_latest_green_and_the_red_before_it_count`.
  RED (a different command), RED (command A), GREEN (command B), GREEN (command A), REFACTOR:
  `counts` is `["", "red", "", "green", ""]`, the predicate is true, no note.
- **R3 — A later RED.** `test_a_later_red_leaves_nothing_counted`. RED, GREEN, RED: nothing marked,
  false, no note.
- **R4 — GREEN with no RED.** `test_a_green_with_no_red_before_it_counts_nothing`. A lone GREEN on a
  normally bound pack: nothing marked, false.
- **R5 — The note never blocks.** `test_a_different_green_command_is_noted_and_never_blocks`. At
  `standard`, RED and a GREEN with a different command: both marked, the note exactly as above, the
  predicate true; the text form shows the label, the note, and the marked RED and GREEN lines with
  their kinds and `counts`; `mark-done` and `publish` succeed.
- **R6 — A spec-only re-freeze (D-36).**
  `test_a_spec_only_refreeze_counts_nothing_until_a_fresh_green`. After RED and GREEN, a
  pack-defect deviation and a re-freeze that changes only the spec: false and nothing marked; after
  a REFACTOR alone, still false; after a fresh GREEN, true, that GREEN and the first RED marked, and
  the note (their commands differ).
- **R7 — A changed test set.** `test_marks_follow_the_current_test_set_after_a_changed_refreeze`.
  After a re-freeze with a changed test file and a new RED and GREEN (same command): records carry
  the receipts' old and new test sets, the current one is the new one, only the last two are marked,
  no note.
- **R8 — A rebound pack.** `test_a_rebound_pack_counts_a_green_with_no_red`. Published, rebound to a
  changed test file: false and nothing marked; after a GREEN, that GREEN alone is marked, true, no
  note.
- **R9 — Order, label-only, empty.** `test_units_are_ordered_and_unbound_or_empty_units_are_unmarked`.
  Units listed by name; a label-only freeze's records have an empty test set and no marks; a
  specified unit with no evidence is exactly `{unit, status: "specified", current_test_set: "",
  current_pack_green: false, records: [], notes: [], chain_error: ""}`.
- **R10 — Versions and `--unit`.**
  `test_superseded_versions_are_hidden_by_default_and_selectable_by_exact_version`. After `u@v1` is
  published and superseded and `u@v2` specified: the default listing is `u@v2`, `w@v1` and `u@v2`
  has no records; `--unit u@v1` shows the superseded version's records; `--unit w@v1` only `w@v1`;
  an unknown unit exits 2 naming it.
- **R11 — Redaction.** `test_redacted_arguments_stay_redacted`. A RED recorded with
  `--redact-arg 2` shows `python3 <shell-quoted script path> '<redacted>'`; the secret appears in
  neither form.
- **R12 — An unreadable chain.** `test_an_unreadable_chain_is_reported_and_exits_one`. With the head
  object (digest from the receipt) deleted: exit 1, the document printed, the broken unit with an
  error, no records, no notes and the predicate true; another unit's records intact; the MCP tool
  returns the same document with `isError: true`.
- **R13 — Read-only.** `test_show_evidence_and_the_mcp_tool_use_a_read_only_open`. With a leftover
  temporary file a writable open would remove, text, JSON and MCP calls leave every file under the
  store byte-identical.
- **R14 — MCP.** `test_the_mcp_evidence_tool_is_listed_and_matches_the_cli`. `tools/list` names
  `evidence` (checked first, on an empty store); calling it with `{}` returns the CLI's JSON and no
  `isError`; calling it with an argument is an error.
- **R15 — Docs.** `test_docs_name_show_evidence_and_its_limits`. `docs/claim-boundary.md` contains
  (whitespace normalised) ``\`show-evidence\` lists the stored command line of every RED and GREEN
  and notes when the GREEN that counts has a different stored command line from the RED that
  counts; the note never blocks publication.``; the reviewer role card names `` `show-evidence` ``;
  the generated `docs/commands.md` has a heading for it.
- **R16 — JSON reference.** `test_the_json_reference_shows_a_worked_red_and_green`.
  `docs/schemas.md` has a section ``## `show-evidence --json` `` whose first JSON block uses exactly
  the keys above and shows a consistent worked pair within one unit: a counted RED (`kind` red,
  `tests-failed`) before a counted GREEN (`kind` green, `tests-passed`), both on that unit's
  non-empty current test set, with the predicate true.
- **R17 — A format-1 record.** `test_a_format_one_record_is_listed_with_no_test_set_and_no_mark`. A
  format-2 store holding a format-1 RED record, upgraded with the CLI: the record is listed exactly
  as written (sequence, kind, outcome, command, time, digest) with an empty test set and no mark;
  the predicate is false, no note, no error.
- **Guard.** `test_guard_the_pack_fixtures_produce_a_bound_in_progress_unit`: the fixtures produce a
  bound, in-progress unit and a receipt for its test set and literal command line today.

## Ground rules

- **Code.** The view is a projection over the snapshot and the evidence objects it names. It reports
  `current_pack_evidence_is_green` as the predicate rather than re-implementing it, and derives the
  marks from the records only when that is true. It reuses the store's existing strict decoding of
  evidence records and opens the store read-only. No change to recording, any gate, `check`, `next`,
  `status`, the generated projections, or any state, event, evidence or pack format. Pure code stays
  pure (D-1); the CLI and MCP do the I/O.
- **Docs.** `scripts/gen_commands_doc.py` regenerates `docs/commands.md`. `docs/claim-boundary.md`'s
  Evidence section gains the R15 sentence; the reviewer card names the verb; `docs/schemas.md` gains
  the R16 section built from real output on a scratch store. Every new statement is true of the
  code; the docs keep D-49's deferral of comparing which tests ran (`vouch_mk2-inv.4`). Keep every
  phrase other tests pin. `tests/test_mcp.py` (bound to no pack) may be updated for the new tool
  list; list it in the DONE file.
- **Out of scope.** Which tests each run executed and how GREEN's set relates to RED's
  (`vouch_mk2-inv.4`). Binding an evidence command in the frozen pack (`vouch_mk2-inv.3`). Showing
  evidence in `vouch-context.md` or `map.json`. Actor claims (evidence records carry none). Whether
  the gate should accept a REFACTOR as fresh evidence after a spec-only rebind (`vouch_mk2-inv.5`).

## Limits (convergence bound, set before review)

A review blocks only on: a record, mark, note, predicate or `chain_error` value that is wrong for a
store the CLI can produce; an exit code, `--unit` selection, ordering or MCP result that differs from
the Promise; a byte written by `show-evidence` or the MCP tool; a gate, check or format change; a
false statement in a changed doc; or a pinned phrase removed. Hand-forged evidence chains are
`vouch_mk2-22a`'s. Text layout and wording beyond what R5 pins are notes.
