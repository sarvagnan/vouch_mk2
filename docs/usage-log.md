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
| 2026-08-15 | 8 | codex wrapper (impl, foreground 600s then bg relaunch) | sonnet | 9,407 | 2 | 10.1 min | UNDER target (9.4k vs 15k) — but a process bug: on the 600s foreground timeout it relaunched a DUPLICATE codex exec while the original was still running (two agents on one tree). Fable killed the duplicate. Fix: on timeout, do NOT relaunch — just wait on the existing pid. |
| 2026-08-15 | 8 | codex wrapper (impl, final relay after original completed) | sonnet | 23,797 | 4 | 17.8 min | 1.6x target; includes the second notification cycle caused by the duplicate-launch instruction. Combined Phase 8 impl wrapper: 33.2k across both notifications. |
| 2026-08-15 | 8 | codex mechanical review (DIRECT launch, no wrapper) | — (Codex only) | 0 Claude tokens | 0 | ~9 min | Zero wrapper cost — the leanest pattern; adopt for all read-only reviews. Found the argv[0] preference (converges w/ Opus). |
| 2026-08-15 | 8 | Opus adversarial review (novel attacks only) | opus | 75,480 | 36 | 8.6 min | Under the 100k target. 12 findings incl. 1 critical (MCP never answered a live client) + 3 high (server killable by one line; init symlink escape; hook/MCP hang on held lock). Worth every token on this surface. |
| 2026-08-15 | 9 + D-9 | codex impl x2 + review (ALL direct launches) | — (Codex only) | 0 Claude tokens | 0 | ~4h wall total | The pattern has fully converged: no wrapper at all. Combined review found 12 findings (5 high: superseded deps in amendment impact, evidence binding superseded versions, M3 name lookup, map decoder rejecting history, empty criteria, approve ignoring round-trip failure) — all fixed. |
| 2026-08-16 | unit #1 ingress-invariants@1 | codex impl x2 + review (ALL direct launches) | — (Codex only) | 0 Claude tokens | 0 | ~2.5h wall | First self-hosted unit. Codex raised DEV-ingress-01 itself rather than editing the pack. Review found 2 false refusals + gaps -> DEV-ingress-02 (contract conflict vs D-9). Two of three deviations were pack-author (Fable) defects. |
| 2026-08-16 | unit #2 assurance-tier@1 | codex impl (direct launch) | — (Codex only) | 0 Claude tokens | 0 | in progress | Pack API names verified pre-freeze this time; one pack defect (build_lock root) caught BEFORE the freeze instead of becoming a deviation. |
| 2026-08-16 | unit #2 assurance-tier@1 (cont.) | codex impl + review + fix pass (ALL direct launches) | — (Codex only) | 0 Claude tokens | 0 | ~2h wall | Codex raised DEV-tier-01 itself (rejected: pack stood). Review: 7 findings, 1 HIGH reachable fail-open (status change erased the tier obligation) — all fixed. Codex added ~0 new test FUNCTIONS across both passes (extended existing ones); Fable added F1(b)/F3 regression tests in the final pass. Published 2d44410. |
| 2026-08-16 | unit #3 lifecycle-cli@1 | codex impl + mechanical review + fix pass (ALL direct launches) | — (Codex only) | 0 Claude tokens | 0 | ~2h wall | Codex raised DEV-cli-01 (my capsys test defect) itself. Mechanical review: 2 HIGH (next injectable; re-entrancy not thread-aware) + 6 MED + 2 LOW; two were pack defects -> DEV-cli-02 amend (pack/3). |
| 2026-08-16 | unit #3 lifecycle-cli@1 | Opus adversarial (store lock re-entrancy + evidence subprocess boundary only) | opus | 76,779 | 46 | 9.9 min | Under the 100k target. 2 HIGH (read_only is a convention not a capability; read verbs take the exclusive writer lock and block forever behind a stuck writer — a THIS-DIFF regression class) + 4 MED + 2 LOW; its 8-writer race test HELD (no lost update). Distinct from Codex's set except 3 overlaps — worth it on a locking change. |
| 2026-08-16 | unit #4 state-format@1 | codex impl + mechanical review + fix pass (ALL direct) | — (Codex only) | 0 Claude tokens | 0 | ~2h wall | Codex mechanical review: 3 P1 (raw replace primitive unvalidated; regen-after-commit failure not repairable; MY empty golden pinned no nested layout -> DEV-sf-01) + P2 + 2 P3. |
| 2026-08-16 | unit #4 state-format@1 | Opus adversarial (stale-open path, atomicity, upgrader oracle) | opus | 86,365 | 55 | 17.5 min | 1 HIGH that was MINE at the root: D-13/R5 derived published_once (a latch) from status = invented state, disarming tier gates -> DEV-sf-02, D-13 corrected. Stale-open, TOCTOU, crash-consistency, dry-run purity, spoofing all HELD. Slightly over the 80k ask; worth it. |
| 2026-08-16 | unit #5 operator-docs@1 | codex mechanical half (generator/commands.md/README) + docs ACCURACY review (direct) | — (Codex only) | 0 Claude tokens | 0 | ~1.5h wall | Fable wrote the quickstart (user-facing copy stays Claude-side). Accuracy review checked 45 sentences: 16 OVERSTATED the claim boundary (pack bytes not hashed; no role identity; self-check = config consistency; next incomplete) — all corrected; 3 became product beads (bd4.6 pack-bytes binding, bd4.7 CLI polish). One dead launch (stdin) relaunched. |
