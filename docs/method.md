# Working method

The binding procedure for every forward change to Vouch mk2: each change is a unit run through
mk2's own lifecycle (D-11). D-11 exempts `DECISIONS.md` entries, prose edits with no test binding
and no behavioral claim, housekeeping (this file, `docs/usage-log.md`, the beads export, the
README test count), and the dogfood store's own lifecycle commands. The user picks the model
for each role (D-27). Implementation and review run on Codex quota, which is plentiful; Claude's
is scarce.

| Role | Who | Owns |
|---|---|---|
| Acceptance owner / planner | Claude (Opus 5.5, the main session) | plan, beads, decision records, **the pack**, adjudication of deviations, the final pass, publish, commit |
| Implementer | GPT 6.1 Sol (T3 `delegate_task`: provider `codex`, model `gpt-6.1-sol`, `reasoningEffort: high`) | implementation against a frozen pack; raises deviations; owns and closes its implementation bead |
| Reviewer | GPT 6 Astra (T3 `delegate_task`: provider `codex`, model `gpt-6-astra`) | reproduces defects in the uncommitted diff, ranked, with minimal fixes; novel attacks on the riskiest surface |
| Decider | the user | contract-conflict deviations, D-numbered decisions, `git push`, merging pull requests, `bd sync` |

The planner never implements a unit it wrote the pack for, the implementer never edits a pack,
and the reviewer is neither. Every deviation so far was caught by someone other than its author.

Each unit runs on its own branch, `unit/<unit>-<v>`, cut from the latest `main` after CI's anchor
commit for the previous unit, and reaches `main` only through a pull request (D-32). Keep one
unit in flight at a time, so a branch rarely needs `main` merged into it.

## 0. Plan and beads

1. Record the design as one `D-n` entry in `DECISIONS.md` before the pack. A design that needs
   a user decision becomes a `bd human` question.
2. Create the beads:
   - `<epic>`: the outcome (for example `bd4`, "first usable release");
   - `<epic>.<n>`: one per unit, assigned `claude`, `in_progress` once the pack is written;
   - `<epic>.<n>.1`: "Implement `<unit>@<v>` against frozen pack/1", assigned `sol`. Only Sol
     closes it (`bd close … --reason`). Review, final pass and publish close with the unit bead.

Beads is the task list and `docs/usage-log.md` the cost ledger; nothing lives only in chat.

## 1. The pack (planner), frozen before implementation

1. Write `tests/test_<unit>.py` and `packs/<unit>-<v>.spec.md`: requirements R1…Rn, each bound
   to a test name, ground rules, and what is out of scope.
2. Check every API name the tests use against the real tree (an `inspect.signature` sweep);
   unit #1's pack-author defects were of this kind. Mark tests that already pass `# guard:`.
3. Confirm the split: `pytest -q tests/test_<unit>.py | grep -c ^FAILED` gives the RED count,
   and `pyrefly check tests/test_<unit>.py` errors only on names not yet implemented.
4. Freeze in the dogfood store: `specify → freeze <pack> → mark-ready → start`, with pack
   version `"<unit>-pack/<n>@" + sha256(b"vouch-mk2/pack\0"+spec+b"\0"+tests)[:16]`.
