# Agent instructions

Vouch mk2 is a typed policy engine that governs units of work, including its own development.
Start from `README.md`.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:1105d646 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

## Working method

Every forward change to mk2 is a unit run through mk2's own lifecycle (D-11, D-27). Claude
(Opus 5.5, the main session) plans, writes the frozen pack and publishes. GPT 6.1 Sol implements
through a T3 delegated task and never edits the pack. GPT 6 Astra reviews through a T3 delegated
task. The procedure, roles, cost rules and commands are in `docs/method.md`.

## Gates

Before handing off, run the commands in `README.md` § Gates. They are what CI runs.

## Gotchas

- Prose in `README.md` and `docs/*.md` is test-bound. Tests pin exact phrases, some across a
  line break, and `tests/test_operator_docs.py` runs every `sh` block in `docs/quickstart.md`.
  Edit around the pinned phrases and run the full suite.
- A frozen pack (`packs/<unit>-<v>.spec.md` plus its test file) is byte-bound in `.vouch/`.
  Change it only through a deviation and a re-freeze (`docs/method.md` §3).
- `cp`, `mv` and `rm` may be aliased to prompt. Use `cp -f`, `mv -f`, `rm -f` and `rm -rf` so a
  command never waits for input, and `-o BatchMode=yes` with `ssh` and `scp`.
