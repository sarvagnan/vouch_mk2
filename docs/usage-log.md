# Subagent usage log

One line per delegated run. Purpose: detect a run whose Claude-side spend is out of
proportion to its job. The `codex` wrapper is a Sonnet agent on the SHARED session limit;
its healthy cost is a thin launch-wait-relay. Codex's own (gpt-5.6-sol) tokens are free
of Claude quota and are NOT what this log measures.

Baselines from Phases 2-6 (wrapper asked to verify + read diffs — the expensive pattern):
41k / 65k / 97k / 140k / 51k subagent tokens, 19-71 tool uses. Opus adversarial reviews:
65k-164k tokens, 21-60 tool uses (expected — they execute probes).

Targets from Phase 7 on: wrapper (spec-by-file, no verification) <= 15k tokens and <= 8 tool
uses; Codex-side residual reviews (via wrapper) <= 20k tokens; Opus reviews only with the
user's prior OK. Anything above 2x target = "out of whack": stop, report, do not relaunch.

| date | phase | agent | model | subagent tokens | tool uses | duration | verdict vs target |
|---|---|---|---|---|---|---|---|
| 2026-08-14 | 7 | codex wrapper (spec-by-file, launch-wait-relay) | sonnet | 17,933 | 5 | 19.3 min | ~1.2x the 15k target — acceptable; the residual is the 3-min polling loop (fix: foreground run, no loop). vs Phase 6 wrapper 140k = 7.8x cheaper. |
| 2026-08-14 | 7 | codex wrapper (residual review, foreground, no polling) | sonnet | 30,025 | 7 | 10.4 min | 1.5x the 20k review target: acceptable but not yet lean — the relayed report is long (7 tool uses incl. reading it back). Codex-side: 253k-state BFS, 497k next-actions verified. |
