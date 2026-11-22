# rebind-fresh-green@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_rebind_fresh_green.py` or `packs/rebind-fresh-green-1.effects.json`.
**Decision:** D-51 and its note (`vouch_mk2-inv.5`). **Seams (D-44):** the CLI on scratch
projects (`init`, the lifecycle, `record-evidence`, `rebind-pack`, `show-evidence --json`,
`check`); `Store` read-only and its strict codec (the folded snapshot; one state written as the
code before this unit wrote it); the pure check `check_pack_bound`; the formal package's public
universes, relations and invariants; the text of `docs/*.md`.
**Effects:** none.

## Promise

Folding a rebind after publication (`AcceptancePackRebound`) sets the unit's
`refreeze_green_required` flag, whether or not the test set changed and whether or not the flag
was already set (a second rebind renews it). While the unit stays active, as after a changed
re-freeze (D-36), only an explicit GREEN with outcome `tests-passed` recorded for the unit's
current test set clears it; a REFACTOR or a RED leaves it set. Because `current_pack_evidence_is_green`
already returns false while the flag is set, every consumer of that predicate (the pure
`check_pack_bound` at `high`, `show-evidence`'s `current_pack_green`, `next`) now requires that
GREEN after a rebind, as `check.evidence-chains` and the chain-walking `check.pack-bound` already
did. Folding the supersession of a unit whose binding is `rebound` clears its flag: a retired
version takes no evidence and every chain check skips it, so its obligation ends with it (a unit
bound any other way keeps its flag on supersession, as before). No state, event or evidence
format changes.

Formal M12 no longer exempts `rebind-pack`: any transition that changes a unit's bound inventory,
a rebind included, must leave the flag on. M12 does not count a transition that leaves the unit
superseded as clearing the requirement without a GREEN. In the `tier-v1` universe the rebind
transition turns it on. The formal run still passes every invariant and witness.

The rule applies to rebind events folded by this code; stored states are not rebuilt, and a
pre-log checkpoint keeps the state it captured. A store written before this unit replays the same
as it is stored when, for each rebind, the unit's flag was off when it was rebound and the rebind
was followed by a passing GREEN for the then-current test set, or by the supersession of that
rebound unit, before anything else depended on the flag. If a replayed rebind event leaves an
active unit waiting for that GREEN while its stored flag is off, `check.event-log` fails; the row
passes again once the whole replayed state agrees with the stored one. Each such unit's flags agree
again after a passing GREEN for its current test set or after its supersession while its binding
is still rebound; either reconciles only that unit. If the stored flag was already on when the
unit was rebound (a D-36 requirement still pending), replay agrees on that unit's flag after the
rebind, but the supersession of that unit while its binding is still rebound and before a GREEN
now clears a flag the old code kept, so that retired
version's replay disagrees with its stored state and `check.event-log` fails for good, since a
superseded version takes no evidence (reachable only at `lightweight`, where publication needs no
evidence; found by Astra's review, DEV-rfg-01). A rebind captured inside a pre-log checkpoint is
not replayed and does not cause any of this. Other old histories whose replay under the new fold
differs from their stored state (for example a rebound unit respecified and published again with
only a REFACTOR after the rebind) fail `check.event-log`; that is the cost of changing the fold
without a format change (D-51). `docs/claim-boundary.md` states the rule (R7).

## Amended by DEV-rfg-01 (pack/2)

Astra's implementation review found a legacy history the Promise described wrongly: at
`lightweight`, a unit re-frozen with a changed pack (flag on), published without a GREEN, rebound
and superseded. Under the old fold the stored flag stays on; the new fold clears it on the
supersession of a rebound unit, so `check.event-log` fails permanently for that store. Keeping the
supersession rule protects the more common legacy history (rebound with the flag off, then
superseded before a GREEN), and no rule without a new field keeps both. The owner accepts the
loss under D-51's no-format-change decision. The Promise above now states both cases; no test,
code or effects change.

## Amended by DEV-rfg-02 (pack/3)

Astra's re-check: the old-store paragraph said the event-log row recovers once every affected unit
has its GREEN, but superseding a still-rebound unit also brings its flags into agreement (R9's
history). The paragraph now names both ways for each unit and scopes the flag-on case to
supersession while the binding is still rebound. No test, code or effects change.

## Requirements

Each test fails before the change because the rebind leaves the flag off, a REFACTOR after a
spec-only rebind makes the predicate true, M12 exempts the rebind, or the docs lack the text,
except the guard.

- **R1 — A spec-only rebind needs a GREEN.** `test_a_spec_only_rebind_needs_an_explicit_green`.
  A unit published at `standard` after RED and GREEN, then rebound with only its spec changed:
  the folded unit has the flag set and `current_pack_green` is false; after a REFACTOR, both are
  unchanged; after a passing GREEN, the flag is off and `current_pack_green` is true.
- **R2 — A changed test file too.** `test_a_rebind_with_a_changed_test_file_sets_the_flag`. The
  same with the test file changed: the flag is set after the rebind and after a REFACTOR, and
  cleared by a passing GREEN.
- **R3 — A RED does not clear it.** `test_a_red_after_a_rebind_leaves_the_flag_set`. After a
  spec-only rebind, a RED: the flag stays set and `current_pack_green` is false; a passing GREEN
  after it clears the flag and makes `current_pack_green` true. (`record-evidence` refuses a GREEN
  whose command fails, so a failing GREEN cannot reach the fold.)
- **R4 — The pure pack-bound check.** `test_the_pure_pack_bound_check_wants_the_green_at_high`.
  With the tier then set to `high`, `check_pack_bound` on the folded snapshot after a spec-only
  rebind and a REFACTOR is `FAIL` naming the unit; after a passing GREEN it is `PASS`.
- **R5 — A store written before the change.**
  `test_a_store_rebound_before_the_change_fails_the_event_log_until_its_green`. After a spec-only
  rebind, `state.json` is rewritten with the flag off (as the code before this unit folded it):
  `check` reports `check.event-log` as `FAIL`; after a passing GREEN is recorded, `check.event-log`
  is `PASS` and the flag is off.
- **R6 — Formal.** `test_m12_requires_a_rebind_to_turn_the_flag_on`. In the `tier-v1` relation,
  every `rebind-pack` transition that changes a unit's bound inventory leaves that unit's flag on;
  M12 holds on the real relation, and reports a violation when one such transition's target is
  replaced by the same state with the flag off; and M12 reports nothing for an added `supersede`
  transition from that rebind target to the same state with the unit superseded and its flag
  off.
- **R7 — Docs.** `test_claim_boundary_states_the_rebind_rule`. `docs/claim-boundary.md` contains,
  whitespace normalised: ``A rebind after publication turns on the fresh-GREEN requirement; while
  the unit stays active, only a passing GREEN for the current test set turns it off, and a
  REFACTOR does not.`` and ``Superseding a rebound unit ends its requirement.``
- **R8 — A second rebind renews it.** `test_a_second_rebind_renews_the_requirement`. Rebind A,
  a passing GREEN (flag off), then rebind B with a different spec: the flag is set again; after a
  REFACTOR it stays set and `current_pack_green` is false; a passing GREEN clears it. Then rebind
  C and, with the flag still set, rebind D: the flag stays set until a passing GREEN.
- **R9 — Supersession ends it.**
  `test_superseding_a_rebound_unit_clears_the_requirement` (`# guard:` today, where the flag is
  never set). A published unit rebound and then superseded before any GREEN: the superseded record
  has the flag off and `check.event-log` is `PASS`, the same as a store written before this unit.
- **R10 — Other bindings keep the flag on supersession.**
  `test_a_refrozen_unit_keeps_its_requirement_when_superseded` (`# guard:`, passes today and must
  keep passing). At `lightweight`, a unit re-frozen with a changed spec after a pack-defect
  deviation (D-36 sets the flag), published (which needs no evidence at that tier) and superseded
  before any GREEN: the superseded record keeps the flag and
  `check.event-log` is `PASS`.
- **Guard.** `test_guard_a_rebind_followed_by_a_green_replays_cleanly`: a unit rebound and then
  given a passing GREEN has the flag off, `current_pack_green` true and `check.event-log` `PASS`,
  today and after. (That `vouch-mk2 formal` passes every invariant and witness is checked by the
  README gates, not here: a full run takes minutes.)

## Ground rules

- **Code.** The change is in the fold (`apply` of `AcceptancePackRebound`) and in M12; the
  predicate, the gates and the checks keep their code unless the Promise needs otherwise. No
  state, event, evidence or pack format change; no new field. Pure code stays pure (D-1).
- **Fallout (owner's).** The formal relation of `tier-v1` and the recorded certificate change.
  Frozen tests of other units that pin them (for example `formal-memory@1`) are adapted and
  rebound by the owner, not by the implementer; Sol lists each such failure in the DONE file and
  does not edit those files. mk2's own certificate is re-recorded by the owner.
- **Docs.** The R7 sentence goes near the rebind text in `docs/claim-boundary.md`; any other doc
  sentence the change makes false is corrected (for example where M12 or the rebind exception is
  described). Keep every pinned phrase.
- **Out of scope.** Whether a published unit can be published again. The JUnit test sets of
  `vouch_mk2-inv.4`. Any change to how a rebind binds its new inventory.

## Limits (convergence bound, set before review)

A review blocks only on: a flag, predicate, check row or M12 result that differs from the Promise
for a store the CLI can produce (by this code or, as the Promise describes, by the code before
it) or for a relation `explore` produces; a Promise claim about old stores that is false; a
format change; a frozen
test edited by the implementer; a false statement in a changed doc; or a pinned phrase removed.
Hand-edited stores other than R5's are `vouch_mk2-22a`'s boundary (D-50). Wording beyond what the
tests pin is a note.
