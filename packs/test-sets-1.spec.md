# test-sets@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_test_sets.py`, `tests/fixtures/test-sets/legacy-store.json` or
`packs/test-sets-1.effects.json`.
**Decision:** D-52 (`vouch_mk2-inv.4`). **Seams (D-44):** the CLI on scratch projects (`init`,
the lifecycle, `record-evidence --json` receipts as the oracle for recorded values,
`show-evidence`, `check`, `merge`), in process and, for the FIFO case, as a subprocess with a
timeout; a child command the test writes itself (a Python script that writes the JUnit report it
is told to write, so the expected IDs and outcomes are known independently); `Store` read-only
and the strict codec (`decode_event_record`/`encode_event_record`,
`decode_evidence_event`/`encode_evidence_event`); a store fixture written by the code before this
unit (`tests/fixtures/test-sets/legacy-store.json`: its files and its `show-evidence --json`
output then); the MCP server (`vouch_mk2.mcp.serve`); `tempfile.tempdir` set to a scratch
directory; the text of `docs/*.md`.
**Effects:** declared in `packs/test-sets-1.effects.json`.

## Promise

**Recording.** `record-evidence ... --junit-report ... -- <argv>` requires the literal token
`{junit}` to occur exactly once in the child argv, as a whole argument or inside one (for example
`--junitxml={junit}`). Token errors are refused before the child starts. Just before running the
child, Vouch creates a new, unpredictable directory with Python's `tempfile` (private, mode
`0700`, from creation), and replaces the token with the path of a report file inside it; the file
does not exist when the child starts, and each recording gets a new directory. The child runs
with the expanded argv in both the plain and the `--json` path (in `--json` mode stdout carries
only the one JSON receipt, as today). After the child exits, Vouch reads the report, removes the
directory in every case (success, refusal, error, a child killed by a signal), and records the
evidence. The stored command line is built from the argv with the literal token, after
redaction (a redacted token-bearing argument is stored as `<redacted>`), and is validated in that
form, so it never contains the private path. A child killed by a signal is refused as today,
whatever report it wrote.

**Decoding.** The report is opened without following a symlink and without blocking (a FIFO or
other non-regular file is refused), and at most the store's artifact bound plus one byte is read
(a larger file is refused). It must be an XML document with no document type declaration (refused
by a parser callback, so the check holds in any encoding the parser accepts, and a declaration's
spelling inside a comment is not one); entities other than XML's predefined ones and character
references are therefore impossible, and those are decoded in attribute values. Vouch does not
build an unbounded tree. `<testcase>` elements anywhere under the root must number at least one;
at the 100,001st the report is refused. Each test's ID is its `classname`, `::`, and its `name`
(a missing attribute is the empty string), and must be unique and 1–4,096 printable characters;
other attributes and text are ignored whatever their size. A test's outcome is `failed` if it has
a `<failure>` child, else `error` for `<error>`, else `skipped` for `<skipped>`, else `passed`.
A report that breaks any of this, a missing report, or a missing or repeated token refuses the
recording: exit 2, stderr naming `evidence.test-report-invalid`, no evidence record and no store
object written.

**Storage.** The tests and outcomes are encoded canonically, sorted by ID, in a test-report object
in its own digest domain (the same tests in any report order give the same digest); if the encoded
object exceeds the store's artifact bound the recording is refused as above. The evidence record
names its digest in a new field `test_report`, as evidence format 3; the evidence-recorded event
in the event log carries the field only for a format-3 record. A record made without
`--junit-report` is format 2 with an empty `test_report`, and its evidence object and event-log
record are byte-for-byte what the code before this unit wrote; format-1 and format-2 records and
the event-log records that carry them decode, re-encode to the same bytes, and replay as before.
The `record-evidence --json` receipt shows `evidence_format` and `test_report` (empty for format 2).
Every format-3 record in a chain, counted or not, must have its report object resolve and decode
strictly; otherwise the chain is unreadable (`check.evidence-chains` fails; `show-evidence` and the
MCP `evidence` tool give the unit a `chain_error`). A merge, fast-forward or replayed, that brings
in format-3 records brings their report objects. A Vouch release older than this one cannot read
or replay a format-3 record (a declared store action).

