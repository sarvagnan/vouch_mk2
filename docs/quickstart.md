# Quickstart

This walkthrough takes a scratch project from `init` to a published unit and a completed release.
`tests/test_operator_docs.py` runs every `sh` block in order against a freshly built `vouch.lock`,
so the commands are known to work. Where this guide and `--help` disagree, trust `--help`.
For exactly what each step proves, see [claim-boundary.md](claim-boundary.md).

**Prerequisites** (nothing here uses the network):
- A POSIX `sh`
- Python 3.14 or newer with `pytest` installed
- `vouch.lock` — a source checkout does not include the lock; build one with
  `vouch-mk2 build-lock --output vouch.lock` or obtain the released file

## 1. Initialize a project

`init` creates a Vouch store in `.vouch/` with CI policy `strict`, records the lock's digest in
`.vouch/config.json`, and writes a Python 3.14 CI workflow. Use
`--tier lightweight|standard|high` to set the starting assurance tier (default: `lightweight`).

`self-check` confirms that the running `vouch.lock` matches the digest recorded in
`.vouch/config.json`. Because you control both files, this is a consistency check, not proof that
the pin is trustworthy (see D-2).

```sh
python3 vouch.lock init --project . --lock vouch.lock
python3 vouch.lock self-check --expect @.vouch/config.json
```

Store commands in this walkthrough use `python3 vouch.lock <verb> --root .vouch …`.
`refresh-workflow` takes `--project`. Run `--help` on any verb to see its options, or read
[commands.md](commands.md). `record-evidence` also takes `-- <argv…>` after its options.

## 2. Specify a unit and freeze its acceptance pack

A **unit** is a named, versioned piece of work. Its **acceptance pack** is the tests that define
done, frozen before implementation starts.

Freezing records a pack-version label you choose. With `--pack-file`, Vouch also stores each pack
file's path, size, and sha256 hash; from then on, `check --project .` fails if any bound file
changes. Vouch cannot prove that the freeze happened before implementation, because implementation
work is not recorded; keeping that order is up to your process.

**Naming rules:**
- A unit name or version may not begin with `-`, because a CLI option reads a separate value token
  starting with `-` as another option. `specify` refuses such a name or version; history a store
  already holds, or adopts through a fast-forward merge, is not re-checked.
- A deviation id, release id, pack version or amendment version may not begin with `-` either.

**Effect declarations:** New stores require an effect declaration for bound packs. An empty list
declares a transform with no effects; observations disclose a source and capture, and actions
disclose a system and declared outcome. Run `show-effects --root .vouch` to inspect these
declarations.

Here the pack is one failing test:

```sh
python3 vouch.lock specify --root .vouch --name greeter --version v1
mkdir -p tests packs
cat > packs/greeter-1.effects.json <<'JSON'
{"effects_format": 1, "effects": []}
JSON
cat > tests/test_greeter.py <<'PY'
from greeter import greet

def test_greets_by_name():
    assert greet("Ada") == "Hello, Ada"
PY
```

Next, freeze the unit with the pack file bound. Including a short digest in the label is a
readable convention; the file binding is what Vouch checks. At `standard` and above, every frozen
unit must be bound. At `lightweight`, a label-only freeze is allowed and reported as
NOT-APPLICABLE.

```sh
python3 vouch.lock freeze --root .vouch --name greeter \
  --pack-version "greeter-pack/1@$(shasum -a 256 tests/test_greeter.py | cut -c1-16)" \
  --pack-file tests/test_greeter.py \
  --pack-effects-file packs/greeter-1.effects.json
python3 vouch.lock mark-ready --root .vouch --name greeter
python3 vouch.lock start --root .vouch --name greeter
```

## 3. Record evidence: red, then green

`record-evidence` runs the command after `--` and records the exit status itself; you cannot type
a result in. It claims only that the recorded outcome is the exit status of the process it
started. Whether that process was an honest test runner is up to your harness.

These lifecycle conditions refuse `record-evidence`:
- The pack is not yet frozen.
- While the command ran, a pack change, supersession, or re-versioning moved the unit or its
  test-set digest. (An unrelated revision bump is not refused.)

To keep a secret argument out of the store, pass `--redact-arg INDEX` before `--`. Vouch stores
`<redacted>` in place of that argument while still passing the real value to the child process.
Positions are zero-based and include `argv[0]`, which cannot itself be redacted. Child output is
not redacted, so prefer passing secrets through the environment. This example redacts argument 3:

