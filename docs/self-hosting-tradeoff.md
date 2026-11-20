# D-11 tradeoff: should mk2 govern its own development?

*Historical decision note written 2026-08-15 for bead `vouch_mk2-dvz.2` as input to a user
decision.* **Outcome (2026-08-17):** D-11 was adopted with named exceptions after eight pilot
units; the stop rule never fired (see `DECISIONS.md`). The roles below reflect that time; D-27
defines the current roles.

D-10 rejected retroactive self-hosting as fabricated history and left prospective self-hosting
open. This note weighed whether to adopt prospective self-hosting.

## Proposal

Starting with the next change, every change to mk2 moves through mk2's own unit lifecycle. Each
event is recorded in the dogfood store as it happens, and anchoring makes the record
tamper-evident. Existing modules are not declared as units after the fact.

| Concept | For an mk2 change | Owner |
|---|---|---|
| Unit | One bead-tracked change, such as `tier-in-snapshot@1` | — |
| Acceptance pack | Spec plus acceptance tests, frozen before implementation | Acceptance owner |
| Red evidence | Tests fail on the pre-change tree, recorded before implementation | Harness, at freeze |
| Implementation | Built against a frozen pack the implementer cannot edit | Implementer |
| Green evidence | Same tests pass, recorded live | Harness, at completion |
| Deviation | Review finding that contradicts the pack; blocks the unit until the owner amends or rejects | Reviewer opens, owner adjudicates |
| Publish | Real definition-of-done gates, including tier evidence (`gate.dod.tier-evidence`) | `decide()` |
| Release | Tagged mk2 version: gates, re-recorded certificate, and walkthrough | Operator |

## Independence

Two models were compared:
- **Model 2 (separated contexts):** the pack author (Fable) is separate from the implementer
  (Codex). The pack is frozen in the store, the brief forbids editing it, and the user adjudicates
  deviations.
- **Model 1 (user-authored packs):** the user writes the packs directly. This is stronger, but
  costlier.

Under either model, independence between agents is only a format heuristic. The claim boundary
keeps independence between real people permanently `NOT_CHECKED`.

## Costs

1. **Per-change friction:** every change requires freezing first and recording red and green
   evidence, and a deviation, when one arises, needs formal handling.
2. **Timing:** it lands on the smallest remaining work (eight open dispositions and performance).
3. **One-time infrastructure:** it needs re-versioning through `decide()` (which retires D-9's
   seeded state anyway) and a pytest-to-`record_evidence` adapter.
4. **Risk of theater:** ceremony can decay into rubber-stamping. Red-before-green and deviation
   blocking help, but human discipline remains the real guarantee.

## Benefits

1. **Real dogfooding:** tests whether the method is worth shipping (D-10 alone is thin).
2. **Surfaces early gaps:** exposes a first user's week-one gaps—re-versioning, an evidence
   adapter, and a release of mk2 with launch criteria.
3. **Clean first pilot:** D045 is a bounded, apt first unit whose subject is the tier matrix
   itself.
4. **Reversible:** stopping is cheap, and the outcome is recorded either way.

## Recommendation

Pilot self-hosting on D045 under Model 2 with a stop rule, then decide on full adoption afterward.

**Stop rule:** if the D045 unit cannot reach `published` through the real gates without
hand-editing the store or bending a gate, treat that as a finding about the method or mk2. Record
it and rule on it.
