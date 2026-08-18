# Acceptance pack — unit `ci-adoption-closure@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or
`tests/test_ci_adoption_closure.py`. Second external review of B1: `generated-ci@1` was declared
done and was not. Every finding below was reproduced by the owner before this pack was written.

## Requirements (each bound to a test)

R1 **The first CI run must not deadlock.** `--pending-anchor-ok` tolerates BOTH pending-anchor
   shapes — a head with no workflow run claim AND a head still at `GENESIS` (never anchored),
   which is exactly the state every new project is in — and nothing else. The row is still
   printed. The B1 test masked this by advancing an anchor locally first; the fixture here is a
   genuinely fresh project. → `test_a_never_anchored_store_passes_pending_anchor_ok`,
   `test_pending_anchor_ok_still_refuses_everything_else`
R2 **mk2's own `.github/workflows/ci.yml` gets the shape B1 generates for adopters**: no job-wide
   `contents: write`, `persist-credentials: false`, no `[skip ci]`, top-level `concurrency:`,
   every `uses:` pinned to a 40-hex SHA, anchoring only on default-branch pushes. B1 fixed the
   class and left the instance. → `test_mk2_own_ci_has_the_same_shape_it_generates`,
   `test_mk2_own_ci_anchors_only_on_default_branch_pushes`
R3 **No repository-controlled code runs under a persisted write credential.** Every checkout in
   both workflows — including the anchor job's — sets `persist-credentials: false`; the anchor
   job obtains a credential only for the final push step (e.g. an explicit token on the push
   URL), after `advance-anchor`/`generate` have run.
   → `test_anchor_jobs_do_not_persist_credentials_while_running_repository_code`
R4 **The project's own tests gate anchoring** (D-18 says anchoring follows the project's tests
   passing; a comment cannot do that). `init --test-job NAME` (repeatable) writes those job names
   into the anchor job's `needs:` alongside validation. Without it the generated workflow contains
   a placeholder job that **fails** (`exit 1`) with a message naming `--test-job`, so no adopter
   can believe their tests gate anchoring when they do not.
   → `test_init_can_wire_the_projects_own_test_job_into_the_gate`,
   `test_without_a_test_job_the_generated_workflow_fails_loudly_rather_than_silently`
R5 **`--json` forwards promptly**: six bytes of child stdout reach stderr while the child is
   still running (use `read1()`/`os.read` semantics, not a fixed-size `read()` that blocks until
   the buffer fills). Bounded memory is retained.
   → `test_small_child_output_reaches_stderr_before_the_child_exits`
R6 Standards items the review named: `captured_revision` is either used by the guarantee in
   `decide()` or removed from the protocol; the pending-anchor exception uses
   `mk2.CHECK_EVIDENCE_ANCHORS`, not a string literal; `init_project()` does not expose a
   test-only `on_step` hook in its public signature (inject the fault another way — e.g. a
   module-private seam the test patches).
   → `test_no_dead_captured_revision_in_the_evidence_protocol`,
   `test_pending_anchor_exception_uses_the_ir_constant`,
   `test_init_does_not_expose_a_test_only_hook_in_its_public_signature`

## Ground rules
- Do not weaken any check's verdict. `--pending-anchor-ok` remains exit-code reporting only, and
  must still refuse a FAIL row, a strict-policy violation, or any other NOT-CHECKED row.
- `init`'s recovery guarantees from `init-recovery@1` must survive, including the crash test;
  if removing `on_step` changes how that fault is injected, keep the guarantee and the test's
  meaning.
- The review also noted that `init` publishes external files individually, so a hard process
  death mid-placement can leave a subset. Each individual placement must be atomic (temp+rename,
  never a partial file); a subset of complete files is acceptable because a retry treats
  identical bytes as no collision. State in DONE how this is now true.
- Do not touch `.vouch/`, README's test-count line, git. Pyrefly strict; import-linter kept;
  every pre-existing test green — `tests/test_generated_ci.py` and `tests/test_init.py` assert
  the current shapes and will need updating; `tests/test_ci_anchor_closure.py`'s guard on mk2's
  own ordering must keep passing.
