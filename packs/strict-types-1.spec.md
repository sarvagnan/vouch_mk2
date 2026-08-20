# Acceptance pack — unit `strict-types@1` (pack/1)

**Owner:** Fable under the user's 2026-08-19 method-role override. **Implementer:** Codex — may
NOT edit this file or `tests/test_strict_types.py`. This unit closes finding 5 in the 2026-08-19
external-adoption review and makes D-1's existing assurance claim true. Every non-guard test must
fail against the frozen pre-implementation tree.

## Requirements (each bound to a test)

R1 `[tool.pyrefly]` selects the authoritative `strict` preset and continues to include both
   `src` and `tests`. It contains no file exclusion, nested diagnostic table, severity override,
   ignore setting, or other configuration that weakens the strict preset.
   → `test_pyrefly_configuration_selects_unweakened_strict_preset`

R2 A real `pyrefly check --preset strict` from the repository root exits 0 across the configured
   project. The pre-implementation baseline is 74 errors; none may be hidden by narrowing the
   project or changing a diagnostic's severity.
   → `test_explicit_strict_preset_is_green`

R3 Python files below `src/` and `tests/` contain no `Any` name token and no `# type: ignore` or
   `# pyrefly: ignore` suppression. This unit repairs the existing escapes rather than giving them
   retrospective defect labels merely to pass the gate. D-1's future linked-defect exception is
   unchanged, but no such exception is needed for the current tree.
   → `test_source_and_tests_need_no_any_or_type_suppression`

R4 The dogfood workflow's accurately named `Types (Pyrefly strict)` step invokes the ordinary
   configured `uv run pyrefly check` command. Strictness has one authority in project config; CI
   does not carry a second preset selection that can drift.
   → `test_ci_runs_the_authoritative_configured_strict_gate`

R5 The ordinary configured `pyrefly check` invocation remains green. This is a guard before the
   preset is enabled and becomes a proof that local/CI use resolves to the same strict gate after
   R1 is implemented.
   → `test_configured_type_gate_is_green`

## Ground rules

- Preserve runtime behavior. Type repairs may introduce typed named helpers, explicit container
  annotations, narrowing assertions, protocols, or precise reflection-boundary types; they may
  not change lifecycle, storage, checking, formal, CLI, merge, or workflow semantics.
- Do not make the gate green with `Any`, casts to `Any`, type-checker suppressions, diagnostic
  severity changes, file exclusions, an alternate config, generated stubs, or a reduced include
  set. `object` plus explicit narrowing is preferred at untrusted boundaries.
- Existing acceptance tests may receive annotation-only or helper-shape repairs when required by
  strict checking. Do not weaken, delete, skip, deselect, or change their behavioral assertions.
  Such edits can change published pack inventories; the owner, not the implementer, will inspect
  and perform any D-14 rebinds plus fresh evidence after implementation.
- Keep Python 3.14, stdlib-only shipped runtime, frozen dataclasses, closed enums, import-linter
  purity, deterministic lock construction, and all formal universe behavior unchanged.
- Do not edit this pack, its acceptance test, `.vouch/`, Beads, DECISIONS.md, README's test-count
  line, usage log, or git history. Do not commit or push.
