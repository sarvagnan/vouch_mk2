# Acceptance pack — unit `evidence-command-safety@1` (pack/1)

**Owner:** Fable under the user's 2026-08-19 method-role override. **Implementer:** Codex — may
NOT edit this file or `tests/test_evidence_command_safety.py`. This unit closes findings 7 and 8
in the 2026-08-19 external-adoption review. Every non-guard test must fail against the frozen
pre-implementation tree.

## Requirements (each bound to a test)

R1 `record-evidence` renders and validates the exact command descriptor that it would store
   before spawning the child. Empty, non-printable, control-bearing, or more-than-4,096-character
   rendered descriptors exit 2 with `evidence.command-line-invalid`; the child has no side effect
   and no evidence object is recorded.
   → `test_invalid_command_descriptor_refuses_before_child_runs`

R2 `--redact-arg INDEX` is a repeatable `record-evidence` option placed before `--`. `INDEX` is
   zero-based over the child argv, including the executable. The child receives the original argv,
   while each selected value is replaced by the visible literal `<redacted>` in the immutable
   command descriptor. Neither the original value nor a digest of it is retained by Vouch.
   → `test_redacted_argument_executes_original_argv_but_is_absent_from_the_store`

R3 Redaction never permits argv position 0 (the executable), a negative position, an out-of-range
   position, or the same position twice. Invalid selections exit 2 with
   `evidence.redaction-invalid`, before opening a child process or recording evidence.
   → `test_invalid_redaction_positions_refuse_before_child_runs`

R4 Without `--redact-arg`, a valid ordinary command keeps the existing behavior and its immutable
   descriptor remains exactly `shlex.join(child_argv)`.
   → `test_unredacted_valid_command_preserves_the_exact_descriptor`

R5 Adoption documentation defines the option and its boundary: positions are zero-based and
   include argv[0], argv[0] cannot be redacted, stored values visibly become `<redacted>`, no
   secret digest is retained, and the child still sees the original value. It warns that child
   output and side effects are not redacted and recommends environment-based secret injection.
   → `test_docs_define_the_redaction_boundary`

## Ground rules

- Keep the subprocess exit status as the sole observed outcome; do not add a user-supplied outcome.
- Validate the single stored descriptor before the existing frozen-pack capture and spawn, then
  reuse that exact string when constructing `EvidenceEvent`; do not render it a second time.
- Redaction is explicit and positional. Do not guess which values to rewrite, silently redact,
  store a reversible value, or retain a hash/digest of a selected secret.
- Secret-looking detection may still warn about unredacted original argv, but its wording must not
  claim that explicitly selected values are stored verbatim.
- Keep the existing pack-movement TOCTOU guarantee and bounded JSON streaming behavior intact.
- Do not change the evidence-event schema or core lifecycle interface: the sanitized descriptor is
  still the existing `command_line` field.
- GitHub Actions remains the only CI adapter; this unit does not add release, merge-driver, or type
  strictness work.
- Update existing tests and bound docs as needed, but do not edit this pack, its acceptance test,
  `.vouch/`, README's test-count line, Beads, or git history.
