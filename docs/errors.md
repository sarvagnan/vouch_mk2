# Errors and recovery

Use this starter catalog to interpret a refusal before retrying. Some errors have only a message.
The [command reference](commands.md) lists flags.

## Exit status

| Exit | Meaning |
|---|---|
| 0 | The command succeeded. For `check`, the report passed, or `--pending-anchor-ok` allowed the sole mechanical `NOT_CHECKED` for a pending anchor and there were no failures. |
| 1 | A verification command can exit 1 for a non-pass. For `check`, the composed verdict is `FAIL`, `NOT_CHECKED`, or `NOT_APPLICABLE`, subject to the pending-anchor option above. |
| 2 | Examples include a lifecycle refusal, a store error, or invalid CLI arguments. |
| 3 | `init` found an owned path that already exists with different content and left it alone; it prints a suggested replacement beside each such file (for example `.github/workflows/vouch.yml.vouch-suggested`). Review the printed paths. |
| 4 | `init` began publication but could not finish. Review the reported paths before retrying. |

A refusal from the decision engine applies no lifecycle events. By the time a
`record-evidence` run reaches the engine, its child has already run and its side effects remain.
An `upgrade` can exit 2 after upgrading state if projection regeneration fails. In that case,
inspect the message and use `python3 vouch.lock generate --root .vouch` to retry regeneration.

## Common refusals

| Code | What happened | Recovery |
|---|---|---|
| `gate.dod.current-pack-green` | The tier requires current-pack GREEN at publication. The pack is label-only or the current evidence does not satisfy the gate. | Ask the acceptance owner to inspect the binding and evidence. For a label-only pack, the owner can evaluate late binding and its tier implications. If a changed re-freeze or a later RED calls for a fresh GREEN and the baseline is valid, run the intended tests on the current implementation. |
| `evidence.kind-outcome-mismatch` | The requested kind disagrees with the process exit status, for example a RED exited 0 or a GREEN exited nonzero. | For a failed GREEN, investigate the failure before retrying the intended test command. For a passing RED, inspect the test selection and raise a deviation if the frozen pack is defective. |
| `evidence.pack-moved` | The unit or its test-set binding no longer matches what Vouch captured before the child ran. | Inspect the current unit and binding with the acceptance owner. Use the current pack and rerun only when the evidence kind truthfully describes the run. |
| `evidence.redaction-invalid` | An argument position repeats or falls outside the allowable range. | Choose each existing child argument at most once and leave the executable at position 0 unselected. |
| `evidence.test-report-invalid` | Exit 2. The child arguments lack exactly one `{junit}` token, the report file is missing or unreadable, or the report fails XML, test case, or size validation. | Pass `--junit-report` before `--` and a runner argument such as `--junitxml={junit}` after it. Ensure the runner writes a regular JUnit XML file with unique test IDs at that path. The input and stored report each have a 64 MiB limit. |
| `evidence.command-not-bound` | Exit 2. The child arguments differ from the command in the frozen policy. | Run the exact bound argument list in its original order and leave any `{junit}` token literal. |
| `evidence.redaction-refused` | Exit 2. `--redact-arg` was supplied for a bound command. | Omit `--redact-arg` and pass any credentials the runner needs through its environment. |
| `evidence.report-required` | Exit 2. `--junit-report` was omitted for a bound command with `{junit}` or for a RED or GREEN whose policy requires reports. | Supply `--junit-report` and have the runner write to the path substituted for its `{junit}` token. |
| `evidence.red-report-no-failure` | Exit 2. The RED report lists no test as `failed` or `error`. | Inspect the intended RED run and its report. Raise a deviation with the acceptance owner if the pack is defective. |
| `evidence.report-no-red` | Exit 2. There is no comparison RED, or that RED has no report. | Inspect the latest RED for the current test set, or the latest RED in the unit's chain if the set has none. Ask the acceptance owner about a missing RED or report. |
| `evidence.report-incomplete` | Exit 2. The GREEN report omits a test ID listed by the comparison RED or fails to list a RED `failed` or `error` ID as `passed`. | Run the intended tests so the GREEN report lists every comparison RED ID and lists all comparison RED failures as passed. |
| `merge.conflict` | Replay cannot safely reconcile divergent facts, for example conflicting changes to the same setting or CI exception. | Read the refusal detail. For conflicting settings or exceptions, align the affected sides with ordinary Vouch commands and retry. For Git conflicts, follow the [merge and rebase walkthrough](quickstart.md#recovering-from-a-git-merge-or-rebase-conflict) so the retry reads fresh index stages. |
| `merge.ancestry-unproven` | After exact exclusions, a version-1 history leaves replay without enough ancestry information. | Inspect the refusal and each side's version-1 marker count using `python3 vouch.lock upgrade --root .vouch --dry-run` against the respective store. Ask the owner to reconcile the work through supported commands and preserve both histories for inspection. |
| `transition.illegal` | The command is not a legal transition from the subject's current state. | Inspect the subject in the system map and look up the edge in the [lifecycle reference](lifecycle.md). `python3 vouch.lock next --root .vouch --json` lists binding next work, a subset of the commands you could run. |
| `identifier.leading-dash` | An identifier begins with `-`, which conflicts with the CLI's option syntax. | Choose an identifier whose first character is not `-`. |
| `pack.effects-required` | A bound freeze or bind lacks a declaration in a store that requires one. | Supply a truthful [effects declaration](schemas.md#effects-declaration) with `--pack-effects-file`. An empty list claims the unit has no effects. |
| `pack.evidence-policy-invalid` | Exit 2. The evidence-policy file does not follow the required JSON schema, the option repeats, or the pack has no spec or test file. | Pass `--pack-evidence-file` once beside `--pack-file` or `--pack-test-file`. Use UTF-8 JSON with integer `evidence_format` set to `1` and supply `command`, `reports` set to `"required"`, or both. A command needs a non-empty list of non-empty arguments and a printable command line of at most 4,096 characters. It may contain one `{junit}` token and must contain it when `reports` is `"required"`. |
| `ci-policy.exception-missing` | `clear-open-in-ci` found no exception for the current exact unit reference. | Inspect `ci_exceptions` in `python3 vouch.lock status --root .vouch --json`. Check the unit version and whether the exception is already gone. |
| `rebind.stale-pack` | The old digest supplied to the rebind decision differs from the unit's current pack. | Reinspect the current pack and the proposed file changes before deciding whether to rebind. The CLI reads the old binding under the writer lock. |
| `rebind.evidence-policy-changed` | Exit 2. The proposed pack adds, removes, or changes the decoded evidence policy. | Keep the frozen policy for the rebind. Use a new unit version when the policy needs to change. |

### Missing current-pack GREEN

Never record a RED after the implementation exists to satisfy this gate; if the current pack has no RED, ask the acceptance owner.
A recorded RED establishes only that a spawned process exited non-zero. Causal RED history remains `NOT_CHECKED`.

### A RED run exits zero

Do not re-run a passing RED as GREEN: unless the owner has rebound the pack, a first GREEN with no RED before it makes `check.evidence-chains` fail, and the chain is append-only.
The rebind exception applies only when the GREEN matches the rebound test set and its recorded
rebind interval.

### Argument redaction

`--redact-arg` positions run from 1 to the last argument, inclusive.
Redaction changes the stored command descriptor. The child still gets its original arguments,
with path substitution when `--junit-report` is enabled. Redaction leaves the child's output and
side effects as they occur.
