# claim-corrections@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_claim_corrections.py` or `packs/claim-corrections-1.effects.json`.
**Decision:** D-48 (`vouch_mk2-inv.1`, from the non-guarantee review `vouch_mk2-inv`).
**Seams (D-44):** the CLI (`upgrade` on a scratch store, which reaches `verify.upgrade_store` and
`Store`); the text of `README.md` and `docs/*.md`.
**Effects:** none. `upgrade` already refuses with this code path; the change widens the condition
under which it refuses and adds no read or write.

## Promise

`upgrade` (and `upgrade --dry-run`) refuses, before any migration write, a store whose raw
`state.json` declares a format below 4 (including a missing `state_format`, format 0) while its raw
document still has an `event_head` key or a canonical event object exists, with the existing
refusal and its git-history recovery hint. The refused upgrade leaves `state.json`, every object
and the format mark unchanged; opening the store for a real upgrade still does the housekeeping
every writable open does (creating `.lock`, removing leftover temporary files, rolling back an
interrupted merge transaction whose saved state still matches), and the refusal judges the store
as that housekeeping leaves it. The dry run opens the store read-only and does no housekeeping. A genuine pre-log store, which has neither signal,
upgrades as before. `docs/claim-boundary.md` says exactly what the cleanup on a writable open
removes and that other unreferenced objects stay; names the step from format 3 to format 4 as the
one that writes the pre-log checkpoint and every format below 4 as covered by the refusal; says the
publication gates compare the frozen test set, not the command; and states the fork rule as a
design choice. `README.md` and `docs/concepts.md` say what "the same tests" means.

## Requirements

Each test fails before the change for the stated reason, except the guards.

- **R1 — Upgrade refuses a re-baseline below format 4.**
  `test_upgrade_refuses_a_rebaseline_below_format_four` (six cases). A fresh `init` store with two
  `specify` events has its `state-format.json` mark removed and its declared format set to 0
  (key removed), 1 or 2; in the `event_head` cases its event objects are deleted and the key kept,
  in the `event object` cases the key is removed and the objects kept, so each case carries one
  signal. `upgrade` exits 2, its output names the signal (`event_head` or `event object`) and
  `git history`, no file under the store changes (the scenario has no leftover temporary files;
`.lock` is excluded), and no mark is written. *Before:* `upgrade`
  exits 0, runs the steps up to the current format and mints a fresh pre-log checkpoint over the
  existing log.
- **R2 — The dry run refuses the same.** `test_the_dry_run_refuses_the_same_rebaseline` (two
  cases, declared format 2). `upgrade --dry-run` exits 2, names the signal, and changes nothing.
  *Before:* it exits 0 and lists the steps.
- **R3 — A genuine pre-log store still upgrades (guard).**
  `test_a_genuine_pre_log_store_still_upgrades` (format 1, 2 and 3 golden fixtures, with no
  evidence heads and no objects). `upgrade` exits 0, writes a pre-log checkpoint (a non-empty
  `event_head`) and the mark. *Passes before:* such a store carries neither signal; this guards
  against a fix that refuses every legacy store.
- **R4 — Cleanup.** `test_claim_boundary_says_what_cleanup_leaves`. `docs/claim-boundary.md`
  (whitespace normalised) no longer contains `An opportunistic cleanup reclaims those files` or
  `may differ until cleanup runs`, and contains `Other unreferenced objects, such as evidence and
  pack objects, stay in the store as inert files.` and `The event-object sweep is best-effort`.
  *Before:* the old sentences.
- **R5 — The checkpoint step and the formats the refusal covers.**
  `test_claim_boundary_names_the_checkpoint_step_and_every_format_below_four`. Gone:
  `Upgrading to format 3 writes`, `to format 3:**`, `refuses format 3 if`. Present: `Upgrading a
  store from format 3 or older writes a pre-log checkpoint at the step from format 3 to format 4.`
  and ``The upgrader refuses a store that declares a format below 4 if its raw `state.json` still
  has an `event_head` key or a canonical event object already exists.`` *Before:* the old
  heading and sentences.
