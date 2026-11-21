# adoption-docs@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_adoption_docs.py` or `packs/adoption-docs-1.effects.json`.
**Decision:** D-47 (settles `vouch_mk2-b6g.15` and `vouch_mk2-1l0`). **Seams (D-44):** the text
of `README.md` and `docs/**/*.md`; the CLI parser and the CLI's JSON output on a scratch store;
the policy IR (`MK2`); the effects-file decoder; `scripts/gen_lifecycle_doc.py`;
`.github/workflows/ci.yml`.
**Effects:** one observation (the generator reads the target file to compare it) and one action
(it writes the target), declared in the effects file.
**Source material:** Gemini Neon's reviewed proposal v5 (the owner hands Sol a copy) and its
claims ledger; the reviews `docs-proposal_review.OUT.md` and `docs-proposal_recheck.OUT.md`; the
pre-freeze read `adoption-docs_prefreeze.OUT.md`.

## Promise

`README.md` opens as a plain introduction to Vouch for people who did not build it, keeps its
caveats, links the new router, and ends with the contributor part: `## Where mk2 came from` then
`## Gates`, whose test count stays the README's only "N tests"; `## Layout` moves to
`docs/contributing.md`. `docs/` gains `index.md` (a router that links every page), `concepts.md`,
`lifecycle.md` (generated from the policy IR by a script with a `--check` mode that CI runs),
`errors.md` (a starter refusal-recovery catalog whose recoveries never tell an agent to fabricate
evidence), `schemas.md` (JSON examples whose keys match the CLI), `agents/SKILL.md` and three role
cards. Contributor pages carry one banner line. `claim-boundary.md` stops saying every "Never
mechanically checked" item is a report row. Every command, option, decision, check id and anchor
mode shown in the README or `docs/` (except the generated `commands.md` and the history in
`usage-log.md`) is real, and none of the overclaim words appears there. No existing doc moves and
no code outside the generator changes.

## Requirements

Each test fails before the change for the stated reason, except the two guards.

- **R1 — The README opens as a plain introduction.** `test_the_readme_opens_as_a_plain_introduction`.
  Line 1 is `# Vouch: keeping a checkable record of agent-delivered work`; its first 12 lines hold
  inline links `](docs/index.md)`, `](docs/quickstart.md)` and `](docs/commands.md)`; it contains
  (whitespace normalised) `It cannot prove that the tests were frozen before the code was
  written.`, ``At the default `lightweight` tier, `publish` has no evidence gate; at `standard` and
  `high` it needs a GREEN for the current pack, after a RED for the same tests or an owner rebind,
  and after any re-freeze that changed the pack.`` and `Vouch records who claimed each step but
  does not verify their identity`. *Before:* line 1 is `# Vouch mk2`.
- **R2 — The contributor part is at the end.** `test_the_readme_keeps_the_contributor_part_at_its_end`.
  No `## Layout`; the last two H2s are `## Where mk2 came from` and `## Gates`; it links
  `](docs/contributing.md)`; it keeps `checking twelve hand-written invariants` (pinned by
  `formal-fresh-green@1`); exactly one `N tests` match, inside `## Gates`. *Before:* `## Layout`
  is present.
- **R3 — Layout moves.** `test_contributing_holds_the_layout`. `docs/contributing.md` has
  `## Layout`, ``The Python package lives under `src/vouch_mk2/`.`` and names `ir/`, `core/`,
  `store/`, `formal/`, `projections/`. *Before:* no such file.
- **R4 — Banners.** `test_contributor_pages_carry_the_banner`. Within the first five lines of
  `method.md`, `defect-closure.md`, `self-hosting-tradeoff.md`, `performance.md`, `usage-log.md`
  and `contributing.md`: `> For people changing mk2: this page describes how mk2 itself is built
  and run. You do not need it to use Vouch.` (one line). *Before:* absent from `method.md`.
- **R5 — Router.** `test_the_router_links_every_page_and_every_link_resolves`. The D-47 pages
  exist (`index.md`, `concepts.md`, `lifecycle.md`, `errors.md`, `schemas.md`, `contributing.md`,
  `agents/SKILL.md`, `agents/roles/{owner,implementer,reviewer}.md`). `docs/index.md` uses inline
  links only; every relative link resolves to a file, and a `#fragment` to a heading's GitHub slug
  in that file; every other `docs/**/*.md` page is linked. *Before:* no router.
