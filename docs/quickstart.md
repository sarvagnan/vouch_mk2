# Vouch quickstart

This page is executable. Every `sh` block below is run, in order, by
`tests/test_operator_docs.py` against a freshly built `vouch.lock`, so the *commands* cannot
drift from the tool — if a block stops working, the test fails, not you. The prose around
them was reviewed against the code sentence by sentence, but it is prose: where it and a
`--help` disagree, trust `--help`.

You need: a POSIX `sh`, `python3` (3.14+) with `pytest` importable, and `vouch.lock` — the
single vendored artifact Vouch ships as (a source checkout does not contain it; build one with
`vouch-mk2 build-lock --output vouch.lock` or obtain the released file). Nothing is fetched
from the network at any step.

Vouch tracks *units of work* through a fixed lifecycle, refuses transitions the policy
forbids, spawns the evidence commands you give it and records their exit-derived outcomes,
and reports what it checked mechanically and — separately — what it did not. It never claims
more than that; `docs/claim-boundary.md` is the exact statement.

## 1. Initialise a project

`init` creates the store (`.vouch/`) with CI policy `strict`, records the lock's digest in
`.vouch/config.json`, and writes a Python 3.14 CI workflow fragment. Pass
`--tier lightweight|standard|high` to choose the initial assurance tier (the default is
`lightweight`). `self-check` verifies that the lock you are running has the
digest recorded in that config — it is a consistency check between two files you control,
not proof that either is a trustworthy pin (see D-2 for what sits outside that boundary).

```sh
python3 vouch.lock init --project . --lock vouch.lock
python3 vouch.lock self-check --expect @.vouch/config.json
```

Every command from here on is `python3 vouch.lock <verb> --root .vouch …`. `--help` on any verb
lists its options and `docs/commands.md` reproduces them; one exception is `record-evidence`,
which additionally takes `-- <argv…>` after its options.

## 2. Specify a unit and freeze its acceptance pack

A unit is named work with a version. Its *acceptance pack* — the tests that define done — is
meant to be frozen **before** implementation. Vouch records a pack-version *label* you supply
and, when you pass `--pack-file`, stores an inventory of the pack's files (path, size, sha256)
as a content-addressed object bound to the unit. From then on `check --project .` re-hashes
those files and FAILs if any moved — the goalposts are mechanically pinned. What Vouch still
cannot prove is *chronology relative to unrecorded implementation work*: that the freeze
preceded the implementation is your method's control. The event log is the authoritative chain
Vouch presents, and `check.event-log` proves the snapshot is derivable from that recorded chain by
replaying it through `apply()` and byte-comparing the derived cache. It does not independently
prove the real-world order in which events occurred. Here the pack is one failing test.

On a store upgraded from before event logging, the chain begins with a **pre-log checkpoint**.
Everything before that checkpoint is attested rather than verified, and replay proves nothing
about pre-log history; the store's git history is the backstop for those earlier revisions.

```sh
python3 vouch.lock specify --root .vouch --name greeter --version v1
mkdir -p tests
cat > tests/test_greeter.py <<'PY'
from greeter import greet

def test_greets_by_name():
    assert greet("Ada") == "Hello, Ada"
PY
```

Freeze it, binding the pack file. The version label is yours to name (embedding a short digest
is a readable convention); the binding is the mechanism. At tier `standard` and above, `check`
requires every frozen unit to be bound; at `lightweight`, a label-only freeze is allowed and
reported as NOT-APPLICABLE.

```sh
python3 vouch.lock freeze --root .vouch --name greeter \
  --pack-version "greeter-pack/1@$(shasum -a 256 tests/test_greeter.py | cut -c1-16)" \
  --pack-file tests/test_greeter.py
python3 vouch.lock mark-ready --root .vouch --name greeter
python3 vouch.lock start --root .vouch --name greeter
```

## 3. Record evidence — red first, then green

`record-evidence` **runs the command you give it after `--`** and records the outcome itself.
There is no flag to type an outcome by hand. What Vouch claims about this record is exactly:
the recorded outcome is the exit status of a process this verb spawned, at the time it
spawned it. It does not claim the command was a test runner, or that the runner was honest —
that is your harness's job. Recording is refused until the unit has a frozen acceptance
pack, and each record binds the current pack's test-set digest read while the store is
locked. Format-2 inventories mark `--pack-test-file` entries as executable tests and
`--pack-file` entries as specs; legacy format-1 packs keep binding the whole inventory.

The pack test fails now, because `greeter` does not exist yet. That is the point: a `red`
record proves the spawned command *exited non-zero* before any implementation existed —
which, if the command is your real test runner, is your tests proving they can fail.

```sh
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind red -- \
  python3 -m pytest -q tests
```

Now implement, and record `green`.

