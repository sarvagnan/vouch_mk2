# Vouch mk2 decision log

Records decisions where the goal fixed an outcome but not one unique mechanism, with
the rejected option and why — the 0.6 `DECISIONS.md` discipline, carried over. Entries
cite the 0.6 precedents they uphold or reverse. Append-only; a reversed decision gets a
new entry, never an edit.

## D-1 — Core language: typed Python

**Status:** decided 2026-08-10.

- **Question:** What language is the mk2 core written in?
- **Decision:** Typed Python: Pyrefly strict, frozen dataclasses, closed enums with
  `typing.assert_never` exhaustiveness, real module boundaries. Ground rules (enforced
  by lint/CI from the first commit, not convention):
  - Pyrefly strict everywhere; no `Any`, no `# type: ignore` without a linked defect id.
  - All domain types are frozen dataclasses; all state/claim/verdict enums are closed,
    and every `match` over them ends in `assert_never`.
  - Raw `dict`/JSON never crosses a module boundary — decode to types at the ingress, once.
  - `decide()` and all projections are import-forbidden from doing I/O (enforced with an
    import-linter contract, not review).
  - Stdlib-only for the shipped artifact (dev-time tooling may use pinned dev deps, as
    0.6 did).
- **Rejected:** Rust. The 0.6 defect classes were design defects, not language defects;
  typed Python captures most of the closed-type value at a fraction of the rewrite risk,
  and the contributor/agent ecosystem is Python.
- **Consequence accepted:** the D070 ancestor-symlink TOCTOU stays a *narrowed,
  documented* threat-level-2 residual (descriptor-relative `dir_fd`/`O_NOFOLLOW` where
  the platform provides it), rather than fully closed. See
  `docs/threat-model.md` and 0.6 `verification-kit/DEFECTS.md` D070.

## D-2 — Distribution: one digest-pinned zipapp artifact

**Status:** decided 2026-08-10.

- **Question:** How do consuming projects obtain and trust the tool?
- **Decision:** Vendor exactly one opaque digest-pinned artifact: `vouch.lock` = a
  single-file **zipapp** built from the module tree, tracked by one digest. Keeps the
  founding hermetic-CI constraint (no network, no registry, air-gap safe), collapses
  ownership tracking to one digest, and moves no trust boundary — a hostile lock-editor
  is threat level 3 under either option.
- **Rejected:** registry-retrieved signed release (breaks the no-network constraint,
  adds a registry trust root without moving the hostile-editor boundary).
- **Why the zipapp specifically:** it is the piece that lets mk2 keep "single vendorable
  file" *without* repeating 0.6's god-file — vendoring is a packaging decision, not a
  code-organization decision. Upholds the 0.6 single-file-vendoring precedent
  (0.6 `DECISIONS.md`, "CLI packaging and project-local execution") while reversing its
  code-organization consequence.