- **R6 — Lifecycle reference.** `test_the_lifecycle_reference_matches_the_policy_ir`. For each IR
  machine, the section headed ``## `<machine>` `` holds exactly one table headed `From | Command |
  To | Gates` whose rows, each four code-span cells (gates as code spans in IR order), equal the
  machine's command transitions as a multiset; and, if the machine has adjudication entries,
  exactly one table headed `Trigger | To | From | Except` whose rows equal them (otherwise none).
  The `unit` section contains the hand-checked row ``| `blocked` | `respecify` | `specified` |
  `gate.unblock.no-blocking-deviations` |``. *Before:* no such file.
- **R7 — Generator.** `test_the_lifecycle_generator_writes_checks_and_ci_runs_it`.
  `scripts/gen_lifecycle_doc.py --output PATH` creates PATH (and its parent) with bytes equal to
  the committed `docs/lifecycle.md`; `--check` exits 0 on the committed doc and on that fresh
  copy; on a hand-edited copy `--check --output PATH` exits 1 and leaves the file unchanged; an
  executable `run` line of the `gates` job in `.github/workflows/ci.yml` is exactly `uv run python
  scripts/gen_lifecycle_doc.py --check`. *Before:* no script.
- **R8 — Errors guide.** `test_the_errors_guide_names_real_codes_and_honest_recoveries`. Every row
  of every `docs/errors.md` table whose first header cell is `Code` starts with one code span
  holding a dotted lowercase code that is not a `check.`/`org.` id and that appears as a token in a
  string literal under `src/vouch_mk2`; together they include `gate.dod.current-pack-green`,
  `evidence.kind-outcome-mismatch`, `evidence.pack-moved`, `evidence.redaction-invalid`,
  `merge.conflict`, `merge.ancestry-unproven`, `transition.illegal`, `identifier.leading-dash`,
  `pack.effects-required`, `ci-policy.exception-missing` and `rebind.stale-pack`. It contains:
  `Never record a RED after the implementation exists to satisfy this gate; if the current pack
  has no RED, ask the acceptance owner.`, ``Do not re-run a passing RED as GREEN: unless the owner
  has rebound the pack, a first GREEN with no RED before it makes `check.evidence-chains` fail, and
  the chain is append-only.`` and `` `--redact-arg` positions run from 1 to the last argument,
  inclusive.``. *Before:* no such file.
- **R9 — JSON reference.** `test_the_json_reference_matches_real_output`. On a scratch store with
  one specified unit: in `docs/schemas.md`, the first JSON block of each of the sections
  ``## `check --json` ``, ``## `next --json` `` and ``## `status --json` `` has exactly the live
  output's top-level keys, and every item it shows in `mechanical`, `organizational` (check) and
  `actions` (next) has the live first item's keys (lists may be shortened, keys never changed);
  the section ``## Lifecycle `--json` `` shows an accepted then a refused result with the keys of a
  live accepted and refused `specify --json`; the section `## Effects declaration` shows a file the
  effects decoder accepts; every JSON block in the page parses. *Before:* no such file.
- **R10 — Commands and options are real (guard).** `test_documented_commands_and_options_exist`.
  In the README and `docs/**` (except `commands.md` and `usage-log.md`), every command shown in a
  code span or code block (continuations joined, split on `&&`, `;`, `|`) that starts
  `python3 vouch.lock <verb>` or `vouch-mk2 <verb>` with a literal lowercase verb names a real
  subcommand, and every `--option` before ` -- ` is that subcommand's; at least 30 are seen.
  *Passes before:* today's docs are correct; it binds the new pages.
- **R11 — Ids and anchor modes are real (guard).** `test_documented_ids_and_anchor_modes_are_real`.
  Over the same files: every `D-n` has a DECISIONS heading, every `check.*` id is an IR obligation,
  every `--mode` value (whole token) is `none`, `ci`, `{none,ci}`, `none|ci` or `MODE`, and no
  anchor mode is called `` `local` ``. *Passes before:* as R10.