```sh
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind red \
  --redact-arg 3 -- python3 -c \
  "import sys; raise SystemExit(1 if sys.argv[1] == 'private-value' else 0)" private-value
```

Now run the real test suite. It fails because `greeter` does not exist yet. A `red` record shows
that the spawned command exited non-zero before the implementation existed; when the command is
your test runner, that shows the tests can fail:

```sh
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind red -- \
  python3 -m pytest -q tests
```

Implement the unit, then record `green`:

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

`next` lists every legal next step with the store root and subject filled in. Some verbs need
extra arguments (such as `freeze --pack-version` or `set-criteria`); see `--help`.

```sh
python3 vouch.lock next --root .vouch
python3 vouch.lock mark-done --root .vouch --name greeter
python3 vouch.lock publish --root .vouch --name greeter
```

`publish` succeeds only if all definition-of-done gates pass:
- A recorded pack version
- Satisfied dependencies
- No blocking deviations
- The evidence required by the current tier

At `standard` and `high`, `gate.dod.current-pack-green` also requires RED then GREEN for the
current pack, with green as the latest result. A re-freeze that changes any bound pack file needs
a GREEN recorded after it or after the `bind-pack` that bound it; RED stays keyed to the test-set
digest. If a shared interface changes after publication, `rebind-pack --reason …` updates the
bound files without changing the label or evidence chain; checks then require a fresh GREEN.

A refused lifecycle command prints its violation code and leaves the store unchanged. (Files that
a `record-evidence` child changed outside the store stay changed.) Publishing twice is refused:

```sh refuses
python3 vouch.lock publish --root .vouch --name greeter
```

## 5. When the pack was wrong: a deviation

Suppose the test should have required an exclamation mark. The implementer does not edit a frozen
pack directly. Instead, they open a **deviation**, and the acceptance owner decides it. Vouch
records these actions but has no notion of who holds which role: it neither prevents edits nor
authenticates the owner.

The deviation flow works like this:
1. Opening a deviation blocks the unit.
2. Amending records the new pack version and invalidates every unit it affects.
3. Close the deviation, respecify the unit, freeze the new pack, and record evidence again.

`check` rejects a chain whose first green has no earlier red, though nothing forces an eventual
green.

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
  --pack-file tests/test_greeter.py \
  --pack-effects-file packs/greeter-1.effects.json
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

The store now holds the closed deviation, the unit at its current pack version, and four evidence
records. The snapshot keeps only the current pack version; the deviation and the evidence chain
carry the history.

## 6. Assurance tier and anchoring

The **assurance tier** sets how much evidence the project demands. You change it only through a
recorded command. You can raise the tier at any time, but you cannot lower it once work is
committed. At `standard`, publishing requires an evidence chain, and a passing report also
requires anchor mode `ci` and a chain anchored past GENESIS.

**Anchors** only move forward. CI is meant to advance them, though `advance-anchor` is an ordinary
command:
- A local advance has no workflow-run claim. In anchor mode `ci`, `check.evidence-anchors` stays
  `NOT_CHECKED` with `no workflow run claim` until a real CI run advances the anchor. That is
  expected; do not lower the anchor mode to avoid it.
- For evidence recorded after a CI-claimed anchor, `check.evidence-anchors` reports `NOT_CHECKED`
  with `anchor lags its head` until the next CI run advances it.

This walkthrough sets an example `GITHUB_RUN_ID` to take the same path CI does. Vouch records the
value without verifying it.

```sh
python3 vouch.lock set-tier --root .vouch --tier standard
python3 vouch.lock set-anchor-mode --root .vouch --mode ci
GITHUB_RUN_ID=123456 python3 vouch.lock advance-anchor --root .vouch --unit-name greeter --unit-version v1
```

Pass `--unit-version` to claim an exact unit reference; without it, Vouch uses the current
version. CI claims every head that has no run claim or whose anchor differs from its head, by name
and version, including a superseded version's head after a rebind.

The tier covers only what Vouch can check. Every other control in the tier matrix appears in
`check` as an organizational NOT-CHECKED row naming the tier and control, so a declared tier never
implies a control was checked.

### Wiring up CI

