---
status: accepted
---

# Adopt sound fail-closed incremental verification

Ordinary verification evaluates a human-confirmed affected closure from an exact verified baseline
and reuses a scope's repository-local verification capsule only when all bound inputs, dependencies,
execution identities, policy, and verifier identity remain unchanged. Unknown scope, opaque or
volatile inputs, missing trust, incompatible changes, and invalid checkpoints widen verification or
block progression; force-full remains permanently available. Full verification of active scopes and
genesis-to-head historical scrubbing remain distinct modes. Naive Git-diff scoping and untrusted CI
caches were rejected because they can exclude implementation-only or indirect changes without
proof; always re-running every scope was rejected because its cost scales poorly and undermines
adoption in large projects.

