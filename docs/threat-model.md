# Vouch mk2 threat model

**Status:** Phase 0 draft, 2026-08-11. Written before any code: every check is born
declaring its scope, instead of extracting the boundary through thirty defects (the
0.6 lesson — see `verification-kit/DEFECTS.md` in the 0.6 repo).

Three explicit levels. Every mechanical check in mk2 cites the level it operates at;
a check that cannot state its level does not ship.

## Level 1 — Accidental inconsistency

**Vouch prevents it.** Ordinary mistakes by honest tools and people: forgotten
re-verification, stale generated artifacts, coupled writes torn by a crash, a claim
recorded against the wrong version, a check silently skipped.

Mechanisms: one authoritative snapshot with CAS commits on a monotonic revision;
immutable content-addressed objects; strict typed decoding at every ingress; digest
bindings on every cross-artifact reference; the obligation registry (every registered
check maps to an evaluator that ran, or the run fails). These make level-1 faults
unrepresentable or mechanically detected — never absorbed.

## Level 2 — Adversarial repository content

**Vouch parses and validates safely.** Hostile *content* presented to an honest tool:
crafted paths, symlinks, malformed or over-long artifacts, unknown fields, type
confusion, references outside the project root.

Mechanisms: capability-rooted storage (one root handle, validated relative-path
types, symlink refusal); descriptor-relative operations (`dir_fd`, `O_NOFOLLOW`)
where the platform provides them; strict decoders that reject unknown fields and
coercions; content-addressed retrieval (an object that does not hash to its name does
not load).

### Accepted residual: ancestor-symlink TOCTOU (0.6 D070)

A concurrent hostile process with write access to the project tree could swap an
ancestor directory for a symlink between the ancestor check and the atomic replace.
CPython's stdlib exposes no portable `renameat`-style `dir_fd`-relative replace on
every platform (`os.replace not in os.supports_dir_fd` on some), so under D-1 (typed
Python) the race is **narrowed, not closed**: descriptor-relative operations are used
where the platform provides them, and ancestors are re-verified immediately before
the replace elsewhere.

The residual attacker — a process concurrently writing inside the project tree during
a Vouch operation — can already tamper with inputs and outputs directly, so the
practical exposure sits at the level-3 boundary. Recorded here rather than claimed
closed; no discriminating witness exists. Revisit if a portable `renameat` becomes
available. (Precedent: 0.6 `DEFECTS.md` D070; decision D-1 in `DECISIONS.md`.)

## Level 3 — Hostile rewrite of tool, lock, and branch rules

**Outside Vouch — by declaration.** An actor who can rewrite `vouch.lock`, the CI
workflow, or branch protection defeats any repository-local mechanism, whatever
language or packaging is used (this is why D-2's zipapp moves no trust boundary).
Addressed by source review, signatures, and forge governance; never claimed
otherwise. Checks at this level print `NOT_CHECKED`, not a pass.

## Non-goals of this model

- No claim about runtime behavior or security sufficiency of the *governed project* —
  mk2 verifies recorded structural, integrity, lifecycle, and evidence gates within
  its declared bounded model (the 0.6 `public_assurance_scope`, carried over).
- No denial-of-service hardening beyond bounded decoding: availability of a local CLI
  against a hostile local filesystem is not a defended property.
- The boundary between level 2 and level 3 is *write access to the project tree
  during an operation*: content an attacker placed before the run is level 2;
  concurrent mutation during the run is level 3 (with the D070 narrowing above as the
  one deliberate overlap).
