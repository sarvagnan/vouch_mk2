Self-hosted unit **ingress-invariants@1** — implementation task under DECISIONS.md D-11 (read it). Repo: /Users/sarvagnan/Documents/project_blueprint/vouch_mk2.

## The contract you implement against is FROZEN
- Acceptance pack = THIS spec + `tests/test_ingress_invariants.py` (15 tests, all currently RED). Their digest is bound to unit ingress-invariants@1 in the repo's dogfood store (`.vouch/`).
- **You may NOT edit tests/test_ingress_invariants.py or this spec.** If you believe the pack is WRONG (a test asserts something the system should not do), STOP on that item and say so in your final message with the reason — that becomes a DEVIATION adjudicated by the acceptance owner. Do not "fix" the test.
- Do not touch `.vouch/`, do not run `vouch-mk2 formal --record`, do not commit to git, do not touch beads except closing your own task vouch_mk2-4it.3.
- All 546 pre-existing tests must stay green; the README "N tests" binding must be updated to the new collected total; `uv run pyrefly check` 0 errors; `uv run lint-imports` kept (new pure helpers go in ir/ or core/; nothing I/O-capable enters the pure contracts).

## Diagnosis (why these holes exist)
Vacuity/coherence guards were implemented at the COMMAND layer (PrepareRelease refuses empty rosters; decide() refuses RED+PASSED) but not at every INGRESS: the snapshot decoder, the migration candidate, store.commit, and the evidence chain walk each admit state the commands would refuse. Fabricated or migrated state slips past. The remedy is structural, not a scatter of patches:

## Design (follow exactly)
1. **One central snapshot-invariant validator** in a NEW pure module `src/vouch_mk2/core/invariants.py`: `validate_snapshot(snapshot) -> tuple[Violation, ...]` (reuse ir.schema.Violation). It checks: every release roster non-empty; roster refs unique (exact NAME@VERSION) per release; closure roster_snapshot rows unique by name and its name-set == roster name-set; at most one non-superseded record per unit name; every UnitRef in deviations/rosters/evidence heads resolves; identifier grammar (below) for every unit name/version, deviation id, release id, pack version, evidence unit refs. Apply it at ALL THREE ingresses: `decode_snapshot` (any violation → DecodeError naming it), `migrate.plan_migration` (violations → refusals, so apply_migration cannot reach a target — this closes the P1 "migration commits failing candidates" finding for STRUCTURAL failures; standing-check FAILs that are genuine 0.6 data defects remain approvable-and-reported per D-8), and `Store.commit` (violations → StoreError, nothing written; also make Store.commit call it BEFORE the CAS lock so a bad snapshot never even takes the lock).
2. **Identifier grammar** in the same module: `valid_identifier(text) -> bool` — non-empty, ≤ 200 chars, no control characters (< 0x20, 0x7f), no leading/trailing whitespace, printable. Enforce in decide() for SpecifyUnit (name/version), OpenDeviation (id), PrepareRelease (id) → refuse with code "identifier.invalid"; and in the validator (hence codec/migrate/commit).
3. **Empty roster at the completion gate**: `_gate_release_completion_units` / roster-resolves refuse an empty roster with code "gate.release.roster-empty" (defense in depth even though the validator already refuses it upstream — a gate over zero members must never pass vacuously).
4. **Write-side artifact bound**: Store enforces `max_artifact_bytes` BEFORE creating any temp file in `_initialize_layout`, `commit` (state bytes), `put_object`, and `write_generated` → StoreError containing "bound"; refuse `max_artifact_bytes < 1` at construction with StoreError. Add a small test in tests/test_store.py mirroring the pack's.
5. **Shared coherence predicate**: move `_evidence_kind_outcome_coherent` from decide.py to `core/invariants.py` as public `evidence_kind_outcome_coherent(kind, outcome)`; decide() imports it; verify.py's `_check_evidence_chains` calls it for EVERY walked event and FAILs with detail containing "incoherent outcome" naming the unit and sequence.
6. **Exact refs**: `check_release_bound_units_rostered` compares `UnitRef`s (a release-bound record is rostered iff SOME release's includes contains its exact ref). Audit `checks/standing.py` and `projections/next_actions.py` for any other name-only comparison in a version-bearing context and fix likewise (report what you changed).
7. **Tuple identity in the map codec**: `_require_unique` for map units must key on the (name, version) TUPLE, not f"{name}@{version}"; same for any other concatenated key you find in codec.py.
8. **Markdown escaping in agent context**: `projections/context.py` escapes identifiers when interpolating into markdown (at minimum: backslash-escape `\ ` `[ ] ( ) * _ \` # < >` and replace newlines) via one helper `_escape_md(text)`. Do NOT weaken the grammar to compensate; both layers.
9. **build_lock**: `os.lstat` the package ROOT (`source_root/vouch_mk2`) and refuse a symlink with StoreError containing "symlink" BEFORE any walk; keep the existing per-entry refusals.
10. **init sidecar**: in `init._plan_file`, if `<target>.vouch-suggested` exists: equal bytes → status UNCHANGED (no write); differing bytes → refuse the whole init with InitError naming the sidecar (never overwrite user content; a numbered sidecar is NOT wanted — refusing is the honest behavior). This must be checked in the PREFLIGHT phase before any write.
11. **IR**: `validate()` refuses two transitions sharing (source, command) with different targets — code "transition.ambiguous-command"; the existing decide `_index()` may then assert uniqueness rather than silently keep the last.
12. Update docs/defect-closure.md ONLY for D039 (now (a): cite tests/test_ingress_invariants.py::test_p1_migration_refuses_structurally_invalid_candidate_before_touching_target) — leave the other open dispositions alone; the doc-binding test must stay green.

## Definition of done
`uv run pytest` fully green INCLUDING all 15 tests in tests/test_ingress_invariants.py; pyrefly 0 errors; lint-imports kept; the formal run still passes (`uv run vouch-mk2 formal` — if the identifier grammar or validator changed the reachable relation, report the new counts; do NOT re-record the certificate — the acceptance owner does that at publish). Close bead vouch_mk2-4it.3. Final message: files, per-item notes, anything you believe is a PACK defect (deviation candidates), gate tails, new collected count.


## Amendment — pack/2 (DEV-ingress-01, adjudicated AMEND by the acceptance owner 2026-08-15)

Item 4 (bound enforced at initialization) contradicted the original test's 64-byte bound: the canonical empty state is 128 bytes. The IMPLEMENTATION was right to refuse; the TEST was wrong. Amended: the write-side test uses a bound the empty store fits under (256/257), and a new corollary test asserts `Store.init` itself refuses a bound smaller than the initial state. Prior version immutable; this diff is the amendment record.


## Amendment — pack/3 (DEV-ingress-02, contract-conflict, adjudicated AMEND by the acceptance owner 2026-08-16)

Item 1 required closure roster-snapshot rows to be "unique by name and its name-set == roster
name-set". That contradicts D-9: a roster may legally hold `u@v1` and `u@v2`, and `RollBack` on
such a roster produced a snapshot the validator refused — a false refusal on a legal state.
Amended contract:

1. `ClosureRecord.roster_snapshot` rows are `(name, version, status)`; uniqueness is on the
   exact `(name, version)` ref, and the set of refs must equal the roster's `includes`. The
   codec (state + system map), migrator (0.6's 2-tuple rows migrate with the version taken from
   the matching roster include; an unmatched row is a refusal), standing checks, and D-8's
   mapping follow.
2. The identifier grammar additionally forbids `@` in unit NAMES and unit VERSIONS (only), so
   the canonical `NAME@VERSION` label is injective wherever it is parsed or printed. Pack
   versions and other identifiers are unaffected.

Acceptance tests added: `test_p2_closure_snapshot_is_ref_keyed_and_survives_two_version_roster`,
`test_p2_at_sign_refused_in_unit_name_and_version`. Prior versions immutable.


## Amendment — pack/4 (DEV-ingress-03, pack-defect, adjudicated AMEND 2026-08-16)

Two TYPING defects in the frozen test file, both the pack author's: a pack/1 test still built a 2-tuple closure row (now `(name, version, status)` under pack/3), and a pack/3 test dereferenced `Snapshot.release()` without narrowing. Behaviour unchanged; the D-1 Pyrefly-clean ground rule blocked publish. Prior versions immutable.
