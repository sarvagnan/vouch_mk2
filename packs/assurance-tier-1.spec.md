# Acceptance pack — unit `assurance-tier@1` (pack/1)

**Owner:** acceptance owner (Fable, on the user's behalf). **Implementer:** Codex. The
implementer may NOT edit this file or `tests/test_assurance_tier.py`. A finding that this
pack is wrong is a DEVIATION (`vouch-mk2 raise-deviation`), adjudicated by the owner.
Decision record: DECISIONS.md D-12. Closes 0.6 DEFECTS D045.

## Intent

The 0.6 tier lived in two places (config + ledger) that could disagree; the weaker one
silently won. mk2 gives the tier ONE authority — the snapshot — and exactly ONE mechanical
consumer: tier-scoped evidence requirements. Everything else the tier matrix names is
reported as tier-scoped organizational NOT_CHECKED, never implied checked.

## Requirements (each bound to a test)

R1 `Snapshot.tier: Tier` — closed enum `LIGHTWEIGHT | STANDARD | HIGH` (wire values
   `lightweight|standard|high`), default `LIGHTWEIGHT`; codec round-trips; canonical form
   changes accordingly (bump the snapshot format only if the codec's strictness requires it;
   old canonical bytes WITHOUT a tier field must NOT decode — no silent default on read).
   → `test_tier_is_snapshot_state_defaulting_to_lightweight_and_round_trips`
R2 `SetTier(tier)` command in `vouch_mk2.core`; `TierSet` event; recorded and
   table-visible; raising always accepted.
   → `test_set_tier_is_a_recorded_command_and_raising_is_free`
R3 Lowering is refused with `tier.lowering-refused` iff any unit is beyond `SPECIFIED`
   (i.e. status not in {SPECIFIED}); a snapshot with only specified units may lower.
   → `test_lowering_tier_is_refused_once_work_is_committed`
R4 (guard) No `tier`/`assurance_tier` key in `.vouch/config.json` — ever.
   → `test_no_tier_field_exists_in_project_config`
R5 `MK2.tier_requirements` carries the 13-row matrix as first-class mk2 IR with the same
   `control_id` set as `VOUCH_06`. → `test_tier_matrix_is_first_class_mk2_ir_and_matches_06`
R6 `check.evidence-anchors` (artifact check): under `AnchorMode.NONE`, verdict is
   NOT_APPLICABLE at LIGHTWEIGHT but **FAIL** at STANDARD/HIGH, detail naming the tier.
   → `test_standard_tier_makes_missing_anchoring_a_fail_not_not_applicable`,
     `test_lightweight_tier_keeps_anchoring_not_applicable_under_mode_none`
R7 New mechanical standing check `check.tier.evidence-required`
   (`mk2.CHECK_TIER_EVIDENCE_REQUIRED`): at STANDARD/HIGH every PUBLISHED unit must have an
   evidence head; FAIL names the offending unit(s); at LIGHTWEIGHT → NOT_APPLICABLE.
   → `test_standard_tier_requires_a_chain_for_every_published_unit`
R8 New mechanical standing check `check.tier.high-attestation`
   (`mk2.CHECK_TIER_HIGH_ATTESTATION`): at HIGH, every PUBLISHED unit's head must be
   anchored (`anchored != "GENESIS"`, i.e. anchored to a real sequence) AND
   `formal_certificate_digest` must be non-empty; FAIL detail mentions "anchor" or
   "certificate"; below HIGH → NOT_APPLICABLE.
   → `test_high_tier_requires_anchored_chains_and_a_formal_certificate`
R9 New organizational row `org.tier-controls`: NOT_CHECKED; detail lists, for the
   snapshot's tier, the matrix controls mk2 does not mechanically check (at least
   mutation-testing, security-evidence, external map delivery), naming the tier.
   → `test_unchecked_matrix_controls_appear_as_tier_scoped_organizational_rows`
R10 `SystemMap.tier`, `StatusFacts.tier` (wire strings) and the agent context mention the
   tier. → `test_tier_appears_in_status_facts_map_and_context`

## Ground rules (inherited from unit #1)

- Registry-completeness meta-check must still pass (new check ids registered in the IR).
- Formal layer: `SetTier` must be added to the universe generators; M1–M10 must hold;
  add M11 "tier never lowers once work is committed" with a private witness W11 if the
  relation supports it; recompute certificates. If universes grow, note the new counts.
- Doc binding: bind the D-12 text / matrix to the checks that consume it.
- Migration (`migrate.py`): 0.6 config tier maps to `Snapshot.tier` ONLY if config and
  ledger agree; if they disagree the candidate is REFUSED with a report row (never
  silently pick one — that IS D045).
- Pyrefly strict clean; import-linter contracts kept; all pre-existing tests green.
