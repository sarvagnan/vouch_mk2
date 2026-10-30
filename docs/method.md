# The working method: how a change moves through mk2

This is the BINDING procedure for every forward change to Vouch mk2 (D-11 adopted 2026-08-17:
mk2's own development runs as units through mk2's own lifecycle; the named exceptions are in
the D-11 status change). It records what has actually
worked over units #1–#3, not an aspiration. Roles are fixed; the model behind each role is
the user's choice (D-27, 2026-10-30), with implementation and review on Codex quota (Codex is
generous, Claude is scarce).

| Role | Who | Owns |
|---|---|---|
| Acceptance owner / planner | Claude (Opus 5.5, the main session) | plan, beads, decision records, **the pack**, adjudication of deviations, the final pass, publish, commit |
| Implementer | GPT 6.1 Sol (T3 `delegate_task`: provider `codex`, model `gpt-6.1-sol`, `reasoningEffort: high`) | implementation against a frozen pack; raises deviations; owns and closes its implementation bead |
| Reviewer | GPT 6 Astra (T3 `delegate_task`: provider `codex`, model `gpt-6-astra`) | reproduces defects in the uncommitted diff, ranked, with minimal fixes; novel attacks on the riskiest surface |
| Decider | the user | contract-conflict deviations, D-numbered decisions, `git push`, `bd sync` |

The planner never implements a unit it owns the pack for; the implementer never edits a pack;
the reviewer is neither. That separation is the point — every deviation so far was caught by
someone other than the person who made the mistake.

## 0. Before anything: the plan and the beads

1. The planner states the design in one `D-n` entry in `DECISIONS.md` **before** the pack
   (D-12 for the tier unit). If the design needs a user decision, that is a `bd human`
   question, not a guess.
2. Beads, created by the planner, in this shape:
   - `<epic>` — the outcome (e.g. `bd4` "first usable release");
   - `<epic>.<n>` — one bead per unit, assigned `claude`, marked `in_progress` at pack time;
   - `<epic>.<n>.1` — "Implement `<unit>@<v>` against frozen pack/1", assigned `sol`,
     **owned and closed by Sol** (`bd close … --reason`); the planner never closes it for Sol;
   - review / final-pass / publish are the planner's and close with the unit bead.
   Beads is the task list; `docs/usage-log.md` is the cost ledger; nothing lives in chat only.

## 1. The pack (planner) — frozen BEFORE implementation

1. Write `tests/test_<unit>.py` (acceptance tests) and `packs/<unit>-<v>.spec.md` (R1…Rn,
   each bound to a test name; ground rules; what is *out* of scope).
2. **Verify every API name the tests use against the real tree before freezing** (an
   `inspect.signature` sweep). Units #1's DEV-01/03 were pack-author defects; unit #2 and #3
   caught the same class pre-freeze instead. Guard tests that pass today are marked `# guard:`.
3. Confirm the split honestly: `pytest -q tests/test_<unit>.py | grep -c ^FAILED` (RED),
   `pyrefly check tests/test_<unit>.py` (only "not yet implemented" names may error).
4. Freeze in the dogfood store: `specify → freeze <pack> → mark-ready → start`, pack version
   `= "<unit>-pack/<n>@" + sha256(b"vouch-mk2/pack\0"+spec+b"\0"+tests)[:16]`.
