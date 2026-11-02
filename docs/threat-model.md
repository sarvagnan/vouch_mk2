# Threat model

Vouch distinguishes three levels of threat. Every mechanical check states the level it works
at, and a check that cannot state its level does not ship.

## Level 1: accidental inconsistency

**Vouch prevents it.** These are ordinary mistakes by honest people and tools: a forgotten
re-verification, a stale generated file, a pair of writes torn by a crash, a claim recorded
against the wrong version, a check that silently did not run.

Mechanisms: one authoritative snapshot, committed by compare-and-swap on a monotonic revision;
immutable content-addressed objects; strict typed decoding at every input; digest bindings on
every cross-artifact reference; and the obligation registry, where every registered check maps
to an evaluator that ran, or the run fails. These faults are made unrepresentable or detected.
They are never absorbed.

## Level 2: hostile repository content

**Vouch parses and validates it safely.** This is hostile content given to an honest tool:
crafted paths, symlinks, malformed or oversized artifacts, unknown fields, type confusion,
references outside the project root.

Mechanisms: capability-rooted storage (one root handle, validated relative-path types, symlinks
refused); descriptor-relative operations (`dir_fd`, `O_NOFOLLOW`) where the platform has them;
strict decoders that reject unknown fields and coercions; content-addressed reads, so an object
that does not hash to its name does not load.

### Accepted residual: ancestor-symlink TOCTOU (0.6 D070)

A hostile process writing to the project tree at the same time could swap an ancestor
directory for a symlink between path resolution and a filesystem operation. After the store
opens, mk2 performs every operation, including the state replace, relative to the root
descriptor it holds. On a runtime without those primitives it fails closed rather than falling
back to path-based calls. macOS and Linux both provide `renameat` (native Windows is a
non-goal). CPython on macOS under-reports it in `os.supports_dir_fd`, so Vouch checks for it by
attempting the operation. The remaining window is the single path resolution in `Store.open`.

An attacker who can write inside the project tree during a Vouch run can already tamper with
its inputs and outputs, so the practical exposure sits at the level-3 boundary. The residual is
recorded rather than claimed closed, and no witness discriminates it. Revisit it if a portable
`renameat` becomes available. (Precedent: 0.6 `DEFECTS.md` D070; D-1 in `DECISIONS.md`.)

## Level 3: hostile rewrite of the tool, lock or branch rules

**Outside Vouch, by declaration.** Someone who can rewrite `vouch.lock`, the CI workflow or
branch protection defeats any repository-local mechanism, whatever the language or packaging.
This is why D-2's zipapp moves no trust boundary. Source review, signatures and forge
governance address this level. Checks at this level report `NOT_CHECKED`, not `PASS`.

## Out of scope

- The governed project's runtime behavior or security. mk2 verifies recorded structural,
  integrity, lifecycle and evidence gates within its declared bounded model (0.6's
  `public_assurance_scope`, carried over).
- Denial of service, beyond bounded decoding. A local CLI's availability against a hostile
  local filesystem is not defended.

The line between levels 2 and 3 is write access to the project tree during an operation.
Content an attacker placed before the run is level 2; changes made during the run are level 3.
The D070 residual above is the one deliberate overlap.
