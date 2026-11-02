# Performance

Measured, not optimized. `scripts/bench_store.py` builds a snapshot of *n* published units
through `decide()`/`apply()` and times the operations a CLI verb performs. Results from
2026-08-18 on Apple Silicon, Python 3.14:

| units | all commands (n×6) | per command | decode state | all checks | all projections | commit | one decide | state size |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.00 s | 0.04 ms | 0.2 ms | 0.2 ms | 0.2 ms | 12.4 ms | 0.01 ms | 1 KB |
| 100 | 0.05 s | 0.08 ms | 1.1 ms | 0.6 ms | 0.8 ms | 165.0 ms | 0.03 ms | 19 KB |
| 500 | 0.82 s | 0.27 ms | 7.0 ms | 1.6 ms | 4.4 ms | 1,109.4 ms | 0.04 ms | 96 KB |
| 2000 | 12.79 s | 1.07 ms | 21.7 ms | 5.0 ms | 15.8 ms | 3,711.3 ms | 0.08 ms | 387 KB |

Cost per command grows linearly with the number of units, because `apply()` rebuilds unit
tuples for each event. Ordinary reads stay constant in history length because they read the
derived snapshot; `check.event-log` is the deliberate full-replay path. The commit column
places and fsyncs every event object in the synthetic store, so it measures a bulk first
commit, not the normal lifecycle write of one small object.

## Event log size

The event log costs about 357 B per event. Canonical event records measured 351 B/event at 48
events, 354 at 600, 355 at 3,000 and 357 at 12,000. The store's file sizes (`state.json`,
`.lock` and objects) were 18 KB, 226 KB, 1,139 KB and 4,571 KB. Each folded event adds one
small object, and the slow per-event growth comes from longer revision numbers and identifiers.
These are file sizes, not allocated filesystem blocks.

## Disposition

Closed as measured, not needed. Re-run the script if a real project (bd4.4) passes about 10,000
units or its CI runs `check` in a tight loop, and compare the shape of the curve, not the digits.
