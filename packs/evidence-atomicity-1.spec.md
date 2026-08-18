# Acceptance pack — unit `evidence-atomicity@1` (pack/1)

**Owner:** Fable. **Implementer:** Codex — may NOT edit this file or `tests/test_evidence_atomicity.py`.
D-19; external review findings #3 (high) and #7 (medium).

## The defect

`record-evidence` opens the store to pre-flight, **discards** what it read, spawns the child, and
then reads the *then-current* pack digest under the lock. A `freeze` / `rebind-pack` / `respecify`
landing while the tests run therefore attributes the observed exit status to a pack that was not
current when the command ran — D-17 promises the opposite. Separately, `--json` buffers the child's
entire stdout with `subprocess.PIPE` before forwarding it to stderr: a verbose or stuck runner can
exhaust memory.

## Requirements (each bound to a test)

R1 Before spawning, capture under the store lock: the resolved unit ref, its acceptance **test-set**
   digest, and the snapshot revision. Build the evidence event from the CAPTURED digest, not from a
   later read. → `test_evidence_binds_the_digest_captured_before_the_command_ran` (guard)
R2 After the child exits, re-open under the lock and REFUSE with `evidence.pack-moved` (exit 2,
   nothing recorded, no object placed) if the unit's current test-set digest is no longer the
   captured one — including when the unit was respecified, re-frozen, rebound, superseded, or
   re-versioned meanwhile. The message must name both digests (short form) so the operator can see
   what moved. → `test_evidence_is_refused_when_the_pack_changes_while_the_command_runs`
R3 `--json` streams the child's stdout to this process's stderr as it arrives (no unbounded
   in-memory accumulation); stdout still carries exactly one JSON document. A 64 MiB child stdout
   must arrive in full at stderr. → `test_json_mode_does_not_buffer_the_child_output_in_memory`,
   `test_json_mode_still_keeps_stdout_to_exactly_one_document` (guard)
R4 `docs/claim-boundary.md` (and the quickstart where evidence is introduced) state that the digest
   is captured before the command runs and that a concurrent pack change refuses rather than
   rebinding the result. → `test_docs_state_that_a_concurrent_pack_change_refuses`

## Ground rules
- The refusal is a `decide()`-level guarantee where it can be: pass the captured digest into the
  command and let `decide` refuse the mismatch, rather than checking only in the CLI. Say in DONE
  where the check lives and why.
- A revision bump alone must NOT refuse — only a change to the unit's test-set digest (or the unit
  becoming unavailable). Recording evidence must stay possible while unrelated work proceeds.
- Do not touch `.vouch/`, README's test-count line, git. Pyrefly strict; import-linter kept; every
  pre-existing test green. New test functions for anything the pack does not cover (a superseded or
  re-versioned unit mid-run, a revision bump that must NOT refuse).

## Amendment pack/2 (DEV-ea-01, pack-defect, adjudicated amend)

Raised by the implementer. R3's acceptance case built its noisy child as an inline
`python -c` program containing literal newlines, which `valid_command_line` refuses on ingress
(`evidence.command-line-invalid`) — so the test failed on an unrelated, correct contract rather
than on buffering. The child program now lives in a file and the argv carries no control
characters; the recorded command line stays verbatim and the control-character rejection pinned
by `tests/test_lifecycle_cli_regressions.py` is untouched. R3 is otherwise unchanged. The same amendment strengthens the case to DISCRIMINATE streaming
from buffering: 64 MiB arriving after the child exits proves only forwarding, which a buffering
implementation also satisfies. The child now emits a marker and blocks; the marker must already
be at stderr while the child is still running.
