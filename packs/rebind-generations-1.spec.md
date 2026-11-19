# rebind-generations@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_rebind_generations.py` or `packs/rebind-generations-1.effects.json`.
**Decision:** D-39 (settles `vouch_mk2-0tc`). Amends D-14's continuity invariant.
**Effects:** one store action, in `packs/rebind-generations-1.effects.json`: a rebind after a
re-freeze now commits, and a rebind record that does not name the unit's current pack is refused
wherever history is folded or committed. No event, log or snapshot format changes.

## Promise

A pack generation runs from a freeze to the next respecify. `rebind-pack` after a respecify and
re-freeze succeeds and keeps every earlier rebind row. Each `AcceptancePackRebound` must name, as
its old inventory and old test set, the unit's current values immediately before it. That is
checked when events are folded, when the event log is checked, before an event extension is
committed, and on merge source chains. A pre-log baseline, where no event witnesses a generation
break, keeps the contiguous-chain requirement. D-31's effects equality, `rebind.stale-pack`, the
other rebind refusals and D-14's fresh GREEN after each rebind are unchanged.

## Common rules for every scenario

- A scratch store is made with `Store.init`, CI policy `ledger`, and one unit `u@1` whose pack is
  one `--pack-test-file` (`t.py`, one line ending in a marker comment). "Publish" is freeze,
  mark-ready, start, RED, GREEN, `advance-anchor`, mark-done, publish. A rebind writes new bytes to
  `t.py` and runs `rebind-pack --reason`; "with a fresh GREEN" adds a GREEN and `advance-anchor`.
- "Generation 2" is: publish `p/1` (bytes A), rebind A→B with a fresh GREEN, open a `pack-defect`
  deviation, `adjudicate-amend` to `p/2`, close it, `respecify`, publish `p/2` (bytes C).
- A forged rebind names generation 1's last inventory and test set (B) while the unit's current
  pack is C. Because it continues the previous row, the pre-change snapshot invariant accepted it.
  The tests write it with `Store.put_object` and a raw `state.json`, or build it with
  `build_event_records` from a stale copy of the snapshot, so it bypasses `decide()`.
- Refusals carry the code `rebind.history-disconnected` in their message. The pure fold
  (`vouch_mk2.core.decide.apply`) raises `ValueError`; `Store.commit` raises `StoreError`.

## Requirements

- **R1 — A rebind after a re-freeze succeeds.**
  `test_a_rebind_after_a_refreeze_succeeds_and_keeps_both_generations`. In generation 2,
  `rebind-pack` exits 0. The unit stays published at `p/2` with binding `rebound`; its history is
  the unchanged generation-1 row and a new row whose old inventory and test set are C's and whose
  new inventory is the current one, with both reasons kept. Before the new GREEN,
  `check.evidence-chains` fails naming a missing fresh GREEN; after it, `check.event-log` and
  `check.evidence-chains` pass and `check.pack-bound` names `u@1` as rebound after publication.
- **R2 — Three generations, including a return to earlier bytes.**
  `test_three_generations_and_a_return_to_earlier_bytes`. Generation 1 has two rebinds (A→B→C);
  generation 2 re-freezes bytes A as `p/2` and rebinds; generation 3 re-freezes bytes B as `p/3`
  and rebinds. Every step exits 0, the four rows keep their order and reasons, the two rows within
  generation 1 are continuous, and `check.event-log` passes. Generations come from the recorded
  respecify and freeze, never from byte inequality.
- **R3 — The fold refuses a rebind that does not name the current pack.**
  `test_folding_refuses_a_rebind_that_does_not_name_the_current_pack`. On a published unit with
  pack C and one earlier row A→B, folding a rebind from C succeeds and appends its row. Folding a
  rebind whose old inventory is B, whose old test set alone is wrong, or whose old inventory is
  another digest raises `ValueError` with the code.
- **R4 — `check.event-log` fails on it.**
  `test_the_event_log_check_fails_on_a_rebind_that_names_an_earlier_generation`. Generation 2's
  store with a forged rebind appended as the next record and `state.json` naming it:
  `check.event-log` is `fail` and its detail contains the code.
