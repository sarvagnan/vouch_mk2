# evidence-policy@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_evidence_policy.py` or `packs/evidence-policy-1.effects.json`.
**Decision:** D-54 (`vouch_mk2-inv.3`). **Seams (D-44):** the CLI on scratch projects (`init`,
the lifecycle, `freeze --json` and `record-evidence --json` receipts as the oracle for recorded
digests and values, `rebind-pack`, `bind-pack --help`, `show-evidence --json`, `check`), in
process; a child command the test writes itself (a Python script that writes the JUnit report it
is told to write, given as `--report=PATH` or `--report PATH`, and, when asked, a sentinel file
that shows it started); `Store` and the library write `vouch_mk2.verify.record_evidence`, which
records what it is given and stands for a record written by an older release or another clone
that skipped the policy (so the policy is enforced by the `record-evidence` command and verified by
`check`, not by that function); the strict codec `encode_test_report`; a leftover temporary file
`.vouch/.state-victim.tmp`, which a writable store open sweeps; the text of `README.md` and
`docs/*.md`.
**Effects:** declared in `packs/evidence-policy-1.effects.json`.

## Promise

**The file.** `freeze` and `rebind-pack` take `--pack-evidence-file PATH` (project-relative), beside
`--pack-effects-file`, and, like it, only with `--pack-file` or `--pack-test-file`; `bind-pack`
does not take it. The file is read like the other pack files (a regular file, no symlinks, within
the project, a distinct path from every other pack file). It is strict JSON (UTF-8, no duplicate
keys) holding an object with exactly these keys: `evidence_format`, the integer `1` (not a
boolean, not `1.0`); and at least one of `command`, a non-empty list of non-empty strings in which
the literal token `{junit}` occurs at most once in all (as a whole string or inside one), and
`reports`, the string `"required"`. If `reports` is present and `command` is too, `command` must
contain `{junit}`. The bound command line, `shlex.join(command)`, must pass the stored-command-line
validation `record-evidence` applies (1 to 4,096 printable characters). Content that breaks these
rules, the option given twice, and the option without `--pack-file` or `--pack-test-file` are
refused with `pack.evidence-policy-invalid`; an unreadable or symlinked file and a path shared with
another pack file are refused as for any other pack file.

**Binding.** The file is bound in the pack inventory like the other pack files, and the decoded
policy is stored in the inventory, so `record-evidence` and `check` read the frozen policy: editing
or removing the working-tree file after the freeze changes nothing they do. The file is part of
the test set in both projections: with explicit test files the test-set digest covers the test
files and the policy; with the whole-pack fallback it covers every file but the effects file, as
today. Packs that differ only in their policy therefore have different test sets, and freezing a
different policy re-keys the evidence. A pack frozen without the file has the inventory digest and
test-set digest the code before this unit gives it, and behaves exactly as before.

**Bound command.** For a unit whose current pack binds a command, `record-evidence` (every kind)
refuses, before starting anything, a child argv that is not exactly the bound list (refusal
`evidence.command-not-bound`), and refuses `--redact-arg` (refusal `evidence.redaction-refused`).
When the bound list contains `{junit}`, `--junit-report` is required for every kind (refusal
`evidence.report-required`); the argv is compared with the token unexpanded. The stored command
line is `shlex.join` of the bound list. The checks run in this order, after the existing argv
validation of D-52 (a `--junit-report` without exactly one token is still
`evidence.test-report-invalid`): command, redaction, report option.

**Required reports.** For a unit whose current pack requires reports:
- A RED or GREEN without `--junit-report` is refused before starting anything (refusal
  `evidence.report-required`). A REFACTOR needs no report. `--redact-arg` stays allowed unless a
  command is bound.
- A RED whose report lists no test as `failed` or `error` is refused (refusal
  `evidence.red-report-no-failure`).
- A GREEN is compared with a comparison RED: the latest RED for the current test set before it,
  or, when there is none, the latest RED in the unit's chain. With no comparison RED, or one
  without a report, the GREEN is refused (refusal `evidence.report-no-red`). Otherwise it is
  refused (refusal `evidence.report-incomplete`) unless the GREEN's report lists every test ID the
  comparison RED's report lists, and every ID that report lists as `failed` or `error` is listed
  as `passed` in the GREEN's report. Extra IDs in the GREEN's report, and RED-passed IDs that the
  GREEN lists with any outcome, are accepted.
- These three refusals are decided under the store's write lock, against the chain the record
  would join.

