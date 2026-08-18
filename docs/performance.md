# Store and core cost (bead sz2 — measured, not optimised)

`scripts/bench_store.py` builds a snapshot of *n* published units through `decide()`/`apply()`,
then times the operations a CLI verb performs. On 2026-08-18 (Apple Silicon, Python 3.14):

| units | all commands (n×6) | per command | decode state | all checks | all projections | commit | one decide | state size |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.00 s | 0.04 ms | 0.2 ms | 0.2 ms | 0.2 ms | 12.4 ms | 0.01 ms | 1 KB |
| 100 | 0.05 s | 0.08 ms | 1.1 ms | 0.6 ms | 0.8 ms | 165.0 ms | 0.03 ms | 19 KB |
| 500 | 0.82 s | 0.27 ms | 7.0 ms | 1.6 ms | 4.4 ms | 1,109.4 ms | 0.04 ms | 96 KB |
| 2000 | 12.79 s | 1.07 ms | 21.7 ms | 5.0 ms | 15.8 ms | 3,711.3 ms | 0.08 ms | 387 KB |

What this says: `apply()` rebuilds unit tuples per event, so the cost *per command* grows
linearly with unit count. Ordinary reads remain O(1) in history length because they use the
derived snapshot; `check.event-log` is the deliberate full replay path. The commit column now
includes placing and fsyncing every event object in the synthetic store, so it measures a bulk
initial commit rather than the normal one-small-object lifecycle write.

## Event log — measured 357 B per event; 4,571 KB store at 12,000 events

The same 2026-08-18 run measured canonical event-record payloads at 351 B/event (48 events),
354 B/event (600), 355 B/event (3,000), and 357 B/event (12,000). Logical store growth,
including `state.json`, `.lock`, and content-addressed objects, was respectively 18 KB, 226 KB,
1,139 KB, and 4,571 KB. This is one small object per folded event; the small increase per event
comes from longer revision numbers and identifiers. These are file-byte totals, not allocated
filesystem blocks.

**Disposition:** closed as measured-not-needed. Revisit trigger: a real project (bd4.4) whose
store exceeds ~10,000 units or whose CI runs `check` in a tight loop; re-run the script and
compare the shape, not the digits.