```sh
cat > greeter.py <<'PY'
def greet(name: str) -> str:
    return f"Hello, {name}"
PY
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind green -- \
  python3 -m pytest -q tests
```

Try to record a `green` on a failing tree and Vouch refuses — the kind must cohere with the
outcome it observed. This block is *expected* to exit 2:

```sh refuses
python3 vouch.lock record-evidence --root .vouch --unit greeter@v1 --kind green -- \
  python3 -c "raise SystemExit(1)"
```

## 4. Ask what is next, then publish

`next` prints, for each legal next step, the verb with its store root and subject bound;
verbs with further required arguments (`freeze --pack-version`, `set-criteria`, …) need those
added from `--help`.

```sh
python3 vouch.lock next --root .vouch
python3 vouch.lock mark-done --root .vouch --name greeter
python3 vouch.lock publish --root .vouch --name greeter
```

`publish` went through the definition-of-done gates: a pack version is recorded, dependencies
are satisfied, no blocking deviations, and the evidence the assurance tier requires is present.
At `standard` and `high`, `gate.dod.current-pack-green` additionally requires RED followed by
GREEN for the current pack's test-set digest; the latest evidence for the current pack must be
green (a later refactor preserves green, while a later red breaks it). A published or
release-bound unit may use `rebind-pack --reason ...` to adapt its bound files to a changed
shared interface without changing its pack label or evidence chain; checks then require a fresh
GREEN for the new test-set digest, with no artificial RED required.
A refused lifecycle command prints the violation code and leaves the store unchanged —
publishing twice, for instance (a child run by `record-evidence` may of course have changed
files *outside* the store before its record was refused):

```sh refuses
python3 vouch.lock publish --root .vouch --name greeter
```

## 5. When the pack was wrong: a deviation

Suppose the acceptance test should have demanded an exclamation mark. In the working method
the implementer does not edit a frozen pack; they raise a *deviation* and the acceptance owner
adjudicates it. Vouch records those roles' actions but has no notion of *who* — it neither
prevents the edit nor authenticates the owner. Mechanically: opening a deviation blocks the
unit; amending records the amendment version on the deviation and invalidates the units it
impacts; you then close it, respecify, freeze the new pack, and record evidence again. `check`
will reject a chain whose first green has no earlier red; nothing forces an eventual green.

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

The amended pack is genuinely red against the old implementation, and green after the fix:

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

The store now holds the closed deviation (with the amendment version it introduced), the
unit at its current pack version, and a chain of four immutable evidence objects. The
snapshot keeps the *current* pack version, not both; the deviation record and the chain are
what carry the history.

## 6. Assurance tier and anchoring

The *tier* says how much evidence the project demands. It lives in the store, is set by one
recorded command, and can be raised freely but not lowered once work is committed. At
`standard`, publishing requires an evidence chain, and a passing report additionally requires
anchor mode `ci` and a chain anchored beyond GENESIS. The anchor is promotion-only; the
intended operator is CI, but `advance-anchor` remains an ordinary command. A local promotion
has no workflow-run claim and stays honestly `NOT_CHECKED` in `ci` mode. This executable
walkthrough supplies an example `GITHUB_RUN_ID` claim to exercise the same path as the generated
workflow; Vouch records that caller-asserted value but does not verify it:

```sh
python3 vouch.lock set-tier --root .vouch --tier standard
python3 vouch.lock set-anchor-mode --root .vouch --mode ci
GITHUB_RUN_ID=123456 python3 vouch.lock advance-anchor --root .vouch --unit-name greeter
```

Consequently, in anchor mode `ci`, no project can compose `PASS` until a real CI run advances
its current evidence anchor. Before that first run, even a local `advance-anchor` leaves
`check.evidence-anchors` honestly `NOT_CHECKED` with `no workflow run claim`; this is expected,
not a reason to lower the anchor mode. One eligible CI run records the run-ID claim and resolves
the row.

The tier governs only what Vouch can mechanically check. Everything else in the tier matrix
appears in `check` as an organizational NOT-CHECKED row naming the tier and the control — a
declared tier never makes a report imply a control was checked.

The workflow generated by `init` runs only after its check and exhaustive formal steps pass.
It advances every evidence head with the CI run ID supplied by the workflow environment,
regenerates `.vouch/` projections, and pushes those files in a bot commit whose message contains
`[skip ci]`, preventing the anchor commit from retriggering the workflow. On a pull request from
a fork, the workflow's repository guard skips the entire anchor-and-push step; the fork cannot
write the base repository and its anchor rows remain `NOT_CHECKED` until a base-repository run
observes and advances them after merge.

