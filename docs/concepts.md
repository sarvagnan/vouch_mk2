# Vouch concepts

## What the store records

- A **unit** is a named, versioned piece of work such as `greeter@v1`. Dependencies refer to units
  by name.
- An **acceptance pack** describes the work and defines its tests. Use `--pack-file` for specs and
  other supporting files and `--pack-test-file` for test files. For a bound pack, Vouch records the
  files' SHA-256 hashes. At `standard` and `high`, Vouch checks active bound packs against
  the project tree when a project root is available.
- An **effects declaration** is a JSON file bound with the pack. It describes observations and
  external actions, or declares no effects with an empty list. Vouch validates its structure
  and binds its bytes. Its truth remains `NOT_CHECKED`.
- An **evidence chain** is an append-only record for an exact unit name and version.
  `record-evidence` runs the command after `--` and records whether it exited zero or nonzero
  if the evidence is accepted. Which tests ran, whether the runner was honest, and whether the
  implementation already existed require human review.
- A **deviation** records an issue against an exact unit name and version. An open or rejected
  deviation blocks the unit. Closing the deviation does not move the unit out of `blocked` or
  `invalidated`.
- A **release** rosters eligible published or release-bound units. Before deploying, it needs
  launch criteria, a verification window, a rollback trigger, and a go/no-go authority.
  A release can optionally bind a hashed bundle inventory. Its operational states cover
  deployment, verification, completion, and protective closure.

## From unit to release

The usual sequence is `specify` → `freeze` → `mark-ready` → `start` → `mark-done` → `publish`.
Run the intended tests through `record-evidence` for RED before implementation and for GREEN
after implementation. Recording evidence leaves the unit's lifecycle state as it was.

When a frozen test is wrong, open a deviation. An amendment invalidates the affected units
and releases. After closing the deviation, use `respecify` and freeze the amended pack.
If the owner rejects the deviation and the pack stands, close the deviation and use
`begin-reverify` while keeping the pack. Both recovery transitions require that no blocking
deviations remain. `begin-reverify` also requires a recorded pack.

`prepare-release` moves eligible rostered units to `release-bound`. See the
[lifecycle tables](lifecycle.md) for the full set of state transitions and the
[quickstart](quickstart.md) for runnable examples.

## Assurance tiers

The store's tier sets requirements for publication and for verification. Vouch refuses to lower
the tier once any unit is beyond `specified`. The anchor modes are `none` and `ci`.

| Tier | Publication evidence gates | Additional facts a passing report checks |
|---|---|---|
| `lightweight` (default) | No publication evidence gate. A freeze can record a pack label without binding files. | Anchor mode `none` is allowed. Existing evidence chains still have to pass the chain checks. |
| `standard` | An evidence head and a current-pack GREEN after RED for the same tests or after a qualifying rebind. A changed re-freeze requires a subsequent GREEN. | Active packs with a version must have a byte binding. Anchor mode must be `ci`, with current anchors carrying workflow run claims for recorded chains. |
| `high` | The standard evidence requirements plus an evidence head anchored past `GENESIS` and a recorded formal certificate. | The standard checks plus a valid recorded formal certificate. `check.pack-bound` also rejects late binding and rebound packs that lack fresh GREEN. |

"The same tests" means the same frozen test set; Vouch does not compare the commands that RED
and GREEN ran.

These are evidence requirements. Publication also checks the recorded pack version, dependencies
and blocking deviations. To interpret other report rows, see the [claim boundary](claim-boundary.md).

## Verification verdicts

Run `python3 vouch.lock check --root .vouch` to see the report. The composed mechanical verdict
is `FAIL` if any mechanical row fails. Otherwise `NOT_CHECKED` takes precedence over `PASS`.
`NOT_APPLICABLE` rows do not prevent a pass when at least one row passes, and a set of only
`NOT_APPLICABLE` rows composes to `NOT_APPLICABLE`. Plain `check` exits 0 for `PASS` and 1
for a composed non-pass.

Organizational `NOT_CHECKED` rows do not affect the exit code. They mark questions for human
judgment, such as whether the tests cover the intended behavior, whether the actors were
independent, and whether the declared effects were complete and truthful. The report fails
if an organizational evaluator violates its contract and the registry emits a `FAIL` row.