**Reporting.** `show-evidence` adds `test_report` (the digest, or `""`) to every record and
`test_comparison` to every unit. When D-49 marks both a RED and a GREEN that count and both have
reports, `test_comparison` is `{"relation", "red_only", "green_only", "red_failures_not_passed"}`:
the relation of the GREEN report's IDs to the RED report's (`equal`, `superset`, `subset`,
`overlap` when they share some but neither contains the other, `disjoint` when they share none),
the sorted IDs only in RED and only in GREEN, and the sorted IDs whose RED outcome is `failed` or
`error` and whose GREEN outcome is not `passed` (absent from GREEN included); otherwise it is
`null`. Notes come in this order, each at most once: the existing command note (unchanged, with or
without a comparison), then `GREEN's report lacks tests RED's report lists` when the relation is
`subset`, `overlap` or `disjoint`, then `a test that failed in RED's report did not pass in GREEN's
report` when `red_failures_not_passed` is not empty. The text form shows, for a unit with a comparison, a line
`tests: <relation>`, and the notes. Nothing refuses or blocks because of the comparison. The MCP
`evidence` tool returns the same object. The reports are what the commands' reports list: Vouch
knows where it collected each report, not which tests ran.

## JSON additions

```json
{"records": [{"...": "...", "test_report": "<64 hex or empty>"}],
 "test_comparison": {"relation": "superset", "red_only": [],
   "green_only": ["t::c"], "red_failures_not_passed": []}}
```

## Requirements

Each test fails before the change because `record-evidence` has no `--junit-report` option
(argparse exits 2 without the refusal code), the receipt has no `test_report`, `show-evidence` has
no `test_report` or `test_comparison`, or the docs lack the text, except the guard.

- **R1 — A recorded report.** `test_a_junit_report_is_recorded_with_its_tests`. A RED with
  `--junit-report --json` and the token inside `--report={junit}`; the child prints to stdout and
  writes `t::a` (failed), `t::b` (passed) and `t::x&y` (from `name="x&amp;y"`): exit 0, stdout is
  one JSON receipt with `evidence_format` 3, a 64-hex `test_report` whose object exists and holds
  the decoded ID `t::x&y`, and a stored command line containing `--report={junit}`;
  `show-evidence` shows the same digest. With `--redact-arg 2` on the token-bearing argument (argv
  is interpreter, script, token), the stored line has `<redacted>` there and the report is still
  recorded.
- **R2 — Vouch's private path.** `test_the_child_writes_to_a_private_path_vouch_removes`. With
  `tempfile.tempdir` set to an empty scratch directory, the child records the path it received:
  inside the scratch directory, not inside the project, its parent a directory of mode `0700`, the
  file absent at start; a second recording gets a different directory; the scratch directory is
  empty afterwards.
- **R3 — Without the option.** `test_without_the_option_the_record_is_format_two`. The child
  receives `{junit}` literally; the receipt has `evidence_format` 2 and `test_report` `""`.
- **R4 — A superset.** `test_a_green_that_ran_more_tests_is_a_superset_with_no_note`. RED `t::a`
  failed, `t::b` passed; GREEN (same command) `t::a`, `t::b`, `t::c` passed: `superset`,
  `green_only` `["t::c"]`, no note, and the text form shows `tests: superset`.
- **R5 — Missing tests are noted, never blocking.**
  `test_a_green_missing_tests_is_noted_and_never_blocks`. At `standard`, RED `t::a` failed and
  `t::b` passed; GREEN `t::a` passed: `subset`, `red_only` `["t::b"]`, the missing-tests note in
  JSON and text; `mark-done` and `publish` succeed.