The generated workflow has four jobs.
- **Validation (read-only):** checks the lock digest, runs `check --ci --pending-anchor-ok` and the
  formal check, and keeps no checkout credentials. `--pending-anchor-ok` lets the first
  otherwise-clean run reach planning while still showing the pending anchor row; any FAIL or other
  mechanical `NOT_CHECKED` row still fails.
- **Merge check (read-only)** runs on pull requests and checks the merged store against a replay
  of the head's store onto the base's store.
- **Anchor planning (read-only):** advances anchors, runs `check --ci` and `formal` again on the
  result, and prepares a `.vouch/**` candidate commit.
- **Publisher (only job with write access):** runs no repository code. It rechecks the candidate's
  parent, paths, file modes, and live remote head, then pushes it as a bot commit.

Direct runs of the generated workflow never anchor.

To gate anchoring on your project's tests, pass each test job to `init` with `--test-job`. `init`
prints a caller job for you to paste into the workflow that defines those jobs; it does not edit
that workflow. For example:

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

The `needs:` edge is what makes your project tests gate anchoring. The workflow does not set up
required checks or branch protection, and it cannot give a fork write access to the base
repository.

A bot commit pushed with `GITHUB_TOKEN` does not trigger another workflow run, which is why the
planner validates the candidate before the push. If your policy requires independent forge checks
on that commit, use GitHub App credentials; otherwise allow it with a scoped ruleset bypass.

**CI policy** is a separate store fact. New projects start at `strict`: when `check --ci` runs,
every unit past `specified` must be published, release-bound, or superseded. Run
`set-ci-policy --policy ledger` to switch to the older coherence-only check. A unit genuinely in
flight can carry a visible exception with `mark-open-in-ci --name UNIT --reason REASON`, bound to
its exact current `UNIT@VERSION`. That exception clears automatically when the record becomes
terminal or inactive, or sooner with `clear-open-in-ci --name UNIT`.

### Concurrent branches

`vouch-mk2 merge --root OURS --other THEIRS [--actor ACTOR]` reconciles two copies of a store.
It finds the deepest shared event record and replays THEIRS' divergent commands through `decide()`
on top of OURS:

- **Ancestor cases:** If THEIRS is already an ancestor of OURS, nothing is written. If OURS is an
  ancestor of THEIRS, the merge fast-forwards.
- **Divergent replay:** Otherwise, the replayed records are appended to OURS, ending with a merge
  record that names both pre-merge heads.
- **Provenance and companions:** The replayed records keep their source records' actor, run claim
  and recorded time, matched by event kind and subject, and the merge record carries the merge's
  actor claim. A companion record the replay generates with no matching record on THEIRS carries
  the merge's claims and `generated: true`, and subsequent copies keep that flag. A THEIRS
  companion the replay no longer produces is not written.
- **Refusals:** A command that would act on another unit version than it did on THEIRS, or an
  anchor whose evidence head on OURS differs from the one THEIRS promoted, is refused. Histories
  with no shared record are refused (`merge.unrelated-histories`). Any single refused replay is a
  `merge.conflict` that leaves OURS unchanged.

A merge refuses with `merge.conflict` when both sides changed the same setting or open-in-CI
exception to different values. This covers CI policy, anchor mode, assurance tier, the recorded
formal certificate, and each unit's open-in-CI reason or its absence. A write that has already
reached the other side through a merge is shared. Replay also refuses if it would undo a value
both sides agree on. To resolve different values, align one side with an ordinary command and
merge again. Every incoming command must still replay legally, so the direction can matter: if A
published a unit while B re-marked its exception and then cleared it to match, B's re-mark cannot
replay onto A's published unit, so merge A into B instead.

**Replayed merge boundaries and deduplication:**
- A replayed merge record names, as its OURS head, the record on the receiving chain where its
  replayed block begins. It keeps the original THEIRS head. If replay filtered out the original
  boundary, the record names the receiving head at that point in the replay.
- A merge skips a replayed copy of a record OURS already holds. It skips the whole command batch
  and continues with independent work. If OURS lacks the effect of a companion fact generated by
  replay in the skipped batch, the merge refuses. The merge reports batches newly excluded by
  origin separately from redundant clears.
- An incoming clear of an open-in-CI exception that OURS has already cleared is skipped without
  writing a record, provided the clear would resolve to the same exact unit reference. The merge
  reports how many redundant clears it skipped. If OURS cleared it and THEIRS cleared and marked
  it again, the merge refuses with `merge.conflict`.

#### Recovering from a Git merge or rebase conflict