- **R12 — No overclaim words.** `test_no_page_uses_the_overclaim_words`. Over the same files,
  case-insensitively: `tamper-proof`, `tamperproof`, `tamper-evident`, `catches it immediately`,
  `cannot be faked`, `can't be faked`, `impossible to fake`. *Before:* `self-hosting-tradeoff.md`
  says anchoring makes the record `tamper-evident`.
- **R13 — Agent skill and role cards.** `test_the_agent_skill_and_role_cards_state_the_limits`.
  `docs/agents/SKILL.md` has front matter with `name: vouch` and a non-empty `description:`, at
  most 150 lines, an inline link `](../errors.md)`, and contains `` `check --ci` exits 1 on a
  branch while an evidence anchor waits for CI on the default branch; do not lower the anchor mode
  or the CI policy, and do not set `GITHUB_RUN_ID`, to make it pass.`` and `Vouch records who
  claimed each step but does not verify identity or enforce roles.`; the role cards name their
  duties: owner `` `freeze` `` and `` `adjudicate-amend` ``, implementer `` `open-deviation` `` and
  `` `record-evidence` ``, reviewer `` `check` `` and `` `next` ``. *Before:* no such files.
- **R14 — claim-boundary (1l0).** `test_the_claim_boundary_no_longer_says_every_exclusion_is_a_row`.
  ``Reports list the items below as organizational `NOT_CHECKED` rows.`` is gone and ``Some of the
  items below appear in reports as organizational `NOT_CHECKED` rows; the others are documented
  exclusions with no report row.`` is present. *Before:* the old sentence.

## Ground rules

- **Claims.** Every statement about Vouch's behaviour in a new or rewritten page must be true of
  the code today and traceable to a source (README, `claim-boundary.md`, `quickstart.md`,
  `commands.md`, `ir/mk2.py`, `cli.py`, `core/decide.py`, `core/invariants.py`, `verify.py`,
  `mcp.py`, the workflow templates). Where the drafts and the code disagree, the code wins. Sol
  keeps a claims ledger in the DONE file: one row per behavioural statement with its source
  `file:line`. The reviewer checks it.
- **Caveats.** Keep `NOT_CHECKED` caveats where a page touches what Vouch cannot prove. Adopter
  pages use `python3 vouch.lock …`; contributor pages may use `uv run vouch-mk2 …`. State no new
  counts (tests, invariants, universes, defects) outside README § Gates; inherited pinned phrases
  (for example `checking twelve hand-written invariants` and `M1–M12`) stay where tests need them.
- **Pinned phrases.** Every phrase other tests pin in `README.md` and `docs/*.md` stays (the full
  suite is the check); `docs/quickstart.md`'s `sh` blocks still run. No existing file moves. If a
  frozen test of a published unit blocks a required change, stop on it and report; do not edit it.
- **Generator.** `scripts/gen_lifecycle_doc.py` follows `scripts/gen_commands_doc.py`: reads
  `MK2`, writes deterministic Markdown, `--check` compares without writing (exit 1 on difference),
  `--output PATH` overrides the target. It is added to the `gates` job and to README § Gates in
  the same order (D-43's tests compare them).
- **Out of scope.** The GitHub integration guide (`vouch_mk2-b6g.19`), a refusal-code registry,
  collapsing `org.tier-controls` rows (`vouch_mk2-k76`), any change to Vouch's behaviour.

## Limits (convergence bound, set before review)

Prose checks are bounded by what the tests pin. A review blocks only on: a behavioural statement
that is false or unsupported by the code; a recovery that would lead an agent to fabricate,
forge or destroy evidence or to weaken policy; a pinned phrase removed; or a command, option, code
or key that does not exist. Wording, tone, structure, MCP examples beyond key accuracy, and crafted
text that slips past a regex are review notes, not blockers.

## Verification outside pytest

Before the unit commit, Astra checks every row of Sol's claims ledger, and the owner renders the
README and router locally and runs the README Gates block. After the push, the owner reads the
rendered README and router on the pull request.
