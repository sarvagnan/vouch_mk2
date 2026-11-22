# JSON and MCP reference

The `check`, `next`, and `status` examples come from a scratch project created with `init`,
after specifying `greeter@1`. The check report shows a subset of its rows.

## `check --json`

Run `python3 vouch.lock check --root .vouch --json`. Verdicts in JSON are lowercase and
hyphenated where applicable. Organizational `not-checked` rows describe the claim boundary
and do not worsen the composed verdict.

```json
{
  "composed": "pass",
  "mechanical": [
    {
      "check_id": "check.amended-deviations-complete",
      "detail": "IR applicability rule not met: subjects-present: amendment-records",
      "verdict": "not-applicable"
    },
    {
      "check_id": "check.dependencies-exist",
      "detail": "",
      "verdict": "pass"
    }
  ],
  "organizational": [
    {
      "check_id": "org.acceptance-ownership",
      "detail": "a recorded owner identity proves a string was recorded, not that a person with authority and time validated intent",
      "verdict": "not-checked"
    }
  ],
  "revision": 2
}
```

## `next --json`

Run `python3 vouch.lock next --root .vouch --json`. Each action gives a verb in `command`
and a suggested argument vector in `argv`. Supply values for the listed `placeholders`.
When running the lock, invoke the vector through `python3` from the directory holding
`vouch.lock`. For a unit action, `subject_id` is the unit name. On a store with no units, the
first action is `specify` with `subject_id` `<UNIT_NAME>` and an `argv` holding
`--name <UNIT_NAME> --version <VERSION>`; replace both, although only `--version` is listed in
`placeholders`.

```json
{
  "actions": [
    {
      "argv": [
        "vouch.lock",
        "freeze",
        "--root",
        ".vouch",
        "--name",
        "greeter",
        "--pack-version",
        "<PACK_VERSION>"
      ],
      "command": "freeze",
      "placeholders": [
        "--pack-version"
      ],
      "reason": "Record the acceptance pack and freeze.",
      "subject_id": "greeter",
      "subject_kind": "unit"
    }
  ]
}
```

## `status --json`

Run `python3 vouch.lock status --root .vouch --json` for counts and store policy facts.

```json
{
  "anchor_mode": "none",
  "anchor_run_claims": [],
  "anchored_current": 0,
  "ci_exceptions": [],
  "ci_policy": "strict",
  "deviation_counts": {},
  "effects_required": true,
  "evidence_chains": 0,
  "formal_certificate_recorded": false,
  "late_bound_pack_units": [],
  "next_action_count": 1,
  "pack_rebounds": [],
  "rebound_pack_units": [],
  "release_counts": {},
  "tier": "lightweight",
  "unbound_pack_units": [],
  "unit_counts": {
    "specified": 1
  }
}
```

## `show-evidence --json`

Run `python3 vouch.lock show-evidence --root .vouch --json` to read recorded evidence oldest
first for each non-superseded unit, or add `--unit NAME@VERSION` to read one exact version,
including a superseded version. This example records a failing pytest run for one greeter test,
then a passing run for both greeter tests after a fix. Both runs used `--junit-report`.

```json
{
  "revision": 7,
  "units": [
    {
      "chain_error": "",
      "current_pack_green": true,
      "current_test_set": "ad2c53f36dc9182e879a40866f35ca1782bc286dd94b5a5a4d74cf368fed36f8",
      "notes": [
        "GREEN ran a different command from RED"
      ],
      "records": [
        {
          "command": "python3 -m pytest -q '--junitxml={junit}' tests/test_greeter.py::test_greets_by_name",
          "counts": "red",
          "digest": "dff3b16103a657adc1c81053902891ebe9df117ce42e3445bf3f3b18fa29a4f8",
          "kind": "red",
          "outcome": "tests-failed",
          "recorded_at": "2026-11-22T15:48:30+00:00",
          "sequence": 1,
          "test_report": "91300c3a9e2f6e68ad75c7e89d722bca4ef899064a37a0c57619c25d5bd04e75",
          "test_set": "ad2c53f36dc9182e879a40866f35ca1782bc286dd94b5a5a4d74cf368fed36f8"
        },
        {
          "command": "python3 -m pytest -q '--junitxml={junit}' tests/test_greeter.py",
          "counts": "green",
          "digest": "03e1d49c1a82265a89271e8649d8d9b7a82dc7e2572c6bf4b9da42ed7c3882f1",
          "kind": "green",
          "outcome": "tests-passed",
          "recorded_at": "2026-11-22T15:48:30+00:00",
          "sequence": 2,
          "test_report": "d475ea96cd4dd1bfe075595cba3480ffedf5a056ef8a59f4b16666ed8b26c5ed",
          "test_set": "ad2c53f36dc9182e879a40866f35ca1782bc286dd94b5a5a4d74cf368fed36f8"
        }
      ],
      "status": "in-progress",
      "test_comparison": {
        "green_only": [
          "tests.test_greeter::test_greets_another_name"
        ],
        "red_failures_not_passed": [],
        "red_only": [],
        "relation": "superset"
      },
      "unit": "greeter@1"
    }
  ]
}
```

