# Vouch mk2

Rebuild of Vouch (`../vouch`) as a typed policy engine with one authoritative
snapshot, content-addressed artifacts, pure projections, and thin adapters.
Vouch requires Python 3.14 or newer.

Start here: [`docs/quickstart.md`](docs/quickstart.md) is the executable lifecycle walkthrough;
[`docs/commands.md`](docs/commands.md) is the generated command reference.

The one rule (from [`docs/claim-boundary.md`](docs/claim-boundary.md), emitted verbatim into
every agent context): Vouch checks only what artifacts can prove; where it cannot, it prints
`NOT_CHECKED` rather than offering a button that fakes it.

Evidence base: dual independent reviews of the Vouch 0.6.2 history — 70 recorded defects,
31 behavioral hardening commits (2026-08-03 → 2026-08-10).

## Documents

- [`architecture.html`](architecture.html) — entity/authority diagram, mutation flow,
  verification flow, threat levels (self-contained; open in a browser, light/dark).
- [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) — phases 0–9, cross-cutting
  disciplines, foundational decisions, reuse from 0.6, risks.
- [`DECISIONS.md`](DECISIONS.md) — decision log (D-1 typed Python, D-2 zipapp
  vendoring, …). Append-only.
- [`docs/threat-model.md`](docs/threat-model.md) — the three explicit threat levels;
  the D070 TOCTOU residual scoped to level 2.
- [`docs/claim-boundary.md`](docs/claim-boundary.md) — what mk2 proves mechanically
  vs. what is permanently `NOT_CHECKED`.

## Code (Phases 0–9)

- `src/vouch_mk2/ir/` — Policy IR schema (frozen dataclasses, structural
  validation), the 0.6.2 lifecycle transcription, and the mk2 tables `decide()`
  executes.
- `src/vouch_mk2/core/` — the pure typed domain core: closed command/event
  unions and `decide(Snapshot, Command) → Accepted | Refused` with gates inside
  transitions; `apply()` folds events into the next snapshot.
- `src/vouch_mk2/store/` — the only I/O: capability-rooted CAS store, the sole
  digest registry (domain-separated), and one strict canonical codec per
  artifact type.
  Format 1 and later declare `state_format`; absence denotes legacy format 0.
  Strict reads accept exactly the current format. Older stores require
  `vouch-mk2 upgrade`; newer stores require a newer Vouch executable and are
  never read speculatively.
- `src/vouch_mk2/checks/` — verdict lattice, standing mechanical checks,
  organizational NOT_CHECKED rows, registry completeness as a FAILable result.
- `src/vouch_mk2/verify.py` / `bundle.py` — I/O-side artifact checks (evidence
  chains, anchors, bundles, formal certificates) and exclusion-glob bundle
  inventories.
- `src/vouch_mk2/formal/` — the model checker: the relation is emitted by
  executing `decide()` over the empty `core-v1` and seeded-history `history-v1`
  universes; eleven hand-authored invariants with private mutation witnesses;
  recomputed content-addressed certificates bind both relations.
- `src/vouch_mk2/projections/` — canonical system map, binding next actions,
  status facts, and deterministic agent-context generation.
- `src/vouch_mk2/cli.py` — thin transports: the command dataclasses are reflected
  into lifecycle verbs such as `vouch-mk2 specify`, `vouch-mk2 freeze`, and
  `vouch-mk2 record-evidence`; verification and projection verbs remain
  `vouch-mk2 check`, `vouch-mk2 formal [--record]`, `vouch-mk2 generate`,
  `vouch-mk2 next`, `vouch-mk2 status`, and `vouch-mk2 emit-context`.
- `src/vouch_mk2/migrate.py` — strict Vouch 0.6 decoder, D-8 field mapper,
  standing-check dry run, complete loss report, and approve-only fresh-store write.

## Transports (Phase 8)

- `vouch-mk2 build-lock` creates the reproducible, offline `vouch.lock` zipapp;
  `vouch-mk2 self-check` prints the digest of the lock being executed.
- `vouch-mk2 init` writes only the fresh store, digest-bound lock configuration,
  vendored lock, and CI workflow defined by D-7.
- `vouch-mk2 mcp` serves the permanent read/verify-only MCP surface over
  line-delimited JSON-RPC stdio.
- `vouch-mk2 hook` powers the user-wired, warn-only fragments in `hooks/`; CI,
  not the hook, remains the hard enforcement layer.
- Lifecycle mutations are CLI-only and require `--root`. Evidence is recorded by
  `vouch-mk2 record-evidence --root ROOT --unit NAME@VERSION --kind red|green|refactor
  -- <argv...>`. Its exact claim is that the recorded outcome is the exit status
  of a process this verb spawned, at the time it spawned it. The child inherits
  cwd and environment; mk2 does not claim the runner is honest. Store writers
  wait on an exclusive lock, read-only handles never lock, and generated
  projections lock only while replacing their output files.

## Gates

Run what CI runs (`.github/workflows/ci.yml`):

```sh
uv sync
uv run pyrefly check   # D-1: strict types
uv run lint-imports    # D-1: Policy IR purity contract
uv run pytest          # 795 tests: matrices, property BFS, fault rig, formal, transports, migration
uv run vouch-mk2 formal  # invariants over core-v1 + history-v1; witness discrimination
```