**Refusals.** Every refusal above (the policy file and option refusals of `freeze` and
`rebind-pack`, and the `record-evidence` refusals) exits 2, writes no refusal payload to standard
output (with `--json` too; a child's own output is the child's), and writes a line starting
`vouch-mk2: <refusal>` to standard error. Other `freeze` and `rebind-pack` refusals are presented as
today. A refusal before starting anything happens before the child process starts. A `freeze`
refused for its policy file or option, and a `record-evidence` refusal before starting anything,
leave every store file other than `.lock` byte-for-byte unchanged, a leftover temporary file
included. A refusal after the child ran adds no evidence record and no object and changes no other
store file, except that a leftover temporary file is either kept with its bytes or swept and the
store's `.lock` may be created (the writer lock the append takes). Units whose current pack has no
policy behave exactly as before.

**Rebind.** `decide` refuses `RebindPack` with `rebind.evidence-policy-changed` when the old and
new inventories' decoded policies differ (one added, one removed, or different content). It is
checked right after `rebind.effects-changed` and before the path rules, and, being in `decide`, it
also refuses such a rebind replayed by `merge` or `check-merge`. `rebind-pack` presents it as any
decision refusal: exit 2, `rebind.evidence-policy-changed: ...` on standard error, or, with
`--json`, `{"accepted": false, "violations": [...]}` naming it on standard output; nothing is
recorded. The comparison is of the decoded policy, so a whitespace-only edit of the file at the
same path is accepted, and the same bytes under a new path meet the existing
`rebind.paths-removed`. A rebind that keeps the policy and changes the test files proceeds as
before; the next GREEN is compared with the RED before the rebind, as above.

**Check.** For every unit whose current pack binds a policy, `check.evidence-chains` walks every
evidence record for the current test set, in chain order, and fails if any breaks it: a stored
command line other than `shlex.join` of the bound command, or a record without a report when the
bound command contains `{junit}` (any kind); with required reports, a RED or GREEN without a
report, a RED whose report lists no failure, or a GREEN whose report does not meet the comparison
above against its own comparison RED. Records for other test sets are not held to the policy, but
remain candidates for comparison RED. Units without a policy are checked as before. When a unit's
pack inventory object cannot be read, `check` fails either way: for a unit that is not superseded,
`check.evidence-chains` keeps the verdict it gave before this unit and `check.effects-declared`
fails naming the unavailable inventory; for a superseded unit, which `check.effects-declared`
skips, `check.evidence-chains` fails naming it.

## Amended by DEV-ep-01 (pack/2)

Astra's implementation review found two places where the Promise said more than the store allows.
A refusal after the child ran is decided under the writer lock in the same writable open that
would append, so on a fresh checkout it creates the store's `.lock`; the Refusals paragraph now
says so (unlinking a lock another writer may hold would break serialization, and the tests already
leave `.lock` out of every snapshot). And a lost pack inventory object hides a bound policy from
`check.evidence-chains`, which keeps its earlier verdict so legacy fixtures without inventory
objects keep theirs; the missing object already fails `check.effects-declared` for every unit that
is not superseded, so `check` still fails. The Check paragraph now says so. No test or effects
change.

## Amended by DEV-ep-02 (pack/3)

Astra's re-check: `check.effects-declared` skips superseded units, so a superseded unit whose
inventory was lost and whose chain breaks its policy left `check` passing. The chain row now fails
naming the unavailable inventory for a superseded unit, and the Check paragraph says which row
fails for which unit. No test or effects change.

## Requirements

Expected RED reasons, before the change. R1 is a guard (it passes today and must keep passing);
R16's `bind-pack` assertion passes today too. Every test that freezes a scratch unit with a policy fails at that
freeze: `freeze` has no `--pack-evidence-file` option, so argparse exits 2 with a usage message.
R3 reaches its assertion and fails because the refusal does not name `pack.evidence-policy-invalid`
(or `duplicate path`). R2's no-policy projections pass; its policy projections fail at the freeze.
R13's `added` case freezes and publishes without a policy and fails on the unknown `rebind-pack`
option. R16's `freeze` and `rebind-pack` help lacks the option; R15 lacks the docs text.

- **R1 — No policy, no change.** `test_a_pack_without_a_policy_keeps_its_digests_and_commands`
  (guard). A pack without a policy has the inventory digest and test-set digest the code before
  this unit gives it, pinned as literals from the `freeze --json` receipt; RED and GREEN may run
  different commands and redact arguments, and the record is format 2.
- **R2 — The policy is in the test set.** `test_the_policy_is_part_of_both_test_set_projections`.
  With explicit test files and with the whole-pack fallback, varying one input at a time from a
  policy pack: the policy changes the test set; the effects file does not; the spec file does only
  in the fallback; a whitespace-only edit of the policy changes it too. No policy pack has R1's
  test set. Three policies (reports only; two commands
  differing by one argument) give three test sets. A RED's receipt carries the current one.
