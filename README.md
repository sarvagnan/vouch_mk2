# Vouch mk2

- **What it is.** Vouch is a command-line tool that keeps a checkable record of a piece of work:
  the tests frozen as its definition of done, and the exit statuses Vouch recorded when it ran them.
- **Why it exists.** Passing tests alone do not show which tests the work was held to. Vouch lets
  someone who was not there inspect the frozen tests and check whether the bound files still match.
  It cannot prove that the tests were frozen before the code was written.
- **How it was built.** mk2 rebuilds Vouch 0.6 from two reviews of its defects. Since August 2026,
  changes to mk2's behavior go through mk2 itself.

Start with [`docs/quickstart.md`](docs/quickstart.md), a walkthrough that the test suite runs.
[`docs/commands.md`](docs/commands.md) is the command reference, generated from `--help`.

## What it is for

Vouch is for work delivered by agents. When an agent claims a unit is done, Vouch lets you check
that claim against tests frozen for that unit. Vouch runs the tests and records the results itself
rather than letting the agent type them in.

Vouch is not a planning tool. Decide what to build first, however you get there, then split the
work into units. Agents call Vouch's lifecycle commands at each step, and Vouch either records the
step or refuses it.

The lifecycle supports separated roles: one agent writes and freezes the tests, another implements
against them, and a third reviews before publication. Vouch records who claimed each step but does
not verify their identity, so keeping the roles apart is up to your process. Whether the tests ask
for the right thing remains a human judgment.

## How it works

A **unit** is a named, versioned piece of work, such as `greeter@v1`. Its **acceptance pack** is
the set of tests that define done. Each unit moves through a fixed lifecycle, and Vouch refuses
any step its policy does not allow:

1. **Specify** the unit and write its acceptance pack.
2. **Freeze** the pack. When pack files are bound, Vouch stores each file's hash, and
   `vouch-mk2 check --project .` fails from then on if any bound file changes. New stores also
   require an effect declaration for a bound pack; an empty list declares a transform with no
   effects.
3. **Record red.** Vouch runs the test command itself and records its failing exit status. You
   cannot type a result in.
4. **Implement and record green.** Write the implementation, then have Vouch run the test command
   and record its passing exit status.
5. **Publish.** Publication succeeds only if all definition-of-done gates pass. At the `standard`
   and `high` tiers, those gates require red then green for the current pack.

If a frozen test turns out to be wrong, the implementer opens a **deviation** instead of editing
the test. The acceptance owner then decides the deviation. Vouch records both actions, but it does
not know who holds which role. CI runs the checks and serves as the hard enforcement layer.

Vouch needs Python 3.14 or newer. It ships as a single file, `vouch.lock`: a reproducible, offline
zipapp built with `vouch-mk2 build-lock`.

## The one rule

This sentence comes from [`docs/claim-boundary.md`](docs/claim-boundary.md) and appears in every
generated agent context:

> Vouch checks only what artifacts can prove; where it cannot, it prints `NOT_CHECKED` rather than offering a button that fakes it.

For example, `record-evidence` makes one exact claim: the recorded outcome is the exit status of a
process this verb spawned, at the time it spawned it. It does not claim that the command was a
test runner, or that the runner was honest.

## Documents

| Document | Covers |
|---|---|
| [`docs/quickstart.md`](docs/quickstart.md) | Executable walkthrough from `init` to release |
| [`docs/commands.md`](docs/commands.md) | Every command and its options |
| [`docs/claim-boundary.md`](docs/claim-boundary.md) | What a report proves, and what it never proves |
| [`docs/threat-model.md`](docs/threat-model.md) | The three threat levels and the accepted residual |
| [`docs/method.md`](docs/method.md) | How changes to mk2 itself run through Vouch |
| [`DECISIONS.md`](DECISIONS.md) | Append-only decision log (D-1 typed Python, D-2 zipapp vendoring, …) |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Original build plan: phases 0–9, disciplines, foundational decisions, reuse from 0.6, risks |
| [`architecture.html`](architecture.html) | Original design proposal (2026-08-10), drawn before the event log (D-16): entity/authority diagram, mutation flow, verification flow, and threat levels (self-contained; open in a browser, light/dark) |

