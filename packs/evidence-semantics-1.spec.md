# Acceptance pack — unit `evidence-semantics@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_evidence_semantics.py`.
Decision: DECISIONS.md D-17. Adoption reviews' finding #1 (both reviews). Bead: see epic bd5.

## Requirements (each bound to a test)

R1 `EvidenceEvent.pack_digest: str` — the **test-set digest** of the unit's current acceptance pack at
   recording (`""` when the unit is label-only). The pack inventory gains a per-entry `role` (`test` |
   `spec`) at `pack_format` 2; `freeze`/`bind-pack`/`rebind-pack` take `--pack-test-file` (role `test`,
   repeatable) alongside `--pack-file` (role `spec`), and emit `pack_format` 2 whenever any
   `--pack-test-file` is given, `pack_format` 1 otherwise. The **test-set digest** is: for `pack_format`
   2, the digest over the canonical encoding of the entries whose role is `test`; for `pack_format` 1
   (every pack frozen before this amendment), **exactly the inventory digest** — unchanged behaviour, so
   no existing binding is silently weakened and evidence recorded either side of this change agrees.
   The evidence codec carries the field (a new field means a new evidence format; see the amendments
   below for what happens to legacy objects); `record-evidence` fills it from the snapshot under the
   lock; `decide(RecordEvidence)` refuses a digest that does not equal the unit's current test-set digest
   (`evidence.pack-mismatch`). → `test_evidence_binds_the_pack_digest_it_ran_against`,
   `test_freeze_with_test_files_records_roles_and_a_test_set_digest`

R2 `record-evidence` before `acceptance-frozen` → refused `evidence.no-pack`, nothing recorded; allowed
   in every later status incl. published/release-bound (late evidence). → `test_record_evidence_is_refused_before_the_pack_is_frozen`, `test_late_evidence_on_a_published_unit_is_allowed` (guard)
R3 New DoD gate `gate.dod.current-pack-green` (IR-registered; listed in the `harness-recorded-red-green`
   cell's `mechanical_checks` at standard/high): at standard+ `Publish` is refused unless the unit's
   chain, restricted to records whose `pack_digest` equals the unit's CURRENT digest, contains a RED
   followed later by a GREEN and its LATEST such record is GREEN (a `refactor` after green keeps it
   green; a red after green breaks it). Lightweight: not applied. → `test_red_only_evidence_cannot_publish_at_standard`,
     `test_red_then_green_publishes_and_green_then_red_does_not`, `test_evidence_for_an_old_pack_does_not_count_after_amendment`,
     `test_lightweight_keeps_publish_free_of_the_green_gate` (guard), `test_ir_cell_lists_the_green_gate`
R4 `check.evidence-chains` (artifact) additionally reports, at standard+, a published/release-bound
   unit whose latest current-pack record is not GREEN as FAIL naming the unit; other verdict rules
   unchanged. → `test_check_reports_a_published_unit_whose_latest_current_pack_evidence_is_not_green`
R5 Re-versioning: `SpecifyUnit(name, version)` is accepted when every existing record for `name` is
   superseded and `version` differs from all of them (refused `unit.exists` otherwise); the new record
   is the active one; evidence heads are keyed by ref so the new version starts with no chain and the
   old version's chain stays attached to the superseded record (adjust `evidence_head(name)` callers
   to resolve the ACTIVE record; the codec/EvidenceHead may need `unit_version` — if so that is state
   format 3 with an upgrader deriving it from the then-only record per name; say so in DONE).
   → `test_specify_accepts_a_name_whose_only_records_are_superseded`, `test_reversioned_unit_starts_with_an_empty_evidence_chain_keyed_by_ref`
R6 Docs: quickstart §3/§4 and claim-boundary state the rule (`gate.dod.current-pack-green`; "the latest
   evidence for the current pack must be green"); the IR cell's coverage for
   `harness-recorded-red-green` says exactly what is mechanical now. → `test_quickstart_and_claim_boundary_state_the_rule`

## Ground rules
- Formal: universes gain the new refusal paths; M1–M11 hold; certificate recomputed; universe sizes
  explained in DONE (a shrink must be explained — dropping a command is a coverage decision).
- Do not touch `.vouch/`: the owner re-records/upgrades the dogfood store (its published units'
  chains predate `pack_digest`; the owner will handle them explicitly — say in DONE what the
  upgrader does with legacy evidence objects: they are content-addressed and immutable, so the
  honest path is: legacy records decode under the OLD evidence format and count as
  `pack_digest == ""`; the check reports "pre-D-17 evidence" by name at standard+ rather than FAIL).
- Every pre-existing test green (adjust coherently); new test functions for non-pack behavior;
  pyrefly 0; import-linter kept; README count is the owner's.

## Requirements added at pack/3

R7 **Prose-only amendments keep RED** (D-17 amendment, 2026-08-17). Because evidence binds the test-set
   digest, amending only the spec file of a pack leaves prior evidence valid for the new pack version;
   changing any file whose role is `test` still voids prior evidence for that unit. A `pack_format` 1
   pack keeps whole-inventory behaviour. → `test_prose_only_amendment_preserves_current_pack_evidence`,
   `test_changing_a_test_file_still_voids_prior_evidence`,
   `test_legacy_pack_format_one_binds_the_whole_inventory`

R8 **`rebind-pack`** (D-14 amendment, 2026-08-17) — for a `published` or `release-bound` unit whose bound
   pack files must change ONLY to adapt to a changed shared interface. Records old digest → new digest
   with a stated non-empty `--reason` (refused empty: `rebind.reason-required`), replaces the unit's pack
   inventory, carries the evidence chain forward (no `respecify`, no new pack version, chain untouched),
   and sets `Unit.pack_binding = "rebound"`. Refused below `published` (`rebind.not-published`).
   `check.evidence-chains` does not pass for a rebound unit until a fresh **GREEN** exists on the new
   test-set digest — a RED is NOT required and must not be demanded. `check.pack-bound` lists rebound
   units by name in its detail ("rebound after publication: …"), like late binding, and is FAIL at
   `high` unless every rebound unit has that GREEN. →
   `test_rebind_pack_replaces_the_inventory_and_requires_a_fresh_green`,
   `test_rebind_pack_is_refused_before_publication`, `test_rebind_pack_requires_a_reason`,
   `test_rebind_pack_is_listed_by_name_in_pack_bound`

## Amendments (recorded; the requirement text above is edited in place)

- **pack/2 (DEV-es-01, pack-defect, adjudicated amend).** R1 originally said a new evidence format must
  "bump and refuse the old", contradicting the ground rule that legacy evidence objects decode as
  `pack_digest == ""`. Raised by the implementer. Resolution, now written into R1 and the ground rules:
  the evidence codec is versioned; new records carry `pack_digest` and the new format number; legacy
  (pre-D-17) records remain immutable, decode as `pack_digest == ""`, and are reported at standard+ as
  "pre-D-17 evidence" naming the unit rather than refused or counted as current-pack green. No refusal
  of old objects.
- **pack/3 (DEV-es-02, contract-conflict, adjudicated by the USER 2026-08-17, amend).** The pack bound
  evidence to the whole pack inventory, so a prose-only amendment voided RED and unit #9 could not
  publish through its own gate (batch-A finding O-1); and adapting a published unit's bound pack tests
  to a changed shared type had no sanctioned repair (finding O-3). The user amended D-17 (evidence binds
  the pack's executable entries) and D-14 (`rebind-pack`). R1 is edited in place and R7/R8 added above.
