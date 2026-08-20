# Acceptance pack — unit `ci-trust-boundary@1` (pack/1)

**Owner:** Fable under the user's 2026-08-19 method-role override. **Implementer:** Codex — may
NOT edit this file or `tests/test_ci_trust_boundary.py`. This unit closes D-20 and the remaining
B1 trust/gating defects. Every non-guard test must fail against the frozen pre-implementation
tree.

## Requirements (each bound to a test)

R1 `vouch_mk2.init.generated_workflow(test_jobs) -> str` remains the exact workflow written by
   `init`. It exposes both direct `push`/`pull_request` validation and `workflow_call`; the latter
   has a boolean, default-false input that is the only route to anchor planning. Direct runs can
   never anchor. The called workflow must not require `github.event_name == 'workflow_call'`:
   GitHub retains the caller's event context, so a push caller still reports `push`. →
   `test_generated_workflow_is_callable_but_direct_runs_cannot_anchor`

R2 Project jobs are gated in the workflow that owns them. Passing `--test-job unit-tests
   --test-job lint` makes `init` print a copyable caller job with `needs: [unit-tests, lint]`, a
   local reusable-workflow `uses: ./.github/workflows/vouch.yml`, and explicit anchoring. Those
   foreign job ids never appear in any generated job's `needs`. With no `--test-job`, `init`
   reports that anchoring is disabled and still generates a validation-only-safe workflow. It
   writes no fifth owned path and does not edit an existing project workflow. The caller fragment
   itself is restricted to a default-branch push. Invalid job ids remain refused. →
   `test_init_prints_a_real_same_workflow_test_gate`,
   `test_no_test_jobs_fail_closed_without_claiming_a_gate`

R3 Generated CI separates **read validation**, **read anchor planning**, and **write
   publication**. Jobs that execute `vouch.lock`, project scripts, locally built code, or local
   actions declare `contents: read`. The sole `contents: write` job consumes the plan and runs no
   such repository-controlled command. It must not install project dependencies or invoke
   `vouch.lock`. → `test_generated_write_job_executes_no_repository_controlled_code`

R4 The publisher validates the candidate before push: it is based on the plan job's exact base
   revision; has exactly one parent; changes only `.vouch/**`; introduces no symlink, submodule,
   or non-regular mode; and the remote default-branch head still equals the planned base. Any
   mismatch exits nonzero before push. Transport between jobs is same-run and integrity-bound.
   → `test_generated_publisher_fail_closes_on_stale_or_malformed_candidates`

R5 The generated workflow keeps immutable 40-hex action pins, `persist-credentials: false` on
   every checkout, and ref-keyed concurrency. → `test_generated_workflow_keeps_ci_hardening`

R6 mk2's own `.github/workflows/ci.yml` uses the same read-plan/write-publish boundary: no
   write-capable job runs `uv`, the built lock, project scripts, a local action, or another
   checked-out executable. The write job validates and publishes a prepared candidate only.
   → `test_mk2_workflow_has_the_same_privilege_boundary`

R7 D-20 and `docs/claim-boundary.md` say the candidate bundle is transport, not evidence or a
   new trust root, and identify forge/runner claims as organizational. Adoption-facing docs show
   where the caller job belongs and that direct generated-workflow runs never anchor.
   → `test_docs_describe_the_gate_and_transport_boundary`

## Ground rules

- Preserve D-7: `init` owns only `.vouch/`, `.vouch/config.json`, `vouch.lock`, and
  `.github/workflows/vouch.yml`. It may print integration text but may not create/edit a caller
  workflow.
- Preserve D-18 evidence semantics and D-2's offline stdlib-only lock. Do not add an
  import-observation/import-evidence command or treat a CI artifact as proof.
- A write job may run fixed workflow publisher logic and standard action implementations; it
  may not execute bytes obtained from the checked-out project as code. `persist-credentials:
  false` is required but is not accepted as privilege separation by itself.
- Keep the first-run `--pending-anchor-ok` behavior and reporting visible. Do not weaken any
  check, formal invariant, or evidence rule.
- Update generated workflow snapshots/assertions and adopter documentation as needed. Do not
  edit this pack, its acceptance test, `.vouch/`, README's test-count line, Beads, or git.
- A real forge probe is deliberately a separate dependent unit (`vouch_mk2-b6g.9`); this pack
  proves the generated structure locally.

## Amendment pack/2 (DEV-ctb-01, pack defect)

Pack/1's test required the planner condition to contain `workflow_call`. GitHub's called workflow
uses the caller's event context, making that condition false for the intended push caller. R1's
promise is unchanged: a default-false call input is the capability direct runs lack. Pack/2
removes the impossible event-name assertion, rejects that comparison, and makes the printed
caller's push/default-branch guard executable acceptance.
