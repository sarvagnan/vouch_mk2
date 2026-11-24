# Claim boundary

This document defines what a Vouch report proves and what it never proves. Other documents and
checks defer to this page.

**The one rule:** Vouch checks only what artifacts can prove; where it cannot, it
prints `NOT_CHECKED` rather than offering a button that fakes it.

No command turns an organizational claim into a mechanical pass. Features that would do so—such as
an `attest-independence` command or an auto-generated intent-validation record—are rejected at
design time.

Checks return one of four verdicts: `PASS`, `FAIL`, `NOT_CHECKED`, or `NOT_APPLICABLE`. Only
`PASS` composes to `PASS`. Every `NOT_APPLICABLE` must cite an IR applicability rule, and nothing
is silently skipped.

## What Vouch proves

Every row below maps to the obligation registry: a registered check ID must map to an evaluator
that ran, or the run fails. Threat levels come from [threat-model.md](threat-model.md).

| Claim | Mechanism | Level |
|---|---|---|
| Snapshot matches the recorded chain | `check.event-log` replays the event chain through `apply()` and byte-compares the result with `state.json`. Replay proves the snapshot is derivable from the recorded chain; it does not prove that the recorded order is the order in which events really occurred. | 1 |
| Artifact integrity | Objects are content-addressed; any object whose digest does not match its name fails to load. | 1–2 |
| Reference integrity | Every cross-artifact reference is a typed `{kind, digest}` field that resolves or fails. | 1 |
| Schema conformance | One strict decoder owns each artifact type. Unknown fields and coercions are errors. | 1–2 |
| Lifecycle legality | `decide(Snapshot, Command)` refuses every transition the IR does not license. Gates run inside the transition. | 1 |
| Deviation blocking | A unit with an open deviation cannot advance. Deviations bind to `NAME@VERSION`. | 1 |
| Claim shape | Completion claims re-check units, the criteria digest, and the roster. Negative outcomes require criteria and outcome deviations. Protective closures require a trigger, a roster snapshot, and `no_completion_claim`, and contaminated state never blocks them. | 1 |
| Evidence chains | One canonical-JSON hash chain per `NAME@VERSION`. Each new record binds the current pack's test-set digest (or the whole inventory digest for legacy pack format 1). RED precedes the first GREEN, except that a rebind after publication requires only a fresh GREEN. Anchors only move forward. | 1–2 |
| Tier evidence | The tier recorded in the snapshot selects the IR-bound chain, anchor, and high-attestation checks. No configuration overrides it. | 1–2 |
| Projection freshness | `check.map-freshness` and `check.context-freshness` regenerate projections and byte-compare them. A stale projection is a `FAIL`, not a broken commit. | 1 |
| Generated workflow freshness | For a store `init` wrote, checked with `--project`, `check.workflow-current` fails when `.github/workflows/vouch.yml` differs from the workflow the running Vouch generates. | 1 |
| Registry completeness | Any check declared in the IR without an evaluator fails CI on the mk2 repository. | 1 |
| Recorded formal certificate | Cheap verification of a recorded certificate; see [Formal certificate](#formal-certificate). | 1 |
| Formal invariants over the bounded model | `vouch-mk2 formal` exhaustively regenerates the bounded relations, evaluates M1–M12, and runs the private mutation witnesses; see [Formal certificate](#formal-certificate). | 1 |

## Store writes

**Locking:**
- Each lifecycle verb (`vouch-mk2 specify`, `vouch-mk2 record-evidence`, and the rest) opens one
  store and holds the writer lock from `decide` through `apply` to commit.
  `record-formal-certificate` holds the lock through the revision it reports.
- Writers
wait on a blocking exclusive lock. Read-only handles never acquire that lock.
- Projection generation takes the lock only while replacing files.
- Store upgrade holds its outer writer lock through regeneration.
- MCP is read-only, so there is no remote write path.

**Atomicity and cleanup:**
- A commit places all objects before replacing `state.json`, so `state.json` never names a missing
  record.
- An interrupted write before the swap can leave inert, unreferenced objects and temporary
  transaction files that are unreachable from the authoritative state.
- On the next writable open, cleanup runs only if it can take the writer lock immediately. It
  removes leftover temporary files and interrupted merge transaction directories. If `state.json`
  still matches the pre-merge state, cleanup also removes the supporting objects that the
  transaction placed.
- The event-object sweep is best-effort. It removes canonical event objects unreachable from the
  current event chain. It skips the scan if the store format differs from the current format,
  the state or chain cannot be read, or the object directory's modification time is no later than
  `state.json`'s.
- Other unreferenced objects, such as evidence and pack objects, stay in the store as inert files.
  After `SIGKILL`, the store can differ from its pre-write state even after cleanup.
- A caught merge failure whose state is still unchanged removes the exact objects it created
  before releasing the writer lock.

## Actors and run IDs are claims

Every event records two self-reported fields:
- **Actor:** the caller's `--actor` value (default: the OS user).
- **Run ID:** the recognized CI provider's environment value, if any.

Vouch checks event structure and replay, but it never verifies an actor's identity or confirms
that a run ID belongs to the person, workflow, repository, or event it names. No check reports an
actor as verified. A person can check a run ID against its workflow run URL; Vouch never opens
that URL.

## Event log and replay

The event log is authoritative; `state.json` is a derived cache. `check.event-log` walks every
record back to genesis, checks the links between revisions, replays every event through `apply()`
from the empty snapshot, and compares canonical bytes. Replay sets `event_head` to the last
record's digest, because a record cannot contain its own digest.

Replay proves the chain is self-consistent and its snapshot is derivable. It does not prove
wall-clock order: Vouch has no independent monotonic trust root to establish when two commuting
events really happened. Run-ID and anchor claims are the only external ties, and they remain
claims. An anchor labeled "advanced by workflow run N" only repeats what was recorded.

**Upgrading a pre-log store:**
- Upgrading a store from format 3 or older writes a pre-log checkpoint at the step from
  format 3 to format 4. The first record contains the entire asserted snapshot. Everything before
  the checkpoint is attested rather than verified, and replay proves nothing about pre-log history.
- The upgrader refuses a store that declares a format below 4 if its raw `state.json` still has an
  `event_head` key or a canonical event object already exists. A real upgrade judges the store as
  the writable open's cleanup leaves it, so a hand-made merge transaction that makes that cleanup
  remove the event objects has the same effect as deleting them by hand. `upgrade --dry-run` opens
  the store read-only and does no cleanup.
- `state-format.json` records the highest format the store has held. It is strictly decoded,
  raised under the writer lock, and replaced atomically using descriptor-relative calls with
  fsyncs. `upgrade` refuses a lower format even if both event-log signals are gone.
- That mark detects a downgrade while it survives, but cannot prevent someone from deleting it.
  Removing or lowering the mark sits outside every repository-local trust root; the operator
  restores it from the store's git history.

## State format

Format 1 and later declare `state_format`; absence denotes legacy format 0.
Strict reads accept only the current format. Older stores require an explicit, locked
`vouch-mk2 upgrade`; newer stores are refused as written by a newer Vouch.

## Evidence

**What `record-evidence` claims:** The exact claim is that the recorded outcome is the exit status
of a process this verb spawned, at the time it spawned it. The child
inherits the caller's environment and working directory. This is not a claim that the argv names a
test runner, that the runner is honest, or that the exit status reflects a test suite.

**Pack binding during execution:** Recording is refused until the pack is frozen. The unit ref,
test-set digest, and store revision are captured before the command runs. Afterwards, under the
writer lock, `decide()` refuses with `evidence.pack-moved` if the unit ref is no longer current
or the digest changed; the outcome is never rebound to a replacement pack. An unrelated revision
bump is not refused.

**Tier gates for current-pack evidence:**
- At `standard` and `high`, `gate.dod.current-pack-green` refuses publication unless the current
  pack's test-set digest has RED then GREEN and the latest evidence for it is green. A later
  refactor keeps that result; a later RED breaks it.
- Legacy pack format 1 uses the whole inventory digest. Evidence recorded before D-17 has no pack
  digest; checks name it and never count it as current-pack GREEN.
- A re-freeze that changes any bound pack file also requires a GREEN recorded after the re-freeze;
  RED stays keyed to the test-set digest. Identical-byte re-freezes keep earlier evidence.

The publication gates compare the frozen test set, not the command: a GREEN can run a different
command from the RED before it. This example applies to a pack without a bound command.
A pack may bind an evidence-policy file (D-54) that fixes the command RED and GREEN must run,
requires JUnit reports, or both.
With reports required, `record-evidence` refuses a RED whose report lists no failure and a GREEN
whose report omits a test the RED's report lists or does not list one of RED's failures as passed.
The policy is part of the test set, so a different policy is a different test set, and `rebind-pack`
cannot change a published pack's policy.

`show-evidence` lists the stored command line of every RED and GREEN and notes when the GREEN
that counts has a different stored command line from the RED that counts; the note never blocks
publication.

`record-evidence --junit-report` stores the test IDs and outcomes that the JUnit report at a path
only Vouch gave the command lists; the report is the command's claim, like the exit status, and
Vouch does not see which tests ran.
`show-evidence` compares the tests the counted GREEN's report lists with those the counted RED's
report lists and notes when GREEN's report lacks some of RED's; the comparison never blocks
publication.
Vouch releases that predate `--junit-report` cannot read or replay the format-3 evidence records
created with it.

**Command validation and argument redaction:**
- The CLI validates the exact command descriptor before opening the store or starting the child,
  refusing an invalid one without running it.
- `--redact-arg INDEX` (repeatable, placed before `--`) takes zero-based positions that include
  `argv[0]`, though `argv[0]` itself cannot be redacted.
- With `--junit-report`, the child's argv has the private report path in place of the single
  `{junit}` token. Without the option, the child receives the original argv.
- The stored descriptor uses the argv from before path substitution and shows `<redacted>`
  at the selected positions. Vouch keeps no digest of the redacted argument value.
- Redaction covers only the stored descriptor: child output is not redacted, and neither are the
  child's side effects. Prefer passing secrets through the environment.
- The CLI warns on stderr whenever the original argv looks like it carries a secret. Without
  explicit redaction, the command line is stored verbatim.

## Acceptance packs

`freeze --pack-file` stores the pack's files as a byte-bound inventory. A format-2 inventory
separates executable `--pack-test-file` entries from `--pack-file` specs when judging evidence,
while keeping both byte-bound. At every tier, the pack's EXECUTION is not verified — runner
configuration (conftest.py, pytest.ini, pyproject testpaths) can deselect a bound test; bind those
files too.

**Rebinding after publication (`rebind-pack`):**
- `rebind-pack` applies only to published or release-bound units.
- Vouch records the old and new inventory and test-set digests and the evidence-head sequence. It
  records the stated reason as a claim. Label, status, and evidence chain are kept.
- No bound path may be removed and no executable entry may be demoted.
- Vouch does not verify the reason's truth and does not verify the adequacy of the new test content.
- Afterwards, `check.evidence-chains` requires a fresh GREEN for the new test-set digest at every
  tier (no RED needed), and `check.pack-bound` names rebound units and enforces that GREEN at
  `high`.
- A rebind after publication turns on the fresh-GREEN requirement; while the unit stays active,
  only a passing GREEN for the current test set turns it off, and a REFACTOR does not.
  Superseding a rebound unit ends its requirement. This rule applies when Vouch folds a rebind
  event. A pre-log checkpoint keeps the state it captured.
- Each rebind must name the unit's current inventory and test set. A re-freeze starts a new pack
  generation, so the first rebind after it does not continue an earlier generation's rebinds.

## CI policy

`check --ci` enforces the recorded `ci_policy`:
- **`strict`:** every unit past `specified` must be published, release-bound, or superseded,
  unless a recorded `mark-open-in-ci` exception names its exact current `NAME@VERSION`. The
  exception clears when that record becomes terminal or inactive, so it never covers a later
  version.
- **`ledger`:** the coherence-only behavior from before D-18.

The `check.ci-policy` row reports the active policy and each exception's reason. Vouch verifies
that these were recorded, not that a reason is true or adequate.

## CI anchoring

**Anchor status in `ci` mode:**
- In anchor mode `ci`, the composed verdict stays `NOT_CHECKED` until a real CI run advances the
  current evidence anchor.
- A local `advance-anchor` carries no workflow-run claim, so `check.evidence-anchors` reports
  `no workflow run claim`; one eligible CI run resolves it. Lowering the anchor mode would only
  hide the boundary.
- For evidence recorded after a CI-claimed anchor, `check.evidence-anchors` reports `NOT_CHECKED`
  with `anchor lags its head` until the next CI run advances it.

**Allowing the first clean run (`--pending-anchor-ok`):** The generated workflow uses
`check --ci --pending-anchor-ok` so an otherwise-clean first run can reach anchoring. This flag
changes only the exit code, and only when the pending-anchor row is the sole mechanical
`NOT_CHECKED`. The row and its detail remain in text and JSON output, and any `FAIL` or other
mechanical `NOT_CHECKED` still exits nonzero.

**Job separation and trust boundaries:** Direct `push` and `pull_request` runs validate with
read-only contents permission and never anchor. Anchoring runs only when the adopter's own
workflow calls `./.github/workflows/vouch.yml` with the anchoring input set to true. That caller's
`needs:` edge, in the same workflow run, is the project-test gate.

The workflow separates read-only validation, read-only planning, and a publisher (the only job
that can write):
- **Planning** may run the checked-out lock. It advances anchors, regenerates projections, runs a
  post-anchor `check --ci` (without `--pending-anchor-ok`) and `formal`, and only then stages a
  one-parent candidate commit on the exact revision it observed.
- **Publisher** never checks out or runs repository code. It accepts only a same-run candidate
  whose parent is that revision, whose changes are all under `.vouch/` with regular-file modes,
  and whose base is still the live default-branch head.

When a publisher validation fails, its `REFUSED publish:` line names the failed check.
The generated planner's `vouch: anchoring … late:` line means the same evidence head was already
pending at the first parent, which can happen within a single push of several commits. The line
cannot establish whether an earlier push or anchoring run took place.

The candidate bundle is transport, not evidence, and not a second trust root. No Vouch command
imports it, and it cannot advance an anchor without passing the publisher's checks.

A push made with `GITHUB_TOKEN` does not trigger a follow-up workflow, so the post-anchor checks
are the semantic validation of what Vouch publishes. Allowing that commit through a ruleset
bypass, or using GitHub App credentials so independent forge checks run on it, is the
organization's choice.

**What the workflow does not cover:** The workflow does not verify the GitHub identity behind run
IDs, configure required checks or branch protection, or prove that the workflow file is unchanged.
Forge and runner identity, artifact delivery, required checks, and branch protection are
organizational governance and trust-root concerns.

By design, the generated workflow never anchors from a fork, so a fork's work is anchored only
after it is merged into the base repository.

## Merging stores

`merge --root OURS --other THEIRS` reconciles two copies of a store by replaying events—never by
comparing snapshots—and requires a shared event-record ancestor.

**Replay mechanics:**
- If THEIRS is already merged into OURS, nothing changes. If OURS is an ancestor of THEIRS, the
  merge fast-forwards.
- Otherwise, each divergent THEIRS command replays through `decide()` against the evolving OURS
  state, ending with a merge record naming both heads.
- **Redundant open-in-CI clears:** If an incoming open-in-CI clear targets an exact unit reference
  that has no exception on the evolving OURS state, and would resolve to that same reference, it
  is skipped without writing a record and reported as skipped. A clear that resolves to a
  different unit version is still refused.
- **Provenance and companions:** The replayed records keep their source records' actor, run claim
  and recorded time, matched by event kind and subject, and the merge record carries the merge's
  actor claim. A companion record the replay generates with no matching THEIRS record carries the
  merge's claims too; a THEIRS companion the replay no longer produces is not written.
- **Refusals:** Every replayed command must act on the same unit version as on THEIRS, and every
  anchor must match the evidence head THEIRS promoted. Unrelated histories are refused, and any
  single refused replay refuses the whole merge without writing OURS.

**Replayed merge boundaries:** A replayed merge record names the receiving-chain record where its
replayed block begins as its OURS head, and keeps the original THEIRS head. If replay filtered out
the original boundary, the record names the receiving head at that point in the replay. A merge
that needs an unlocatable boundary from an old record refuses and names that record's digest.

**Origin tracking and deduplication:**
- Each copied record carries its first occurrence's EVENT digest and envelope version. A companion
  generated during replay carries `generated: true` and starts at its own EVENT digest; later
  copies carry that digest as `origin` and keep the flag.
- The final merge record lists the roots of facts satisfied without a record and carries forward
  the source chain's `satisfied` entries. OURS covers the digests, origins, and `satisfied`
  entries in its own chain.
- Replay skips any source command batch whose primary root OURS already covers and continues with
  the remaining batches. For every batch excluded as already incorporated by digest, merge block,
  or origin, replay checks the effect of each record flagged `generated` in the state reached
  after staging the preceding work; if that effect is absent, it refuses with `merge.conflict`.
- **Version-1 envelopes** lack origin information. After exact provenance exclusions, replay
  refuses with `merge.ancestry-unproven` if a remaining source batch is inside a version-1 merge
  block, or if a remaining primary has a version-1 root and OURS has a version-1 merge after the
  last shared record. Fast-forwards and exact no-ops still work. `upgrade` reports how many
  version-1 merge markers a store holds.

**Trust boundary for merge metadata:** `origin` and `satisfied` are claims written by the merging
tool, trusted at the same level as `theirs_head`. That trust level also applies to `generated`.
Vouch checks the syntax and placement of `origin` and `generated` inside version-2 merge blocks
and compares claims when the receiver holds a counterpart. A hand-edited store can mislabel new
work as a copy and cause a receiver to skip it.

**Pull-request merge check.** `check-merge` replays HEAD onto a private copy of BASE and passes
only when the result has the same state as MERGED. When BASE or HEAD is at an older state format
than the running Vouch, `check-merge` upgrades a private copy of it first, as `upgrade` would, so a
pull request that upgrades the store is checked too. MERGED is never upgraded, and a store older
than format 4, from before the event log, fails the check. The read-only `merge_check` job uses
the first and second parents of the pull request's merge commit as BASE and HEAD, and the merge
commit as MERGED. A base commit with no store passes as an adoption pull request, with no
replay. Someone who can push to the default branch directly is outside this check: the base is
trusted. A forged claim that changes no state can pass undetected; a later check fails if the
claim makes its replay refuse or reach a state different from MERGED. An honest pull request also
fails when its work cannot be replayed onto the base. The check runs code from the pull request,
so a pull request that edits the workflow, the lock or the source the lock is built from can
remove or change the check. Requiring the job to pass before a merge is the repository's choice.

**What merge success proves:** Success proves only that the merged log replays to the merged
snapshot. It does not prove that the branches were compatible in any wider sense, that their
intentions agreed, that their claims were true, or that the actor was verified.

**Git conflict recovery (`merge-git-stages`):** `merge-git-stages` applies this merge to a Git
conflict. It treats the index (paths, modes, object IDs, and stages) as untrusted input, requires
complete, safe copies of both sides, and leaves the index unchanged. It trusts neither
branch names nor organizational intent: stages 2 and 3 are Git's mechanical OURS and THEIRS.
Success proves only what the semantic merge proves. It does not prove that the merge or rebase
was authorized, that either side's intent was represented correctly, or that the operator reviewed
and staged the result.

## Formal certificate

Plain `check` never explores the bounded model. When a certificate is recorded—at any tier—`check`
verifies only these cheap facts:
- The certificate resolves at its digest and decodes strictly.
- The checker identity is current.
- The recorded policy tables equal freshly emitted ones.
- The invariant and witness rows cover the current registries and are all true.

If all of those hold, `check.formal-certificate` reports `PASS` for that narrowed obligation; if
any fails, it reports `FAIL` at every tier.

That `PASS` does not mean `check` regenerated the relations, recomputed their digests or counts,
ran M1–M12, or reran the witness mutations. `vouch-mk2 formal` performs that full exploration over
every formal universe. The generated workflow runs it after `check --ci` on every CI run, and
the read-only MCP `formal` summary runs the same exploration (D-7). Only a passing
`vouch-mk2 formal --record --root ROOT` records results (tables, relation digests and counts, and
the certificate). MCP `check`, CLI `check`, and hooks never explore.

The `refreeze-v1` formal universe reaches D-36's fresh-GREEN requirement at the `standard` tier,
and M12 checks that a changed pack turns it on, that no unit publishes while it is on, and that
only a GREEN for the current pack turns it off.
The `tier-v1` universe reaches the same requirement through a rebind. M12 includes rebinds in its
inventory-change rule and excludes superseded targets from its GREEN requirement for clearing
the flag.

With no recorded certificate, the row is `NOT_APPLICABLE` under
`subjects-present: formal-certificate`. Publishing at `high` separately requires a recorded
certificate through `gate.dod.tier-evidence`; that requirement gates publication, not the checking
of an existing certificate.

## Never mechanically checked

Some of the items below appear in reports as organizational `NOT_CHECKED` rows; the others are documented exclusions with no report row.

| Claim | Why no artifact can prove it |
|---|---|
| Acceptance ownership is real | A recorded owner proves a string was recorded, not that a person with authority and time validated the intent. |
| Intent validation | Whether the pack tests the right thing is human judgment. RED shows the tests can fail, not that they should. |
| Independence between people | Comparing identities is a format heuristic (the 0.6 commit-author lesson), never proof of distinct people. |
| Causal red history | An anchored chain proves ordering and anchor timing, not why the RED happened. |
| Protective-closure truthfulness | A closure record proves a trigger and roster snapshot were recorded, not that the trigger was real or the response appropriate. |
| Residual tier controls | `org.tier-controls` lists active cells without full coverage, separates partial from unchecked residuals, and reports waived or no-obligation cells separately. |
| Semantic test quality | Mutation testing and statistical oracles are ecosystem-specific and need judgment Vouch cannot supply. |
| Model matches reality | The formal certificate covers the model under a finite abstraction, not the implementation's full behavior. |
| Forge enforcement | Required checks and branch protection live in the forge. A pull request can change the workflow file. Hooks only warn. |
| Trust-root integrity (level 3) | Coordinated edits to `vouch.lock`, the workflow, and branch rules defeat any repository-local check. Source review and forge governance are the boundary. |

## Changing this boundary

- Every mechanical row maps to obligation-registry entries, and that mapping is a checked
  projection of the IR.
- Moving a claim from the organizational table to the mechanical table requires a decision in
  `DECISIONS.md` naming the new artifact that makes it provable.
- `check` output lists the `NOT_CHECKED` rows verbatim.

## Effect declarations

A declaration records what was declared, not that the code has only those effects.
Vouch checks declaration structure and binds its raw file bytes into the pack inventory.
- `check.effects-declared` reports declarations for active units. Missing or undecodable bound
  inventories fail, while legacy undeclared units are reported without failing CI.
- `org.effects-truth` is always `NOT_CHECKED`: truth, completeness, and working compensation
  require human review.
- An empty declaration claims a transform; observations disclose a source and capture; actions
  disclose an external system and a declared confirmed, compensated, or irreversible outcome.

**Opt-in and enforcement:** `init` records the one-way `require-effects` switch for new stores.
Existing stores can opt in with `require-effects --root .vouch`; no command clears it. Once set,
future bound freezes and binds must declare effects. Label-only freezes remain allowed and are
reported as undeclared. No upgrade invents declarations for old units, and a pack rebind cannot
add, remove, or change a declaration. Run `show-effects --root .vouch [--json]` to read
declarations from bound inventory objects.

**Verification scope:** The CLI and library derive `effects_declared` from the inventory. The pure
`decide()` core trusts that adapter-supplied flag, just as it trusts `test_set_digest`; it does
not read artifact objects. The `effects-v1` formal universe
reaches the effects requirement but does not verify its gate: declarations live in pack objects,
not the snapshot. Tests and `check.effects-declared` verify the gate, including undeclared packs
bound after the recorded requirement. At `standard` and `high` with a project root, that check
also compares a hash-matching effects file's parsed declaration with the embedded declaration.
Pack-file verification checks raw bytes according to tier, not declaration truth. A project's own
effect checker is not yet bindable as a dedicated control.
