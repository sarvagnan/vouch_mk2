# formal-marker@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_formal_marker.py` or `packs/formal-marker-1.effects.json`.
**Decision:** D-62 (`vouch_mk2-o5h`, the first of its candidates). **Seams (D-44):** pytest run as a
subprocess with the test's interpreter on the repository's own test configuration (`--markers`,
`--collect-only` with `-m formal` and `-m "not formal"`, and a run of a probe file beside copies of
`tests/conftest.py` and `pyproject.toml`), a seam the user approved for this unit on 2026-11-25;
the text of `README.md`. **Effects:** declared in `packs/formal-marker-1.effects.json` (none: test
configuration and docs only).

## Promise

pytest knows a `formal` marker (`pytest --markers` lists `@pytest.mark.formal:` with a description).
A test runs the formal model when it calls `run_formal()`, directly, through a fixture it requests,
or through a command it runs in process or in a subprocess. Every such test carries the marker.
Today that is the 23 tests this pack names (`KNOWN_CALLERS`): twelve callers, and the eleven tests
in `tests/test_formal.py` that request its module-scoped `formal_run` fixture. Tests that do not
run the formal model are not marked, including the formal-named tests this pack names
(`NOT_CALLERS`). The marker is applied by the test configuration (`tests/conftest.py`, with its
registration there or in `pyproject.toml`), so no frozen test file changes; a test may also mark
itself with `@pytest.mark.formal`.

`pytest -m "not formal"` selects every other test, and `-m formal` and `-m "not formal"` together
select exactly the full suite. Some unmarked tests still explore small or history universes
directly (for example `tests/test_formal_memory.py`, which explores `HISTORY_V1`); that cost stays
in the quick loop by design.

In a test without the marker, a call of `run_formal()` that would run the exploration (one not
answered from its per-process cache) fails instead of exploring, with a message containing
`runs the formal model without the formal marker`, whatever name or route reached it: the
`vouch_mk2.formal.run` function, the `vouch_mk2.formal` export, `verify`, the CLI's `formal`
command, the MCP `formal` tool (which answers with an internal error), an alias imported before the
test ran, or a function-scoped fixture. A call answered from the cache explores nothing and is not
refused. Running the unmarked tests on their own (`-m "not formal"`) starts with a cold cache, so
it catches every in-process call reached while the guard is active; a subprocess or a module-scoped
fixture that explores is outside the guard and is covered only by being on the marker list, which
review checks by reading those paths. In a test with the marker, every name still refers to the
original `run_formal`, and its cached answer stays available to later tests.

`README.md`'s Gates section states the quicker loop with the sentence R5 pins. The gates block, CI
and every existing test's assertions are unchanged.

## Requirements

Expected RED reasons, before the change: R1 finds no `formal` marker; R2 and R3 find nothing marked
(`-m formal` collects no test, so pytest exits 5); R4's unmarked probes explore the model and do
not raise, the MCP tool answers and the fixture probe passes; R5 finds no sentence. There are no
guards.

- **R1 — Registered.** `test_the_formal_marker_is_registered`: `pytest --markers` exits 0 and has a
  line starting `@pytest.mark.formal:`.
- **R2 — Exactly the callers.** `test_every_known_caller_of_the_formal_model_is_marked`: every
  `KNOWN_CALLERS` and `NOT_CALLERS` entry is collected; every collected instance (each parameter)
  of a known caller is in the `-m formal` collection, and none of a known non-caller is.
- **R3 — The rest.** `test_not_formal_selects_everything_else`: the `-m formal` and
  `-m "not formal"` collections together equal the full collection (as sorted lists, so no test is
  in both or neither); the `not formal` collection holds no known caller and every known
  non-caller.
- **R4 — Unmarked calls fail.** `test_an_unmarked_test_that_runs_the_formal_model_fails`: in a
  scratch directory holding copies of `tests/conftest.py` and `pyproject.toml` and a probe file,
  a pytest run (timeout 900 s, a backstop whose expiry is inconclusive and fails the test) exits 1. Each unmarked route (the early alias, the
  canonical function, the package export, `verify`, `main(["formal"])`) passes by raising the
  refusal inside `pytest.raises(match=...)`; the MCP probe passes by getting an internal error
  (`-32603`) and no result; the function-fixture probe errors at setup with the refusal; and the
  marked probe passes, finding the original `run_formal` (captured at import) under every name.
- **R5 — README.** `test_the_readme_names_the_quicker_loop`: after `## Gates`, whitespace
  normalised, `README.md` contains "For a quicker local loop, `uv run pytest -m "not formal"`
  skips the tests marked `formal`, which run the formal model; the gates and CI run every test."

## Ground rules

- The marker list and the refusal live in the test configuration. `tests/conftest.py` must work
  when copied beside a probe file (R4): no imports from test modules. Keep its existing CI-identity
  isolation fixture as it is.
- Mark by node id or by fixture (a test requesting `formal_run`), not by keyword or file-name match.
- No source change under `src/`, no change to any frozen pack file or test file, to the README
  gates block, `.github/workflows/ci.yml`, the lock or the formal model. The existing `slow`
  marker stays.
- The full suite must still pass with every test selected, and `pytest -m "not formal"` must pass
  on its own.
- **Out of scope.** The bead's other candidates (a cheaper walkthrough, faster exploration,
  splitting CI); detecting explorations in subprocesses automatically.

## Limits (convergence bound, set before review)

A review blocks only on: a known caller left unmarked or a known non-caller marked; another test
that calls `run_formal()` left unmarked (found by running `-m "not formal"` for in-process calls,
and by reading subprocess and module-scoped fixture paths); a marked test that does not run the
formal model; an unmarked call that explores instead of failing; marking by keyword or file name;
a change to `src/`, a frozen file, the gates block, CI or an existing assertion; or a false
statement in the README. Other findings are filed as beads.
