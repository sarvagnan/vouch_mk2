# Quickstart

This walkthrough takes a scratch project from `init` to a published unit and a completed
release. `tests/test_operator_docs.py` runs every `sh` block, in order, against a freshly built
`vouch.lock`, so the commands are known to work. Where the prose and `--help` disagree, trust
`--help`. For exactly what each step proves, see [claim-boundary.md](claim-boundary.md).

You need a POSIX `sh`, Python 3.14 or newer with `pytest` installed, and `vouch.lock`. A source
checkout does not include the lock; build one with `vouch-mk2 build-lock --output vouch.lock`
or obtain the released file. Nothing here uses the network.

## 1. Initialize a project

`init` creates the store in `.vouch/` with CI policy `strict`, records the lock's digest in
`.vouch/config.json`, and writes a Python 3.14 CI workflow. `--tier lightweight|standard|high` sets the
starting assurance tier (default `lightweight`). `self-check` confirms that the running lock
matches the recorded digest. You control both files, so this is a consistency check, not proof
that the pin is trustworthy (see D-2).

```sh
python3 vouch.lock init --project . --lock vouch.lock
python3 vouch.lock self-check --expect @.vouch/config.json
```

From here on, every command is `python3 vouch.lock <verb> --root .vouch …`. `--help` on any
verb lists its options, and [commands.md](commands.md) collects them. `record-evidence` also
takes `-- <argv…>` after its options.

## 2. Specify a unit and freeze its acceptance pack

A unit is a named, versioned piece of work. Its acceptance pack is the tests that define done,
frozen before implementation starts. Freezing records a pack-version label you choose. With
`--pack-file`, Vouch also stores the pack files' paths, sizes and sha256 hashes, and `check --project .`
fails from then on if any of them change. Vouch cannot prove that the freeze came before the
implementation, because that work is not recorded; keeping the order is up to your process.

Here the pack is one failing test:

```sh
python3 vouch.lock specify --root .vouch --name greeter --version v1
mkdir -p tests
cat > tests/test_greeter.py <<'PY'
from greeter import greet

def test_greets_by_name():
    assert greet("Ada") == "Hello, Ada"
PY
```

Freeze the unit with the pack file bound. A short digest in the label is a readable convention;
the binding is what Vouch checks. At `standard` and above every frozen unit must be bound. At
`lightweight` a label-only freeze is allowed and reported as NOT-APPLICABLE.

```sh
python3 vouch.lock freeze --root .vouch --name greeter \
  --pack-version "greeter-pack/1@$(shasum -a 256 tests/test_greeter.py | cut -c1-16)" \
  --pack-file tests/test_greeter.py
python3 vouch.lock mark-ready --root .vouch --name greeter
python3 vouch.lock start --root .vouch --name greeter
```

## 3. Record evidence: red, then green

`record-evidence` runs the command after `--` and records the outcome itself; you cannot type
one in. It claims only that the recorded outcome is the exit status of the process it started.
Whether that process was an honest test runner is up to your harness. Recording is refused
until the pack is frozen. It is also refused if, while the command ran, a pack change,
supersession or re-versioning moved the unit or its test-set digest; an unrelated revision bump
is not refused.

`--redact-arg INDEX`, placed before `--`, stores `<redacted>` in place of an argument while the
child still receives the real value. Positions count from zero and include `argv[0]`, which
cannot itself be redacted. Child output is not redacted, so prefer passing secrets through the
environment. This first record redacts argument 3:

```sh
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind red \
  --redact-arg 3 -- python3 -c \
  "import sys; raise SystemExit(1 if sys.argv[1] == 'private-value' else 0)" private-value
```

Now the real test run. It fails because `greeter` does not exist yet. A `red` record shows the
spawned command exited non-zero before the implementation existed; when the command is your test
runner, that shows the tests can fail:

```sh
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind red -- \
  python3 -m pytest -q tests
```

Implement the unit, then record green:

```sh
cat > greeter.py <<'PY'
def greet(name: str) -> str:
    return f"Hello, {name}"
PY
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind green -- \
  python3 -m pytest -q tests
```

Recording `green` from a failing command is refused, because the kind must match the outcome.
This block is expected to exit 2:

```sh refuses
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind green -- \
  python3 -c "raise SystemExit(1)"
```

## 4. Check what is next, then publish

`next` lists each legal next step with the store root and subject filled in. Some verbs need
more arguments (`freeze --pack-version`, `set-criteria`, …); see `--help`.

```sh
python3 vouch.lock next --root .vouch
python3 vouch.lock mark-done --root .vouch --name greeter
python3 vouch.lock publish --root .vouch --name greeter
```

`publish` succeeds only if the definition-of-done gates pass: a recorded pack version,
satisfied dependencies, no blocking deviations, and the evidence the tier requires. At
`standard` and `high`, `gate.dod.current-pack-green` also requires RED then GREEN for the
current pack, with green as the latest result. If a shared interface changes after publication,
`rebind-pack --reason …` updates the bound files without changing the label or evidence chain;
checks then require a fresh GREEN.

A refused lifecycle command prints the violation code and leaves the store unchanged, though files a
`record-evidence` child changed outside the store stay changed. Publishing twice is refused:

```sh refuses
python3 vouch.lock publish --root .vouch --name greeter
```

## 5. When the pack was wrong: a deviation

Suppose the test should have required an exclamation mark. The implementer does not edit a
frozen pack. They open a deviation, and the acceptance owner decides it. Vouch records these
actions but has no notion of who holds which role: it neither prevents the edit nor
authenticates the owner.

Opening a deviation blocks the unit. Amending records the new pack version and invalidates the
units it affects. You then close the deviation, respecify, freeze the new pack and record
evidence again. `check` rejects a chain whose first green has no earlier red, but nothing forces
an eventual green.

```sh
python3 vouch.lock open-deviation --root .vouch --deviation-id DEV-1 \
  --deviation-type pack-defect --unit greeter@v1
python3 vouch.lock status --root .vouch
cat > tests/test_greeter.py <<'PY'
from greeter import greet

def test_greets_by_name():
    assert greet("Ada") == "Hello, Ada!"
PY
python3 vouch.lock adjudicate-amend --root .vouch --deviation-id DEV-1 \
  --amendment-version "greeter-pack/2@$(shasum -a 256 tests/test_greeter.py | cut -c1-16)"
python3 vouch.lock close-deviation --root .vouch --deviation-id DEV-1
python3 vouch.lock respecify --root .vouch --name greeter
python3 vouch.lock freeze --root .vouch --name greeter \
  --pack-version "greeter-pack/2@$(shasum -a 256 tests/test_greeter.py | cut -c1-16)" \
  --pack-file tests/test_greeter.py
python3 vouch.lock mark-ready --root .vouch --name greeter
python3 vouch.lock start --root .vouch --name greeter
```

The amended pack fails against the old implementation and passes after the fix:

```sh
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind red -- \
  python3 -m pytest -q tests
cat > greeter.py <<'PY'
def greet(name: str) -> str:
    return f"Hello, {name}!"
PY
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind green -- \
  python3 -m pytest -q tests
python3 vouch.lock mark-done --root .vouch --name greeter
python3 vouch.lock publish --root .vouch --name greeter
```

The store now holds the closed deviation, the unit at its current pack version, and four
evidence records. The snapshot keeps only the current pack version; the deviation and the
evidence chain carry the history.

## 6. Assurance tier and anchoring

The tier sets how much evidence the project demands. It is changed only by a recorded command
and can be raised at any time, but not lowered once work is committed. At `standard`,
publishing needs an evidence chain, and a passing report also needs anchor mode `ci` and a chain
anchored past GENESIS.

