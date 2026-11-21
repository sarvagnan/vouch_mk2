# Implementer

Use this card with the [Vouch skill](../SKILL.md). Your team assigns the role.
Vouch records actor claims and leaves identity and role enforcement to your team.

1. Build against the frozen pack and keep its bound files fixed.
2. If a frozen test is defective or conflicts with the contract, use `open-deviation` with the
   exact unit name and version. Stop on that requirement until the owner decides the deviation.
   Continue any work the blocker leaves independent.
3. Run the intended test suite. When the owner delegates evidence recording, use
   `record-evidence` for the passing run and confirm that Vouch accepted it. Follow the
   [recovery guide](../../errors.md) for a refusal or missing baseline.
4. Hand the change and run details to the reviewer. Leave pack binding, deviation adjudication,
   policy settings, and publication to the acceptance owner.
