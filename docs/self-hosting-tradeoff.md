# D-11 tradeoff: should mk2 govern its own development?

Written 2026-08-15 for bead `vouch_mk2-dvz.2`, as input to a user decision. **Outcome
(2026-08-17):** D-11 adopted with named exceptions after eight pilot units; the stop rule never
fired (see `DECISIONS.md`). Roles below are those of the time; D-27 has the current ones.

D-10 rejected retroactive self-hosting as fabricated history and left prospective self-hosting
open. This note weighed it.

## Proposal

From the next change on, every change to mk2 goes through mk2's own unit lifecycle, with each
event recorded in the dogfood store as it happens, where anchoring makes the record
tamper-evident. Existing modules are not declared units after the fact.

| Concept | For a mk2 change | Owner |
|---|---|---|
| Unit | one bead-tracked change, such as `tier-in-snapshot@1` | — |
| Acceptance pack | spec plus acceptance tests, frozen before implementation | acceptance owner |
| Red evidence | the tests fail on the pre-change tree, recorded before implementation | harness, at freeze |
| Implementation | against a frozen pack the implementer cannot edit | implementer |
| Green evidence | the same tests pass, recorded live | harness, at completion |
| Deviation | a review finding that contradicts the pack; the unit is blocked until the owner amends or rejects | reviewer opens, owner adjudicates |
| Publish | the real DoD gates, including tier evidence (`gate.dod.tier-evidence`) | `decide()` |
| Release | a tagged mk2 version: gates, re-recorded certificate, walkthrough | operator |

## Independence

Model 2, separated contexts: the pack author (Fable) is not the implementer (Codex). The pack is
frozen in the store and the brief forbids editing it, and the user adjudicates deviations.
Model 1, where the user writes the packs, is stronger and costlier. Either way, independence
between agents is a format heuristic; the claim boundary keeps independence between real people
permanently `NOT_CHECKED`.

## Costs

1. Friction on every change: freeze first, formal deviations, red and green evidence.
2. It lands on the smallest remaining work (eight open dispositions, performance).
3. One-time infrastructure: re-versioning through `decide()`, which retires D-9's seeded state
   anyway, and a pytest-to-`record_evidence` adapter.
4. Ceremony can decay into theater. Red-before-green and deviation blocking help; human
   discipline is the real guarantee.

## Benefits

1. A real dogfood, testing whether the method is worth shipping. D-10 alone is thin.
2. It surfaces a first user's week-one gaps: re-versioning, an evidence adapter, a release of mk2
   with launch criteria.
3. D045 is a bounded, apt first unit: its subject is the tier matrix itself.
4. It is cheap to stop, and the outcome is recorded either way.

## Recommendation

Pilot it on D045 under model 2 with a stop rule, and decide on wholesale adoption after.

**Stop rule:** if the D045 unit cannot reach `published` through the real gates without
hand-editing the store or bending a gate, that is a finding about the method or mk2. Record it
and rule on it.