Anchors only move forward. CI is meant to advance them, but `advance-anchor` is an ordinary
command. A local advance has no workflow-run claim, so in anchor mode `ci`,
`check.evidence-anchors` stays `NOT_CHECKED` with `no workflow run claim` until a real CI run
advances the anchor. That is expected; do not lower the anchor mode to avoid it. This
walkthrough sets an example `GITHUB_RUN_ID` to take the same path CI does. Vouch records the
value without verifying it.

```sh
python3 vouch.lock set-tier --root .vouch --tier standard
python3 vouch.lock set-anchor-mode --root .vouch --mode ci
GITHUB_RUN_ID=123456 python3 vouch.lock advance-anchor --root .vouch --unit-name greeter --unit-version v1
```

`--unit-version` claims an exact unit reference; without it, the current version is used. CI
claims every unclaimed head by name and version, including a superseded version's head after a
rebind.

The tier covers only what Vouch can check. Every other control in the tier matrix appears in
`check` as an organizational NOT-CHECKED row naming the tier and control, so a declared tier
never implies a control was checked.

### Wiring up CI

The generated workflow has three jobs: read-only validation, read-only anchor planning, and a
publisher, the only job that can write. Validation checks the lock digest, runs
`check --ci --pending-anchor-ok` and the formal check, and keeps no checkout credentials. The
flag lets the first otherwise-clean run reach planning with the pending anchor row still shown;
any FAIL or other mechanical `NOT_CHECKED` row still fails. Direct runs of the generated
workflow validate only and never anchor.

Pass each project test job to `init` with `--test-job`. `init` prints a caller job to paste into
the workflow that defines those jobs; it does not edit that workflow. For example:

```yaml
jobs:
  # unit-tests and lint are defined in this same adopter workflow.
  vouch:
    needs: [unit-tests, lint]
    if: github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
    uses: ./.github/workflows/vouch.yml
    with:
      anchoring: true
    permissions:
      contents: write
```

The `needs:` edge is what makes your project tests gate anchoring. Once they pass, the planner
advances anchors, runs `check --ci` and `formal` again on the result, and prepares a
`.vouch/**` candidate commit. The publisher runs no repository code; it rechecks the
candidate's parent, paths, file modes and the live remote head, then pushes it as a bot commit.
The workflow does not set up required checks or branch protection, and cannot give a fork write
access to the base repository.

A bot commit pushed with `GITHUB_TOKEN` does not trigger another workflow run, which is why the
candidate is validated before the push. If your policy requires independent forge checks on
that commit, use GitHub App credentials; otherwise allow it with a scoped ruleset bypass.

CI policy is a separate store fact. New projects start `strict`: when `check --ci` runs, every
unit that has left `specified` must be published, release-bound or superseded.
`set-ci-policy --policy ledger` switches to the older coherence-only check. A unit genuinely in
flight can carry a visible exception, `mark-open-in-ci --name UNIT --reason REASON`, bound to
its exact current `UNIT@VERSION`. It clears when that record becomes terminal or inactive, and
`clear-open-in-ci --name UNIT` removes it sooner.

### Concurrent branches

`vouch-mk2 merge --root OURS --other THEIRS [--actor ACTOR]` reconciles two copies of a store.
It finds the deepest shared event record and replays THEIRS' divergent commands through
`decide()` on top of OURS. If THEIRS is already an ancestor, nothing is written; if OURS is an
ancestor, the merge fast-forwards. Otherwise the new records are appended to OURS with both
pre-merge heads and the actor claim. Histories with no shared record are refused
(`merge.unrelated-histories`), and one refused replay is a `merge.conflict` that leaves OURS
unchanged.

#### Recovering from a Git merge or rebase conflict

When Git reports unmerged paths under `.vouch`, recover from the index stages:

```text
python3 vouch.lock merge-git-stages --root .vouch
```

The adapter loads stage 0 files into both temporary stores, stage 2 as OURS and stage 3 as
THEIRS, and runs the merge above. In a merge, OURS is your current branch and THEIRS is the
branch being merged in. A rebase swaps them: OURS is the rebased series starting from upstream,
and THEIRS is your commit that Git is replaying.

