# Reviewer

Use this card with the [Vouch skill](../SKILL.md). Your team assigns the role.
Vouch records actor claims and leaves identity and role enforcement to your team.

1. Compare the diff with the frozen specification, test files, and effects declaration.
   Confirm that the tests exercise the intended behavior and that the declaration accounts
   for the effects the change introduces. Vouch leaves those judgments `NOT_CHECKED`.
2. Inspect the report with `check` and the binding next work with `next`. Use
   `python3 vouch.lock mcp --root .vouch` for read-only store queries when using MCP.
   Use `show-evidence` to review stored evidence commands and the marked RED and GREEN records.
   Use the [claim boundary](../../claim-boundary.md) to interpret the results.
3. Reproduce findings in a scratch project when a run is needed. Report the location, the
   failing case, and the expected behavior. Keep the project store read-only during review.
