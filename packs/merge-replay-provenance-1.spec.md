# merge-replay-provenance@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_merge_replay_provenance.py` or `packs/merge-replay-provenance-1.effects.json`.
**Decision:** D-33 (settles `vouch_mk2-bd4.4.2` and `vouch_mk2-bd4.4.6`). **Effects:** one
observation (the source store's event chain, captured as `theirs_head`) and one action (the target
store, confirmed), under the D-31 note of 2026-11-15.

## Promise

A divergent merge replays each incoming command onto the same unit reference its source acted on,
and each replayed fact keeps the actor, run claim and recorded time of the source record it
corresponds to. A replay that would act on another version, or claim an anchor for evidence the
source run never saw, is refused as `merge.conflict`, and nothing is written.

## Common rules for every scenario

- Two store directories are built from shared history (`shutil.copytree`) and merged with
  `vouch-mk2 merge --root OURS --other THEIRS`, except where a requirement says
  `merge-git-stages`. Each side writes its branch-local records under its own `--actor`, and the
  first divergent command differs on each side, so no two independently written records are
  byte-identical. Every divergent scenario asserts that the merge used replay (not fast-forward
  or no-op).
- The tests clear every CI run-id variable Vouch recognises before running commands, and set one
  only where a requirement names a run claim. Source records that a requirement checks for time
  are written with recorded times strictly earlier than the merge's (the test crosses a second
  boundary before merging).
- Each fixture asserts its own preconditions: the source and target pre-states named in the
  requirement, and, for identity refusals, that `decide()` on ours accepts the recovered command
  (so the refusal can only come from the identity or head check).
- **Refused** means:
  - The exit status is non-zero, and the output contains `merge.conflict`.
  - The output names the source fact whose reference differs, as `<EventName>` with that
    record's own `revision <n>`. For a companion that is the companion, not its leading event:
    `UnitInvalidated` in R5, and `ReleasePrepared` in R3.
  - The output names both references in `NAME@VERSION` form. For R8, it names the reference and
    both full 64-character head digests.
  - Ours' `.lock` is removed before the merge. Afterwards every file under ours' store root is
    byte-identical, no file is added, `.lock` does not exist, and `Store.write_locked` was never
    entered during the merge.

## Requirements

### Identity of operation targets

- **R1 — A lifecycle command never lands on another version.**
  `test_lifecycle_replay_refuses_another_version[<op>]`. Shared history brings `x@1` to `<op>`'s
  pre-state. Theirs runs `<op>` on `x`. Ours opens its own deviation on `x@1`, supersedes it,
  specifies `x@2`, and brings `x@2` to the same pre-state with its own deviation ids and pack
  versions (no `pack.version-reused`). The merge is refused naming `x@1` and `x@2`.

  | `<op>` | Pre-state on both versions |
  |---|---|
  | `freeze` | specified |
  | `bind-pack` | frozen with a label-only pack, no binding |
  | `mark-ready` | acceptance-frozen |
  | `start` | ready |
  | `mark-done` | in-progress |
  | `publish` | done-pending-acceptance |
  | `respecify` | blocked, its deviation rejected and closed |
  | `begin-reverify` | blocked with a recorded pack, its deviation rejected and closed |
  | `supersede` | blocked |
- **R2 — A rebind never lands on another version bound to the same inventory.**
  `test_rebind_replay_refuses_another_version_with_the_same_inventory`. Shared history publishes
  `x@1`. Ours supersedes it and publishes `x@2` bound to the same acceptance-pack inventory digest,
  so `rebind.stale-pack` passes on ours. Theirs rebinds `x@1` to a changed inventory that keeps
  every bound path, the executable roles and the identical effect declaration. The merge is
  refused naming `x@1` and `x@2`.
- **R3 — A release roster must be the recorded one.**
  `test_release_replay_refuses_a_different_roster`. Theirs prepares a release including published
  `x@1`. Ours has superseded `x@1` and published `x@2`. Refused at `ReleasePrepared`, naming `x@1`
  and `x@2`.