- **R6 — RED failures and errors.** `test_red_failures_and_errors_not_passing_are_noted_in_order`.
  RED `t::e` error and `t::a` failed; GREEN `t::b` passed and `t::a` skipped: `overlap`,
  `red_only` `["t::e"]`, `green_only` `["t::b"]`, `red_failures_not_passed` `["t::a", "t::e"]`,
  notes exactly `[missing-tests note, failure note]`.
- **R7 — Every relation.** `test_the_relation_is_named_for_each_shape` (parametrized): equal,
  superset, subset, overlap and disjoint ID sets give those relations, sorted `red_only` and
  `green_only`, the missing-tests note exactly for subset, overlap and disjoint, and the text line
  `tests: <relation>`.
- **R8 — Only the marked pair.** `test_the_comparison_follows_the_marked_pair_only`. (a) RED with no
  report, GREEN with one: `null`, no test note. (b) RED, GREEN and then RED, all with reports: no
  marks, `null`, no notes. (c) RED with no report and GREEN with a report and a different command:
  `null` and exactly the command note.
- **R9 — Bad reports and tokens refuse.**
  `test_a_bad_report_or_token_refuses_and_writes_nothing` (parametrized): no token, the token twice
  in two arguments, the token twice in one argument (each refused before the child starts: a
  sentinel the child would write is absent), no report written, malformed XML, a DTD, a DTD in a
  UTF-16 document, duplicate IDs, no test case, a symlink in place of the report, a 4,097-character
  ID, 100,001 short unique test cases, a file one byte over the store's 64 MiB artifact bound, and
  a file under that bound whose encoded test-report object would exceed it (16,384 IDs of 4,000
  backslashes each; all written by the child) each exit 2 naming
  `evidence.test-report-invalid`, with no file added to or changed in the store and the scratch
  temporary directory empty.
- **R10 — A FIFO does not hang.** `test_a_fifo_in_place_of_the_report_is_refused_without_blocking`.
  Run as a subprocess with a 120-second timeout: the child leaves a FIFO at the path; exit 2 naming
  `evidence.test-report-invalid`, within the timeout.
- **R11 — A signalled child.** `test_a_child_killed_by_a_signal_is_refused_with_its_report`. The
  child writes a valid report and kills itself: exit 2 with the existing no-verdict refusal,
  nothing written, the scratch directory empty.
- **R12 — Large unrelated attributes.** `test_large_unrelated_attributes_are_ignored`. A RED
  report (written by the child) whose one test has a `<failure>` with a 1,000,000-character
  `message` is recorded; with a GREEN listing that test as skipped, `red_failures_not_passed` is
  `["t::a"]`.
