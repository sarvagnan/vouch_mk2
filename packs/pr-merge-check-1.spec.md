# pr-merge-check@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_pr_merge_check.py` or `packs/pr-merge-check-1.effects.json`.
**Decision:** D-50 (`vouch_mk2-22a`). **Seams (D-44):** the CLI on scratch stores (built with
`Store.init` and CLI commands; one forged store rewritten with the strict codec, as a hand editor
would); the generated workflow (`vouch_mk2.init.generated_workflow`) and mk2's own
`.github/workflows/ci.yml`, parsed as YAML and with the `check-merge` step run by `bash` in a
scratch Git repository; the text of `docs/*.md`.
**Effects:** none. The verb reads three stores and writes only its own temporary directory.

## Promise

`vouch-mk2 check-merge --base BASE --head HEAD --merged MERGED` opens the three stores read-only
and changes no byte under any of them. It copies BASE into a new temporary directory made with
Python's `tempfile` (so under `TMPDIR`), merges HEAD into that copy exactly as `merge --root COPY
--other HEAD` would (actor `check-merge`, no run claim), and compares the state the copy reaches
with MERGED's state. All three stores' event chains must read in full with the strict reading
`merge` uses. The comparison covers every field of the state except the revision and the event
head: the five collections (units, deviations, releases, evidence heads, open-in-CI exceptions)
are compared as unordered sets of complete entries, each entry compared in full (ordered fields
inside an entry keep their order), and every other field is compared by value. The temporary
directory is removed in every case.

- **Pass.** The replay is accepted and the states are equal: exit 0, and the first line of stdout
  starts with `PASS check-merge` and names the replay mode (`no-op`, `fast-forward` or
  `replayed`).
- **Fail.** Exit 1, and the first line of stdout starts with `FAIL check-merge`, when the replay
  is refused (the output names the refusal code and its detail), when reading any of the three
  stores or replaying raises a store error such as an unreadable or invalid history (the output
  names the error), or when the states differ. For differing states the output names each unit,
  deviation, release, evidence head or exception entry that differs, whether it is in only one
  state or in both with different content (a unit by its `NAME@VERSION` label), and, for each
  other differing field, its snapshot field name (`tier`, `anchor_mode`, `ci_policy`,
  `formal_certificate_digest`, `effects_required`) and both values.
- **Usage.** Exit 2, naming the path on stderr, when BASE, HEAD or MERGED has no `state.json`.

The generated workflow and mk2's own `.github/workflows/ci.yml` each gain a job named
`merge_check` with `if: github.event_name == 'pull_request'`, `permissions: contents: read`, and
the default checkout (no `ref` input, so the merge commit GitHub creates for the pull request)
with `persist-credentials: false` and `fetch-depth: 0`. Its `check-merge` step has `shell: bash`,
uses no `${{ }}` expressions, reads no event payload, and: fails unless the checked-out commit has
exactly two parents; treats the first parent as the base, the second as the head and the
checked-out commit itself as the merged result; succeeds with a message when the base commit's
tree has no `.vouch/state.json` (an adoption pull request); otherwise copies the `.vouch/` tree of
each of the three commits into its own directory under `$RUNNER_TEMP` as the raw blobs Git stores
(read with `git ls-tree` and `git cat-file`, not `git archive` or a checkout, so no
`.gitattributes` rule such as `export-ignore`, `eol` or a filter changes or hides a file), fails
when a commit has no `.vouch/` or holds an entry under it that is not a regular file, and runs
`check-merge` on the three copies, failing when it fails. In the generated workflow the job first verifies the lock
(`python vouch.lock self-check --expect @.vouch/config.json`) and the step runs `python vouch.lock
check-merge`; in mk2's workflow an earlier step builds the lock (`uv run vouch-mk2 build-lock
--output "$RUNNER_TEMP/vouch.lock"`) and the step runs `python "$RUNNER_TEMP/vouch.lock"
check-merge`.

`docs/claim-boundary.md` states what the check proves and its boundary (R11), and
`docs/commands.md` is regenerated.

## Requirements

Each test fails before the change because `check-merge` is not a subcommand (argparse exits 2),
the workflows have no `merge_check` job, or the docs lack the text, except the guard.

- **R1 — An honest fast-forward passes.** `test_an_unchanged_base_fast_forwards_and_passes`.
  HEAD adds a unit to a copy of BASE and MERGED is HEAD: exit 0, `PASS check-merge`,
  `fast-forward`.
- **R2 — An honest replayed merge passes.** `test_an_honest_replayed_merge_passes`. BASE and
  HEAD diverge (a unit each), HEAD merges BASE with `merge`, MERGED is HEAD: exit 0, `replayed`.
  HEAD lists its own unit before BASE's, so a replay onto BASE reaches the same units in another
  order.
- **R3 — A pull request with no store change passes.**
  `test_a_head_the_base_already_contains_is_a_no_op_pass`. BASE has advanced past HEAD and MERGED
  is BASE: exit 0, `no-op`.
- **R4 — Dropped base work fails.** `test_a_merged_store_missing_base_work_fails_and_names_it`.
  BASE and HEAD diverge and MERGED is HEAD, which never merged BASE: exit 1, `FAIL check-merge`,
  BASE's unit label named.
- **R5 — A forged merge record fails.** `test_a_forged_theirs_head_fails`. HEAD merged an earlier
  BASE, BASE then gained `b2@1`, and HEAD's merge record was rewritten (re-hashed, state head
  updated) to name BASE's new head, so `merge --root HEAD --other BASE` is a no-op; MERGED is HEAD:
  exit 1, `b2@1` named. The unforged control passes.
- **R6 — Skipped head work and a differing setting fail.**
  `test_a_merged_store_missing_head_work_names_the_unit_and_the_setting`. HEAD specifies `h@1` and
  raises the tier to `standard`; MERGED is BASE: exit 1, output names `h@1`, `tier`, `standard` and
  `lightweight`.
- **R7 — A refused replay fails.** `test_a_head_whose_work_the_base_refuses_fails`. At `standard`,
  HEAD lowers the tier while `x@1` is still `specified`; BASE then freezes `x@1`; HEAD merges BASE
  (accepted) and MERGED is HEAD: exit 1, output names `merge.conflict` and `tier.lowering-refused`.
- **R8 — An unreadable head fails.** `test_an_unreadable_head_history_fails`. HEAD's head event
  object deleted: exit 1, `FAIL check-merge`.
- **R9 — Usage.** `test_a_path_without_a_store_exits_two`. A MERGED path with no `state.json`:
  exit 2, the path on stderr.
- **R10 — Read-only and no residue.** `test_check_merge_writes_nothing_and_leaves_no_temporary_directory`.
  With a leftover temporary file in each root and `tempfile.tempdir` set to an empty directory,
  passing, failing and usage runs leave every file under the three roots byte-identical and the
  temporary directory empty.
- **R11 — Docs.** `test_claim_boundary_states_the_check_and_its_boundary`.
  `docs/claim-boundary.md` contains, whitespace normalised:
  ``\`check-merge\` replays HEAD onto a private copy of BASE and passes only when the result has
  the same state as MERGED.``, ``Someone who can push to the default branch directly is outside
  this check: the base is trusted.``, ``A forged claim that changes no state can pass undetected; a
  later check fails if the claim makes its replay refuse or reach a state different from
  MERGED.``, ``A base commit with no store passes as an adoption pull request, with no replay.``,
  ``The check runs code from the pull request, so a pull request that edits
  the workflow, the lock or the source the lock is built from can remove or change the check.``,
  ``An honest pull request also fails when its work cannot be replayed onto the base.`` and
  ``Requiring the job to pass before a merge is the repository's choice.``; `docs/commands.md` has
  a heading for `check-merge`.
- **R12 — The jobs' shape.** `test_both_workflows_have_a_read_only_pull_request_merge_check_job`.
  In both workflows the `merge_check` job matches the Promise (condition, permissions, a default
  checkout with no `ref`, `shell: bash`, no `${{` in the step, no `git archive`, the lock
  invocation); in the generated workflow the
  self-check step comes before the `check-merge` step, and in mk2's the build-lock step does.
- **R13 — The jobs' behaviour.** `test_the_merge_check_step_passes_honest_and_fails_dropped_merges`.
  Each workflow's `check-merge` step, run by `bash --noprofile --norc -eo pipefail` in a scratch
  Git repository (`python` on `PATH`, `RUNNER_TEMP` set, a `vouch.lock` shim in the repository and
  in `RUNNER_TEMP` that runs this checkout's CLI): exit 0 on a two-parent commit whose tree is an
  honest head's; exit 0 when that honest head also commits a `.gitattributes` marking `.vouch/**`
  `export-ignore` and `-text`; exit 0 when it instead commits `.vouch/** text eol=crlf`, which
  makes the checked-out `.vouch/state.json` differ from the committed blob (the test asserts
  that); nonzero with `FAIL check-merge` when the tree is a head that dropped
  the base's unit; nonzero with `FAIL check-merge` and the head's unit named when the parents are
  honest but the merge tree is the base's (so MERGED differs from HEAD); nonzero when the head
  commit deletes `.vouch/` and the merge tree keeps the base's store; exit 0 when the base commit
  has no store; nonzero on a one-parent commit.
- **R14 — Changed entries and other fields.**
  `test_a_changed_unit_and_a_changed_effects_requirement_are_named`. HEAD freezes the existing
  `x@1` and runs `require-effects`; MERGED is BASE (same unit labels, same tier): exit 1, output
  names `x@1` and `effects_required`.
- **R15 — An unreadable merged history.** `test_an_unreadable_merged_history_fails`. HEAD adds a
  unit to a copy of BASE; MERGED is a copy of HEAD with the event object before its head deleted
  (its head record and state stay valid, so its state fields equal the replay's): exit 1,
  `FAIL check-merge`.
- **Guard.** `test_guard_the_scenarios_merge_as_described`: today, the R2 merge replays, R5's
  forged head merges BASE as a no-op while the control does not, R7's HEAD-side merge is accepted
  and the BASE-side merge on a copy is refused, and the R13 repository builds with two-parent
  merge commits.

## Ground rules

- **Code.** The verb reuses `merge_store_roots` for the replay and the strict codec for reading;
  it adds no second merge implementation and changes no merge behaviour, gate, check, format or
  generated projection. The comparison is a pure function of two snapshots and may live in pure
  code (D-1). The CLI does the I/O.
- **Workflows.** No existing job, step or condition changes, except where an existing test pins
  the generated workflow's job set (`tests/test_ci_anchor_regressions.py`, bound to no pack),
  which may be updated and listed in the DONE file. The new job writes nothing, pushes nothing and
  holds no write permission.
- **Docs.** `docs/claim-boundary.md` gains the R11 sentences in a paragraph on the pull-request
  check near the merge trust boundary, and keeps every phrase other tests pin. Every new statement
  is true of the code and the workflows. `docs/commands.md` is regenerated.
- **Out of scope.** Direct pushes to the default branch. Pull requests that edit the workflow,
  the lock or the source the lock is built from. Requiring the job in branch protection. Coverage
  claims that change no state at their own pull request (D-50). Any change to how `merge` resolves
  conflicts, the D-45 refusals, or the policy refusals a replay onto the base can hit (R7's tier
  lowering; Astra also reproduced `pack.effects-required` when HEAD freezes without an effects
  declaration and BASE independently runs `require-effects`). Validating the provenance of a
  single store's claims beyond what the replay and the comparison show.

## Limits (convergence bound, set before review)

A review blocks only on an observable violation of this check: a pass or fail that differs from
the Promise for stores the CLI can produce, for R5's forgery, or for any readable store checked
against the wrong Git tree (a parent or merge commit other than the Promise's, or a tree whose
bytes differ from what Git stores); a byte written under BASE, HEAD or MERGED, or a temporary
directory left behind; a merge, gate, check or format change; a workflow job with write
permission, a changed existing job, or a step that runs a different command from the Promise; a
false statement in a changed doc; or a pinned phrase removed. Findings that need single-store
provenance validation (a forged claim that changes no state), a pull request that edits the
workflow, the lock or its source, or a direct push are D-50's stated boundary. Honest histories
whose replay onto the base is refused by policy are reported failures, not defects; an honest
history that this check fails for any other reason is a finding. Output wording beyond what the
tests pin is a note.