5. Record RED #1 through the harness: `record-evidence … --kind red -- pytest
   tests/test_<unit>.py -q`. Then `advance-anchor`, `generate`, and `check --ci` exits 0.
6. Commit "`<unit>@<v>`: pack frozen, RED recorded, in progress" on the unit branch. The user
   pushes the branch and opens the pull request. Its CI stays red until GREEN, and the red run
   shows less than it seems: the gates job stops at its first failing step, usually the type
   check on names the frozen tests use, so the later gates never run on that commit and an
   unrelated regression looks the same as the expected RED. CI anchors nothing from a branch or
   a red run; RED and GREEN are anchored together after the merge, when `main` goes green.
   Pull-request CI runs only the gates job. The dogfood check and the walkthrough run after the
   merge, which is why §6 step 3 runs them locally.

## 2. Implementation (Sol, T3 delegated task)

Write the brief to `/tmp/codex_prompts/<unit>.md` (read-first list, hard rules, done file, bead
id) and pass its text to one T3 `delegate_task`: provider `codex`, model `gpt-6.1-sol`, options
`{"reasoningEffort": "high"}`, in this checkout. The planner commits. Every brief says:

- the pack may not be edited; on a contradiction, stop on that requirement, write
  `<unit>.DEVIATION.md`, and implement the rest;
- leave `.vouch/`, the README count and git alone;
- which gates must be green;
- write `<unit>.DONE.md`, then `bd close <bead>`.

Quote `bd create --description` in single quotes, since descriptions contain backticks.

The task reports when it finishes, so do not poll it. Run one implementer per tree, and only as a
delegated task: a detached `nohup codex exec` kept editing the tree after its planner was
cancelled on 2026-10-30 (D-27). Stop an unwanted run with `task_cancel`.

## 3. Deviations

The owner opens the deviation in the dogfood store on the implementer's behalf (`open-deviation
… --deviation-type pack-defect|contract-conflict|plan-gap|capability-request`), which blocks the
unit (D-3). Then:

- **Amend** (the pack was wrong): `adjudicate-amend --amendment-version <pack/n+1>`,
  `close-deviation`, `respecify`, edit spec and tests, re-freeze as pack/n+1, and re-record RED
  if the amendment demands new behavior.
- **Reject** (the pack stands): `adjudicate-reject`, `close-deviation`, `begin-reverify`; the fix
  lies outside the pack.

The planner adjudicates pack defects following the user's standing pattern. A contract conflict
(the pack against a `D-n` decision) goes to the user.

## 4. Verification (planner)

The DONE file is not evidence.

- Read the task's report and `git status`, then run `pyrefly check`, `lint-imports` and
  `pytest -q` yourself, and confirm the pack file has no failures.
- Count new tests with `git diff tests/ | grep -c '^+\s*def test_'`. Implementers mostly extend
  existing tests, so the review's scenarios may need regression tests the planner adds.
- Do the owner obligations the implementer is barred from: `upgrade` the dogfood store when the
  unit bumps the state format (D-13; no invented state), `formal --record`, `generate`, and the
  README test count.
- Record GREEN through the harness, `advance-anchor`, and `check --ci` exits 0.

## 5. Review (Astra, T3 delegated task, read-only)

The brief (provider `codex`, model `gpt-6-astra`) lists defect classes in priority order: false
refusals, fail-opens, honesty of `NOT_CHECKED` rows, formal vacuity, projection and transport
gaps, codec strictness. Then it asks for novel attacks on the riskiest surface, and ends:
"Reproduce with a real snippet before reporting; write `<unit>_review.OUT.md` ranked by
severity, then what you checked and found clean." Astra edits only that file.

Fixes go to Sol in one new delegated task: the review file plus the planner's design for
anything that changes shape. The planner re-verifies as in §4 and reproduces each HIGH finding
against the fixed tree. Astra re-checks in a new task, given its findings and the fix diff.

## 6. Final pass, publish, commit (planner)

1. Read the risky surface yourself (decide paths, checks, codec).
2. Add the regression tests the review's scenarios lack.
3. `build-lock`, the dogfood `check --ci` run from the lock, and `scripts/walkthrough.py --lock`.
4. `mark-done` from in-progress, through the real DoD gates. A unit returning through
   re-verifying after a deviation stays there; it is published in step 6, like any other. The
   stop rule ([self-hosting-tradeoff.md](self-hosting-tradeoff.md)): if a gate must be bent to
   publish mk2's own unit, adoption stops and that is recorded.
5. `generate`, certificate, `check --ci` exits 0. Commit "`<unit>@<v>`: … GREEN recorded, done
   pending acceptance" (or "… GREEN recorded, re-verifying") on the unit branch. The user pushes it and merges the pull request with a
   merge commit, not a squash, so the frozen-pack commit stays in `main`'s history ahead of the
   implementation. If `main` moved meanwhile, merge it into the branch locally first: GitHub
   cannot merge `.vouch/`, and a conflict there is recovered with `merge-git-stages`
   ([quickstart.md](quickstart.md), "Recovering from a Git merge or rebase conflict").
6. CI on `main` anchors RED and GREEN and pushes "vouch: advance evidence anchors". Pull it, cut
   `publish/<unit>-<v>`, run `publish` (from done-pending-acceptance or re-verifying, the only
   states it accepts), `generate` and `check --ci` (exits 0), and commit with the
   lifecycle as recorded facts (pack versions, deviations, RED/GREEN numbers, gates). Close the
   unit beads, append `docs/usage-log.md`, and `bd remember` the lessons. The user pushes the
   branch, merges its pull request, and runs `bd sync`.

## Cost rules

Sol and Astra cost no Claude tokens beyond the brief and the planner's verification. Run Codex
work as T3 delegated tasks, not through the `codex` Claude subagent wrapper (Sonnet, on the
shared session limit). Run `/code-review` at medium.

## What to watch

- **Formal universe sizes are a coverage gauge; read them every pass.** A shrink means
  transitions became refused or a command was dropped: unit #6's fix pass removed `Respecify`
  from two universes (core-v1 109k → 66k) and M1–M11 still held over the smaller relation.
  Accept a smaller universe only with the reason in the DONE file.
- To undo your own change to a file the implementer also edited, restore from the implementer's
  `git diff` in its log; `git checkout` would discard its work too.
- An implementer reports owner items as "blocked" instead of doing them. That is correct; read
  its notes.
- When a row moves between categories, fix the totals and the open list in the same change.
- `generate` writes the map and the agent context, both freshness-checked. Anchor after every
  evidence record, or the committed store is NOT-CHECKED mid-lifecycle.