5. Record RED #1 through the harness (never by hand): `record-evidence … --kind red -- pytest
   tests/test_<unit>.py -q` (the harness refuses an incoherent kind); `advance-anchor`;
   `generate`; `check --ci` must be exit 0.
6. Commit: "`<unit>@<v>`: pack frozen, RED recorded, in progress" — the pack tests are
   intentionally red in that commit; CI is red until GREEN, and says so.

## 2. Implementation (Sol, T3 delegated task — zero Claude tokens)

The planner keeps the brief in `/tmp/codex_prompts/<unit>.md` (read-first list, HARD RULES,
done-file, bead id) and passes its text as the task of one T3 `delegate_task`: provider
`codex`, model `gpt-6.1-sol`, options `{"reasoningEffort": "high"}`, working in this checkout
(no separate worktree; the planner commits). The brief always says: may NOT edit the pack; a
contradiction → STOP on that requirement, write `<unit>.DEVIATION.md`, implement the rest; do
not touch `.vouch/`, README count, git; gates that must be green; write `<unit>.DONE.md`;
`bd close <bead>`.

Quote `bd create --description` in SINGLE quotes (backticks).

Waiting: the delegated task reports when it finishes; do not poll it. Never start a second
implementer in the same tree — the worst failure mode — and never launch a detached
`codex exec`: on 2026-10-30 a `nohup` launch outlived its cancelled planner and kept editing
the tree (D-27). A run that is no longer wanted is stopped with `task_cancel`.

## 3. Deviations (planner adjudicates; the user decides contract conflicts)

A deviation is opened in the dogfood store *by the owner on the implementer's behalf*
(`open-deviation … --deviation-type pack-defect|contract-conflict|plan-gap|capability-request`),
which blocks the unit (D-3). Then one of:
- **amend** — the pack was wrong: `adjudicate-amend --amendment-version <pack/n+1>`,
  `close-deviation`, `respecify`, edit spec+tests, re-freeze pack/n+1, **re-record RED** if
  the amendment demands new behavior (unit #2's RED #3 was real), else continue;
- **reject** — the pack stands: `adjudicate-reject`, `close-deviation`, `begin-reverify`;
  the resolution is outside the pack (unit #2: the codec's own docstring made the store
  owner responsible for re-encoding at a field addition).
Pack-defect deviations the planner adjudicates following the user's standing pattern; a
contract conflict (pack vs a `D-n` decision) goes to the user. Every deviation is a store
fact, so the history shows who raised what.

## 4. The planner verifies — never trusts the DONE file

- The task's report, `git status`, then the gates yourself: `pyrefly check`, `lint-imports`,
  `pytest -q`, pack file red-count = 0.
- **Count the implementer's tests**: `git diff tests/ | grep -c '^+\s*def test_'` — implementers
  tend to extend existing tests and add ~0 new functions; the review's exact scenarios may have
  no regression test until the planner adds one.
- Owner obligations that the implementer is forbidden from doing: re-encode the dogfood store when a
  snapshot field was added (pre-1.0 format is disposable; **no invented state** — defaults are
  the pre-field semantics, then any real value is an explicit recorded command), re-record
  the certificate (`formal --record`), `generate`, README count re-pin.
- Record GREEN through the harness; `advance-anchor`; `check --ci` exit 0.

## 5. Review — Astra (T3 delegated task, read-only)

Astra review (provider `codex`, model `gpt-6-astra`; zero Claude tokens): a brief listing the
defect classes in priority order — false refusals, fail-opens, honesty of NOT_CHECKED rows,
formal vacuity, projection/transport gaps, codec strictness — then novel attacks on the riskiest
surface (Phase 8: MCP never answering a live client was a transport finding). "Reproduce with a
real snippet before reporting; write `<unit>_review.OUT.md` ranked by severity, then what you
checked and found clean." Astra edits nothing but that file. Every finding so far reproduced.

Fixes go back to Sol in one fix pass (a new delegated task, brief = the review file + the
planner's chosen design for anything that changes shape); the planner re-verifies as in §4 and
reproduces the HIGH findings personally against the fixed tree. Astra re-checks the fixes in a
new delegated task, given its findings and the fix diff.

## 6. The planner's overall pass, publish, commit

1. Read the risky surface yourself (decide paths, checks, codec) — not the DONE file.
2. Add the regression tests the review's scenarios lack.
3. Gates + lock: `build-lock`, dogfood `check --ci` **from the lock**, `scripts/walkthrough.py --lock`.
4. `mark-done` (from in-progress) or straight `publish` (from re-verifying) — through the
   real DoD gates. A refused publish is information, never something to route around: the
   stop rule (self-hosting-tradeoff.md) is that if a gate must be bent to publish mk2's own
   unit, adoption stops and that fact is recorded.
5. `generate`; certificate; `check --ci` exit 0; commit with the lifecycle written as
   recorded facts (pack versions, deviations, RED/GREEN numbers, gates); `bd close` the unit
   bead(s); `bd sync`; append `docs/usage-log.md`; update memory. **The user pushes.**

## Cost rules (from docs/usage-log.md)

Delegated Sol and Astra tasks run on Codex quota: 0 Claude tokens beyond the brief and the
planner's verification. Never use the `codex` Claude subagent wrapper (Sonnet on the shared
session limit). `/code-review` at medium.

## What to watch (observed, not hypothetical)

- **Formal universe sizes are a coverage gauge — read them every pass.** A shrink means
  transitions became refused or a command was dropped; unit #6's fix pass silently removed
  `Respecify` from two universes (core-v1 109k → 66k) because a new refusal made the loop
  hit the state cap, and "M1–M11 hold" stayed true over the smaller relation. Never accept a
  smaller universe without the reason in the DONE file; never `git checkout` a file the implementer
  has edited to undo your own change (I lost its edit that way — recover from its log's `git diff`).

- The implementer reports "blocked" for owner items rather than doing them — correct; read its notes.
- Doc totals drift when a row moves (DEFECTS 28→29→30): fix the summary and the open list
  in the same change as the row.
- `generate` writes map **and** context now; both are freshness-checked. Anchor after every
  evidence record or the committed store is NOT-CHECKED mid-lifecycle.
