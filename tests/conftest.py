"""Shared test-environment isolation and formal model selection."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Never

import pytest

from vouch_mk2.formal import run as formal_runner
from vouch_mk2.formal.universe import Universe
from vouch_mk2.store.event_log import _CI_RUN_ID_VARIABLES


# Callers that do not request the formal_run fixture, including subprocesses.
_FORMAL_CALLERS = frozenset({
    "tests/test_adoption_seam.py::test_check_does_not_re_explore_the_bounded_model",
    "tests/test_adoption_seam.py::test_formal_verb_still_explores",
    "tests/test_anchor_unit_ref.py::"
    "test_history_v1_witnesses_claiming_a_superseded_versions_head_without_shrinking",
    "tests/test_ci_anchor_closure.py::test_walkthrough_from_the_lock_ends_clean_again",
    "tests/test_cli.py::test_cli_formal_reports_closed_relation_and_discriminators",
    "tests/test_effect_declarations.py::"
    "test_the_formal_model_covers_the_requirement_without_moving_other_universes",
    "tests/test_formal_fresh_green.py::test_formal_runs_m12_everywhere_and_its_witness_discriminates",
    "tests/test_formal_fresh_green.py::test_refreeze_v1_is_a_small_gated_universe_that_runs_m12",
    "tests/test_formal_fresh_green.py::test_the_formal_command_reports_refreeze_v1_and_m12",
    "tests/test_formal_memory.py::test_the_formal_run_certifies_the_recorded_relation_digests",
    "tests/test_mcp.py::test_mcp_remaining_read_tools_return_structured_objects",
    "tests/test_pack_binding_regressions.py::"
    "test_formal_bind_pack_transitions_preserve_m10_and_skip_superseded",
})


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark model callers before pytest applies the marker selection."""
    for item in items:
        if item.nodeid.partition("[")[0] in _FORMAL_CALLERS or (
            isinstance(item, pytest.Function) and "formal_run" in item.fixturenames
        ):
            item.add_marker(pytest.mark.formal)


@pytest.fixture(autouse=True)
def _guard_formal_exploration(request: pytest.FixtureRequest) -> Iterator[None]:
    def refuse_exploration(_universe: Universe) -> Never:
        raise RuntimeError(f"{request.node.nodeid} runs the formal model without the formal marker")

    # Patch inside the cached call so existing aliases still share cache hits.
    # Give the guard its own lifetime because tests can undo their monkeypatches.
    with pytest.MonkeyPatch.context() as patch:
        if request.node.get_closest_marker("formal") is None:
            patch.setattr(formal_runner, "_run_universe", refuse_exploration)
        yield


@pytest.fixture(autouse=True)
def _isolate_ci_run_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make CI identity opt-in for every repository test."""
    for name in _CI_RUN_ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)
