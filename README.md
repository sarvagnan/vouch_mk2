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

## Code (Phase 0)

- `src/vouch_mk2/ir/schema.py` — Policy IR schema: frozen dataclasses for state
  machines, claim taxonomy, applicability, tier requirements, obligation registry,
  version identity; pure structural validation.
- `src/vouch_mk2/ir/vouch_06.py` — the 0.6.2 lifecycle transcribed as IR data (the
  Phase 0 exit-criterion artifact).
- `tests/` — diffs the transcription against 0.6's own machine-readable tables
  (vendored, digest-pinned under `tests/data/`) and the machine.md §14 tables.

## Gates

Run what CI runs (`.github/workflows/ci.yml`):

```sh
uv sync
uv run pyrefly check   # D-1: strict types
uv run lint-imports    # D-1: Policy IR purity contract
uv run pytest          # transcription diff + schema validation
```
