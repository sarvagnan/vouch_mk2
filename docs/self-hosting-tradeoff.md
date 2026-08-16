# D-11 tradeoff: should mk2 govern its own forward development as units?

**Status:** written 2026-08-15 for bead `vouch_mk2-dvz.2` (user decision). Companion to
DECISIONS.md D-10 (which rejected *retroactive* self-hosting as fabricated history and left
*prospective* self-hosting open).

## What is proposed

Not "declare the existing modules units". From the next change onward, every increment to
mk2 goes through mk2's own unit lifecycle, with every event recorded at the moment it
happens into the dogfood store — which the anchoring machinery makes tamper-evident.

| Method concept | For a mk2 change | Owner |
|---|---|---|
| Unit | one bead-tracked change, e.g. `tier-in-snapshot@1` | — |
| Acceptance pack | the spec file PLUS acceptance tests, written and frozen BEFORE implementation | acceptance owner |
| Red evidence | acceptance tests run and fail against the pre-change tree, recorded before implementation | harness at freeze |
| Implementation | Codex implements against a frozen pack it cannot edit | implementer |
| Green evidence | the same tests pass; recorded live | harness at completion |
| Deviation | a review finding that contradicts the PACK (not merely an implementation bug), adjudicated amend/reject by the owner; unit blocked meanwhile | reviewer opens, owner adjudicates |
| Publish | the real DoD gates: pack recorded, deps satisfied, no blockers, red-before-green chain | `decide()` |
| Release | a tagged mk2 version with launch criteria (gates, certificate re-recorded, walkthrough) | operator |

## Independence model

Model 2 (separated contexts): the pack author (Fable) is not the implementer (Codex); the
asymmetry is mechanical (the pack lives frozen in the store; the implementer's spec forbids
editing it), and the USER adjudicates deviations. Model 1 (user writes packs) is stronger
and costlier. Stated plainly, independence between Fable and Codex remains a *format
heuristic* — the claim boundary already lists "independence between real persons" as
permanently NOT_CHECKED, and self-hosting does not change that.

## Costs

1. Friction on every change (freeze first, formal deviations, per-red/green evidence) —
   the method's deliberate friction; today's flow is fast because it skips it.
2. That friction lands on the smallest remaining work (9 open dispositions, D045, perf).
3. One-time infrastructure the pilot forces: re-versioning through `decide()` (which
   retires D-9's seeded state — good regardless) and a pytest→`record_evidence` adapter.
4. Ceremony can rot into theater; the mechanical guards (red-before-green, blocked-on-open
   -deviation) help but human discipline is the actual guarantee.

## Benefits

1. A TRUE dogfood: mk2's whole development under mk2's gates — the proof if the method is
   worth shipping to others. D-10 alone is honest but thin.
2. Flushes real gaps a first user would hit in week one (re-versioning, evidence adapter,
   a release of mk2 itself with launch criteria).
3. D045 is a bounded, real, unusually apt first unit (its subject matter IS the tier matrix).
4. Cheap to stop: one change of overhead; the outcome becomes a recorded decision either way.

## Recommendation

Adopt as a PILOT on D045 under Model 2 with an explicit stop rule; do NOT adopt wholesale
up front — decide that after D045 shows what the friction costs on this repo.

**Stop rule:** if the D045 unit cannot reach `published` through the real gates without
hand-editing the store or bending a gate, that is a finding about the method or about mk2 —
record it and rule; never paper over it.
