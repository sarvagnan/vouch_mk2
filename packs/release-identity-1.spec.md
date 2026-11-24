# release-identity@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_release_identity.py` or `packs/release-identity-1.effects.json`.
**Decision:** D-56 (`vouch_mk2-b6g.14.1`, the first of release@1's three units). **Seams (D-44):**
the CLI in process (`--version`, `build-lock`); the built lock run as a subprocess with the test's
interpreter (`--version`, `self-check`); the lock read as a zip archive; `pyproject.toml` read with
`tomllib`; the text of `LICENSE`, `src/vouch_mk2/LICENSE` and `README.md`.
**Effects:** declared in `packs/release-identity-1.effects.json`.

## Promise

**Licence.** The repository root holds `LICENSE`: the standard MIT License text, first line
`MIT License`, with the line `Copyright (c) 2026 sarvagnan`. `src/vouch_mk2/LICENSE` holds the same
bytes, so every lock (which binds every package file) carries the notice as `vouch_mk2/LICENSE`.
`pyproject.toml` declares `license = "MIT"` and `license-files = ["LICENSE"]`.

**Version.** The version is `0.1.0` in `pyproject.toml` and `vouch_mk2.__version__`. `vouch-mk2
--version` (no subcommand) prints exactly `vouch-mk2 0.1.0` and a newline to standard output,
nothing to standard error, and exits 0. Run from a lock, it prints exactly
`vouch-mk2 0.1.0 lock <digest>` and a newline, where `<digest>` is the lock's digest: the one
`build-lock` printed for it and `self-check` prints. Every other command is unchanged.

**Docs.** `README.md` states the licence and the lock's `--version` with the sentences R4 pins.

## Requirements

Expected RED reasons, before the change: there is no `LICENSE` (R1's first test fails opening it);
`pyproject.toml` has no `license` key; `--version` is not an option, so argparse exits 2 asking for a
command (in process and from the lock); the lock has no `vouch_mk2/LICENSE`; `README.md` lacks the
sentences. `test_the_version_is_0_1_0_everywhere` is a guard: it passes today and pins the decided
version so a later change to it is deliberate.

- **R1 — Licence.** `test_the_repository_carries_the_mit_licence`;
  `test_the_package_metadata_declares_the_licence`.
- **R2 — Version.** `test_the_version_is_0_1_0_everywhere` (guard);
  `test_the_cli_prints_its_version`.
- **R3 — The lock.** `test_a_lock_prints_its_version_and_digest`: the built lock's `--version`
  output, and its digest equals `build-lock`'s and `self-check`'s. `test_a_lock_carries_the_licence`.
- **R4 — Docs.** `test_the_readme_states_the_licence_and_the_version_flag`: `README.md` contains,
  whitespace normalised, "Vouch is released under the MIT License (see `LICENSE`)." and
  "`python3 vouch.lock --version` prints the version and the lock's digest."

## Ground rules

- **Code.** `--version` is a top-level option of the existing parser; the version comes from
  `vouch_mk2.__version__` and the digest from the same running-lock path `self-check` uses. No
  change to any subcommand, the store, `decide`, gates, formats, the generated workflow, or the lock
  builder (the licence reaches the lock as a package file).
- **Fallout.** If a test pins the exact top-level usage or the lock's file list, Sol updates it
  when it is not bound to a frozen pack and lists it; otherwise Sol lists the failure and the owner
  adapts. The lock digest changes with any source change, as always.
- **Docs.** The R4 sentences in `README.md` (the licence near the top or in a short licence
  section; the `--version` sentence beside the other lock commands); keep every pinned phrase;
  `docs/commands.md` regenerated only if its generator output changes.
- **Out of scope.** Tags, release builds, provenance and upgrade (release-build@1 and
  release-upgrade@1); third-party licence notices (Vouch has no runtime dependencies).

## Limits (convergence bound, set before review)

A review blocks only on: an output, exit code or file content that differs from the Promise; a
`LICENSE` that is not the standard MIT text; two licence copies that differ; a lock digest from
`--version` that differs from `self-check`'s; a change to another command's behaviour; a false
statement in a changed doc; or a pinned phrase removed. Wording beyond what the tests pin is a note.
