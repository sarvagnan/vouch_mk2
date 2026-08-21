# projection-anchor-ordering@1 acceptance pack

**Owner:** Fable. **Implementer:** Codex — may not edit this file or
`tests/test_projection_anchor_ordering.py`.

## Promise

System-map generation remains canonical when multiple CI anchor claims include unit names for
which one name is a prefix of another. All evidence-head-derived projections use the core's
authoritative `(unit_name, unit_version)` identity order, while strict decoder integrity remains
unchanged.

## Requirements

- **R1 — Prefix-related claims round-trip.** A system map containing claimed anchors for
  `ci-anchor@1` and `ci-anchor-closure@1` encodes and decodes successfully through the production
  projection codec.
- **R2 — One canonical order.** The decoded evidence heads and aggregate anchor-run claims both
  appear in `(unit_name, unit_version)` order, with the shorter `ci-anchor` identity before
  `ci-anchor-closure`.
- **R3 — Integrity does not weaken.** A map whose aggregate anchor claim disagrees with its
  evidence head remains rejected by the decoder.
- **R4 — The real candidate path closes.** A GitHub Actions run from the repaired commit advances
  the pending heads, validates and uploads the candidate, downloads it in the write-only publisher,
  and publishes it without a Node 20 deprecation warning.

## Ground rules

- Repair the pure projection ordering seam; retain exact canonical decoding.
- Do not make comparison order-insensitive, sort untrusted input during decoding, or silently
  repair malformed artifacts.
- Do not change evidence events, anchor semantics, CI run-ID discovery, or workflow authority.
- Do not edit this frozen pack. A contradiction is a deviation, not permission to weaken it.

## Out of scope

- A second CI adapter.
- Changes to unit-name syntax or rendered unit labels.
- Organizational strengthening of the run-ID claim.
