# Acceptance pack — unit `event-log@1` (pack/1)

**Owner:** the acceptance owner. **Implementer:** Codex — may NOT edit this file or
`tests/test_event_log.py`. Decision: DECISIONS.md **D-16** and its dated correction (the correction
moves `event_head` to **state format 4**, because `evidence-semantics@1` took format 3).
Bead: `vouch_mk2-179.2` under epic `vouch_mk2-179`.

## Requirements (each bound to a test)

R1 **Event records.** Every event that `apply()` folds becomes a content-addressed record under a new
   `ArtifactKind.EVENT` digest domain. Canonical JSON fields: `event` (the event type name), `payload`
   (the event's own canonical fields), `recorded_at`, `prev` (the previous record's digest, or
   `"GENESIS"`), `revision` (the snapshot revision the record produces), `actor` (a CLAIM, see R5),
   `run_id` (a CLAIM, empty when absent), and `pack_digest` for pack-bearing events (empty otherwise).
   A new digest domain means identical payloads never alias another kind.
   → `test_event_records_are_content_addressed_with_the_declared_fields`

R2 **`Snapshot.event_head`** holds the digest of the latest record (`""` only before the first logged
   event). **State format 4**; the 3→4 upgrader sets the genesis marker and records, as the log's own
   FIRST event, that history before the store's current revision N is not in the log — the recorded
   text names the range (`pre-log revisions 1..N: git history only`). No invented state: the upgrader
   never fabricates the events it did not witness. → `test_state_format_4_upgrader_records_the_pre_log_gap`

R3 **Atomicity.** Records are placed objects-first in the SAME commit as the snapshot: there is never a
   committed snapshot whose `event_head` names an object the store does not hold, and a crash between
   the two must not leave one without the other.
   → `test_no_committed_snapshot_without_its_event_records`

R4 **`check.event-log`** (mechanical, artifact check, IR-registered): the chain from `event_head` back to
   genesis decodes, is unbroken (each record's `prev` is its predecessor's digest and `revision`
   decreases by exactly one per step), and **replaying it through `apply()` from the empty snapshot
   reproduces the committed snapshot**. The comparison is byte-for-byte on the canonical encoding, with
   exactly one defined exception: `event_head` itself, which the replay sets to the digest of the last
   record replayed (it cannot appear inside the record it names). A broken link, a missing record, or a
   replay that diverges is FAIL naming the revision at which it diverged.
   → `test_check_event_log_replays_the_chain_and_reproduces_the_snapshot`,
     `test_check_event_log_fails_on_a_broken_link`,
     `test_check_event_log_fails_when_replay_diverges_from_the_committed_snapshot`

R5 **Actor and run-id are CLAIMS, never verified.** The actor is whatever the caller asserts: `--actor`
   on every lifecycle verb, defaulting to the OS user; the run id is read from the CI environment
   (`GITHUB_RUN_ID`, and the equivalent for other providers) when present, empty otherwise. Vouch
   records both and verifies STRUCTURE and REPLAY only — never identity. `docs/claim-boundary.md` says
   so in the same words, and no check may report an actor as verified.
   → `test_actor_defaults_to_the_os_user_and_is_overridable`,
     `test_run_id_is_captured_from_the_ci_environment_when_present`,
     `test_claim_boundary_states_actor_and_run_id_are_claims`

R6 **The log is authoritative; the snapshot is a derived cache.** `check.event-log` is the mechanism that
   says so, and `docs/claim-boundary.md`'s row for chronology stops citing git history and cites the log.
   The quickstart sentence about "the recorded order of Vouch's own events" becomes true and says how it
   is verified. → `test_docs_state_the_log_is_authoritative_and_the_snapshot_derived`

R7 **Cost is measured, not assumed.** `scripts/bench_store.py` is re-run and the per-event object cost
   and the store growth are recorded in `docs/performance.md` as real measured numbers.
   → `test_performance_doc_records_measured_event_log_cost`

R8 **The store remembers the highest state format it has ever held** (a monotonic high-water mark
   recorded in the store). `upgrade` refuses a document whose declared `state_format` is lower than
   that mark, naming both numbers and telling the operator to restore the correct state from the
   store's git history. This closes the re-baselining route that remains open when a caller removes
   BOTH `event_head` and every event object: without a mark, such a store is indistinguishable from a
   genuine legacy store, and a fresh baseline would silently become the trust root for arbitrary
   state. What this does NOT claim: a store is a directory of files, so a caller who also removes the
   mark is outside any repository-local trust root — the backstop is the store's own git history, and
   docs/claim-boundary.md must say so rather than implying Vouch prevents what it only detects.
   → `test_a_format_downgrade_cannot_re_baseline_the_log`

## Ground rules
- Formal: the universes gain no new commands unless a refusal is added; M1–M11 hold; the certificate is
  recomputed; every count change is explained in DONE (a shrink must be explained).
- Do not touch `.vouch/` (the owner's store — the owner runs `upgrade` and the verbs), the README
  test-count line, or git.
- Every pre-existing test stays green — adjusted coherently, NEVER by weakening an assertion.
- New test functions for non-pack behaviour; pyrefly 0; import-linter contracts kept.
- `check --ci --project .` on the dogfood store must exit 0 for the owner when the unit is done, and it
  must also exit 0 when run FROM THE LOCK.

## Amendments (recorded; the requirement text above is edited in place)

- **pack/2 (DEV-el-01, pack-defect, adjudicated amend by the owner).** Raised by the implementer.
  R2's fixture simulated a "legacy" format-3 store by DOWNGRADING a format-4 store that already held
  event objects — which is exactly the re-baselining path the H1 review finding requires `upgrade` to
  refuse. No implementation could satisfy both. The fixture now builds a genuine legacy store from the
  owner-pinned format-3 golden, and the requirement that made the conflict visible is promoted to R8
  with a stronger rule than the review proposed: a monotonic state-format high-water mark, so the
  downgrade is refused even when `event_head` AND every event object have been removed. This
  amendment changes a test file, so RED is re-recorded.
