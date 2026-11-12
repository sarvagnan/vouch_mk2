# Claim boundary

What a Vouch report proves, and what it never proves. Other documents and checks defer to this
page.

**The one rule:** Vouch checks only what artifacts can prove; where it cannot, it
prints `NOT_CHECKED` rather than offering a button that fakes it.

No command turns an organizational claim into a mechanical pass. Features that would, such as
an `attest-independence` command or an auto-generated intent-validation record, are rejected
at design time.

Verdicts are `PASS`, `FAIL`, `NOT_CHECKED` and `NOT_APPLICABLE`. Only `PASS` composes to
`PASS`, `NOT_APPLICABLE` must cite an IR applicability rule, and nothing is silently skipped.

## What Vouch proves

Each row is backed by the obligation registry: a registered check id mapped to an evaluator
that ran, or the run fails.
Levels are from [threat-model.md](threat-model.md).

| Claim | Mechanism | Level |
|---|---|---|
| The snapshot is derivable from the recorded chain | `check.event-log` replays the event chain through `apply()` and byte-compares the result with `state.json`. Replay proves the snapshot is derivable from the recorded chain; it does not prove that the recorded order is the order in which events really occurred. | 1 |
| Artifact integrity | Objects are content-addressed; one whose digest does not match its name does not load. | 1–2 |
| Reference integrity | Every cross-artifact reference is a typed `{kind, digest}` field that resolves or fails. | 1 |
| Schema conformance | One strict decoder per artifact type. Unknown fields and coercions are errors. | 1–2 |
| Lifecycle legality | `decide(Snapshot, Command)` refuses every transition the IR does not license. Gates run inside the transition. | 1 |
| Deviation blocking | A unit with an open deviation cannot advance. Deviations bind to `NAME@VERSION`. | 1 |
| Claim shape | Completion claims re-check units, the criteria digest and the roster. Negative outcomes need criteria and outcome deviations. Protective closures need a trigger, a roster snapshot and `no_completion_claim`, and contaminated state never blocks them. | 1 |
| Evidence chains | A canonical-JSON hash chain per `NAME@VERSION`. Each new record binds the current pack's test-set digest (the whole inventory digest for legacy pack format 1). RED precedes the first GREEN, except that a rebind after publication needs only a fresh GREEN. Anchors only move forward. | 1–2 |
| Tier evidence | The tier recorded in the snapshot selects the IR-bound chain, anchor and high-attestation checks. No configuration overrides it. | 1–2 |
| Projection freshness | `check.map-freshness` and `check.context-freshness` regenerate and byte-compare. Stale is a FAIL, not a broken commit. | 1 |
| Registry completeness | A check declared in the IR without an evaluator fails CI on the mk2 repository. | 1 |
| Recorded formal certificate | Cheap verification of a recorded certificate; see [Formal certificate](#formal-certificate). | 1 |
| Formal invariants over the bounded model | `vouch-mk2 formal` exhaustively regenerates the bounded relations, evaluates M1–M11 and runs the private mutation witnesses; see [Formal certificate](#formal-certificate). | 1 |

## Store writes

Each lifecycle verb (`vouch-mk2 specify`, `vouch-mk2 record-evidence` and the rest) opens one
store and holds the writer lock from `decide` through `apply` to commit;
`record-formal-certificate` holds it through the revision it reports. Writers
wait on a blocking exclusive lock. Read-only handles never acquire that lock. Projection
generation takes the lock only while replacing files, and upgrade holds its outer writer lock
through regeneration. MCP is read-only, so there is no remote write path.

A commit places objects before replacing `state.json`, so the state never names a missing
record. An interrupted write before the swap can leave inert, unreferenced objects and temporary
transaction files, unreachable from the authoritative state. An opportunistic cleanup reclaims
them on the next writable open, and is skipped while another writer holds the lock. The store
does not guarantee byte identity after `SIGKILL`: its bytes may differ until that cleanup runs.
A caught merge failure whose state is still unchanged removes exactly the objects it created
before releasing the writer lock.

## Actors and run IDs are claims

Every event records an actor (the caller's `--actor`, default the OS user) and a run ID (the
recognized CI provider's environment value, if any). Vouch checks event structure and replay.
It never verifies the actor's identity, or that a run ID belongs to the person, workflow,
repository or event it names, and no check reports an actor as verified. A person can check a
run ID against its workflow run URL; Vouch never opens that URL.

## Event log and replay

The event log is authoritative; `state.json` is a derived cache. `check.event-log` walks every
record back to genesis, checks the links between revisions, replays every event through
`apply()` from the empty snapshot, and compares canonical bytes. Replay sets `event_head` to the
last record's digest, since a record cannot contain its own.

This shows that the chain is self-consistent and its snapshot derivable. It does not show
wall-clock order: Vouch has no independent monotonic trust root to say when two commuting
events really happened. Run-ID and anchor claims are the only external ties, and they remain
claims. An anchor "advanced by workflow run N" repeats what was recorded.

Upgrading to format 3 writes a pre-log checkpoint, holding the whole asserted snapshot, as the
first record. Everything before the checkpoint is attested rather than verified, and
replay proves nothing about pre-log history. The upgrader refuses format 3 if the raw document still
has an `event_head` key or a canonical event object exists. `state-format.json` records the
highest format the store has held (strictly decoded, raised under the writer lock, replaced
atomically and descriptor-relative with fsyncs), and `upgrade` refuses a lower format even if
both event-log signals are gone. That detects a downgrade while the mark survives but cannot
prevent deleting it. Removing or lowering the mark is outside every repository-local trust
root; the operator restores it from the store's git history.

## State format

Format 1 and later declare `state_format`; absence denotes legacy format 0.
Strict reads accept only the current format. An older store needs the explicit, locked
`vouch-mk2 upgrade`; a newer one is refused as written by a newer Vouch.

## Evidence

For `record-evidence`, the exact claim is: the recorded outcome is the exit status of a process
this verb spawned, at the time it spawned it. The child inherits the caller's environment and working directory.
This is not a claim that the argv names a test runner, that the runner is honest, or that the
exit status reflects a test suite.

Recording is refused until the pack is frozen. The unit ref, test-set digest and store revision
are captured before the command runs. Afterwards, under the writer lock, `decide()` refuses with
`evidence.pack-moved` if the unit ref is no longer current or the digest changed; the outcome is
never rebound to a replacement pack. An unrelated revision bump is not a refusal.

At `standard` and `high`, `gate.dod.current-pack-green` refuses publication unless the current
pack's test-set digest has RED then GREEN and the latest evidence for it is green. A later
refactor keeps that result; a later RED breaks it. Legacy pack format 1 uses the whole inventory
digest. Evidence from before D-17 has no pack digest; checks name it and never count it as
current-pack GREEN.

The CLI validates the exact command descriptor before opening the store or starting the child,
and refuses an invalid one without running it. `--redact-arg INDEX` (repeatable, before `--`)
takes zero-based positions that include `argv[0]`, though `argv[0]` itself cannot be redacted.
The child gets the original argv; the stored descriptor shows `<redacted>`, and Vouch keeps
no digest of the value. Only the descriptor is covered: child output is not redacted, and neither
are the child's side effects. Prefer passing secrets through the environment. The CLI warns on
stderr whenever the original argv looks like it carries a secret; without explicit redaction,
the command line is stored verbatim.

## Acceptance packs

`freeze --pack-file` stores the pack's files as a byte-bound inventory. A format-2 inventory
separates executable `--pack-test-file` entries from `--pack-file` specs when judging evidence;
both stay byte-bound. At every tier, the pack's EXECUTION is not verified — runner
configuration (conftest.py, pytest.ini, pyproject testpaths) can deselect a bound test; bind
those files too.

`rebind-pack` applies only to published or release-bound units. Vouch records the old and new
inventory and test-set digests and the evidence-head sequence, and records the stated reason as a claim.
Label, status and evidence chain are kept. No bound path may be removed and no executable entry
demoted. Vouch does not verify the reason's truth and does not verify the adequacy of the new test content.
Afterwards `check.evidence-chains` requires a fresh GREEN for the new test-set digest at every
tier (no RED needed), and `check.pack-bound` names rebound units and enforces that GREEN at
`high`.

## CI policy

`check --ci` applies the recorded `ci_policy`. `strict` requires every unit past `specified` to
be published, release-bound or superseded, unless a recorded `mark-open-in-ci` exception names
its exact current `NAME@VERSION`. The exception clears when that record becomes terminal or
inactive, so it never covers a later version. `ledger` is the coherence-only behavior from
before D-18. The `check.ci-policy` row names the policy and each exception's reason. Vouch
verifies that these were recorded, not that a reason is true or adequate.

## CI anchoring

In anchor mode `ci`, the composed verdict stays `NOT_CHECKED` until a real CI run advances the
current evidence anchor. A local `advance-anchor` has no workflow-run claim, so
`check.evidence-anchors` reports `no workflow run claim`; one eligible CI run resolves it.
Lowering the anchor mode would only hide the boundary.

The generated workflow lets that first otherwise-clean run proceed with
`check --ci --pending-anchor-ok`. The flag changes only the exit code, and only when the
pending-anchor row is the sole mechanical `NOT_CHECKED`. The row and its detail remain in text
and JSON output, and any FAIL or other mechanical `NOT_CHECKED` still exits nonzero.

Direct `push` and `pull_request` runs validate with read-only contents permission and never
anchor. Anchoring runs only when the adopter's own workflow calls
`./.github/workflows/vouch.yml` with the anchoring input set to true. That caller's `needs:`
edge, in the same workflow run, is the project-test gate.

The jobs are read-only validation, read-only planning, and a publisher that alone can write.

- Planning may run the checked-out lock. It advances anchors, regenerates projections, runs a
  post-anchor `check --ci` (without the pending-anchor exception) and `formal`, and only then
  stages a one-parent candidate commit on the exact revision it observed.
- The publisher never checks out or runs repository code. It accepts only a same-run candidate
  whose parent is that revision, whose changes are all under `.vouch/` with regular-file modes,
  and whose base is still the live default-branch head.

The candidate bundle is transport, not evidence, and not a second trust root. No Vouch command
imports it, and it cannot advance an anchor without passing the publisher's checks.

A push made with `GITHUB_TOKEN` does not trigger a follow-up workflow, so the post-anchor
checks are the semantic validation of what Vouch publishes. Allowing that commit through a
ruleset bypass, or using GitHub App credentials so independent forge checks run on it, is the
organization's choice.

The workflow does not verify the GitHub identity behind run IDs, configure required checks or
branch protection, prove that the workflow file is unchanged, or give forks write access to the
base repository. Forge and runner identity, artifact delivery, required checks and branch
protection are organizational governance and trust-root concerns.

## Merging stores

`merge --root OURS --other THEIRS` reconciles two copies of a store by replay, never by
comparing snapshots, and needs a shared event-record ancestor. An already-merged THEIRS changes
nothing, and an ancestor OURS fast-forwards. Otherwise each divergent THEIRS command is replayed
through `decide()` against the evolving OURS state, recording both heads and the actor claim.
Unrelated histories are refused, and one refused replay refuses the whole merge without
writing OURS.

Success proves only that the merged log replays to the merged snapshot. It does not prove that
the branches were compatible in any wider sense, that their intentions agreed, that their
claims were true, or that the actor was verified.

`merge-git-stages` applies this merge to a Git conflict. It treats the index (paths, modes,
object IDs, stages) as untrusted input, requires complete, safe copies of both sides, and leaves
the index unchanged. It trusts neither branch names nor organizational intent: stages 2 and 3
are Git's mechanical OURS and THEIRS. Success proves what the semantic merge proves. It does not
prove that the merge or rebase was authorized, that either side's intent was represented
correctly, or that the operator reviewed and staged the result.

## Formal certificate

Plain `check` never explores the bounded model. When a certificate is recorded, at any tier,
`check` verifies that the certificate resolves at its digest and decodes strictly, the checker
identity is current, the recorded policy tables equal freshly emitted ones, and the invariant
and witness rows cover the current registries and are all true. If so, `check.formal-certificate`
is `PASS` for that narrowed obligation; if any of those fails, it is `FAIL` at every tier.

That `PASS` does not mean `check` regenerated the relations, recomputed their digests or counts,
ran M1–M11, or reran the witness mutations. `vouch-mk2 formal` does that, over `core-v1` and
`history-v1`. The generated workflow runs it after `check --ci` on every CI run, and the
read-only MCP `formal` summary runs the same exploration (D-7). Only a passing
`vouch-mk2 formal --record --root ROOT` records results: tables, relation digests and counts,
and the certificate. MCP `check`, CLI `check` and hooks never explore.

With no certificate, the row is `NOT_APPLICABLE` under `subjects-present: formal-certificate`.
Publishing at `high` separately requires a recorded certificate through
`gate.dod.tier-evidence`; that gates publication, not the checking of an existing certificate.

## Never mechanically checked

Reports list these as organizational `NOT_CHECKED` rows. They are deliberate exclusions.

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
| Trust-root integrity (level 3) | Coordinated edits to `vouch.lock`, the workflow and branch rules defeat any repository-local check. Source review and forge governance are the boundary. |

## Changing this boundary

- Every mechanical row maps to obligation-registry entries, and that mapping is a checked
  projection of the IR.
- Moving a claim from the organizational table to the mechanical one needs a decision in
  `DECISIONS.md` naming the new artifact that makes it provable.
- `check` output lists the `NOT_CHECKED` rows verbatim.

## Effect declarations

A declaration records what was declared, not that the code has only those effects.
Vouch checks declaration structure and binds its raw file bytes into the pack inventory.
`check.effects-declared` reports declarations for active units; missing or undecodable bound
inventories fail, while legacy undeclared units are reported without failing CI. `org.effects-truth`
is always NOT_CHECKED: truth, completeness and working compensation require human review.
An empty declaration claims a transform; observations disclose a source and capture, and
actions disclose an external system and a declared confirmed, compensated or irreversible outcome.

`init` records the one-way `require-effects` switch for new stores. Existing stores can opt in
with `require-effects --root .vouch`; no command clears it. Future bound freezes and binds must
declare effects. Label-only freezes remain allowed and are reported as undeclared. No upgrade
invents declarations for old units, and rebind cannot add, remove or change a declaration.
`show-effects --root .vouch [--json]` reads declarations from bound inventory objects.

The CLI and library derive `effects_declared` from the inventory. The pure `decide()` core trusts
that adapter-supplied flag, just as it trusts `test_set_digest`; it does not read artifact objects.
The `effects-v1` formal universe reaches the effects requirement but does not verify its gate:
declarations live in pack objects, not the snapshot. Tests and `check.effects-declared` verify
the gate, including undeclared packs bound after the recorded requirement. At `standard` and
`high` with a project root, that check also compares a hash-matching effects file's parsed
declaration with the embedded declaration.
Pack-file verification checks raw bytes according to tier, not declaration truth. A project's
own effect checker is not yet bindable as a dedicated control.