## Where mk2 came from

mk2 rebuilds Vouch 0.6 (`../vouch`). Its design comes from two independent reviews of the Vouch
0.6.2 history (2026-08-03 to 2026-08-10), which recorded 70 defects and 31 behavioral hardening
commits.

Every change to mk2's behavior is a unit run through mk2's lifecycle (D-11). The named exceptions
are decision records, prose edits that change no claim, and housekeeping. Claude plans and writes
the frozen pack, GPT 6.2 Sol implements against it, and GPT 6.2 Astra reviews. See
[`docs/method.md`](docs/method.md) for the procedure.

The rest of this file is for people changing mk2.

## Layout

Internally, mk2 is a typed policy engine built around one authoritative snapshot,
content-addressed artifacts, pure projections, and thin adapters. All code lives under
`src/vouch_mk2/`.

- `ir/` — Policy IR (intermediate representation): the schema (frozen dataclasses with structural
  validation), a transcription of the 0.6.2 lifecycle, and the mk2 tables that `decide()`
  executes.
- `core/` — Pure, typed domain core with closed command and event unions.
  `decide(Snapshot, Command) → Accepted | Refused` enforces every gate, and `apply()` folds events
  into the next snapshot.
- `store/` — The only I/O layer: a capability-rooted, content-addressed store, the sole
  domain-separated digest registry, and one strict canonical codec per artifact type.
  Format 1 and later declare `state_format`; absence denotes legacy format 0.
  Older stores need `vouch-mk2 upgrade`; newer stores need a newer Vouch.
- `checks/`, `verify.py`, `bundle.py` — Verification layer: the verdict lattice, standing checks,
  organizational `NOT_CHECKED` rows, artifact checks (for evidence chains, anchors, bundles, and
  certificates), and exclusion-glob bundle inventories.
- `formal/` — Bounded model checker. It exhaustively explores what `decide()` allows over the
  empty `core-v1` and seeded-history `history-v1` universes, checking eleven hand-written
  invariants. Each invariant pairs with a private mutation witness it must catch. Recomputed,
  content-addressed certificates bind both relations.
- `projections/` — Pure read models: the canonical system map, binding next actions, status facts,
  and deterministic agent-context generation.
- `cli.py`, `mcp.py`, `hook.py`, `init.py`, `lock.py` — Transports:
  - Lifecycle verbs reflect the command types and require `--root`. Verification and projection
    use `vouch-mk2 check`, `vouch-mk2 formal [--record]`, `vouch-mk2 generate`, `vouch-mk2 next`,
    `vouch-mk2 status`, and `vouch-mk2 emit-context`.
  - `vouch-mk2 build-lock` builds the lock, and `vouch-mk2 self-check` prints the digest of the
    lock being executed.
  - `init` writes only the store, the lock's config, the lock, and the CI workflow (D-7).
  - `vouch-mk2 mcp` serves the permanent read/verify-only MCP surface over line-delimited
    JSON-RPC on stdio.
  - `vouch-mk2 hook` powers the user-wired, warn-only fragments in `hooks/`. CI, not the hook, is
    the hard enforcement layer.
- `migrate.py` — Imports a Vouch 0.6 ledger into a fresh store. It runs a strict decoder, applies
  the D-8 field mapping, dry-runs the standing checks, produces a complete loss report, and writes
  only when approved.

## Gates

Run what CI runs (`.github/workflows/ci.yml`):

```sh
uv sync
uv run pyrefly check     # strict types (D-1)
uv run lint-imports      # policy IR purity contract (D-1)
uv run pytest            # 1394 tests: matrices, property BFS, fault rig, formal, transports, migration
uv run vouch-mk2 formal  # invariants over core-v1 and history-v1
```
