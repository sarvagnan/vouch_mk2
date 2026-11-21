# Acceptance owner

Use this card with the [Vouch skill](../SKILL.md). Your team assigns the role.
Vouch records actor claims and leaves identity and role enforcement to your team.

1. Specify the unit and write its spec, acceptance tests, and truthful effects declaration.
   At `freeze`, bind specs and supporting files with `--pack-file`, test files with
   `--pack-test-file`, and the declaration with `--pack-effects-file`. Bind relevant runner
   configuration so the project-root pack check at `standard` or `high` can detect edits to it.
   Check test selection separately.
2. Before handing off implementation, run the actual frozen tests on the pre-change code
   through `record-evidence` and confirm the failure is the expected RED. Mark the unit ready
   and start it. Under strict CI policy, use `mark-open-in-ci` with a reason if an intentionally
   unfinished post-specified, nonterminal unit needs an exception.
3. When a deviation arrives, choose `adjudicate-amend` to record an amendment version or
   `adjudicate-reject` to keep the pack. Close the deviation. If the requirements changed,
   coordinate `respecify` and a new freeze. If the pack stands, use `begin-reverify` once its
   gates allow it.
4. Review the change and evidence before publication. Regenerate projections and run the CI
   check. At `high`, publication requires a recorded formal certificate and an evidence head
   anchored past `GENESIS`. Use the [tier guide](../../concepts.md#assurance-tiers) to check the
   publication and report requirements before `publish`.