- **R13 — Canonical storage and note order.**
  `test_reports_are_canonical_and_the_command_note_comes_first`. Two REDs whose reports list the
  same tests in different orders have the same `test_report`; a GREEN with a different command,
  fewer tests and RED's failed test skipped gives notes exactly `[command note, missing-tests
  note, failure note]`.
- **R14 — Broken report objects break the chain.**
  `test_a_missing_or_corrupt_report_object_makes_the_chain_unreadable` (parametrized): the GREEN's
  report object deleted, or an earlier, uncounted RED's report object overwritten: `check` reports
  `check.evidence-chains` `FAIL`, `show-evidence` exits 1 with the unit's `chain_error` set.
- **R15 — Merges carry reports.** `test_a_merge_brings_the_report_objects` (parametrized: a
  fast-forward, and a replayed merge after the base specifies another unit): the base gets the same
  `test_comparison` and `check.evidence-chains` `PASS`.
- **R16 — A store from before.** `test_a_store_written_before_reads_replays_and_reencodes_unchanged`.
  The legacy fixture, copied: each event-log and evidence object it names re-encodes to its exact
  bytes, and so does its format-1 evidence object; `check.event-log` and `check.evidence-chains`
  pass; `show-evidence` gives the fixture's output with `test_report: ""` added to each record and
  `test_comparison: null` to each unit; a new format-2 GREEN recorded on it has exactly the old
  evidence object's keys, its event-log record has no `test_report`, and both checks still pass.
- **R19 — `--json` keeps stdout for the receipt.** `test_json_mode_keeps_stdout_for_the_receipt`.
  As a subprocess: stdout is one JSON receipt (`evidence_format` 3) and the child's stdout line is
  on stderr.
- **R20 — Outcome precedence.** `test_failure_outranks_other_outcome_children`. A RED test case with
  `<skipped/>` then `<failure/>` is `failed`, one with `<skipped/>` then `<error/>` is `error`; with
  a GREEN listing neither, both are in `red_failures_not_passed`.
- **R17 — MCP.** `test_the_mcp_evidence_tool_shows_the_comparison`. `structuredContent` equals
  `show-evidence --json` for a store with a comparison.
- **R18 — Docs.** `test_docs_state_what_the_report_proves`. `docs/claim-boundary.md` contains,
  whitespace normalised, ``\`record-evidence --junit-report\` stores the test IDs and outcomes that
  the JUnit report at a path only Vouch gave the command lists; the report is the command's claim,
  like the exit status, and Vouch does not see which tests ran.`` and ``\`show-evidence\` compares
  the tests the counted GREEN's report lists with those the counted RED's report lists and notes
  when GREEN's report lacks some of RED's; the comparison never blocks publication.``;
  `docs/commands.md` mentions `--junit-report`; `docs/errors.md` names
  `evidence.test-report-invalid`; `docs/schemas.md`'s `## \`show-evidence --json\`` section shows
  `test_report` and `test_comparison`.
- **Guard.** `test_guard_the_fixtures_record_evidence_today`: the scratch project gives a started,
  bound unit, and `record-evidence` without the option records the writer child's exit status
  today.

## Ground rules

- **Code.** Decoding is strict and bounded as the Promise says; the standard library's expat
  parser is acceptable with a doctype callback that refuses and an incremental parse. Pure code
  stays pure (D-1): the comparison belongs with the `show-evidence` projection and receives
  decoded reports; file and process I/O stay in the CLI and the store. No change to any gate,
  check verdict (other than reading report objects as part of a chain), `next`, `status`, state
  format, or the encoding of format-1 and format-2 evidence and their event-log records. Formal
  universes are untouched.
- **Fallout (owner's).** `show-evidence@1`'s frozen tests pin its exact JSON keys and its
  `docs/schemas.md` example; they will fail. The owner adapts and rebinds them; Sol lists each such
  failure in the DONE file and does not edit them.
- **Docs.** The R18 sentences in `docs/claim-boundary.md`'s Evidence section, with the
  compatibility boundary (older releases cannot read format 3); `docs/schemas.md`'s
  `show-evidence --json` section and the `record-evidence` receipt section from real output;
  `docs/errors.md` with the refusal's exit, cause and recovery; `docs/commands.md` regenerated;
  keep every pinned phrase.
- **Out of scope.** Constraining IDs to the frozen test files; normalising IDs across runners;
  storing raw XML; binding the evidence command (`vouch_mk2-inv.3`); any gate on the comparison;
  same-user processes racing the private path (level 3).

## Limits (convergence bound, set before review)

A review blocks only on: a recorded, refused, compared, noted or displayed value that differs from
the Promise for a report the child can write; a report path that is not Vouch's private one, or a
directory left behind; a DTD, entity expansion, external fetch, hang, crash or unbounded read or
allocation reachable from a report; a format-3 record or report object that a check,
`show-evidence`, the MCP tool or a merge mishandles; a format-1 or format-2 record, event-log
record or mixed chain that this code decodes, re-encodes or replays differently from the code
before it; a change to a gate or a state format; a false statement in a changed doc; or a pinned
phrase removed. Older releases failing on format-3 records is the declared boundary.
Hand-edited stores are `vouch_mk2-22a`'s boundary (D-50). Wording beyond what the tests pin is a
note.