- **R3 — A malformed file is refused.** `test_freeze_refuses_a_malformed_policy` (parametrized:
  not JSON, invalid UTF-8, a non-object root, a duplicate key, format 2, a boolean format, format
  `1.0`, no opt-in, an unknown key, another `reports` value, an empty command, a string command, a
  non-string argument, an empty argument, the token twice, required reports with a command lacking
  the token, a nonprintable argument, a command line over 4,096 characters):
  `pack.evidence-policy-invalid`, the refusal contract, store unchanged.
  `test_freeze_refuses_a_misused_option` (the option twice, the option alone):
  `pack.evidence-policy-invalid`. `test_a_policy_file_is_read_like_any_pack_file` (a symlink; the
  test file's path): exit 2, nothing on standard output, standard error names the path and
  `cannot open` or `duplicate path`, store unchanged.
- **R4 — The bound command is accepted.** `test_the_bound_command_is_accepted_for_every_kind`.
  RED, GREEN and REFACTOR running exactly the bound argv are recorded with the same command line,
  `shlex.join` of the bound list, and `check.evidence-chains` passes.
- **R5 — Another command is refused before launch.**
  `test_another_command_is_refused_before_launch` (parametrized over RED, GREEN, REFACTOR and an
  extra, a different, a dropped and a reordered argument): `evidence.command-not-bound`, the
  sentinel absent, store unchanged.
- **R6 — No redaction with a bound command.** `test_redaction_is_refused_for_a_bound_command`:
  `evidence.redaction-refused`, the sentinel absent, store unchanged.
  `test_reports_only_allows_redaction`: a reports-only RED with `--redact-arg` is recorded with
  `<redacted>` in its command line.
- **R7 — A bound token needs the option.**
  `test_a_bound_command_with_the_token_needs_the_report_option` (parametrized: the token inside an
  argument, the token as a whole argument). Without `--junit-report`: `evidence.report-required`
  before launch; with it: recorded, format 3, the stored line keeps the token.
  `test_a_bound_token_applies_to_a_refactor`: likewise for a REFACTOR.
  `test_a_bound_command_without_the_token_refuses_the_report_option`:
  `evidence.test-report-invalid` before launch. `test_the_command_is_checked_first`: another
  argv without `--junit-report` under a token-bound command is `evidence.command-not-bound`.
- **R8 — Required reports.** `test_required_reports_refuse_a_run_without_one` (RED, GREEN):
  `evidence.report-required`, the sentinel absent, store unchanged.
  `test_a_refactor_needs_no_report`: a REFACTOR without a report is recorded as format 2.
- **R9 — RED must list a failure.** `test_a_red_report_must_list_a_failure` (all passed; skipped
  and passed): `evidence.red-report-no-failure`, no record; a RED with an error is recorded.
- **R10 — GREEN must cover RED.** `test_a_green_report_must_cover_the_red_report` (parametrized):
  refused with `evidence.report-incomplete` for a missing test, a RED failure skipped in GREEN, a
  RED error failing in GREEN, and disjoint IDs; accepted for a superset, a RED error passing with
  a RED-passed test skipped, and the same tests in another order.
- **R11 — The latest RED counts.** `test_the_latest_red_is_the_comparison`. After REDs {a,b}
  then {a}, a GREEN {a} is accepted; after REDs {a} then {a,b}, it is refused.
- **R12 — No RED.** `test_a_green_with_no_red_is_refused`: `evidence.report-no-red`, no record.
- **R13 — Rebind.** `test_rebind_cannot_change_the_policy` (parametrized: a command added at the
  same path, one argument of a bound command changed with both opt-ins kept, removed, added to a
  published unit without one; text and `--json`): `rebind.evidence-policy-changed`, store
  unchanged. `test_decide_refuses_a_rebind_that_changes_the_policy`: `decide` itself refuses a
  `RebindPack` whose real old and new inventories (read from two frozen stores) differ in the
  command, with exactly that violation.
  `test_rebind_compares_what_the_policy_says`: a whitespace-only edit is rebound; the same bytes
  under a new path are refused with `rebind.paths-removed`.
  `test_after_a_rebind_green_is_compared_with_the_red_before_it`: a GREEN forged with a short
  report fails the check; after a rebind that keeps the policy and changes the test file, a GREEN
  missing a test the pre-rebind RED listed is refused with `evidence.report-incomplete`, a
  covering GREEN is recorded, and the check passes (the forged GREEN is for another test set).
  `test_a_red_for_the_current_test_set_comes_first`: after RED {a} for test set A, a rebind to B,
  RED {a,c} for B, and a rebind back to A, a GREEN {a} is accepted and the check passes.
  `test_a_reportless_fallback_red_refuses_the_green`: with a reportless RED forged last before a
  rebind, the first GREEN after it is refused with `evidence.report-no-red`.
- **R14 — Check.** `test_check_walks_every_record_against_the_policy` (parametrized, each forged
  through `vouch_mk2.verify.record_evidence` after an honest RED and GREEN: a GREEN with another
  command, a GREEN without a report, a GREEN missing a RED test, a GREEN listing a RED failure as
  skipped, a short GREEN followed by an honest covering GREEN, a RED listing no failure, a
  REFACTOR with another command): `check.evidence-chains` is PASS before and FAIL after.
  `test_check_needs_a_report_for_a_token_bound_command`: with a command-only policy whose command
  holds the token, a forged record with the bound line and no report fails the check.
  `test_check_needs_reports_when_they_are_required`: with a reports-only policy, a forged RED
  with no report fails the check. `test_check_compares_each_green_with_its_own_red`: honest RED {a,b}, GREEN, RED {c}, GREEN {c}:
  PASS.
- **R15 — Docs.** `test_docs_state_the_policy`. `docs/claim-boundary.md` contains, whitespace
  normalised: "A pack may bind an evidence-policy file (D-54) that fixes the command RED and GREEN
  must run, requires JUnit reports, or both."; "With reports required, `record-evidence` refuses
  a RED whose report lists no failure and a GREEN whose report omits a test the RED's report lists
  or does not list one of RED's failures as passed."; "The policy is part of the test set, so a
  different policy is a different test set, and `rebind-pack` cannot change a published pack's
  policy." `README.md`
  and `docs/concepts.md` each contain "A pack that binds a command in its evidence-policy file
  fixes that command." `docs/errors.md` names each of the eight refusals in backticks.
- **R16 — Where the option is.** `test_the_option_is_on_freeze_and_rebind_only`: the help of
  `freeze` and `rebind-pack` names `--pack-evidence-file`; `bind-pack`'s does not (guard part).
- **R17 — The frozen policy is used.** `test_the_frozen_policy_is_used_not_the_file`: after the
  freeze the policy file is rewritten with another command: the bound command is still recorded
  and the file's new command is refused with `evidence.command-not-bound`; after the file is
  removed the bound command is still recorded and `check.evidence-chains` passes, and a GREEN
  forged with the file's command then fails it.

## Ground rules

- **Code.** The policy decode is strict as the Promise says and lives with the pack inventory
  code; the decoded policy is carried in the inventory like the effects declaration, and its
  encoding grows only for packs with a policy. The comparison of reports is a pure function (D-1)
  fed decoded reports, shared by `record-evidence` and `check`; file and process I/O stay in the
  CLI and the store. The command-level refusals happen in the `record-evidence` command (before
  launch where the Promise says so, from a read-only store open); `vouch_mk2.verify.record_evidence`
  keeps recording what it is given. `decide` gains only the rebind refusal. No change to any gate,
  `next`, `status`, the state format, the evidence or event-log encodings, or formal universes; if
  the formal certificate or a pinned digest moves, that is owner fallout.
- **Fallout (owner's).** If another unit's frozen test pins something this unit must change (for
  example the `freeze` or `rebind-pack` usage text, the list of options, or a formal digest), Sol
  lists the failure in the DONE file and does not edit it; the owner adapts and rebinds.
- **Docs.** The R15 sentences in `docs/claim-boundary.md`'s Evidence section, after the existing
  sentence that the gates compare the frozen test set, not the command (qualify its example in
  context); the R15 sentence in `README.md` and `docs/concepts.md` right after the pinned "The
  same tests" sentence; `docs/errors.md` with each refusal's exit, cause and recovery;
  `docs/commands.md` regenerated; `docs/quickstart.md` unchanged unless a pinned block needs it;
  keep every pinned phrase. Describe a report's content as what the report lists.
- **Out of scope.** A policy required at any tier; per-kind commands; constraining report IDs to
  the frozen test files; showing the policy in `show-evidence`; environment variables or working
  directory in the bound command.

## Limits (convergence bound, set before review)

A review blocks only on: a recorded, refused or checked outcome that differs from the Promise for
a command or report the child can produce; a refusal that starts the child where the Promise says
"before starting anything", or that writes a store file the Promise says it leaves; a policy file
the Promise refuses that freeze accepts, or the reverse; a policy read from the working tree after
the freeze; a pack without a policy whose inventory, test set or behaviour changes; a record for
the current test set that breaks a bound policy and passes `check.evidence-chains`; a rebind
changing the decoded policy that `decide` accepts; a change to a gate, a state, evidence or
event-log format; a false statement in a changed doc; or a pinned phrase removed. Older releases
failing on a pack inventory with a policy is the declared boundary. Whether a report came from the
tests is D-52's boundary; hand edits that forge a whole consistent chain are `vouch_mk2-22a`'s
(D-50). Wording beyond what the tests pin is a note.
