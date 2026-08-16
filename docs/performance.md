# Store and core cost (bead sz2 — measured, not optimised)

`scripts/bench_store.py` builds a snapshot of *n* published units through `decide()`/`apply()`,
then times the operations a CLI verb performs. On 2026-08-17 (Apple Silicon, Python 3.14):

| units | all commands (n×6) | per command | decode state | all checks | all projections | commit | one decide | state size |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.00 s | 0.01 ms | 0.1 ms | 0.3 ms | 0.1 ms | 0.5 ms | 0.01 ms | 1 KB |
| 100 | 0.02 s | 0.03 ms | 0.7 ms | 0.2 ms | 0.5 ms | 0.7 ms | 0.01 ms | 16 KB |
| 500 | 0.39 s | 0.13 ms | 3.1 ms | 0.7 ms | 2.2 ms | 1.8 ms | 0.01 ms | 81 KB |
| 2000 | 6.25 s | 0.52 ms | 12.4 ms | 2.5 ms | 8.5 ms | 6.2 ms | 0.03 ms | 325 KB |

What this says: a CLI invocation on a 2,000-unit store costs ~30 ms end to end (decode + decide
+ commit + projections). `apply()` rebuilds unit tuples per event, so the cost *per command*
grows linearly with unit count — visible above — but mk2 never replays events: the store is a
snapshot, and each verb applies one command. The bead's premise ("index snapshot lookups +
O(1) apply fold before replay sizes bite") therefore does not apply; the only heavy consumer is
the formal layer's exploration, which runs over small fixed universes (~90 s, pinned in
`formal/universe.py`).

**Disposition:** closed as measured-not-needed. Revisit trigger: a real project (bd4.4) whose
store exceeds ~10,000 units or whose CI runs `check` in a tight loop; re-run the script and
compare the shape, not the digits.
