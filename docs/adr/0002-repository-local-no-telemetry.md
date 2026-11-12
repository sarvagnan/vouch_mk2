---
status: accepted
---

# Keep Vouch repository-local with no telemetry

Vouch stores assurance and adoption state locally and never transmits project or adoption data,
including anonymous or opt-in telemetry. Support bundles, feedback reports, notifications, and
portfolio exports exist only through explicit user-controlled local actions; a local portfolio view
is read-only and never becomes a control plane. A hosted adoption service was rejected because it
would introduce a second authority, a privacy-sensitive dependency, and a failure mode capable of
blocking or silently observing governed delivery.