- **R5 — A commit refuses it.**
  `test_a_commit_refuses_a_rebind_that_does_not_name_the_current_pack`. Records built from a copy
  of generation 2's snapshot whose unit names B as its current pack, committed to the real store
  (whose unit names C) with the real expected revision: `Store.commit` raises `StoreError` with the
  code, `state.json` is byte-identical afterwards, and `check.event-log` still passes.
- **R6 — A merge refuses a source chain holding it.**
  `test_a_merge_refuses_a_source_chain_with_a_rebind_that_names_an_earlier_generation`. OURS is
  generation 2; THEIRS is a copy with the forged rebind appended. `merge --root OURS --other
  THEIRS` exits non-zero, its output contains `merge.theirs-history-invalid` and the code, and
  OURS' `state.json` is byte-identical.
- **R7 — A pre-log baseline keeps the contiguous chain.**
  `test_a_pre_log_baseline_keeps_the_contiguous_chain`. Folding a `PreLogHistoryRecorded` whose
  baseline unit has rows A→B, B→D succeeds and yields that baseline's units; one with rows A→B,
  C→D raises `ValueError` with the code. `commit_pre_log_checkpoint` of the disconnected baseline
  raises (`StoreError` or `ValueError`) with the code and leaves the empty store empty.
- **R8 — A stale rebind command is still refused first.** `# guard:`
  `test_a_stale_rebind_command_is_still_refused_before_folding`. `decide()` of `RebindPack` naming
  old pack B on the unit whose pack is C is refused with exactly `rebind.stale-pack`.
- **R9 — The claim boundary states the rule.**
  `test_the_claim_boundary_states_the_generation_rule`. Matching ignores runs of whitespace.
  `docs/claim-boundary.md` contains `Each rebind must name the unit's current inventory and test
  set. A re-freeze starts a new pack generation, so the first rebind after it does not continue an
  earlier generation's rebinds.`

## Ground rules

- The unconditional adjacent-row comparison leaves `validate_snapshot`. The continuity check moves
  to the fold of `AcceptancePackRebound` (both the old inventory and the old test set must equal
  the unit's current values before the event), and to the fold of `PreLogHistoryRecorded` (its
  baseline's rows must be contiguous, as today). Every other snapshot check on rebind rows
  (digest shape, non-empty reason, sequence order, the latest row naming the current inventory and
  test set) stays.
- `Store.commit` checks the folded meaning of a supplied extension before writing anything: each
  supplied `AcceptancePackRebound`, in order, against the on-disk state it extends, and a pre-log
  checkpoint's baseline rows. A refused commit writes no object and no state.
- `check.event-log` and merge source-chain verification reach the rule through the fold; their
  existing error shapes (`replay refused event: …`, `merge.<branch>-history-invalid: …`) stay.
- `decide()` is unchanged: a rebind it accepts always names the unit's current pack, and
  `rebind.stale-pack`, the effects equality and every other rebind refusal keep their order.
- Every rebind row stays in the snapshot. No event, log, snapshot or state format change, no
  upgrade step, and no change to evidence rules (D-14 fresh GREEN, D-17, D-36).
- mk2's own store and every frozen pack's fixtures keep their verdicts. If a frozen test elsewhere
  asserts the old global adjacency, or a formal size, relation digest or the certificate changes,
  stop on it and write `rebind-generations.DEVIATION.md`.
- `docs/claim-boundary.md`'s rebind paragraph gains R9's sentences; every pinned phrase is kept.

## Out of scope

Generation markers in the snapshot and clearing the rebind list at respecify (both rejected in
D-39). Relaxing the pure command's legacy empty `old_pack_digest` default. Forged histories beyond
the rebind rule (vouch_mk2-22a).

## Verification outside pytest (final pass)

The owner runs `check` on a scratch copy of the pilot project's store (never the pilot itself)
before and after the change and records that every verdict is unchanged; mk2's own `check --ci`
stays clean apart from the expected pending anchor.