CI policy is a separate recorded store fact. New projects start `strict`: every unit that
has left `specified` must be published, release-bound, or superseded when `check --ci` runs.
Use `set-ci-policy --policy ledger` for the older coherence-only behavior. A genuinely
in-flight unit can carry a visible, reasoned exception through
`mark-open-in-ci --name UNIT --reason REASON`; the exception is bound to the current exact
`UNIT@VERSION` and is cleared when that record becomes terminal or ceases to be active. Remove an
active exception with `clear-open-in-ci --name UNIT`. The `check.ci-policy` row names the enforced
policy and lists each exact exception ref and reason.

### Concurrent branches

Two copies of a store can be reconciled with
`vouch-mk2 merge --root OURS --other THEIRS [--actor ACTOR]`. Vouch finds the deepest
shared event-record digest and replays THEIRS' divergent commands through `decide()` against
the evolving OURS snapshot. If THEIRS is already an ancestor the operation writes nothing;
if OURS is an ancestor it fast-forwards to THEIRS without changing any of THEIRS' record
digests. A divergent merge appends newly decided records to OURS and records both pre-merge
heads plus the actor claim.

There is no snapshot-comparison fallback. Histories with no shared event record are refused
as `merge.unrelated-histories`, and one refused replay is a `merge.conflict` that leaves OURS
unchanged. A successful merge establishes that the resulting single log replays to its
snapshot. It does not establish broader semantic compatibility between the branches.

## 7. A release

Releases roster published units, carry launch criteria (all four fields are required — Vouch
does not invent a go/no-go authority for you), and move through deploy → verify → complete.

```sh
python3 vouch.lock prepare-release --root .vouch --release-id r1 --includes greeter
python3 vouch.lock set-criteria --root .vouch --release-id r1 \
  --launch-criteria "smoke test green" --verification-window 24h \
  --rollback-trigger "error rate > 1%" --go-no-go-authority release-owner
```

```sh
inventory_digest="$(python3 vouch.lock bundle-inventory --root .vouch --tree . --exclude '.bin/**' --exclude '.vouch/**' | awk '{print $NF}')"
python3 vouch.lock bind-bundle --root .vouch --release-id r1 --inventory-digest "$inventory_digest"
```

```sh
python3 vouch.lock start-deploy --root .vouch --release-id r1
python3 vouch.lock start-verification --root .vouch --release-id r1
python3 vouch.lock complete --root .vouch --release-id r1
```

## 8. Check, generate, and read the report

`check --ci` runs every mechanical check and exits 0 only if the composed verdict is PASS.
`generate` writes the system map and the agent context, both of which `check` verifies for
freshness against the state they were generated from.
`check.event-log` also walks the authoritative event log and replays it from the empty snapshot;
the committed snapshot is the O(1)-read derived cache and must reproduce byte-for-byte.
That derivation starts at a pre-log checkpoint for an upgraded legacy store: everything before
the checkpoint is attested rather than verified, and replay proves nothing about pre-log history.
Replay establishes consistency of the recorded chain presented to it, not independent wall-clock
proof that the events really occurred in that order.

`check` does not re-explore the bounded formal model. At `high` it cheaply verifies the
recorded certificate, current policy tables, and checker identity, then reports
`NOT_CHECKED` for invariant execution and mutation-witness discrimination. Run
`python3 vouch.lock formal --record --root .vouch` when you intend to perform and record
that expensive exploration.

```sh
python3 vouch.lock generate --root .vouch
python3 vouch.lock check --ci --root .vouch
```

Read the report top to bottom: mechanical rows first (PASS / FAIL / NOT-CHECKED /
NOT-APPLICABLE, with the applicability reason where there is one — a chain that has never
been anchored under `ci` mode is a mechanical NOT-CHECKED, for instance), then the
organizational rows, which are NOT-CHECKED by contract because they are never mechanically
provable — Vouch says so rather than pretending.

## 9. Formats and upgrades

`state.json` carries a `state_format`. A newer Vouch refuses to read an older store silently;
you run `upgrade`, which rewrites the file atomically, invents no state, and regenerates the
projections. Where the old document genuinely does not carry an answer — for a legacy unit
missing `published_once`, only a current `published` or `release-bound` status is unambiguous;
every other status may or may not have been published — `upgrade` refuses and asks you to
assert it with `--was-published NAME@V` or `--never-published NAME@V`. `--dry-run` shows what
would happen:

```sh
python3 vouch.lock upgrade --root .vouch --dry-run
```

## Where next

- `docs/commands.md` — every verb's `--help`, generated from the tool.
- `docs/claim-boundary.md` — exactly what a Vouch report does and does not claim.
- `docs/method.md` — how Vouch's own development runs as units through this same lifecycle.
- MCP (`python3 vouch.lock mcp --root .vouch`) serves the same store read-only to agents; the
  hook (`python3 vouch.lock hook …`) is warn-only. Neither can mutate the store.
