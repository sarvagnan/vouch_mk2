# Acceptance pack — unit `pending-anchor-locality@1` (pack/1)

**Owner:** Fable under the user's 2026-08-19 method-role override. **Implementer:** Codex — may
NOT edit this file or `tests/test_pending_anchor_locality.py`. This low-severity cleanup makes
verification the sole owner of the pending-anchor exception. Every non-guard test must fail
against the frozen pre-implementation tree.

## Requirements (each bound to a test)

R1 `vouch_mk2.verify.pending_anchor_is_only_unchecked(report: CheckReport) -> bool` is the
   authoritative classifier. It returns true only when there is no mechanical or organizational
   FAIL and the report has exactly one mechanical `NOT_CHECKED` row whose id is the IR constant
   `CHECK_EVIDENCE_ANCHORS`; zero, multiple, other-id, or organizational-failure cases return
   false. It needs no `Snapshot` because verification already produced the row. →
   `test_verify_owns_the_report_only_pending_anchor_classifier`

R2 The CLI imports and calls that verifier classifier for `--pending-anchor-ok`; it does not
   traverse `Snapshot.evidence_heads` or duplicate the three-way GENESIS/run-claim predicate.
   → `test_cli_does_not_reinterpret_evidence_heads`

R3 (guard) A first CI run at GENESIS still exits zero only with `--pending-anchor-ok`, prints the
   evidence-anchor `NOT_CHECKED` row in text and JSON, and any other failing/non-checked
   condition still exits nonzero. → `test_pending_anchor_behavior_remains_visible_and_narrow`

## Ground rules

- This is a locality/refactoring unit, not a new tolerance. Do not change evaluator verdicts,
  report composition, the authoritative IR check id, or which real CLI scenarios pass.
- Do not parse human-readable detail strings. Classification uses structured report fields.
- Do not retain a compatibility wrapper in `cli.py` that accepts `Snapshot`; there must be one
  implementation in verification.
- Update unit tests/imports as needed. Do not edit this pack, its acceptance test, `.vouch/`,
  README's test-count line, Beads, or git.

