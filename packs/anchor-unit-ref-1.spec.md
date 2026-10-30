# anchor-unit-ref@1 acceptance pack

**Owner:** Fable (acceptance owner). **Implementer:** Codex — may not edit this file or
`tests/test_anchor_unit_ref.py`. **Decision:** D-26. **Beads:** `vouch_mk2-bd4.4.8` (unit),
`vouch_mk2-bd4.4.8.1` (implement). Repairs pilot finding 4 (`vouch_mk2-bd4.4.7`).

## Promise

An anchor advance names the exact unit reference. `advance-anchor --unit-name NAME
[--unit-version VERSION]` claims the evidence head of `NAME@VERSION`, including a superseded
version's head; without `--unit-version` it means the version that is current for that name,
exactly as before. The event records the resolved exact version. The generated CI anchor step and
mk2's own workflow claim every unclaimed head by name and version. Every existing store keeps
replaying: a pre-unit `AnchorAdvanced` record keeps its recorded meaning.

## Requirements

- **R1 — The pilot's main can be anchored again.**
  `test_ci_claims_every_unclaimed_head_of_a_name_including_the_superseded_version`: a project
  holds a rebound, superseded `u@v1` (anchor lagging its head) and its published successor
  `u@v2` (anchor current), both without a run claim; `check --ci` exits 1 with
  "no workflow run claim". The generated workflow's anchor step lists one line per unclaimed
  head, the exact `NAME@VERSION` label (`u@v1`, `u@v2`), and its loop line passes both
  `--unit-name` and `--unit-version` to `advance-anchor`. Claiming each pair with a CI run id
  claims both heads with that run id and promotes `u@v1`; after `generate`, `check --ci` exits 0,
  and the listing is then empty.
- **R2 — An exact reference claims only that version's head.**
  `test_an_exact_reference_claims_only_that_versions_head`: `advance-anchor --unit-name u
  --unit-version v1` under a CI run id promotes and claims `u@v1`, prints `u@v1`, leaves `u@v2`'s
  head unchanged, and the newest `AnchorAdvanced` record's payload is exactly
  `{"unit_name": "u", "unit_version": "v1"}`.
- **R3 — A bare name keeps its meaning; the record is exact; an unknown reference is refused.**
  `test_a_bare_name_still_means_the_current_version_and_is_recorded_exactly`:
  `decide(snapshot, AdvanceAnchor(unit_name="u", run_id="888"))` accepts with exactly
  `(AnchorAdvanced(unit_name="u", unit_version="v2"),)`; `advance-anchor --unit-name u` claims
  `u@v2`, leaves `u@v1` unchanged, and records `{"unit_name": "u", "unit_version": "v2"}`.
  `test_an_unknown_reference_is_refused_and_nothing_is_written`: `--unit-version v9` exits 2 with
  `evidence.no-chain` naming `u@v9`, and `state.json` is byte-identical.
- **R4 — Stores keep replaying.**
  `test_a_legacy_anchor_record_replays_as_the_version_current_when_it_was_recorded`: a record whose
  payload is only `{"unit_name": "u"}` decodes to `AnchorAdvanced(unit_name="u", unit_version="")`;
  after `u@v1` is superseded by `u@v2` with its own chain, `check.event-log` is PASS, `u@v1`'s head
  stays anchored with the record's run id, and `u@v2` stays at GENESIS without a claim.
  `test_mk2_own_store_with_legacy_anchor_records_still_replays` (`# guard:`): mk2's own `.vouch`
  log, which holds pre-unit `AnchorAdvanced` records, replays with `check.event-log` PASS.
- **R5 — The decoder is exact about the new field.**
  `test_an_exact_version_decodes_and_an_explicit_empty_version_is_refused`: a payload with
  `unit_version: "v1"` decodes to the exact event; a payload with `unit_version: ""` is a
  `DecodeError`. No encoder writes an empty version, so it is neither legacy nor exact.
- **R6 — Semantic merge replays the exact reference.**
  `test_a_directory_merge_replays_an_exact_reference_claim`: theirs claimed `u@v1` by reference;
  ours recorded a refactor on `u@v2`. `merge --other` exits 0, ours' `u@v1` head is anchored at its
  head, `u@v2`'s head and anchor are as before the merge, and the merged log holds an
  `AnchorAdvanced` payload `{"unit_name": "u", "unit_version": "v1"}`.
