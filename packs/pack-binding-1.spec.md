# Acceptance pack — unit `pack-binding@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file, `tests/test_pack_binding.py`,
or `tests/fixtures/state-format/format2.golden.json`. Contradiction → DEVIATION. D-14. Bead bd4.6.

## Requirements (each bound to a test)

R1 `Unit.acceptance_pack_digest: str = ""` (64-hex or empty; ingress-validated); **state format 2**;
   `UPGRADERS[1]` sets `""` on every unit (nothing else); format-2 golden pinned by the owner
   (format-1 golden at format 2; core@v1/core@v0 bound with c…/d…, ui@v1 label-only).
   → `test_state_format_is_two_and_the_unit_carries_a_pack_digest`, `test_format1_upgrades_to_format2_with_unbound_packs`, `test_format2_golden_is_pinned`
R2 `PackInventory(entries: tuple[PackEntry,...])`, `PackEntry(path, sha256, size)` in core.model;
   `ArtifactKind.ACCEPTANCE_PACK`; `encode_pack_inventory`/`decode_pack_inventory` in the codec
   (canonical JSON `{"pack_format": 1, "entries": [...]}`, entries sorted by path — unsorted or
   duplicate paths REFUSED at encode and decode; relative POSIX paths, no `..`, no absolute; sha256
   64-hex; size ≥ 0); exported from `vouch_mk2.store`. → `test_pack_inventory_is_a_canonical_object_kind`
R3 `freeze` gains `--project DIR` (default cwd) and repeatable `--pack-file PATH` (relative to the
   project; a path outside the project, a symlink, or a missing file → exit 2, nothing committed);
   with files it builds the inventory, and commits the object ATOMICALLY with the snapshot
   (`Store.commit(snapshot, objects=…)`), setting the digest via a new field on `FreezeAcceptance`
   (`pack_digest: str = ""`). Without files → label-only (`""`), as today.
   → `test_freeze_with_pack_files_binds_an_inventory_object`, `test_freeze_without_files_is_label_only_and_bind_pack_binds_later`, `test_freeze_refuses_a_pack_file_outside_the_project_or_missing`
R4 New command `BindPack(name, pack_digest)` / verb `bind-pack --name --project --pack-file…`
   (event `AcceptancePackBound`): legal when the unit has a pack version and `""` digest, in any
   status; refused `pack.already-bound` once bound (write-once per pack version); refused
   `pack.no-version` on a unit without a pack version. `respecify` clears both. IR: register the
   command (CMD_BIND_PACK, machine entry as a non-transition command like set-tier), the event, and
   the reflected verb (LIFECYCLE_VERBS completeness test will enforce). Formal: universes get
   BindPack; M1–M11 hold; certificate recomputed; note counts.
   → `test_binding_is_write_once_per_pack_version_and_respecify_clears_it`
R5 `check.pack-bound` (mk2.CHECK_PACK_BOUND): standing check tier-scoped through the
   `acceptance-frozen-before-implementation` control's cells (`mechanical_checks` gains it at
   standard/high; lightweight cell → NOT_APPLICABLE "label-only pack"): at standard+ every unit
   with a pack version must have a digest → FAIL naming `NAME@V` otherwise, PASS if all bound.
   As an ARTIFACT check (verify.py) it additionally requires each bound object to exist and
   decode → FAIL "missing"/"undecodable". → `test_pack_bound_check_is_tier_scoped`, `test_bound_object_missing_from_store_is_a_fail`
R6 `check.pack-files-match` (mk2.CHECK_PACK_FILES_MATCH), artifact check: `check --project DIR`
   (new optional flag) → for every bound unit, every inventory entry's file under DIR must exist
   and hash to the recorded sha256 (size too) → PASS / FAIL naming paths (changed vs missing);
   without `--project` → NOT_APPLICABLE "no project root". → `test_pack_files_match_check_verifies_the_working_tree`
R7 Projections: `SystemMap` units carry `acceptance_pack_digest`; `StatusFacts.unbound_pack_units:
   tuple[str,...]` (refs with a pack version and no digest); context mentions binding state.
   Map format → 3 (consistency: same rule as before). → `test_pack_digest_appears_in_map_and_status`
R8 IR honesty: the `acceptance-frozen-before-implementation` standard/high cells list
   `check.pack-bound` (and `check.pack-files-match`) in `mechanical_checks`, coverage PARTIAL,
   residual mentions human attestation of "before implementation". → `test_ir_cell_claims_only_what_is_now_true`
R9 `docs/quickstart.md` freezes with `--pack-file tests/test_greeter.py` (both freezes) and its
   prose about hashing is rewritten by the OWNER (do not edit the quickstart prose yourself —
   only the two `freeze` command lines and the DEV-1 amend line if needed); `scripts/walkthrough.py`
   freezes with `--pack-file`. The quickstart's own executable test must stay green.
   → `test_quickstart_and_walkthrough_freeze_with_pack_files`

## Ground rules
- Do not touch `.vouch/`: the owner runs `upgrade` (1→2) and `bind-pack` on the five published units.
- Every pre-existing test green (adjust codec/format/golden-dependent tests coherently); Pyrefly strict; import-linter kept.
- New test functions for every non-pack behavior (path traversal, symlink, duplicate entries, map format 3 refusal of 2).
