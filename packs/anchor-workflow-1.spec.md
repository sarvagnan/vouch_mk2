# anchor-workflow@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_anchor_workflow.py` or `packs/anchor-workflow-1.effects.json`.
**Decision:** D-57 (`vouch_mk2-b6g.20`, `vouch_mk2-b6g.21`). **Seams (D-44):** the generated
workflow text (`generated_workflow`) and mk2's `.github/workflows/ci.yml`, parsed as YAML; the
`anchor_plan` step with id `candidate` and the `anchor_publish` step named "Validate candidate and
publish", run as bash scripts in scratch git repositories: a project `init` adopted (lightweight,
CI anchoring) with real evidence recorded by its vendored lock, a local bare repository standing
in for GitHub (reached through a `url.<path>.insteadOf` rule for the URL the script builds,
`${{ github.repository }}` replaced by `o/r`), the environment the workflow passes (`RUNNER_TEMP`,
`GITHUB_OUTPUT`, `GITHUB_RUN_ID`, `PLANNED_BASE`, `CANDIDATE`, `DEFAULT_BRANCH`,
`VOUCH_PUSH_TOKEN`), and a `python` shim that skips only `vouch.lock formal` (minutes long, and
not what this unit changes).
**Effects:** declared in `packs/anchor-workflow-1.effects.json`.

## Promise

**Refusal reasons.** In the anchor publish step of both the generated workflow and mk2's `ci.yml`,
each validation that fails prints exactly one line, on standard output or standard error, then
exits 1 with the branch untouched, as today:

| Validation | Line |
|---|---|
| bundle checksum | `REFUSED publish: the candidate bundle does not match its checksum` |
| planned base is 40 hex | `REFUSED publish: the planned base is not a full commit id` |
| candidate is 40 hex | `REFUSED publish: the candidate is not a full commit id` |
| the bundle holds the candidate | `REFUSED publish: the bundle does not hold the planned candidate` |
| one parent | `REFUSED publish: the candidate does not have exactly one parent` |
| parent is the planned base | `REFUSED publish: the candidate's parent is not the planned base` |
| paths only under `.vouch/` | `REFUSED publish: the candidate changes a path outside .vouch/` |
| regular modes under `.vouch/` | `REFUSED publish: the candidate introduces a non-regular file under .vouch/` |
| live head is the planned base | `REFUSED publish: <DEFAULT_BRANCH> moved since this run planned its anchor; a later run will anchor it` |

No other line starts `REFUSED publish:`, and the step's own earlier refusal messages (the inline
Python's `candidate changes a path outside .vouch/` and `candidate introduces non-regular mode ...`)
are replaced by these lines, not repeated beside them; output from the tools the step runs (such as
`sha256sum`) may remain. A valid candidate is published as today with no such line. The order of
the validations and what each checks do not change. The publisher does not depend on its working
directory.