- **R6 — Test set, not command.** `test_docs_say_the_gates_compare_the_test_set_not_the_command`.
  The `## Evidence` section of `docs/claim-boundary.md` contains `The publication gates compare the
  frozen test set, not the command: a GREEN can run a different command from the RED before it.`;
  `README.md` and `docs/concepts.md` each contain `"The same tests" means the same frozen test set;
  Vouch does not compare the commands that RED and GREEN ran.` *Before:* absent.
- **R7 — The fork rule is a design choice.**
  `test_claim_boundary_states_the_fork_rule_as_a_design_choice`. `give forks write access` is gone
  and `By design, the generated workflow never anchors from a fork, so a fork's work is anchored
  only after it is merged into the base repository.` is present. *Before:* the old clause.

## Ground rules

- **Code.** The refusal lives where it is today (`verify._refuse_event_log_downgrade_signals`,
  called by both the dry run and the real upgrade before any write). Only its format condition
  changes: every integer format from 0 to 3, where a missing `state_format` is format 0. A negative
  or non-integer format keeps today's handling. No other behaviour changes; no state, event or
  pack format changes; formal universes, the certificate and the lock source are unaffected except
  for the changed function.
- **Docs.** Every new or changed sentence must be true of the code today (`store/store.py`
  `_sweep_orphans`, `_sweep_merge_transactions`, `_sweep_unreachable_event_objects`;
  `store/upgrade.py`; `verify.py`; `core/decide.py` and `verify.py` evidence gates; `init.py`'s
  generated workflow). The cleanup paragraph names what is removed (temporary files, an interrupted
  merge's transaction and, when the merge never published, the objects it placed; unreferenced
  event objects, best-effort) and when the event sweep does not run. The workflow paragraph keeps
  its real limits (run identity, required checks and branch protection, workflow file integrity).
  `Store.commit`'s docstring stops saying the next writable open sweeps every orphan. Keep every
  phrase other tests pin (the full suite is the check), including `pre-log checkpoint`, `attested
  rather than verified` and `replay proves nothing about pre-log history`.
- **Out of scope.** Showing recorded evidence commands and a pack-bound evidence command
  (`vouch_mk2-inv.2`); sweeping other object kinds; hand-edit defences in general
  (`vouch_mk2-22a`).

## Limits (convergence bound, set before review)

A review blocks only on: a reachable store with a format below 4 and an event-log signal that
`upgrade` or `upgrade --dry-run` accepts or partly writes; a genuine pre-log store (no signal) that
is now refused; a behavioural statement in a changed sentence that is false of the code; or a
pinned phrase removed. Hand edits that also forge the signals away (deleting both the key and every
event object along with the mark) are `vouch_mk2-22a`'s, not this unit's; a hand-made merge
transaction that makes the writable open's rollback remove the event objects is such an edit
(DEV-cc-02). Wording is a note.

## Amended by DEV-cc-01 (pack/2, 2026-11-21)

Sol's implementation (deviation, pack defect): the Promise said a refused upgrade changes no byte,
but the CLI opens the store writable before `upgrade_store` runs, and every writable open may
create `.lock` and remove leftover temporary files before the refusal (the ground rules forbid
changing that). pack/2 narrows the Promise to "before any migration write": `state.json`, every
object and the format mark stay unchanged, and the writable-open housekeeping is named. R1's note
records that its scenario has no leftover temporary files. The tests are unchanged.

## Amended by DEV-cc-02 (pack/3, 2026-11-21)

Astra's review (REWORK, one HIGH): on a format-2 store whose `event_head` key was removed by hand,
a hand-made merge transaction (its saved state equal to the edited `state.json`, every canonical
object hard-linked into it) made the real upgrade's writable open roll the transaction back and
unlink the event objects before the guard ran, so `upgrade` exited 0; the read-only dry run
refused. The owner adjudicated it under the Limits set before review: building that transaction
takes the same write access as deleting the event objects by hand, so it is a hand edit that
forges the signals away (`vouch_mk2-22a`). pack/3 makes the Promise name merge rollback as part of
writable-open housekeeping, say that the refusal judges the store as that housekeeping leaves it,
and say the dry run does none; the Limits name the case. The tests are unchanged.
