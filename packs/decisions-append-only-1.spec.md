# decisions-append-only@1 acceptance pack

**Owner:** Claude (Opus 5.5, acceptance owner). **Implementer:** GPT 6.1 Sol — may not edit this
file, `tests/test_decisions_append_only.py` or `DECISIONS.md`. **Decision:** D-29. **Beads:**
`vouch_mk2-b8j` (unit), `vouch_mk2-b8j.1` (implement).

## Promise

`DECISIONS.md` is append-only within each entry. `scripts/decisions_guard.py` splits the log into
sections, `DECISIONS.pins.json` records a digest of each section's pinned bytes, `verify` fails
when pinned text has changed or new text is not yet pinned, and `pin` extends the pins without
ever accepting a change to pinned text. The test suite runs `verify` on mk2's own log.

## Interface

```
python scripts/decisions_guard.py verify [--log PATH] [--pins PATH]
python scripts/decisions_guard.py pin    [--log PATH] [--pins PATH]
```

`--log` defaults to `DECISIONS.md` and `--pins` to `DECISIONS.pins.json`, both in the repository
root, which is the parent of the script's directory, whatever the working directory.

**Sections.** A line matching `^## D-(\d+)\b` starts an entry, keyed `D-<n>`. The `header` section
is the text before the first entry; every other section runs from its heading line up to the next
entry heading or the end of the file. The sections concatenate to the whole file. Digests are over
the UTF-8 bytes.

**Pins file.** JSON, `{"format": 1, "sections": [{"key": ..., "bytes": ..., "sha256": ...}, ...]}`,
one object per section in log order: `bytes` is the length of the pinned prefix in bytes and
`sha256` the lowercase hex SHA-256 of those bytes.

**Exit codes.** 0: verified, or pinned. 1: the guard found a problem (`verify`), or refused (`pin`).
2: usage error; the log is missing, unreadable or not UTF-8; or the pins file is malformed (not a
JSON object with `format` 1 and a `sections` list of well-formed objects) or, for `verify`,
missing. `pin` creates a missing pins file.

**Problems.** `verify` prints one line per problem, each naming its section:

| Condition | Line contains |
|---|---|
| a pinned section's leading `bytes` no longer hash to its `sha256`, or the section is shorter | `<key>: pinned text changed` |
| a pinned key is absent from the log | `<key>: missing` |
| a section is longer than its pin | `<key>: unpinned text` |
| a section has no pin | `<key>: not pinned` |
| the entries are not `D-1`, `D-2`, … `D-N` in order, each once | `out of order` |

`pin` exits 1 and leaves the pins file byte-identical if verify would report any problem other than
`unpinned text` or `not pinned`. Otherwise it writes pins covering every section in full and
exits 0; when nothing changed, the pins file stays byte-identical.

## Requirements

- **R1 — A pinned log verifies; the pins cover every section exactly.**
  `test_a_pinned_log_verifies_and_the_pins_cover_every_section`: `pin` on a fresh log creates the
  pins file; `verify` exits 0; the pins list `header`, `D-1`, `D-2`, `D-3` with each section's
  full byte length and digest; a second `pin` leaves the file byte-identical.
- **R2 — Changed pinned text fails, naming only that section.**
  `test_changed_pinned_text_fails_naming_only_that_section`: a changed word, a trailing space, a
  line inserted mid-entry, a truncated entry, and a changed header each make `verify` exit 1 with
  `<key>: pinned text changed` for that section and no other.
- **R3 — An amendment appended to an entry is accepted once pinned.**
  `test_an_amendment_appended_to_an_entry_is_accepted_once_pinned`: text appended to the end of
  `D-2` makes `verify` exit 1 with `D-2: unpinned text` and no `pinned text changed`; `pin` exits
  0; `verify` exits 0 and the `D-2` pin covers the amended section.
- **R4 — A new entry fails until pinned.** `test_a_new_entry_fails_until_it_is_pinned`: an
  appended `D-4` makes `verify` exit 1 with `D-4: not pinned`; after `pin`, `verify` exits 0.
- **R5 — `pin` never accepts changed text.** `test_pin_refuses_changed_text_and_writes_nothing`:
  with `D-1` edited and a new `D-4` appended, `pin` exits 1 naming `D-1: pinned text changed`, and
  the pins file is byte-identical.
- **R6 — Removed, reordered and misnumbered entries fail.**
  `test_a_missing_reordered_or_misnumbered_entry_fails`: a removed `D-2` reports `D-2: missing`;
  swapped, duplicated and renumbered entries report `out of order`; `pin` refuses each and writes
  nothing.
- **R7 — Unreadable inputs are usage errors.**
  `test_an_unreadable_log_or_pins_file_is_a_usage_error`: `verify` without a pins file, either
  command with malformed pins (bad JSON, `format` 2, a non-object), and either command without a
  log exit 2 and create or change no file.
- **R8 — mk2's own log is pinned.** `test_mk2_own_log_is_pinned_and_verifies_from_any_directory`:
  `DECISIONS.pins.json` exists at the repository root, covers `header` and every entry `D-1` to
  `D-N` (N ≥ 29), and `verify` with no arguments, run from an unrelated directory, exits 0.
- **R9 — Standard library only.** `test_the_guard_uses_only_the_standard_library`: the script
  imports only standard-library modules, so the guard runs on any Python 3.14 without `uv sync`.
- **R10 — The log states the rule** (`# guard:`, already true at freeze).
  `test_the_log_header_states_the_rule_and_d29_records_it`: the header states that text already in
  the log never changes, that amendments are appended to the end of the entry they amend, and how
  to pin; it no longer says "never an edit"; D-29 exists.

## Ground rules

- Do not edit this file, the test file, `DECISIONS.md`, `.vouch/`, the README count, or git.
  Create `DECISIONS.pins.json` by running `pin` on the current `DECISIONS.md`; never write it by
  hand.
- `pin` writes the pins file atomically (a temporary file in the same directory, then
  `os.replace`), so an interrupted `pin` leaves the old file or the new one, never a mix.
- The script has a module docstring stating what it guards and what it does not: a deliberate edit
  made together with a hand-edited pins file passes, and only review of the diff catches it (D-29).
- Messages go to stdout for `verify` problems and stderr for usage errors; the tests read both.
- Pretty-print the pins file with two-space indentation and a trailing newline, so diffs of it are
  readable.

## Out of scope

- Who may append, and whether an appended note follows the bold-label form: review checks that.
- Protecting the pins file itself; D-29 states that boundary.
- Any change to the `vouch-mk2` package, the dogfood store, or CI configuration. The test suite
  already runs in CI.
