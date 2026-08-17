# Acceptance pack — unit `evidence-semantics@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_evidence_semantics.py`.
Decision: DECISIONS.md D-17. Adoption reviews' finding #1 (both reviews). Bead: see epic bd5.

## Requirements (each bound to a test)

R1 `EvidenceEvent.pack_digest: str` — the unit's `acceptance_pack_digest` at recording (`""` when the
   unit is label-only); the evidence codec carries it (evidence objects are CAS: a new field means a
   new evidence format — bump and refuse the old with a clear message; existing chains in the dogfood
   store are the owner's problem, see ground rules); `record-evidence` fills it from the snapshot under
   the lock; `decide(RecordEvidence)` refuses a digest that does not equal the unit's current one
   (`evidence.pack-mismatch`). → `test_evidence_binds_the_pack_digest_it_ran_against`
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

## Amendment pack/2 (DEV-es-01, pack-defect, adjudicated amend)

R1 said a new evidence format must "bump and refuse the old"; the ground rule said legacy evidence
objects decode as `pack_digest == ""` and are reported by name. Raised by the implementer. R1 now
reads: the evidence codec is versioned — new records carry `pack_digest` (and the new format
number); legacy records (pre-D-17) remain immutable, decode as `pack_digest == ""`, and are
reported at standard+ as "pre-D-17 evidence" naming the unit rather than refused or counted as
current-pack green. No refusal of old objects.