`current_pack_green` reports the evidence predicate used at `standard` and `high`.
On a readable chain with a true predicate, `counts` marks the latest explicit GREEN for the
current test set and the latest preceding RED for that set, if present. Other records have an
empty `counts` string. A command-line note compares the stored text, including any redactions.
`test_report` is the digest of the stored report or an empty string.
`test_comparison` is null unless both marked records have reports. Its `relation` describes the
GREEN report's IDs relative to the RED report's and is one of `equal`, `superset`, `subset`,
`overlap` or `disjoint`. The three ID lists are sorted. `red_failures_not_passed` includes
IDs with `failed` or `error` in RED and no `passed` outcome in GREEN, including IDs absent
from GREEN.

If a chain is unreadable, its unit has a nonempty `chain_error`, empty `records` and `notes`,
a null `test_comparison`, and the snapshot's `current_pack_green` value. The CLI prints the document and exits 1 if it
contains any chain error. An unknown `--unit` exits 2 and names the requested unit on stderr.

## Lifecycle `--json`

`python3 vouch.lock specify --root .vouch --name greeter --version 1 --json` on the new store
returned exit 0 and this accepted result.

```json
{
  "accepted": true,
  "events": [
    {
      "depends_on": [],
      "event": "unit-specified",
      "name": "greeter",
      "version": "1"
    }
  ],
  "revision": 2
}
```

The same command again returned exit 2 and this refusal. Some errors occur before the decision
engine and print a message instead of this JSON envelope.

```json
{
  "accepted": false,
  "violations": [
    {
      "code": "unit.exists",
      "detail": "unit 'greeter' already exists"
    }
  ]
}
```

The accepted `record-evidence --json` receipt for the GREEN above contains a format-3 record
and its `test_report` digest. A recording without `--junit-report` has format 2 and an empty
`test_report` string in the receipt. In JSON mode, the child's stdout goes to stderr.

```json
{
  "accepted": true,
  "events": [
    {
      "event": "evidence-recorded",
      "event_digest": "03e1d49c1a82265a89271e8649d8d9b7a82dc7e2572c6bf4b9da42ed7c3882f1",
      "evidence": {
        "command_line": "python3 -m pytest -q '--junitxml={junit}' tests/test_greeter.py",
        "evidence_format": 3,
        "kind": "green",
        "outcome": "tests-passed",
        "pack_digest": "ad2c53f36dc9182e879a40866f35ca1782bc286dd94b5a5a4d74cf368fed36f8",
        "parent": "dff3b16103a657adc1c81053902891ebe9df117ce42e3445bf3f3b18fa29a4f8",
        "recorded_at": "2026-11-22T15:48:30+00:00",
        "sequence": 2,
        "test_report": "d475ea96cd4dd1bfe075595cba3480ffedf5a056ef8a59f4b16666ed8b26c5ed",
        "unit": {
          "name": "greeter",
          "version": "1"
        }
      }
    }
  ],
  "revision": 7
}
```

## Effects declaration

Pass the file with `--pack-effects-file` when binding pack files. New projects initialized by
`init` require declarations for bound freezes and binds. Label-only freezes can still be
recorded and are reported as undeclared.

An empty list claims that the unit has no external observations or actions.

```json
{
  "effects_format": 1,
  "effects": []
}
```

Observations name a source and what is captured. Actions name a system and declare an outcome
of `confirmed`, `compensated`, or `irreversible`. Vouch validates declaration structure and
binds the bytes to the pack. Whether the declaration is true and complete remains
`NOT_CHECKED`.

```json
{
  "effects_format": 1,
  "effects": [
    {
      "kind": "observation",
      "name": "read-config",
      "source": "input configuration file",
      "capture": "output path"
    },
    {
      "kind": "action",
      "name": "write-report",
      "system": "local filesystem",
      "outcome": "confirmed",
      "detail": "writes the report to the configured output path"
    }
  ]
}
```

Read bound declarations with `python3 vouch.lock show-effects --root .vouch --json`.

## Read-only MCP

Run `python3 vouch.lock mcp --root .vouch`. The server uses line-delimited JSON-RPC 2.0 over
stdio. The store root is set at startup, and the read tools take no parameters. A successful
`tools/call` gives the payload in `structuredContent` and as JSON text in `content[0].text`.

| Tool | Payload |
|---|---|
| `check` | Report with the same keys as CLI `check --json`. It runs without a project tree. |
| `next` | `revision` and `actions`. Actions omit the CLI's `argv` and `placeholders`. |
| `status` | `revision` and `status`. The status object omits the CLI's `effects_required` field. |
| `map` | Canonical system map, including units, dependency edges, deviations, releases, and evidence heads. |
| `formal` | Bounded exploration results for the policy IR. This call does not record a certificate. |
| `evidence` | Same object as CLI `show-evidence --json` without `--unit`. Any chain error also sets `isError: true`. |

Example response to a `tools/call` request for `next`.

```json
{
  "id": 1,
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "text": "{\"actions\":[{\"command\":\"freeze\",\"reason\":\"Record the acceptance pack and freeze.\",\"subject_id\":\"greeter\",\"subject_kind\":\"unit\"}],\"revision\":2}",
        "type": "text"
      }
    ],
    "structuredContent": {
      "actions": [
        {
          "command": "freeze",
          "reason": "Record the acceptance pack and freeze.",
          "subject_id": "greeter",
          "subject_kind": "unit"
        }
      ],
      "revision": 2
    }
  }
}
```