On success it writes `map.json` and `vouch-context.md` but leaves the index unmerged. Review the
`.vouch` files, then stage them and continue:

```text
git add -- .vouch
git merge --continue
```

For a rebase, review and stage the same way, then:

```text
git rebase --continue
```

Recovery refuses if there is no unmerged path under the root, a conflicted path lacks a stage 2
or stage 3 blob (as in delete/modify conflicts), an index entry's mode is not `100644`, a path
escapes the root, or the repository, root or publication ancestor is a symlink. A semantic
`merge.conflict` also leaves the working store and the index unchanged.

## 7. A release

A release rosters published units and carries launch criteria. All four criteria fields are
required; Vouch does not invent a go/no-go authority for you. A release then moves through
deploy, verification and completion.

```sh
python3 vouch.lock prepare-release --root .vouch --release-id r1 --includes greeter
python3 vouch.lock set-criteria --root .vouch --release-id r1 \
  --launch-criteria "smoke test green" --verification-window 24h \
  --rollback-trigger "error rate > 1%" --go-no-go-authority release-owner
```

`bundle-inventory` hashes the release tree into an inventory, and `bind-bundle` binds it to the
release:

```sh
inventory_digest="$(python3 vouch.lock bundle-inventory --root .vouch --tree . --exclude '.bin/**' --exclude '.vouch/**' | awk '{print $NF}')"
python3 vouch.lock bind-bundle --root .vouch --release-id r1 --inventory-digest "$inventory_digest"
```

```sh
python3 vouch.lock start-deploy --root .vouch --release-id r1
python3 vouch.lock start-verification --root .vouch --release-id r1
python3 vouch.lock complete --root .vouch --release-id r1
```

## 8. Check and read the report

`check --ci` runs every mechanical check and exits 0 only if the composed verdict is PASS.
`generate` writes the system map and agent context, and `check` verifies both are fresh.
`check.event-log` replays the event log and requires it to reproduce the committed snapshot,
which is a derived cache, byte for byte.

`check` does not re-explore the bounded formal model. At `high` it cheaply verifies the
recorded certificate, current policy tables, and checker identity, then reports
`NOT_CHECKED` for invariant execution and mutation-witness discrimination. Run
`python3 vouch.lock formal --record --root .vouch` when you intend to perform and record
that expensive exploration.

```sh
python3 vouch.lock generate --root .vouch
python3 vouch.lock check --ci --root .vouch
```

Mechanical rows come first: PASS, FAIL, NOT-CHECKED or NOT-APPLICABLE, with the applicability
reason where there is one. A chain never anchored under `ci` mode, for example, is a mechanical
NOT-CHECKED. Organizational rows follow, and are always NOT-CHECKED because no artifact can
prove them.

## 9. Formats and upgrades

`state.json` carries a `state_format`, and Vouch will not read an older one. `upgrade` rewrites
`state.json` atomically, invents no state and regenerates the projections. A store from before the
event log gets a pre-log checkpoint as its first record. Everything before the checkpoint is attested rather than verified, and
replay proves nothing about pre-log history; the store's git history is the backstop.

Where the old store lacks an answer, `upgrade` refuses and asks you for it. For example, a
legacy unit without `published_once` is unambiguous only if its status is `published` or
`release-bound`; otherwise assert it with `--was-published NAME@V` or `--never-published NAME@V`.
`--dry-run` shows what would happen:

```sh
python3 vouch.lock upgrade --root .vouch --dry-run
```

## Next

- [commands.md](commands.md): every verb's `--help`.
- [claim-boundary.md](claim-boundary.md): exactly what a report claims.
- [method.md](method.md): how mk2's own development runs through this lifecycle.
- `python3 vouch.lock mcp --root .vouch` serves the store to agents read-only, and
  `python3 vouch.lock hook …` only warns. Neither can change the store.
