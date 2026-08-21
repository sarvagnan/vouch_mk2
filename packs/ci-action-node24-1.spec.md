# ci-action-node24@1 acceptance pack

**Owner:** Fable. **Implementer:** Codex — may not edit this file or
`tests/test_ci_action_node24.py`.

## Promise

Every JavaScript action executed by mk2's GitHub workflow or emitted in the adopter workflow is an
independently reviewed, immutable Node 24 revision. The dependency refresh preserves Vouch's
existing workflow authority boundaries, generated-artifact parity, and setup-uv cache policy.

## Reviewed action identities

The acceptance owner reviewed each release and its committed `action.yml` on 2026-08-21:

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` — v7.0.1, `node24`;
- `actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1` — v6, `node24`;
- `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` — v9.0.0, `node24`;
- `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` — v7.0.1, `node24`;
- `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` — v8.0.1, `node24`.

## Requirements

- **R1 — Generated artifact is current.** `generated_workflow()` uses only the reviewed identities
  above for its remote actions. Every reference is a full 40-character commit SHA.
- **R2 — Dogfood instance is current.** `.github/workflows/ci.yml` uses the same reviewed identity
  for every corresponding action. It contains no unreviewed remote action.
- **R3 — No runtime suppression.** Neither workflow sets
  `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION` or otherwise opts back into a deprecated runtime.
- **R4 — Cache behavior is preserved.** Every setup-uv step explicitly sets
  `prune-cache: true`, preserving the behavior of the prior v5 pin despite v9's changed default.
- **R5 — Existing security structure remains binding.** Immutable pins, checkout
  `persist-credentials: false`, the read-plan/write-publish split, same-run artifact identity,
  candidate validation, concurrency, and project-test gating remain covered by the existing CI
  packs and must stay green.
- **R6 — External execution closes the claim.** Final verification includes a real GitHub-hosted
  run showing no Node 20 deprecation warning and successful upload/download candidate transport
  when the anchor path has a candidate to publish.

## Ground rules

- Update the generator and maintained dogfood instance together.
- Preserve exact inputs unless this pack names a required compatibility setting.
- Do not replace full SHAs with tags or branches.
- Do not add a runtime-warning suppression variable.
- Do not edit this frozen pack. A contradiction is a deviation, not permission to weaken it.

## Out of scope

- A second CI adapter or generic provider interface.
- Self-hosted runner support; a future option must require Actions Runner 2.327.1 or newer.
- Opportunistic upgrades to non-JavaScript dependencies or changes to the candidate protocol.

