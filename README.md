# Vouch mk2

Vouch mk2 is a typed policy engine with one authoritative snapshot, content-addressed artifacts,
pure projections and thin adapters. It moves units of work through a fixed lifecycle, from frozen
acceptance tests to publication, refuses any step its policy does not allow, records test
evidence by running the command itself, and reports what it checked separately from what it
could not check.

Start with [`docs/quickstart.md`](docs/quickstart.md), a walkthrough that the test suite runs.
[`docs/commands.md`](docs/commands.md) is the command reference, generated from `--help`.

Vouch needs Python 3.14 or newer and ships as a single file, `vouch.lock`: a reproducible,
offline zipapp built with `vouch-mk2 build-lock`.

mk2 rebuilds Vouch 0.6 (`../vouch`). Its design comes from two independent reviews of the Vouch
0.6.2 history (2026-08-03 to 2026-08-10): 70 recorded defects and 31 behavioral hardening
commits.

## The one rule

This sentence comes from [`docs/claim-boundary.md`](docs/claim-boundary.md) and is included in
every generated agent context:

> Vouch checks only what artifacts can prove; where it cannot, it prints `NOT_CHECKED` rather than offering a button that fakes it.

For example, the claim `record-evidence` makes is exactly this: the recorded outcome is the
exit status of a process this verb spawned, at the time it spawned it. It does not claim that
the command was a test runner, or that the runner was honest.

## Documents

| Document | Covers |
|---|---|
| [`docs/quickstart.md`](docs/quickstart.md) | init to release, as one executable walkthrough |
| [`docs/commands.md`](docs/commands.md) | every command and its options |
| [`docs/claim-boundary.md`](docs/claim-boundary.md) | what a report proves, and what it never proves |
| [`docs/threat-model.md`](docs/threat-model.md) | the three threat levels and the accepted residual |
| [`docs/method.md`](docs/method.md) | how changes to mk2 itself run through Vouch |
| [`DECISIONS.md`](DECISIONS.md) | the append-only decision log (D-1 typed Python, D-2 zipapp vendoring, …) |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | the original build plan: phases 0–9, cross-cutting disciplines, foundational decisions, reuse from 0.6, risks |
| [`architecture.html`](architecture.html) | the original design proposal (2026-08-10), drawn before the event log (D-16): entity/authority diagram, mutation flow, verification flow, threat levels (self-contained; open in a browser, light/dark) |

## Layout

Everything is under `src/vouch_mk2/`.

- `ir/`: the policy IR. The schema (frozen dataclasses with structural validation), a
  transcription of the 0.6.2 lifecycle, and the mk2 tables that `decide()` executes.
- `core/`: the pure, typed domain core, with closed command and event unions.
  `decide(Snapshot, Command) → Accepted | Refused` holds every gate, and `apply()` folds events
  into the next snapshot.
- `store/`: the only I/O. A capability-rooted, content-addressed store, the sole
  (domain-separated) digest registry, and one strict canonical codec per artifact type. Format 1 and later declare `state_format`; absence denotes legacy format 0.
  An older store needs `vouch-mk2 upgrade`; a newer one needs a newer Vouch.
- `checks/`, `verify.py`, `bundle.py`: the verdict lattice, standing checks, organizational
  `NOT_CHECKED` rows, artifact checks for evidence chains, anchors, bundles and certificates, and
  exclusion-glob bundle inventories.
- `formal/`: the model checker. It exhaustively explores what `decide()` allows over the empty
  `core-v1` and seeded-history `history-v1` universes, checks eleven hand-written invariants,
  each paired with a private mutation witness the invariant must catch. Recomputed,
  content-addressed certificates bind both relations.
- `projections/`: the canonical system map, binding next actions, status facts, and
  deterministic agent-context generation.
- `cli.py`, `mcp.py`, `hook.py`, `init.py`, `lock.py`: transports.
  - Lifecycle verbs are reflected from the command types and require `--root`. Verification and
    projection use `vouch-mk2 check`, `vouch-mk2 formal [--record]`, `vouch-mk2 generate`,
    `vouch-mk2 next`, `vouch-mk2 status` and `vouch-mk2 emit-context`.
  - `vouch-mk2 build-lock` builds the lock, and `vouch-mk2 self-check` prints the digest of
    the lock being executed.
  - `init` writes only the store, the lock's config, the lock and the CI workflow (D-7).
  - `vouch-mk2 mcp` serves the permanent read/verify-only MCP surface over line-delimited
    JSON-RPC on stdio.
  - `vouch-mk2 hook` powers the user-wired, warn-only fragments in `hooks/`. CI, not the hook,
    is the hard enforcement layer.
- `migrate.py`: imports a Vouch 0.6 ledger into a fresh store, through a strict decoder, the D-8
  field mapping, a dry run of the standing checks and a complete loss report, and writes only
  when approved.

## Gates

Run what CI runs (`.github/workflows/ci.yml`):

```sh
uv sync
uv run pyrefly check     # strict types (D-1)
uv run lint-imports      # policy IR purity contract (D-1)
uv run pytest            # 1103 tests: matrices, property BFS, fault rig, formal, transports, migration
uv run vouch-mk2 formal  # invariants over core-v1 and history-v1
```