- **R4 — Same-version, self-contained and by-name-dependency replay still merge.** `# guard:`
  - `test_same_version_lifecycle_replays`: theirs takes `y@1` from specified to published while
    ours works on another unit. Exit 0, and `y@1` is published.
  - `test_incoming_supersede_and_successor_replay`: shared `x@1` is blocked. Theirs supersedes
    it, then specifies and freezes `x@2`; ours works on another unit. Exit 0, with `x@1`
    superseded and `x@2` frozen.
  - `test_by_name_dependency_on_another_version_still_replays`: `y` depends on `x`. Theirs
    publishes `y@1`; ours has superseded `x@1` and published `x@2`. Exit 0, and `y@1` is
    published (D-33: identity does not pin by-name dependencies).

### Identity of companion facts

- **R5 — An amendment never invalidates another version.**
  `test_amendment_replay_refuses_invalidating_another_version`. Shared history blocks `x@1` with
  an open deviation. Theirs adjudicates an amendment, so its `UnitInvalidated` names `x@1`. Ours
  supersedes the blocked `x@1`, leaving the deviation open, and specifies `x@2`, so `decide()`
  alone would accept the amendment and invalidate `x@2`. Refused at `UnitInvalidated`, naming
  `x@1` and `x@2`.
- **R6 — Every companion is checked, not only the first.**
  `test_amendment_replay_checks_every_source_companion`. Shared history holds `x@1`, a dependent
  `z@1`, and an open deviation on `x@1`. Ours supersedes `z@1` and specifies a dependent `z@2`,
  leaving `x@1` unchanged. Theirs amends, invalidating `x@1` and `z@1`. Refused at the
  `UnitInvalidated` for `z`, naming `z@1` and `z@2`.
- **R7 — Companions are matched by kind and subject, not position.** `# guard:` for its state
  assertions.
  - `test_redundant_block_replays_without_its_companion`: ours and theirs each open a different
    deviation on shared `x@1`. Exit 0, and both deviations are open. The records the merge adds
    hold no `UnitBlocked`.
  - `test_amendment_reaching_an_ours_only_dependent_replays`: ours specifies `a`, which depends
    on `x` and sorts before it. Theirs amends a deviation on `x@1`. Exit 0, and both `x@1` and `a`
    are invalidated.

### Anchors

- **R8 — An anchor needs the head its source promoted.**
  - `test_anchor_replay_refuses_when_ours_added_evidence`: theirs anchors `u@v1` under CI run
    `ci-7`, and ours recorded further evidence for `u@v1`. Refused at `AnchorAdvanced`, naming
    `u@v1`, theirs' promoted head and ours' head.
  - `test_anchor_replay_accepts_a_superseded_versions_equal_head` (`# guard:`): the two sides fork
    before ours supersedes `u@v1`. Theirs anchors `u@v1` by exact reference; ours supersedes
    `u@v1` and adds no evidence to it. Exit 0 (D-26).
- **R9 — A legacy anchor is replayed against the version its source meant.**
  `test_legacy_anchor_replays_against_its_source_version`. Theirs commits a coherent
  legacy-shaped `AnchorAdvanced` record, with payload `{"unit_name": "u"}` and run id
  `legacy-ci-1`, while its current version is `u@v1` (a hand-built wire record, as the existing
  anchor regressions do). Ours has superseded `u@v1`, leaves its head unchanged, and records
  evidence on `u@v2`. Exit 0. `u@v1` is anchored at its head with run claim `legacy-ci-1`, and
  `u@v2`'s anchor is unchanged.
- **R10 — A replayed CI anchor keeps its run claim, including for evidence in the same merge.**
  `test_replayed_ci_anchor_keeps_its_run_claim` (`bd4.4.6`). Anchor mode `ci`. Theirs records
  GREEN for `u@v1` through the harness, after a RED in shared history, and then anchors it under
  CI run `ci-7`. Ours specifies an unrelated unit. The merge runs through `merge-git-stages` with
  no CI variable set. Assertions:
  - `u@v1`'s `anchor_run_id` is `ci-7`, and its anchor equals its head.
  - After `generate`, `check --ci` exits 0.
  - `status` prints `anchor: u@v1 advanced by workflow run ci-7`.

### Claims

