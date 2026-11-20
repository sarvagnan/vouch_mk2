# Performance

mk2's store operations are measured, not optimized. `scripts/bench_store.py` builds a snapshot of
*n* published units through `decide()` and `apply()`, then times the operations a CLI verb
performs.

**Measured 2026-08-18 on Apple Silicon, Python 3.14:**

| units | all commands (n×6) | per command | decode state | all checks | all projections | commit | one decide | state size |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.00 s | 0.04 ms | 0.2 ms | 0.2 ms | 0.2 ms | 12.4 ms | 0.01 ms | 1 KB |
| 100 | 0.05 s | 0.08 ms | 1.1 ms | 0.6 ms | 0.8 ms | 165.0 ms | 0.03 ms | 19 KB |
| 500 | 0.82 s | 0.27 ms | 7.0 ms | 1.6 ms | 4.4 ms | 1,109.4 ms | 0.04 ms | 96 KB |
| 2000 | 12.79 s | 1.07 ms | 21.7 ms | 5.0 ms | 15.8 ms | 3,711.3 ms | 0.08 ms | 387 KB |

**How to read these numbers:**
- **Per-command cost** grows linearly with the number of units, because `apply()` rebuilds unit
  tuples on each event.
- **Ordinary reads** stay constant in history length because they read the derived snapshot.
  `check.event-log` is the deliberate full-replay path.
- **The `commit` column** writes and fsyncs every event object in the synthetic store. It
  measures a bulk first commit, not a normal lifecycle write of one small object.

## Event log size

The event log costs about 357 B per event. Each folded event adds one small object, and the slow
per-event growth comes from longer revision numbers and identifiers. Store totals cover
`state.json`, `.lock`, and objects, measured as file sizes rather than allocated filesystem
blocks:

| events | canonical record size | total store file size |
|---:|---:|---:|
| 48 | 351 B/event | 18 KB |
| 600 | 354 B/event | 226 KB |
| 3,000 | 355 B/event | 1,139 KB |
| 12,000 | 357 B/event | 4,571 KB |

## Disposition

Closed as measured: at the time, the measurements did not justify optimization. Re-run `scripts/bench_store.py` if a real project
(bd4.4) passes about 10,000 units or its CI runs `check` in a tight loop, and compare the shape
of the curve rather than the exact digits.
