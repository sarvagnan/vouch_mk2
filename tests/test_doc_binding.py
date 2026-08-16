from __future__ import annotations

import argparse
import re
from pathlib import Path

from vouch_mk2.cli import _parser
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.witnesses import WITNESSES
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Tier


ROOT = Path(__file__).resolve().parents[1]


def _read(paths: tuple[Path, ...]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _docs() -> tuple[Path, ...]:
    return tuple(sorted((ROOT / "docs").glob("*.md")))


def test_documented_check_ids_are_declared_ir_obligations() -> None:
    text = _read((ROOT / "README.md",) + _docs())
    documented = set(re.findall(r"check(?:\.[a-z0-9-]+)+", text))
    declared = {obligation.obligation_id for obligation in mk2.MK2.obligations}
    assert documented <= declared
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    d12 = decisions.split("## D-12", 1)[1]
    evidence = next(
        requirement
        for requirement in mk2.MK2.tier_requirements
        if requirement.control_id == mk2.TIER_EVIDENCE_CONTROL
    )
    bound = {
        check_id
        for cell in (evidence.lightweight, evidence.standard, evidence.high)
        for check_id in cell.mechanical_checks
    }
    assert bound == {
        mk2.CHECK_EVIDENCE_ANCHORS,
        mk2.CHECK_TIER_EVIDENCE_REQUIRED,
        mk2.CHECK_TIER_HIGH_ATTESTATION,
    }
    assert mk2.TIER_EVIDENCE_CONTROL in d12
    assert bound <= set(re.findall(r"check(?:\.[a-z0-9-]+)+", d12))
    assert mk2.ORG_TIER_CONTROLS in d12
    for requirement in mk2.MK2.tier_requirements:
        for tier in Tier:
            cell = requirement.cell(tier)
            mechanical = ", ".join(
                cell.mechanical_evidence + cell.mechanical_checks
            ) or "none"
            residual = cell.residual or "none"
            binding = (
                f"{requirement.control_id}/{tier.value}: "
                f"level={cell.level.value} | coverage={cell.coverage.value} | "
                f"mechanical={mechanical} | residual={residual}"
            )
            assert binding in d12


def test_documented_formal_ids_exist_in_catalogs() -> None:
    text = _read((ROOT / "DECISIONS.md", ROOT / "README.md"))
    documented_invariants = set(re.findall(r"\bM[1-9][0-9]*\.[a-z-]+", text))
    documented_witnesses = set(re.findall(r"\bW[1-9][0-9]*\.[a-z-]+", text))
    assert documented_invariants <= {
        invariant.invariant_id for invariant in INVARIANTS
    }
    assert documented_witnesses <= {witness.witness_id for witness in WITNESSES}


def test_documented_cli_commands_are_real_subcommands() -> None:
    # Capture ANY `vouch-mk2 <word>` mention (not just the known-valid names —
    # an allowlist regex could never fail, the D023 class) and check each against
    # the real parser. Option-like tokens (--root) and `vouch-mk2` used as a bare
    # noun are not subcommand mentions.
    text = _read((ROOT / "README.md", ROOT / "DECISIONS.md") + _docs())
    documented = {
        token
        for token in re.findall(r"vouch-mk2 ([a-z][a-z-]*)\b", text)
    }
    subparser = next(
        action
        for action in _parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    unknown = documented - set(subparser.choices)
    assert not unknown, f"documented subcommands not in the CLI: {sorted(unknown)}"
    # Non-vacuity guard: the regex must be able to see an invalid name.
    assert re.findall(r"vouch-mk2 ([a-z][a-z-]*)\b", "vouch-mk2 explode") == ["explode"]


def test_readme_test_count_matches_pytest_collection() -> None:
    # Bind to the number pytest actually reports (collected items, parametrization
    # included) — the figure a reader sees at the end of `uv run pytest`. Binding
    # to `def test_` definitions would be true-but-misleading (245 vs 472).
    import pytest

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"\b(\d+) tests\b", readme)
    assert match is not None

    class _Counter:
        collected = 0

        def pytest_collection_finish(self, session: pytest.Session) -> None:
            self.collected = len(session.items)

    counter = _Counter()
    exit_code = pytest.main(
        ["--collect-only", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
        plugins=[counter],
    )
    assert exit_code == 0
    assert int(match.group(1)) == counter.collected, (
        f"README says {match.group(1)} tests; pytest collects {counter.collected}"
    )


def test_documented_decision_references_have_headings() -> None:
    text = _read((ROOT / "README.md",) + _docs())
    references = set(re.findall(r"\bD-\d\b", text))
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## (D-\d+)\b", decisions, re.MULTILINE))
    assert references <= headings


def test_defect_closure_evidence_citations_and_inventory_are_bound() -> None:
    document = ROOT / "docs" / "defect-closure.md"
    text = document.read_text(encoding="utf-8")

    expected_ids = {f"D{number:03d}" for number in range(1, 71)}
    occurrences = re.findall(r"\bD\d{3}\b", text)
    assert set(occurrences) == expected_ids
    assert len(occurrences) == len(expected_ids), (
        "every source defect id must occur exactly once"
    )

    test_citations = re.findall(
        r"`(tests/[A-Za-z0-9_./-]+\.py)::([A-Za-z_][A-Za-z0-9_]*)`",
        text,
    )
    assert test_citations, "the closure map must cite regression tests"
    for relative, name in test_citations:
        path = ROOT / relative
        assert path.is_file(), f"missing cited test file: {relative}"
        source = path.read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(name)}\s*\(", source, re.MULTILINE), (
            f"missing cited test function: {relative}::{name}"
        )

    source_citations = set(
        re.findall(r"`(src/[A-Za-z0-9_./-]+)`", text)
    )
    assert source_citations, "unrepresentability notes must cite source mechanisms"
    for relative in source_citations:
        assert (ROOT / relative).exists(), f"missing cited source path: {relative}"