When Git reports unmerged paths under `.vouch`, recover from the index stages:

```text
python3 vouch.lock merge-git-stages --root .vouch
```

The adapter loads stage 0 files into both temporary stores, stage 2 as OURS, and stage 3 as
THEIRS, then runs the merge above:
- In a **merge**, OURS is your current branch and THEIRS is the branch being merged in.
- In a **rebase**, Git swaps them: OURS is the rebased series starting from upstream, and THEIRS
  is your commit being replayed.

On success, `merge-git-stages` writes `map.json` and `vouch-context.md` but leaves the index
unmerged. Review the `.vouch` files, then stage them and continue:

```text
git add -- .vouch
git merge --continue
```

For a rebase, review and stage the same way, then run:

```text
git rebase --continue
```

Recovery refuses if any of the following hold:
- No unmerged path exists under the root
- A conflicted path lacks a stage 2 or stage 3 blob (as in delete/modify conflicts)
- An index entry's mode is not `100644`
- A path escapes the root
- The repository, root, or a publication ancestor is a symlink
- The working store already incorporates both stages, as after an earlier divergent recovery
  (`git.already-recovered`): review and stage it, or run `git checkout --conflict=merge -- .vouch`
  to merge again, which discards any changes made since. A working store whose head is a stage head,
  as after a fast-forward or no-op recovery, is replaced as before

A semantic `merge.conflict` also leaves the working store and the index unchanged. For conflicting
settings or exceptions, abort the Git merge or rebase, record the alignment, and retry the Git
operation so recovery can read fresh index stages. For a merge, a commit at either branch tip is
enough. A rebase replays the topic branch one commit at a time and stops at the conflicting commit
before it reaches a later one, so put the alignment in that commit (or an earlier one), or merge
instead of rebasing.

## 7. A release

A **release** rosters published units and carries launch criteria. All four criteria fields are
required; Vouch does not invent a go/no-go authority for you. A release then moves through deploy,
verification, and completion.

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

`check` does not re-explore the bounded formal model. With a valid recorded
certificate, `check.formal-certificate` reports `PASS` at every tier for the narrowed
obligation in [claim-boundary.md](claim-boundary.md#formal-certificate). Run
`python3 vouch.lock formal --record --root .vouch` when you intend to perform and record
that expensive exploration.

```sh
python3 vouch.lock generate --root .vouch
python3 vouch.lock check --ci --root .vouch --project .
```

Mechanical rows come first: PASS, FAIL, NOT-CHECKED or NOT-APPLICABLE, with the applicability
reason where there is one. A chain never anchored under `ci` mode, for example, is a mechanical
NOT-CHECKED. Organizational rows follow, and are always NOT-CHECKED because no artifact can
prove them.

## 9. Formats and upgrades

`state.json` records a `state_format`, and Vouch refuses to read an older format. `upgrade`
rewrites `state.json` atomically, invents no state, and regenerates the projections. A store from
before the event log gets a pre-log checkpoint as its first record. Everything before the
checkpoint is attested rather than verified, and replay proves nothing about pre-log history; the
store's git history is the backstop.

Where an old store lacks an answer, `upgrade` refuses and asks you for it. For example, a legacy
unit without `published_once` is unambiguous only if its status is `published` or `release-bound`;
otherwise assert it with `--was-published NAME@V` or `--never-published NAME@V`. Use `--dry-run`
to preview what would happen:

```sh
python3 vouch.lock upgrade --root .vouch --dry-run
```

`upgrade` reports how many version-1 merge markers a store holds.

### Re-pin the lock

From the project root, replace `vouch.lock` with the version you want to pin and set
`lock_digest` in `.vouch/config.json` to the digest printed by `python3 vouch.lock self-check`. Run
`python3 vouch.lock upgrade --root .vouch` to upgrade the store.

After you re-pin `vouch.lock`, run `python3 vouch.lock refresh-workflow --project .`.
Then run `python3 vouch.lock check --ci --root .vouch --project .` to check the refreshed
workflow and store. If the check passes, commit the changes.

## Next

- [commands.md](commands.md): every verb's `--help`.
- [claim-boundary.md](claim-boundary.md): exactly what a report claims.
- [method.md](method.md): how mk2's own development runs through this lifecycle.
- `python3 vouch.lock mcp --root .vouch` serves the store to agents read-only, and
  `python3 vouch.lock hook …` only warns. Neither can change the store.
