---
status: accepted
---

# Separate the adoption module and record from the assurance kernel

The optimal Vouch keeps adoption state and history in a separate repository-local module and
append-only record, connected to the assurance kernel only through immutable kernel references.
This preserves the kernel's narrow authority and independent verifiability while allowing adoption
health to block the composed project gate; adoption can never reinterpret or strengthen a kernel
verdict. Combining both concerns in one state machine was rejected because rollout, support, pause,
and exit semantics would spread organizational branches through every assurance transition.