- **R7 — mk2's own workflow claims each head too.**
  `test_mk2_own_anchor_step_lists_and_claims_each_unclaimed_head`: `.github/workflows/ci.yml`'s
  anchor step still selects only heads without `anchor_run_id`, lists `NAME@VERSION` per head, and
  its loop line passes both `--unit-name` and `--unit-version`.
- **R8 — Next actions name the exact reference.**
  `test_next_actions_propose_an_exact_reference_advance_for_a_lagging_superseded_head`: at anchor
  mode `ci` the lagging `u@v1` head yields exactly one evidence action, subject `u@v1`, command
  `advance-anchor`; `u@v2` (anchor current) yields none; `next --json` renders that action's argv
  with `--unit-name u` and `--unit-version v1`.
- **R9 — The formal relation witnesses the superseded claim without shrinking.**
  `test_history_v1_witnesses_claiming_a_superseded_versions_head_without_shrinking`:
  `HISTORY_V1.initial` seeds an unanchored evidence head for the superseded `u@v1`, and
  `HISTORY_V1.commands` offers `AdvanceAnchor(unit_name="u", unit_version="v1")`. `run_formal()`
  passes; `history-v1` reaches a state where `u@v1`'s anchor equals its head; its state count is
  above 31,416 and at most 62,832; `core-v1` stays at 264,462 / 1,381,350 and `tier-v1` at
  246 / 1,640.
- **R10 — The operator docs say so.**
  `test_docs_describe_claiming_a_superseded_versions_head`: `docs/quickstart.md` and the
  `advance-anchor` section of the generated `docs/commands.md` show `--unit-version`.

## Ground rules

- `AdvanceAnchor` and `AnchorAdvanced` gain `unit_version: str = ""` as a keyword field; existing
  positional callers (`AdvanceAnchor("u", "run")`, `AnchorAdvanced(unit_name="u")`) keep working.
  On the command, an empty version means the version current for that name, resolved by the
  decision, which always emits the resolved exact version. On the event, an empty version is the
  legacy shape: the fold resolves it by name at that point of the replay, and resolves a
  non-empty version by `Snapshot.evidence_head_ref`. D-4 stands: the event carries no target.
- The event decoder accepts an `AnchorAdvanced` payload without `unit_version` (decoding to
  `""`) and refuses one whose `unit_version` is present but empty. Keep every other event type's
  decoding exactly as strict as it is. Event records are never rewritten; the snapshot format and
  `CURRENT_STATE_FORMAT` do not change. Do not touch `.vouch/`; the owner records the certificate.
- The CLI reflects the new field as an optional `--unit-version` (default: current version).
  Regenerate `docs/commands.md` with `scripts/gen_commands_doc.py`.
- The anchor step (template in `src/vouch_mk2/init.py` and `.github/workflows/ci.yml`) prints the
  label `NAME@VERSION` for each head without a run claim and splits it in the loop on the last
  `@` (neither name nor version may contain `@`). Keep the step's order, credentials, and the
  `anchor_run_id` selection; `advance-anchor` still runs before `generate`, `check --ci`, `formal`.
- Semantic merge (`store/merge.py`) recovers `AdvanceAnchor(name, run_id, unit_version=version)`
  from the recorded event. The conflict label may name the version; do not change how other
  events are replayed. Run-claim preservation across a merge is `bd4.4.6`, not this unit.
- `next_actions` proposes an advance for every head whose anchor lags its head, including a
  superseded version's, with the exact label as subject; the structured invocation splits the
  label into `--unit-name` and `--unit-version`.
- Formal: add only the seeded `u@v1` head and the one exact-reference command to `history-v1`.
  Do not change `core-v1` or `tier-v1`. Re-pin `tests/test_formal.py`'s `history-v1` counts and
  explain every count change in the DONE file; a shrink anywhere is a defect.
- Every pre-existing test stays green, adjusted coherently (`test_projections.py` resolves the
  evidence subject by exact reference), never by weakening an assertion. New regressions are new
  test functions. Pyrefly strict 0 errors; import-linter contracts kept; README test count is the
  owner's.
- Do not edit this frozen pack. A contradiction is a deviation, not permission to weaken it.

## Out of scope

- Anchoring heads whose anchor lags after a claim, and `check.evidence-anchors` reporting that
  state (`bd4.4.4`, pilot finding 2).
- Merge replay keeping a replayed advance's run claim (`bd4.4.6`, pilot finding 3).
- Version checks for lifecycle events that record bare unit names (`bd4.4.2`, D-25 observation).
- The pilot's own re-pin of `vouch.lock` and its workflow.
