# Vouch: keeping a checkable record of agent-delivered work

A passing test suite on an agent's pull request leaves a question for the reviewer. Which tests
was the work supposed to pass, and what ran along the way? Vouch is a command-line tool that
records the files bound to an acceptance pack and whether each command it runs exits zero or
non-zero.
It cannot prove that the tests were frozen before the code was written.

Browse the [documentation](docs/index.md), run the [quickstart](docs/quickstart.md), or look up
a flag in the [command reference](docs/commands.md).

## How a unit works

Decide what to build and give each piece of work a name and version, such as `greeter@v1`.
The acceptance pack describes the work and holds the tests the unit is expected to pass.

1. **Specify and freeze.** Write the acceptance pack. When you bind files to it, Vouch records
   their hashes. At `standard` and `high`, run `python3 vouch.lock check --root .vouch --project .`
   to compare active bound packs with the project tree. New stores created by `init` require an
   effects declaration for a bound pack.
2. **Record RED.** Mark the unit ready and start it. Before implementation, run the intended
   test command through `record-evidence` and let Vouch record that it exited non-zero.
3. **Implement and record GREEN.** Build the change and run the same tests through
   `record-evidence` to record that they exited zero.
4. **Publish.** Mark the unit done, then publish. Vouch checks that a pack version is recorded,
   dependencies are satisfied, and no blocking deviations remain.
   At the default `lightweight` tier, `publish` has no evidence gate; at `standard` and `high`
   it needs a GREEN for the current pack, after a RED for the same tests or an owner rebind,
   and after any re-freeze that changed the pack.
   "The same tests" means the same frozen test set; Vouch does not compare the commands that RED
   and GREEN ran. The [tier guide](docs/concepts.md#assurance-tiers) covers the other gates and
   what the report checks.

If a frozen test is wrong, the implementer opens a deviation and the acceptance owner decides
whether to amend the pack or keep it. A reviewer can read the frozen tests and assess whether
they cover the intended behavior. Vouch records who claimed each step but does not verify their
identity. Keeping the roles separate is up to your team.

## What the record shows

> Vouch checks only what artifacts can prove; where it cannot, it prints `NOT_CHECKED` rather than offering a button that fakes it.

For `record-evidence`, the recorded outcome is the exit status of a process this verb spawned,
at the time it spawned it. Vouch stores that status only as zero (passed) or non-zero (failed),
not the exact number. Whether the command was a test runner and
whether the runner was honest remain questions for the reviewer. See the [claim boundary](docs/claim-boundary.md).

## Getting started

Vouch needs Python 3.14 or newer. Run it from your repository with `python3 vouch.lock`.
The lock is a reproducible zipapp. From the Vouch source checkout, build it with
`uv run vouch-mk2 build-lock --output vouch.lock`. The [quickstart](docs/quickstart.md)
walks through a scratch project from initialization to release.

## Where mk2 came from

mk2 rebuilds Vouch 0.6 based on two independent reviews of its development history. Changes
to mk2's behavior go through its own lifecycle (D-11). See [contributing](docs/contributing.md)
for the package layout and working method.

The formal checker explores bounded policy models, checking twelve hand-written invariants.
A store's state file carries a format number, and `vouch-mk2 upgrade` moves older stores forward.
Format 1 and later declare `state_format`; absence denotes legacy format 0.

## Gates

These commands run in the `gates` job in `.github/workflows/ci.yml`. On a push to the default branch
(`main`) of the repository itself, not a fork, the gates job skips `formal` and the anchor
planner runs it instead (D-43). Locally, `RUNNER_TEMP` stands in
for the CI runner's scratch directory. Run the block as one Bash script with `bash -eo pipefail`,
as CI does, so the first failing command stops it.

```sh
RUNNER_TEMP=$(mktemp -d)
uv sync --locked
uv run python scripts/gen_commands_doc.py --check
uv run python scripts/gen_lifecycle_doc.py --check
uv run pyrefly check     # strict types (D-1)
uv run lint-imports      # policy IR purity contract (D-1)
uv run pytest            # 1515 tests: matrices, property BFS, fault rig, formal, transports, migration
uv run python -c "from vouch_mk2.store import Store; store = Store.init('$RUNNER_TEMP/vouch-mk2-ci-check'); store.close()"
uv run vouch-mk2 check --ci --root "$RUNNER_TEMP/vouch-mk2-ci-check"
uv run vouch-mk2 check --ci --json --root "$RUNNER_TEMP/vouch-mk2-ci-check" | python3 -m json.tool > /dev/null
uv run vouch-mk2 generate --root "$RUNNER_TEMP/vouch-mk2-ci-check"
uv run vouch-mk2 check --ci --root "$RUNNER_TEMP/vouch-mk2-ci-check"
uv run vouch-mk2 formal  # invariants over the bounded relations
uv run vouch-mk2 build-lock --output "$RUNNER_TEMP/vouch.lock" | tee "$RUNNER_TEMP/lock-digest.txt"
uv run vouch-mk2 build-lock --output "$RUNNER_TEMP/vouch2.lock" > /dev/null
cmp "$RUNNER_TEMP/vouch.lock" "$RUNNER_TEMP/vouch2.lock"
python3 "$RUNNER_TEMP/vouch.lock" self-check --expect "$(awk '{print $2}' "$RUNNER_TEMP/lock-digest.txt")"
python3 "$RUNNER_TEMP/vouch.lock" check --ci --root "$RUNNER_TEMP/vouch-mk2-ci-check"
python3 "$RUNNER_TEMP/vouch.lock" record-evidence --help
```
