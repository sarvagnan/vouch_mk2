# Threat model

Vouch distinguishes three threat levels. Every mechanical check states the level it works at; a
check that cannot state its level does not ship.

| Level | Threat | Vouch's posture |
|---|---|---|
| Level 1 | Accidental inconsistency by honest people and tools | Prevented (made unrepresentable or detected) |
| Level 2 | Hostile repository content present before a run | Parsed and validated safely |
| Level 3 | Hostile rewrite of the tool, lock, or branch rules | Outside Vouch, by declaration (`NOT_CHECKED`) |

The line between levels 2 and 3 is write access to the project tree during an operation. Content
an attacker placed before the run is level 2; changes made during the run are level 3. The D070
residual below is the one deliberate overlap.

## Level 1: accidental inconsistency

**Vouch prevents it.** Level 1 covers ordinary mistakes by honest people and tools: a forgotten
re-verification, a stale generated file, a pair of writes torn by a crash, a claim recorded
against the wrong version, or a check that silently did not run.

**Mechanisms:**
- One authoritative snapshot, committed by compare-and-swap on a monotonic revision.
- Immutable, content-addressed objects.
- Strict typed decoding at every input.
- Digest bindings on every cross-artifact reference.
- The obligation registry, where every registered check maps to an evaluator that ran, or the run
  fails.

These faults are made unrepresentable or detected. They are never absorbed.

## Level 2: hostile repository content

**Vouch parses and validates it safely.** Level 2 covers hostile content given to an honest tool:
crafted paths, symlinks, malformed or oversized artifacts, unknown fields, type confusion, and
references outside the project root.

**Mechanisms:**
- Capability-rooted storage: one root handle, validated relative-path types, and refusal of
  symlinks.
- Descriptor-relative operations (`dir_fd`, `O_NOFOLLOW`) where the platform provides them.
- Strict decoders that reject unknown fields and coercions.
- Content-addressed reads: an object whose digest does not match its name fails to load.

### Accepted residual: ancestor-symlink TOCTOU (0.6 D070)

A hostile process writing to the project tree during a Vouch run could create a TOCTOU
(time-of-check to time-of-use) race by swapping an ancestor directory for a symlink between path
resolution and a filesystem operation.

- **What mk2 closes:** Once the store opens, mk2 performs every operation—including the state
  replace—relative to the root descriptor it holds. On a runtime without those primitives, it
  fails closed rather than falling back to path-based calls. Both macOS and Linux provide
  `renameat` (native Windows is a non-goal). Because CPython on macOS under-reports `renameat` in
  `os.supports_dir_fd`, Vouch checks support by attempting the operation.
- **What remains open:** The single path resolution inside `Store.open`.
- **Why it is accepted:** An attacker who can write inside the project tree during a Vouch run can
  already tamper with its inputs and outputs, so the practical exposure sits at the level-3
  boundary. This residual is recorded rather than claimed closed, and no witness discriminates it.
  Revisit it if a portable `renameat` becomes available. (Precedent: 0.6 `DEFECTS.md` D070; D-1
  in `DECISIONS.md`.)

## Level 3: hostile rewrite of the tool, lock, or branch rules

**Outside Vouch, by declaration.** Someone who can rewrite `vouch.lock`, the CI workflow, or
branch protection defeats any repository-local mechanism, regardless of language or packaging. That is
why D-2's zipapp moves no trust boundary. Source review, signatures, and forge governance address
this level. Checks at this level report `NOT_CHECKED`, not `PASS`.

## Out of scope

- **The governed project's runtime behavior or security.** mk2 verifies recorded structural,
  integrity, lifecycle, and evidence gates within its declared bounded model (0.6's
  `public_assurance_scope`, carried over).
- **Denial of service beyond bounded decoding.** Vouch does not defend a local CLI's availability
  against a hostile local filesystem.
