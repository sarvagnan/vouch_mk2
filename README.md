# Vouch mk2

Rebuild of Vouch (`../vouch`) as a typed policy engine with one authoritative
snapshot, content-addressed artifacts, pure projections, and thin adapters.

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

## Code (Phases 0–7)

- `src/vouch_mk2/ir/` — Policy IR schema (frozen dataclasses, structural
  validation), the 0.6.2 lifecycle transcription, and the mk2 tables `decide()`
  executes.
- `src/vouch_mk2/core/` — the pure typed domain core: closed command/event
  unions and `decide(Snapshot, Command) → Accepted | Refused` with gates inside
  transitions; `apply()` folds events into the next snapshot.
- `src/vouch_mk2/store/` — the only I/O: capability-rooted CAS store, the sole
  digest registry (domain-separated), and one strict canonical codec per
  artifact type.
- `src/vouch_mk2/checks/` — verdict lattice, standing mechanical checks,
  organizational NOT_CHECKED rows, registry completeness as a FAILable result.
- `src/vouch_mk2/verify.py` / `bundle.py` — I/O-side artifact checks (evidence
  chains, anchors, bundles, formal certificates) and exclusion-glob bundle
  inventories.
- `src/vouch_mk2/formal/` — the model checker: the relation is emitted by
  executing `decide()` over the core-v1 universe; nine hand-authored invariants
  with private mutation witnesses; recomputed content-addressed certificates.
- `src/vouch_mk2/projections/` — canonical system map, binding next actions,
  status facts, and deterministic agent-context generation.
- `src/vouch_mk2/cli.py` — thin transports: `vouch-mk2 check`, `vouch-mk2 formal
  [--record]`, `vouch-mk2 generate`, `vouch-mk2 next`, `vouch-mk2 status`, and
  `vouch-mk2 emit-context`.

## Gates

Run what CI runs (`.github/workflows/ci.yml`):

```sh
uv sync
uv run pyrefly check   # D-1: strict types
uv run lint-imports    # D-1: Policy IR purity contract
uv run pytest          # 474 tests: matrices, property BFS, fault rig, formal
uv run vouch-mk2 formal  # invariants + witness discrimination over core-v1
```
