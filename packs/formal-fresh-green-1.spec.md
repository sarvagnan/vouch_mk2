# formal-fresh-green@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_formal_fresh_green.py` or `packs/formal-fresh-green-1.effects.json`.
**Decision:** D-46 (settles `vouch_mk2-7kc`). **Seams (D-44):** the `vouch_mk2.formal` registries
(`UNIVERSES`, `INVARIANTS`, `WITNESSES`), `explore`, `run_formal` and relations built from them; the
`vouch-mk2 formal` CLI; the `check.formal-certificate` row from `run_artifact_checks`;
`docs/claim-boundary.md` and `README.md`.
**Effects:** none. The formal layer is pure (D-1); the change adds no store, Git, process or
network read or write.

## Promise

`vouch-mk2 formal` explores a new universe, `refreeze-v1`, that starts at the `standard` tier and
reaches D-36's fresh-GREEN requirement: a unit re-frozen with a bound inventory different from its
last bound inventory waits at `done-pending-acceptance` with the requirement on, and publishes
only after a recorded GREEN turns it off. A new invariant, M12, runs in every universe that runs
M1–M11 in full, in `effects-v1`, and in `refreeze-v1`. It fails a relation in which (set) a
transition other than an owner rebind binds a unit to an inventory different from its last bound
inventory and leaves the requirement off, (honour) a unit enters `published` at `standard` or
`high` while its requirement is on, or (clear) the requirement turns off on a transition that does
not record a GREEN for the unit's current test set. One private witness, W12, discriminates M12.
The certificate check's residual and the docs name M1–M12. `decide()`, `apply()`, the state and
event formats, and the relations of `core-v1`, `history-v1`, `tier-v1` and `effects-v1` do not
change.

## Common rules

- `_relation()` explores `refreeze-v1` once per process. A state "has the requirement on" when a
  unit in its snapshot has `refreeze_green_required`.
- The mutation tests (R5–R7) change only the M12 input they name: one transition's target is
  replaced by a forged copy, or one forged transition is added. M12 must hold on the unmutated
  relation and fail on each mutated one; they do not assert M12's violation text.

## Requirements

Each RED test states why it fails before the change.

- **R1 — A small gated universe that runs M12.**
  `test_refreeze_v1_is_a_small_gated_universe_that_runs_m12`. `refreeze-v1` is in `UNIVERSES`,
  its initial tier is `standard` or `high`, it explores fewer than 20,000 states, and
  `run_formal()` evaluates every invariant except M6 in it. *Before:* no universe named
  `refreeze-v1`.
- **R2 — The requirement is reached through a changed inventory.**
  `test_refreeze_v1_reaches_the_requirement_through_a_changed_inventory`. Some transition at a
  gated tier turns the requirement on, every such transition is a `freeze` or `bind-pack`, and some
  state has a `done-pending-acceptance` unit with the requirement on. *Before:* no universe named
  `refreeze-v1`.
- **R3 — No publish while it is on.** `test_refreeze_v1_never_publishes_while_the_requirement_is_on`.
  No transition of `refreeze-v1` moves a unit into `published` from a state where that unit has the
  requirement on. *Before:* no universe named `refreeze-v1`.
- **R4 — A GREEN turns it off and publishing follows.**
  `test_refreeze_v1_publishes_after_a_green_turns_the_requirement_off`. Some `record-evidence`
  transition turns the requirement off, and a transition into `published` is reachable from its
  target. *Before:* no universe named `refreeze-v1`.
- **R5 — M12 (set).** `test_m12_holds_on_the_real_relation_and_catches_a_change_that_leaves_it_off`.
  M12 holds on `refreeze-v1`; the first transition that turns the requirement on is redirected to a
  copy of its target with the requirement off, and M12 fails. *Before:* no invariant numbered M12.
- **R6 — M12 (honour).** `test_m12_catches_a_publish_while_the_requirement_is_on`. A forged
  `publish` transition is added from a gated state whose `done-pending-acceptance` unit has the
  requirement on to a copy in which that unit is `published`; M12 fails. *Before:* no invariant
  numbered M12.
- **R7 — M12 (clear).** `test_m12_catches_the_requirement_turning_off_without_a_green`. The first
  transition that is not `record-evidence` and keeps the requirement on is redirected to a copy of
  its target with the requirement off; M12 fails. *Before:* no invariant numbered M12.
- **R8 — Formal passes and W12 discriminates.**
  `test_formal_runs_m12_everywhere_and_its_witness_discriminates`. Exactly one witness owns M12,
  `passed(run_formal())`, M12 holds in `core-v1`, `history-v1`, `effects-v1` and `refreeze-v1`,
  and M12's witness discriminates. *Before:* no invariant numbered M12.
- **R9 — The formal command reports them.** `test_the_formal_command_reports_refreeze_v1_and_m12`.
  `vouch-mk2 formal` exits 0, prints `universe: refreeze-v1`, a `PASS` line for M12 and a
  `DISCRIMINATED` line for a `W12.` witness owning M12. *Before:* no invariant numbered M12.
- **R10 — The certificate check's residual.**
  `test_the_certificate_check_names_m1_to_m12_as_its_residual`. A certificate covering the
  current registries passes `check.formal-certificate`, and its detail contains
  `executing M1-M12` and not `M1-M11`. *Before:* the detail says `executing M1-M11`.
- **R11 — The docs.** `test_the_docs_state_twelve_invariants_and_the_refreeze_universe`. Matching
  ignores runs of whitespace. `docs/claim-boundary.md` contains `evaluates M1–M12`, `ran M1–M12`,
  `` `vouch-mk2 formal` performs that full exploration over every formal universe `` and the
  sentence `` The `refreeze-v1` formal universe reaches D-36's fresh-GREEN requirement at the
  `standard` tier, and M12 checks that a changed pack turns it on, that no unit publishes while it
  is on, and that only a GREEN for the current pack turns it off. ``, and no `M1–M11`. `README.md`
  contains `checking twelve hand-written invariants` and not `eleven hand-written`. *Before:* the
  docs say M1–M11 and eleven.

There are no guards: every test needs the new universe, the new invariant or the new wording.

## Ground rules

- `refreeze-v1` may exclude M6 only, as `effects-v1` does, with a comment naming the bound: a
  unit marked done without evidence has no exit once the universe's deviation ids are used up.
- W12 joins the witness registry under its rules (one private mutation per invariant). It runs on
  `core-v1`'s relation like the others, so it forges its own gated states.
- `decide()`, `apply()`, `core/`, `store/`, the codec, `CHECKER_VERSION`, and the state and event
  formats do not change. `core-v1`, `history-v1`, `tier-v1` and `effects-v1` keep their states,
  transitions and relation digests.
- Frozen tests of published units that pin facts this unit changes are adapted by the owner, not
  the implementer: `effect-declarations@1` (the list of universe names) and `formal-memory@1` (the
  recorded relation digests and certificate digest). The implementer reports which fail and why,
  and does not edit them. Unbound tests that pin `M1-M11` are updated by the implementer and listed.
- The quickstart and the other docs keep every pinned phrase.

## Verification outside pytest (final pass)

The owner re-records mk2's certificate with `vouch-mk2 formal --record --root .vouch`, and checks
in a scratch tree that each of three `decide()` mutants (the publish gate ignores the requirement;
the requirement is never turned on; any recorded evidence turns it off) makes `vouch-mk2 formal`
fail on `refreeze-v1`.
