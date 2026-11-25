"""Frozen acceptance pack for self-hosted unit formal-marker@1 (D-62).

``run_formal()`` explores the bounded model once per process, and the tests that call it took most
of the suite's time. D-62 marks every test that runs the formal model ``formal``, so
``pytest -m "not formal"`` runs the rest alone, and makes an unmarked test that runs the model in
process fail instead of exploring. Every test here fails against the pre-change tree. The
implementer may not edit this file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN_CALLERS = (
    # Tests that call run_formal() themselves or through a command, in process or in a subprocess.
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
    # Tests in tests/test_formal.py that request its module-scoped formal_run fixture.
    "tests/test_formal.py::test_core_v1_closes_exhaustively_with_stable_counts",
    "tests/test_formal.py::test_history_v1_closes_exhaustively_with_stable_counts",
    "tests/test_formal.py::test_core_v1_keeps_the_respecify_refreeze_loop_reachable",
    "tests/test_formal.py::test_history_v1_keeps_the_respecify_refreeze_loop_reachable",
    "tests/test_formal.py::test_m6_reports_bounded_label_exhaustion_and_w6_still_discriminates",
    "tests/test_formal.py::test_tier_v1_reaches_a_rebound_published_unit",
    "tests/test_formal.py::"
    "test_m10_m11_hold_in_their_certified_universes_and_witnesses_discriminate",
    "tests/test_formal.py::test_core_v1_has_no_truthful_terminal_states",
    "tests/test_formal.py::test_every_invariant_has_one_private_discriminating_witness",
    "tests/test_formal.py::test_formal_artifact_encodings_are_stable_and_certificate_is_strict",
    "tests/test_formal.py::"
    "test_recorded_certificate_is_checked_cheaply_and_names_unexplored_properties",
)
NOT_CALLERS = (
    # Formal-named tests that do not call run_formal(); they stay in the quick loop.
    "tests/test_formal.py::test_state_fingerprint_ignores_only_revision",
    "tests/test_formal.py::test_witness_registry_rejects_missing_and_borrowed_mutations",
    "tests/test_formal_memory.py::test_streamed_relation_digest_matches_the_encoded_relation",
    "tests/test_formal_memory.py::test_streamed_relation_digest_does_not_build_the_encoded_relation",
    "tests/test_formal_memory.py::test_certificate_hashes_relations_without_encoding_them",
    "tests/test_formal_memory.py::test_a_relation_already_hashed_is_not_read_again",
    "tests/test_formal_memory.py::test_distinct_relations_keep_their_own_digests",
    "tests/test_formal_memory.py::test_explored_transitions_share_each_state_string",
    "tests/test_formal_fresh_green.py::test_refreeze_v1_reaches_the_requirement_through_a_changed_inventory",
    "tests/test_formal_fresh_green.py::test_m12_holds_on_the_real_relation_and_catches_a_change_that_leaves_it_off",
    "tests/test_formal_fresh_green.py::test_the_certificate_check_names_m1_to_m12_as_its_residual",
    "tests/test_rebind_fresh_green.py::test_m12_requires_a_rebind_to_turn_the_flag_on",
    "tests/test_formal_marker.py::test_the_formal_marker_is_registered",
    "tests/test_formal_marker.py::test_every_known_caller_of_the_formal_model_is_marked",
    "tests/test_formal_marker.py::test_not_formal_selects_everything_else",
    "tests/test_formal_marker.py::test_an_unmarked_test_that_runs_the_formal_model_fails",
    "tests/test_formal_marker.py::test_the_readme_names_the_quicker_loop",
)
REFUSAL = "runs the formal model without the formal marker"


def _pytest(*argv: str, cwd: Path = ROOT, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", *argv],
        cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout,
    )


def _collected(*argv: str) -> list[str]:
    result = _pytest("--collect-only", "-q", *argv, "tests")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    nodes = [line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    assert nodes, result.stdout[-3000:]
    return nodes


def _base(node_id: str) -> str:
    return node_id.split("[", 1)[0]


def test_the_formal_marker_is_registered() -> None:
    """R1."""
    result = _pytest("--markers")

    assert result.returncode == 0, result.stderr
    assert re.search(r"^@pytest\.mark\.formal:", result.stdout, re.MULTILINE), result.stdout


def test_every_known_caller_of_the_formal_model_is_marked() -> None:
    """R2: every collected instance of a known caller is marked, and no known non-caller is."""
    everything = _collected()
    marked = set(_collected("-m", "formal"))
    bases = {_base(node) for node in everything}

    assert set(KNOWN_CALLERS) <= bases, sorted(set(KNOWN_CALLERS) - bases)
    assert set(NOT_CALLERS) <= bases, sorted(set(NOT_CALLERS) - bases)
    for node in everything:
        if _base(node) in KNOWN_CALLERS:
            assert node in marked, node
        if _base(node) in NOT_CALLERS:
            assert node not in marked, node


def test_not_formal_selects_everything_else() -> None:
    """R3."""
    everything = _collected()
    formal = _collected("-m", "formal")
    rest = _collected("-m", "not formal")

    assert sorted(formal + rest) == sorted(everything)
    assert not {_base(node) for node in rest} & set(KNOWN_CALLERS)
    assert set(NOT_CALLERS) <= {_base(node) for node in rest}


_PROBE = '''
import importlib
import json

import pytest

from vouch_mk2.formal.run import run_formal as EARLY_ALIAS

RUN = importlib.import_module("vouch_mk2.formal.run")
ORIGINAL = RUN.run_formal
REFUSAL = "runs the formal model without the formal marker"


def test_an_early_alias_refuses():
    with pytest.raises(BaseException, match=REFUSAL):
        EARLY_ALIAS()


def test_the_canonical_function_refuses():
    with pytest.raises(BaseException, match=REFUSAL):
        importlib.import_module("vouch_mk2.formal.run").run_formal()


def test_the_package_export_refuses():
    import vouch_mk2.formal

    with pytest.raises(BaseException, match=REFUSAL):
        vouch_mk2.formal.run_formal()


def test_verify_refuses():
    from vouch_mk2 import verify

    with pytest.raises(BaseException, match=REFUSAL):
        verify.run_formal()


def test_the_cli_formal_command_refuses():
    from vouch_mk2.cli import main

    with pytest.raises(BaseException, match=REFUSAL):
        main(["formal"])


def test_the_mcp_formal_tool_does_not_answer(tmp_path):
    from vouch_mk2.mcp import serve
    from vouch_mk2.store import Store

    Store.init(str(tmp_path / "store")).close()
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "formal", "arguments": {}}}
    (line,) = list(serve(str(tmp_path / "store"), [json.dumps(request)]))
    response = json.loads(line)
    assert "result" not in response, response
    assert response["error"]["code"] == -32603, response


@pytest.fixture
def explored():
    return RUN.run_formal()


def test_a_function_fixture_refuses(explored):
    pass


@pytest.mark.formal
def test_a_marked_test_sees_the_original_run_formal():
    import vouch_mk2.formal
    from vouch_mk2 import cli, mcp, verify

    assert RUN.run_formal is ORIGINAL
    assert vouch_mk2.formal.run_formal is ORIGINAL
    assert verify.run_formal is ORIGINAL
    assert cli.run_formal is ORIGINAL
    assert mcp.run_formal is ORIGINAL
    assert EARLY_ALIAS is ORIGINAL
'''

_PASSING_PROBES = (
    "test_an_early_alias_refuses",
    "test_the_canonical_function_refuses",
    "test_the_package_export_refuses",
    "test_verify_refuses",
    "test_the_cli_formal_command_refuses",
    "test_the_mcp_formal_tool_does_not_answer",
    "test_a_marked_test_sees_the_original_run_formal",
)


def test_an_unmarked_test_that_runs_the_formal_model_fails(tmp_path: Path) -> None:
    """R4: the repository's test configuration, applied to a probe file outside the suite."""
    tests = tmp_path / "tests"
    tests.mkdir()
    shutil.copyfile(ROOT / "tests" / "conftest.py", tests / "conftest.py")
    shutil.copyfile(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tests / "test_probe.py").write_text(_PROBE)

    try:
        result = _pytest("-rA", "tests", cwd=tmp_path, timeout=900)
    except subprocess.TimeoutExpired as error:
        raise AssertionError("the probe run timed out after 900 s; inconclusive") from error
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    for name in _PASSING_PROBES:
        assert re.search(rf"^PASSED tests/test_probe\.py::{name}\b", output, re.M), (name, output)
    assert re.search(r"^ERROR tests/test_probe\.py::test_a_function_fixture_refuses\b", output, re.M), output
    error_section = output[output.index("ERROR at setup of test_a_function_fixture_refuses") :]
    assert REFUSAL in error_section, output


def test_the_readme_names_the_quicker_loop() -> None:
    """R5."""
    text = re.sub(r"\s+", " ", (ROOT / "README.md").read_text(encoding="utf-8"))
    gates = text[text.index("## Gates") :]

    assert (
        'For a quicker local loop, `uv run pytest -m "not formal"` skips the tests marked '
        "`formal`, which run the formal model; the gates and CI run every test." in gates
    )
