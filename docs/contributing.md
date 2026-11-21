# Contributing to Vouch mk2

> For people changing mk2: this page describes how mk2 itself is built and run. You do not need it to use Vouch.

Follow the [working method](method.md) for changes to mk2. The [decision log](../DECISIONS.md)
records design decisions, and [README § Gates](../README.md#gates) lists the commands CI runs.

## Layout

The Python package lives under `src/vouch_mk2/`.

- `ir/` — Policy IR (intermediate representation): the schema (frozen dataclasses with structural
  validation), a transcription of the 0.6.2 lifecycle, and the mk2 tables that `decide()`
  executes.
- `core/` — Pure, typed domain core with closed command and event unions.
  `decide(Snapshot, Command) → Accepted | Refused` enforces every gate, and `apply()` folds events
  into the next snapshot.
- `store/` — Store I/O: a capability-rooted, content-addressed store, the sole
  domain-separated digest registry, and one strict canonical codec per artifact type.
  Format 1 and later declare `state_format`; absence denotes legacy format 0.
  Older stores need `vouch-mk2 upgrade`; newer stores need a newer Vouch.
- `checks/`, `verify.py`, `bundle.py` — Verification layer: the verdict lattice, standing checks,
  organizational `NOT_CHECKED` rows, artifact checks (for evidence chains, anchors, bundles, and
  certificates), and exclusion-glob bundle inventories.
- `formal/` — Bounded model checker. It exhaustively explores what `decide()` allows over the
  `core-v1`, `history-v1`, `tier-v1`, `effects-v1`, and `refreeze-v1` universes, checking twelve
  hand-written invariants across them. Each invariant pairs with a private mutation witness it
  must catch. Recomputed, content-addressed certificates bind the relations.
- `projections/` — Pure read models: the canonical system map, binding next actions, status facts,
  and deterministic agent-context generation.
- `cli.py`, `mcp.py`, `hook.py`, `init.py`, `lock.py` — Transports:
  - Lifecycle verbs reflect the command types and require `--root`. Verification and projection
    use `vouch-mk2 check`, `vouch-mk2 formal [--record]`, `vouch-mk2 generate`, `vouch-mk2 next`,
    `vouch-mk2 status`, and `vouch-mk2 emit-context`.
  - `vouch-mk2 build-lock` builds the lock, and `vouch-mk2 self-check` prints the digest of the
    lock being executed.
  - A fresh `init` sets up the store, the lock's config, the lock, and the CI workflow (D-7).
  - `vouch-mk2 mcp` serves the permanent read/verify-only MCP surface over line-delimited
    JSON-RPC on stdio.
  - `vouch-mk2 hook` powers the user-wired, warn-only fragments in `hooks/`. Required CI checks
    rely on the repository's forge rules.
- `migrate.py` — Imports a Vouch 0.6 ledger into a fresh store. It runs a strict decoder, applies
  the D-8 field mapping, checks the candidate, and produces a loss report. Writing requires
  `--approve` and a target.
