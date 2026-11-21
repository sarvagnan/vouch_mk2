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
including a superseded version. The example was captured after recording a failing `unittest`
run for `greeter@1`, fixing the greeter, and recording a passing run with `-v`.

```json
{
  "revision": 7,
  "units": [
    {
      "chain_error": "",
      "current_pack_green": true,
      "current_test_set": "00de08c8421a65e3732c8385387366f36de4da0146736457162229bef899dfac",
      "notes": [
        "GREEN ran a different command from RED"
      ],
      "records": [
        {
          "command": "python3 -m unittest discover -s tests",
          "counts": "red",
          "digest": "b348d02996b0aafc6234ea1ec462309155984c7b0172c614952fb2f3bc3ae88a",
          "kind": "red",
          "outcome": "tests-failed",
          "recorded_at": "2026-11-21T18:50:03+00:00",
          "sequence": 1,
          "test_set": "00de08c8421a65e3732c8385387366f36de4da0146736457162229bef899dfac"
        },
        {
          "command": "python3 -m unittest discover -s tests -v",
          "counts": "green",
          "digest": "8c33967379bd2b4480ced8aef8c85381e1e29c1eeef3d43bfa55b469d83a95b6",
          "kind": "green",
          "outcome": "tests-passed",
          "recorded_at": "2026-11-21T18:50:03+00:00",
          "sequence": 2,
          "test_set": "00de08c8421a65e3732c8385387366f36de4da0146736457162229bef899dfac"
        }
      ],
      "status": "in-progress",
      "unit": "greeter@1"
    }
  ]
}
```

`current_pack_green` reports the evidence predicate used at `standard` and `high`.
On a readable chain with a true predicate, `counts` marks the latest explicit GREEN for the
current test set and the latest preceding RED for that set, if present. Other records have an
empty `counts` string. A command-line note compares the stored text, including any redactions.
Comparing the tests the two commands actually ran is deferred to `vouch_mk2-inv.4`.

If a chain is unreadable, its unit has a nonempty `chain_error`, empty `records` and `notes`,
and the snapshot's `current_pack_green` value. The CLI prints the document and exits 1 if it
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