- **R11 — Each replayed record keeps its source claims.**
  `test_replayed_records_keep_source_actor_run_and_time`. Theirs writes with actor
  `theirs-agent`: one command under CI run `ci-9`, one with no run claim, and one command that
  emits companions. Ours writes a later record. The merge runs with `--actor merger` under
  `GITHUB_RUN_ID=merge-run`. Assertions:
  - Every replayed leading record and companion has the `actor`, `run_id` and `recorded_at` of
    the source record it corresponds to. The empty run claim stays empty.
  - A preserved source time earlier than ours' latest record is accepted.
  - The final `MergeRecorded` record carries `merger`, `merge-run` and a `recorded_at` different
    from every source record's.
- **R12 — Generated companions and incoming merge records.**
  - `test_inserted_companion_does_not_shift_source_claims`: R7's dependent scenario, with
    distinct source and merge claims. The `UnitInvalidated` for `x@1` keeps its source's actor,
    run claim and recorded time. The one for `a`, which the source never had and which comes
    first in the regenerated batch, carries the merge's.
  - `test_incoming_merge_record_keeps_its_source_claims`: theirs does its own work, then merges a
    third store by replay (one level). When ours merges theirs, that `MergeRecorded` keeps theirs'
    actor, run claim and recorded time.

### Source context and unchanged paths

- **R13 — Source references come from the full source history.** `# guard:`
  - `test_source_reference_uses_provenance_skipped_records`: theirs first does unrelated work,
    then merges ours by replay. Ours' supersede of `x@1` and specification of `x@2` therefore sit
    in a block a later replay skips. Theirs then freezes `x` by bare name. Merging theirs into ours
    exits 0 by replay, with `x@2` frozen. Folding only the replayed suffix would resolve `x@1` and
    refuse.
  - `test_source_reference_uses_pre_log_baseline`: a legacy store is upgraded so that `x@1`
    exists only in its pre-log baseline. Ours does unrelated work and theirs freezes `x`. Exit 0,
    with `x@1` frozen.
- **R14 — The Git stage adapter refuses the same way.**
  `test_git_stage_merge_refuses_a_version_mismatch` and
  `test_git_stage_merge_refuses_an_unequal_anchor_head`. These are R1's `mark-ready` case and R8's
  refusal, run as a conflicted `git merge` recovered with `merge-git-stages`. Same refusal
  assertions, and the Git index is unchanged.
- **R15 — Fast-forward and no-op are unchanged.** `# guard:`
  `test_fast_forward_copies_source_records_byte_for_byte` and `test_no_op_merge_writes_nothing`.
- **R16 — The documents state the new claim.**
  `test_merge_docs_state_that_replayed_records_keep_source_claims`. Matching ignores runs of
  whitespace. `docs/quickstart.md` § Concurrent branches and `docs/claim-boundary.md`
  § Merging stores each contain:
  - `replayed records keep their source records' actor, run claim and recorded time`;
  - `the merge record carries the merge's actor claim`.

  Neither section still says the replayed records carry the merge's actor claim.

## Ground rules

- What `decide()` accepts, refuses and emits does not change, and neither does the folded
  snapshot shape. No event, log or snapshot format change (D-33). A pure helper that derives
  source references may live in `core`, under the import contracts.
- Derive each source reference from the source chain folded record by record from GENESIS or its
  pre-log baseline. Include the records the provenance filter leaves out of the replayed suffix,
  and take command batch boundaries before filtering.
- Match source and replayed facts by event kind and subject, never by position or batch length.
- Keep D-25's behaviour for open-in-CI exceptions. Keep every refusal atomic: no write and no lock
  until every check has passed.
- `tests/test_anchor_unit_ref_regressions.py` has a legacy anchor merge with newer evidence on
  ours, around line 316. It asserts the pre-D-33 behaviour that R8 forbids. Update it to expect
  the refusal. It is not part of this pack.

## Out of scope

- Acceptance-pack epoch identity within one version (D-33 Scope): a command replaying under a
  different pack of the same version is not refused here.
- Nested `MergeRecorded` block boundaries (`vouch_mk2-bd4.4.11`).
- Exact references in new events (deferred, D-30).
- Anchoring a claimed anchor that lags its head (`vouch_mk2-bd4.4.4`).
