# Vouch 0.6 defect-closure map

This is the acceptance map required by `IMPLEMENTATION-PLAN.md` §5 row 1 and §7.
The source was read from the 0.6 verification kit, including each entry's status,
resolution, and underlying defect class. Each source defect has one primary class
and exactly one disposition:

- **(a) regression test** — the cited test discriminates the retained defect class;
- **(b) unrepresentable** — a closed type, strict decoder, or exhaustive table removes
  the bad state or control path;
- **(c) out of scope** — the cited plan non-goal or decision deliberately removes the
  0.6 surface; or
- **(d) open** — mk2 does not yet have a discriminating closure witness.

“Out of scope” is not a claim that the old implementation was correct. It means the
surface on which that defect existed is absent by a recorded mk2 scope decision.

| id | title (short) | class | disposition (a/b/c/d) | mk2 evidence | note |
|---|---|---|---|---|---|
| D001 | First event parent sentinel unspecified | policy drift | (c) out of scope | `DECISIONS.md` D-8 | Methodology events are migration-report data, not mk2 snapshot state, so mk2 has no event-chain sentinel surface. |
| D002 | Methodology-event vocabulary undefined | policy drift | (c) out of scope | `DECISIONS.md` D-8 | The 0.6 methodology registry is deliberately not a runtime mk2 vocabulary. |
| D003 | Required release additions conflict with shipped history | terminal-semantics | (c) out of scope | `DECISIONS.md` D-8 | Migration drops the adoption-era map fields and maps the operational release claim itself; mk2 has no post-adoption backfill rule. |
| D004 | Instance schema cannot prove append-only history | vacuous/self-certifying checks | (c) out of scope | `DECISIONS.md` D-8 | Mk2 does not store the 0.6 methodology history whose schema attempted to self-certify append-only ordering. |
| D005 | “Newest” schema reference is semantic | policy drift | (c) out of scope | `DECISIONS.md` D-8 | Unit-definition and map schema references are deliberately dropped from the mk2 core snapshot. |
| D006 | Producer requiredness is a roster rule | vacuous/self-certifying checks | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal; `DECISIONS.md` D-8 | Mk2 has no definition-to-HTML producer join or Worksheet roster surface. |
| D007 | Schema bookkeeping lacks a class | policy drift | (c) out of scope | `DECISIONS.md` D-1 | Typed Python plus one strict decoder replaces the 0.6 JSON-Schema renderer/classification machinery. |
| D008 | Canonical stage is not a ledger field | stale generated artifacts | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal; `DECISIONS.md` D-6 | The canonical JSON projection emits current typed state directly and has no separately stored HTML stage field. |
| D009 | Template blanks have no JSON types | policy drift | (a) regression test | `tests/test_store_codec.py::test_release_criteria_must_be_object_or_null` | The strict codec refuses an arbitrary replacement for a nested typed value instead of accepting an unspecified JSON shape. |
| D010 | Stable-error addition conflicts with parallel arrays | policy drift | (c) out of scope | `DECISIONS.md` D-8 | Unit contract-definition fields are reported as intentionally dropped migration data, not mirrored into mk2 state. |
| D011 | Correspondence membership under-specified | vacuous/self-certifying checks | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 four-edition and HTML-map non-goals | Mk2 deliberately has no kit correspondence-map machinery or selector language. |
| D012 | Purpose rendering conflicts with leak rule | stale generated artifacts | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal | No HTML purpose element or annotation-leak checker ships in mk2. |
| D013 | Generator identity omits support assets | missing digest bindings | (a) regression test | `tests/test_lock.py::test_lock_binds_shipped_data_files_and_refuses_symlinked_packages` | Adding a shipped data/support file changes the lock digest, and a symlinked package dependency is refused. |
| D014 | Standard gate needs undeclared Worksheet input | fail-opens | (c) out of scope | `DECISIONS.md` D-8 | Worksheet risk fields and legacy `gate_results` are deliberately outside the mk2 snapshot rather than silently treated as checked. |
| D015 | Upgrade indistinguishable from map adoption | policy drift | (c) out of scope | `DECISIONS.md` D-8 | Tool upgrades and methodology adoption are not represented as mk2 methodology events. |
| D016 | Release cannot bind bundle inventory | missing digest bindings | (a) regression test | `tests/test_bundle.py::test_corrupt_bound_bundle_inventory_fails_with_release` | A release-bound inventory or member mutation fails the artifact check. |
| D017 | Release enum cannot persist invalidation | terminal-semantics | (a) regression test | `tests/test_decide_releases.py::test_amendment_invalidates_release_and_second_amendment_is_noop` | Amendment impact persists `invalidated` and makes repeated invalidation idempotent. |
| D018 | Scalar blocker loses multiple deviations | terminal-semantics | (b) unrepresentable | `src/vouch_mk2/core/model.py` | `Snapshot` stores deviations only and `blocking_deviations()` derives the complete typed `UnitRef` blocker set, so no scalar or blocker array can drift. |
| D019 | Kit revision supersedes locked prose shapes | policy drift | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 four-edition non-goal | Mk2 does not maintain four locked prose editions that can lag a kit schema revision. |
| D020 | Uniform terminal prose contradicts claim shapes | terminal-semantics | (a) regression test | `tests/test_decide_releases.py::test_i3_inversion_protective_closure_never_blocked_by_contamination`; `tests/test_decide_releases.py::test_completion_happy_path_rechecks_all_gates` | Protective escape and completion claims exercise different gates and records. |
| D021 | Supersession semantics diverge across surfaces | terminal-semantics | (a) regression test | `tests/test_decide_releases.py::test_supersession_retain_policy_does_not_change_release_state` | The only exposed policy is retain, and the test pins its release-side effect. |
| D022 | Closure, deviation, and event shapes drift | policy drift | (a) regression test | `tests/test_store_codec.py::test_release_invalid_trigger_kind_is_refused`; `tests/test_decide_gates.py::test_stale_name_version_binding_is_refused` | Closed trigger kinds and version-exact unit bindings discriminate the retained shape changes; tooling events are absent by D-8. |
| D023 | Correspondence can pass without evaluation | vacuous/self-certifying checks | (a) regression test | `tests/test_checks_standing.py::test_artifact_not_applicable_without_ir_rule_is_guarded`; `tests/test_checks_standing.py::test_mislabeled_mechanical_evaluator_fails_under_registry_key` | Neither undeclared non-applicability nor an evaluator returning the wrong registered identity can compose to pass. |
| D024 | Lock depends on shared temp namespace | coupled writes | (d) open | Proposed test `test_store.py:test_project_local_lock_survives_distinct_tmpdirs` | The store uses a root-relative `.lock`, but no test launches contending writers with distinct `TMPDIR` values; add that exact pre-fix discriminator. |
| D025 | Formal duplicate check uses function identity | formal self-certification | (a) regression test | `tests/test_formal.py::test_witness_registry_rejects_missing_and_borrowed_mutations` | A counted invariant cannot reuse another invariant's relation mutation as its discriminator. |
| D026 | New binding assets retain old kit stamp | policy drift | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 scaffold-tree non-goal; `DECISIONS.md` D-2 | Mk2 vendors one digest-pinned lock, not a separately stamped scaffold/kit asset family. |
| D027 | Generated-policy check omits inputs and facts | stale generated artifacts | (a) regression test | `tests/test_projections.py::test_map_freshness_passes_then_detects_stale_and_undecodable`; `tests/test_checks_standing.py::test_registry_meta_check_fails_for_missing_artifact_id` | Present generated bytes are regenerated and compared, while a missing registered artifact evaluator fails completeness. |
| D028 | Composite checks rebuild project-wide Gate2 | stale generated artifacts | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal; `DECISIONS.md` D-6 | Mk2 has no HTML Gate2 staging/cache pipeline; its canonical projection is a pure in-memory function. |
| D029 | Atomic replace cannot classify uncertain commit | coupled writes | (a) regression test | `tests/test_store_faults.py::test_genuine_fsync_failure_preserves_invariants`; `tests/test_store_faults.py::test_short_writes_are_completed_by_the_write_loop` | Primitive fsync failures preserve an old or complete-new state, and partial writes cannot be installed as success. |
| D030 | Optional discovery controls applicability | vacuous/self-certifying checks | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 scaffold-tree non-goal; `DECISIONS.md` D-6 | Worksheets and conventional standalone manifests are absent; generated names are closed and release bundles are digest-referenced. |
| D031 | Distribution and generated-fact authority incomplete | stale generated artifacts | (a) regression test | `tests/test_lock.py::test_lock_binds_shipped_data_files_and_refuses_symlinked_packages`; `tests/test_projections.py::test_map_freshness_passes_then_detects_stale_and_undecodable` | One lock digest covers shipped inputs and one canonical projection freshness check covers generated state. |
| D032 | Generated and executed test discovery differ | stale generated artifacts | (a) regression test | `tests/test_doc_binding.py::test_readme_test_count_matches_pytest_collection` | The documented count is bound to pytest's real collected item set, including parametrization. |
| D033 | Next-action failures are warning-only | fail-opens | (a) regression test | `tests/test_checks_standing.py::test_dependency_graph_acyclic_names_a_cycle`; `tests/test_projections.py::test_every_emitted_reachable_action_names_a_legal_ir_edge` | A producer cycle is a binding failure, and every emitted action must resolve to a legal IR edge rather than disappearing behind a warning. |
| D034 | Formal policy coverage hard-codes two policies | formal self-certification | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 invalidate-releases non-goal; `DECISIONS.md` D-5 | Mk2 exposes only retain semantics, and formal exploration executes that same `decide()` relation rather than enumerating a second policy registry. |
| D035 | Gate2 reconstructs generator stage policy | policy drift | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal; `DECISIONS.md` D-6 | There is one pure JSON projection and no second HTML Gate2 stage projection. |
| D036 | Gate2 cache omits lifecycle policy bytes | missing digest bindings | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal; `DECISIONS.md` D-6 | Mk2 has no staged Gate2 cache identity; map freshness is recomputed from the authoritative snapshot. |
| D037 | Formal count overstates semantic uniqueness | formal self-certification | (d) open | Proposed test `test_doc_binding.py:test_formal_claims_do_not_overstate_semantic_uniqueness` | Current wording is bounded and witness-based, but no negative wording test would fail if docs again claimed general semantic distinctness. |
| D038 | Distribution product pin is path-only | vacuous/self-certifying checks | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 scaffold-tree non-goal; `DECISIONS.md` D-2 | The multi-role scaffold contract is replaced by one opaque, digest-pinned product. |
| D039 | Upgrade writes after next-action derivation fails | coupled writes | (a) regression test | `tests/test_ingress_invariants.py::test_p1_migration_refuses_structurally_invalid_candidate_before_touching_target` | A structurally invalid cyclic migration candidate is refused before the target tree is touched. |
| D040 | Editable controls self-certify invariant identity | formal self-certification | (a) regression test | `tests/test_formal.py::test_every_invariant_has_one_private_discriminating_witness` | Every invariant must be broken by its independently registered relation mutation. |
| D041 | Corrupt criteria sidecars are advisory | criteria sidecars | (b) unrepresentable | `src/vouch_mk2/core/model.py`; `src/vouch_mk2/store/codec.py` | Criteria are a typed ledger-native `Release` field decoded with the snapshot; there is no optional sidecar or advisory fallback. |
| D042 | Distinct signatures admit borrowed witnesses | formal self-certification | (a) regression test | `tests/test_formal.py::test_witness_registry_rejects_missing_and_borrowed_mutations` | Reusing another invariant's mutation is an explicit build error. |
| D043 | Applicability partitions one invariant | formal self-certification | (b) unrepresentable | `src/vouch_mk2/formal/invariants.py`; `src/vouch_mk2/formal/witnesses.py` | An `Invariant` has no policy/applicability discriminator and the witness registry requires one owned mutation per invariant. |
| D044 | Status facts omit promised witness evidence | stale generated artifacts | (c) out of scope | `DECISIONS.md` D-5 and D-6 | Mk2 makes the recomputed certificate/CLI the witness-evidence surface; map status facts deliberately carry operational projection facts, not a copied formal report. |
| D045 | Config and ledger disagree on assurance tier | policy drift | (a) regression test | `tests/test_assurance_tier.py::test_tier_is_snapshot_state_defaulting_to_lightweight_and_round_trips` | `Snapshot.tier` is the one runtime authority; migration refuses config/ledger disagreement and tier-matrix cells bind the checks that consume them. |
| D046 | Status can publish before unit-done | fail-opens | (a) regression test | `tests/test_decide_gates.py::test_d046_publication_with_unsatisfied_dependency_is_refused` | The publication command and its dependency gate are one `decide()` path. |
| D047 | Serialized anchor can move backward | coupled writes | (a) regression test | `tests/test_evidence.py::test_anchor_monotonicity_by_construction`; `tests/test_evidence.py::test_anchor_advanced_event_is_promotion_only_by_type` | Anchor advance targets only the current append-only head, so a caller cannot nominate an older digest. |
| D048 | Kit authority and defect metadata not self-contained | missing digest bindings | (d) open | Proposed test `test_doc_binding.py:test_defect_acceptance_source_is_vendored_and_digest_bound` | This map reads the sibling 0.6 defect log but mk2 neither vendors nor digest-binds that acceptance source. |
| D049 | Public claims and version vocabulary drift | policy drift | (d) open | Proposed test `test_doc_binding.py:test_public_claim_and_version_vocabulary_bind_all_surfaces` | Commands are bound today, but no single test binds package version, public claim boundary, migration wording, and all transport docs. |
| D050 | Assurance wording diverges across contexts | stale generated artifacts | (d) open | Proposed test `test_doc_binding.py:test_public_assurance_sentence_is_exact_on_every_surface` | The claim boundary is appropriately bounded, but its sentence is not enforced as one exact source across README and generated context. |
| D051 | Registry equality omits installed-version membership | vacuous/self-certifying checks | (c) out of scope | `DECISIONS.md` D-8 | The 0.6 methodology/version registry is migration-report data and is not an mk2 runtime authority. |
| D052 | Publication commits a stale map | coupled writes | (a) regression test | `tests/test_projections.py::test_map_freshness_passes_then_detects_stale_and_undecodable` | Projections are intentionally not transaction participants; a snapshot mutation makes an existing map fail freshness until explicitly regenerated. |
| D053 | Assurance baselines lack decision markers | vacuous/self-certifying checks | (c) out of scope | `DECISIONS.md` D-5 | Mk2 has no 0.6 assurance-baseline registry; independently authored invariants plus private relation witnesses are the declared formal trust boundary. |
| D054 | Append-only defect history was rewritten | policy drift | (c) out of scope | `DECISIONS.md` D-6 | Mk2 consumes the 0.6 append-only log as acceptance evidence but does not reproduce it as a mutable project authority. |
| D055 | Authority/path checks undershoot stated scope | path handling | (a) regression test | `tests/test_store_paths_digests.py::test_relative_path_refusals`; `tests/test_init.py::test_init_refuses_symlinked_github_components_and_writes_nothing` | Absolute/traversal paths and symlinked owned-path components fail before writes. |
| D056 | Live kit and formal facts are hand-copied | stale generated artifacts | (a) regression test | `tests/test_doc_binding.py::test_readme_test_count_matches_pytest_collection`; `tests/test_formal.py::test_core_v1_closes_exhaustively_with_stable_counts` | The visible test count comes from real collection and formal figures come from the executed bounded exploration. |
| D057 | Certificate consumers accept stale/non-pass | formal self-certification | (a) regression test | `tests/test_formal.py::test_recorded_certificate_recomputes_and_stale_certificate_fails` | The artifact consumer recomputes the relation and rejects tampered or stale certificate content. |
| D058 | Recovery journal chooses map destination | path handling | (c) out of scope | `DECISIONS.md` D-6 | Projections are never transaction participants and generated store names are a closed allowlist, so there is no journal-selected destination. |
| D059 | Evidence snapshots omit directory symlinks | path handling | (a) regression test | `tests/test_bundle.py::test_build_inventory_refuses_symlinks` | Inventory traversal refuses every symlink before considering whether its target is a file or directory. |
| D060 | Amendment overwrites protective history | terminal-semantics | (a) regression test | `tests/test_decide_releases.py::test_amendment_preserves_operator_closure_on_rolled_back_release` | Amendment invalidates state without replacing an existing operator closure record. |
| D061 | Graph sinks and terminal claims share a name | terminal-semantics | (a) regression test | `tests/test_mk2_ir.py::test_operational_release_claim_rules_mirror_06_shapes`; `tests/test_vouch06_transcription.py::test_model_terminal_state_sets_match` | Claim classifications and model-terminal sets are independently named and tested. |
| D062 | Gate2 loses mixed-content DOM order | stale generated artifacts | (c) out of scope | `IMPLEMENTATION-PLAN.md` §1 HTML-map non-goal | Mk2 ships canonical JSON only; the plan explicitly defers an HTML layer and its DOM-order test. |
| D063 | Mini schema validator ignores assertions | fail-opens | (c) out of scope | `DECISIONS.md` D-1 | Mk2 has no hand-written mini JSON-Schema validator; one strict typed decoder owns each artifact ingress. |
| D064 | Methodology/formal citations drift | stale generated artifacts | (d) open | Proposed test `test_doc_binding.py:test_normative_methodology_and_formal_citations_resolve` | Existing binding covers selected IDs and commands, not all normative methodology references or formal source citations. |
| D065 | Completion projection mislabeled graph sink | terminal-semantics | (a) regression test | `tests/test_vouch06_transcription.py::test_model_terminal_state_sets_match`; `tests/test_vouch06_transcription.py::test_claim_shapes_match_release_classification` | The transcription keeps model-terminal and terminal-claim projections separate. |
| D066 | Composite implementation closure was asserted early | policy drift | (d) open | Proposed test `test_doc_binding.py:test_composite_closure_bundle_discriminates_every_retained_surface` | The retained certificate, amendment, and terminal surfaces have tests, but the open citation-binding gap prevents an honest composite-closure claim. |
| D067 | Certificate source reads miss ancestor symlinks | path handling | (a) regression test | `tests/test_lock.py::test_lock_binds_shipped_data_files_and_refuses_symlinked_packages` | A formal/package source tree containing a symlinked directory cannot be built into the digest-bound execution artifact. |
| D068 | Normative docs drift from cited sources | stale generated artifacts | (d) open | Proposed test `test_doc_binding.py:test_all_normative_docs_have_resolving_citations_or_exemptions` | The current doc-binding suite is allowlist-style and does not enroll every normative document or source-derived figure deny-by-default. |
| D069 | Distribution role coverage is vacuous | vacuous/self-certifying checks | (a) regression test | `tests/test_checks_standing.py::test_registry_meta_check_fails_for_missing_artifact_id`; `tests/test_checks_standing.py::test_duplicate_artifact_results_fail_completeness` | Required artifact coverage fails for missing and duplicate outcomes instead of trusting a mutable exemption subtraction. |
| D070 | Ancestor-symlink TOCTOU residual | path handling | (c) out of scope | `DECISIONS.md` D-1; `docs/threat-model.md` Level 3 boundary | The remaining concurrent ancestor swap is explicitly outside the practical level-2 boundary and is recorded rather than claimed closed. |

## Totals by defect class

| defect class | total |
|---|---:|
| vacuous/self-certifying checks | 9 |
| missing digest bindings | 4 |
| fail-opens | 4 |
| policy drift | 15 |
| coupled writes | 5 |
| path handling | 5 |
| stale generated artifacts | 12 |
| formal self-certification | 7 |
| terminal-semantics | 8 |
| criteria sidecars | 1 |
| **all classes** | **70** |

## Disposition summary

| disposition | count |
|---|---:|
| (a) regression test | 29 |
| (b) unrepresentable | 3 |
| (c) out of scope | 29 |
| (d) open | 9 |
| **all defects** | **70** |

The open items, with the proposed discriminators already named in the table, are:

- project-local lock discrimination across distinct temporary namespaces;
- a negative binding against formal semantic-uniqueness overclaims;
- one authoritative runtime assurance tier driving the IR requirements;
- vendored and digest-bound custody of the acceptance source;
- cross-surface public claim and version vocabulary binding;
- exact public-assurance wording across generated contexts;
- resolving normative methodology and formal citations;
- a composite closure test over every retained surface; and
- deny-by-default enrollment of normative documentation and generated figures.