**Concurrency.** The generated workflow's top-level `concurrency` is exactly
`group: vouch-anchor-${{ github.repository_id }}-${{ github.ref }}-${{ inputs.anchoring &&
'anchoring' || github.run_id }}` (one string) with `cancel-in-progress: false`, and no job declares
its own `concurrency`: anchoring calls share one group per repository and ref, and every other run
has a group of its own. mk2's `ci.yml` keeps `vouch-anchor-${{ github.repository_id }}-${{
github.ref }}`.

**Late anchor signal.** In the generated workflow's anchor plan step, for each unit it anchors whose
current evidence head (in `.vouch/state.json` at the checked-out commit) was present with the same
value, and not anchored, in `.vouch/state.json` at that commit's first parent, the step prints
exactly one line `vouch: anchoring <name>@<version> late: its head <first 16 hex of the head> was
already pending at the previous commit`, and no other line starting `vouch: anchoring`. Nothing is
printed for a head that is new or changed since the parent, a head that was anchored at the parent,
a commit with no parent, a parent without a store, or a parent whose store cannot be read or
parsed; none of these fails the step. The step's outputs, candidate (which anchors every listed
head) and exit status are as today, and the head-listing selector the step already runs keeps its
output (anchor-currency@1's frozen tests run it on its own).

## Requirements

Expected RED reasons, before the change: the group is the old string; the plan step prints no late
line; each refusal exits 1 printing only the tool's own output (for example `vouch-candidate.bundle:
FAILED`, or the inline Python's own message), never a `REFUSED publish:` line. Guards (they pass
today and must keep passing): `test_mk2s_own_ci_keeps_its_group`, the six
`test_a_head_not_pending_at_the_previous_commit_is_not_signalled` cases (no signal exists today;
they also pin that a bad parent never fails the step) and the two
`test_a_valid_candidate_is_published` cases (the harness publishes).

- **R1 — Concurrency.** `test_only_anchoring_calls_share_the_concurrency_group`;
  `test_mk2s_own_ci_keeps_its_group` (guard).
- **R2 — Late signal.** `test_a_head_left_pending_at_the_previous_commit_is_signalled`: unit `u`'s
  evidence committed and left pending, then a commit adding unit `w`'s evidence: exactly one late
  line, for `u`; exit 0; `changed=true`; the candidate anchors both heads with the run id.
  `test_a_head_not_pending_at_the_previous_commit_is_not_signalled` (guard; evidence recorded in
  this commit; the only commit; anchored at the parent, then new evidence; a pending head replaced
  by a newer one for the same unit; a parent without a store; a parent with a malformed
  `state.json`): no late line, exit 0, and the candidate anchors the head.
- **R3 — Refusals.** The stand-in remote's default branch is `trunk` and the publisher runs in an
  empty directory. `test_a_valid_candidate_is_published` (guard; both workflows).
  `test_each_refusal_names_its_reason` (each of the nine validations × both workflows): exit 1,
  the line from the table, exactly one `REFUSED publish:` line, neither old inline-Python message,
  the branch unchanged.

## Ground rules

- **Code.** Only the two workflow texts change: `_WORKFLOW_TEMPLATE` (via `generated_workflow`)
  and `.github/workflows/ci.yml`. Print reasons in the shell (for example `|| { echo '...'; exit 1;
  }`) and from the inline Python (its exit messages). The late signal is computed in the plan step
  from `git show HEAD^1:.vouch/state.json` (or equivalent) and the current state; it must not fail
  the step when the parent or its store is missing or unreadable. No change to the CLI, the store,
  `decide`, gates, formats or `init` other than the template text.
- **Fallout.** The generated workflow's bytes change, so `check.workflow-current` (D-55) fails for
  every adopted project until it runs `refresh-workflow`; that is intended. Frozen tests of other
  units that pin workflow text (generated-ci@1, ci-adoption-closure@1, ci-trust-boundary@1,
  ci-candidate-validity@1, pr-merge-check@1, anchor-currency@1, adoption-docs@1 and others) must
  keep passing; if one cannot, Sol lists it with its assertion in the DONE file and does not edit
  it, and the owner adapts and rebinds.
- **Docs.** `docs/claim-boundary.md` or the adoption guide gains a sentence on the late signal and
  the refusal lines where the publisher's checks are described; keep every pinned phrase; quickstart
  re-pin steps already cover refreshing the workflow.
- **Out of scope.** Changing what the publisher checks; mk2's own plan step; a GitHub annotation
  format; anchoring a cancelled call's head any sooner.

## Limits (convergence bound, set before review)

A review blocks only on: a refusal line, exit code or published branch that differs from the
Promise; a validation that no longer refuses what it refused; a late line printed when the Promise
says nothing, or missing when it says one; a plan step whose outputs, candidate or exit status
change; a concurrency block other than the Promise's; a change outside the two workflow texts and
docs; a false statement in a changed doc; or a pinned phrase removed. Wording beyond what the tests
pin is a note.
