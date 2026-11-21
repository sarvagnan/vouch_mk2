---
name: vouch
description: Use when operating a Vouch store, freezing an acceptance pack, recording evidence, adjudicating deviations, publishing a unit, or diagnosing a refusal or CI check.
---

# Operating Vouch

Vouch checks only what artifacts can prove; where it cannot, it prints `NOT_CHECKED` rather than offering a button that fakes it.

Vouch records who claimed each step but does not verify identity or enforce roles.
Use the card for the role your team assigned you.

- [Acceptance owner](roles/owner.md) for pack authoring, deviation decisions, and publication.
- [Implementer](roles/implementer.md) for building against the frozen pack and reporting defects.
- [Reviewer](roles/reviewer.md) for inspecting the diff, evidence, and claim boundary.

`init` leaves skill installation to your team. If you copy this skill into an adopter repository,
keep its linked references available.

## Before changing the store

Read current facts with `python3 vouch.lock status --root .vouch --json`. For binding next work,
run `python3 vouch.lock next --root .vouch --json`. Use
`python3 vouch.lock mcp --root .vouch` when you need a read-only transport.

Change the store through Vouch commands. `check.event-log` compares the replayed event log
with the stored snapshot. Keep frozen files fixed while implementing. At `standard` and
`high`, `check.pack-files-match` compares active bound files with their recorded hashes when
it has a project root.

## When recording evidence

Use `record-evidence` with the intended test command, the exact unit reference, and the kind
that describes the run. Vouch records whether the spawned process exited zero or non-zero, not the exact status. Which tests ran and
whether the runner was honest are review questions.

Declare the code's actual observations and external actions. An empty effects list claims
there are none. The truth of an effects declaration remains `NOT_CHECKED`.

Use `--redact-arg` to hide selected arguments in the stored command descriptor. The child still
receives the original arguments, and the option leaves output and side effects untouched.

## Verify before handing off

Run `python3 vouch.lock generate --root .vouch`, then
`python3 vouch.lock check --ci --root .vouch --project . --json`. Report the actual verdict and
any mechanical failures or unchecked rows. Preserve organizational `NOT_CHECKED` findings when
summarizing the report.

`check --ci` exits 1 on a branch while an evidence anchor waits for CI on the default branch; do not lower the anchor mode or the CI policy, and do not set `GITHUB_RUN_ID`, to make it pass.

Treat a pending anchor as the reason for exit 1 only if `check.evidence-anchors` is the sole
mechanical `NOT_CHECKED` and every other mechanical row passes or is inapplicable. Under strict
CI policy, ask the owner to record an open-in-CI exception for an intentionally unfinished
post-specified unit when one is needed. Any mechanical `FAIL` needs separate investigation.

## When a command is refused

Read the [refusal and recovery guide](../errors.md) before retrying. Consult the
[lifecycle tables](../lifecycle.md) for a state transition, the [JSON reference](../schemas.md) for
a payload, and the [claim boundary](../claim-boundary.md) for the scope of a verdict.
