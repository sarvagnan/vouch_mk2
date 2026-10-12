# merge-open-in-ci-replay@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Codex — may not edit this file or
`tests/test_merge_open_in_ci_replay.py`. **Decision:** D-25.

## Promise

A divergent semantic merge replays the incoming branch's strict-CI exceptions against the same
exact `NAME@VERSION` they were recorded for. A replay that would land on another version of the
unit is refused as a merge conflict instead of moving the exception.

## Requirements

- **R1 — Git stage recovery keeps both exceptions.**
  `test_git_stage_recovery_replays_an_incoming_open_in_ci_exception`: two branches from shared
  history each freeze a unit and mark it open in CI. `merge-git-stages` exits 0. Both units are
  `acceptance-frozen` with their exceptions and reasons intact, and after `generate`,
  `check --ci` exits 0.
- **R2 — Directory merge keeps both exceptions.**
  `test_directory_merge_replays_an_incoming_open_in_ci_exception`: the same divergence between two
  store directories merges with `merge --other` and keeps both exceptions.
- **R3 — A cleared exception replays as cleared.**
  `test_a_cleared_incoming_exception_replays_as_cleared`: the incoming branch marks and then
  clears its exception. After the merge, only ours remains, and the incoming unit is still
  `acceptance-frozen`.
- **R4 — An exception never moves to another version.**
  `test_an_exception_never_moves_to_another_unit_version`: shared history freezes `moving@1`.
  Theirs marks `moving@1` open in CI. Ours opens a deviation on it, supersedes it, and freezes
  `moving@2`. `merge-git-stages` exits non-zero with `merge.conflict`, names both `moving@1` and
  `moving@2`, does not report `unit.unknown`, and leaves `.vouch/state.json` unchanged. Stripping
  the version without checking the replayed reference fails this test.

## Ground rules

- Repair the replay seam in `vouch_mk2.store.merge`. Both CLI paths reach it through
  `merge_store_roots`.
- Compare the replayed event's reference with the recorded one exactly. Do not parse labels
  anywhere except where the recorded event is turned back into a command.
- Keep the refusal shape: one `merge.conflict` violation naming the source event and revision,
  and no write when the replay is refused.
- Do not change event or snapshot encoding, `decide()`, the strict CI policy, or the replay of
  any other event type.
- Do not edit this frozen pack. A contradiction is a deviation, not permission to weaken it.

## Out of scope

- Version checks for lifecycle events that record bare unit names (D-25 observation).
- Any change to how strict CI treats exceptions outside merge.
- The pilot's own re-pin of `vouch.lock`.
