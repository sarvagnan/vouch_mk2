"""Frozen acceptance pack for self-hosted unit tier-summary@1 (D-61).

``check``'s text output printed ``org.tier-controls`` as one line per coverage statement (26 lines
at standard), burying the mechanical rows. D-61 prints one summary line with counts and lists the
controls behind ``--verbose``; ``--json`` is unchanged. Every test here fails against the
pre-change tree, except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.checks import run_checks
from vouch_mk2.cli import main
from vouch_mk2.core.model import Tier
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Coverage, RequirementLevel
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
ROW = "org.tier-controls"
TIERS = ("lightweight", "standard", "high")


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _store(tmp_path: Path, tier: str, *, failing: bool = False) -> str:
    """A fresh store (anchor mode ci) at the tier; ``failing`` leaves stale generated files.

    At lightweight and standard ``check`` then exits 0, and 1 when ``failing``; at high it exits 1
    (no formal certificate), so R1 and R2 do not assert the status.
    """
    root = str(tmp_path / ".vouch")
    Store.init(root).close()
    for argv in (("set-anchor-mode", "--mode", "ci"), ("set-tier", "--tier", tier)):
        code, out, err = _run(argv[0], "--root", root, *argv[1:])
        assert code == 0, out + err
    if failing:
        assert _run("generate", "--root", root)[0] == 0
        code, out, err = _run("specify", "--root", root, "--name", "u", "--version", "1")
        assert code == 0, out + err
    return root


def _snapshot(root: str):  # noqa: ANN202
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot()


def _expected_summary(tier: str) -> str:
    """The summary line, counted from the IR's tier matrix rather than from the report."""
    counts = {"fully checked": 0, "partially checked": 0, "not checked": 0, "waived": 0}
    requirements = mk2.MK2.tier_requirements
    for requirement in requirements:
        cell = requirement.cell(Tier(tier))
        if cell.level is RequirementLevel.WAIVED:
            counts["waived"] += 1
        elif cell.coverage is Coverage.FULL:
            counts["fully checked"] += 1
        elif cell.coverage is Coverage.PARTIAL:
            counts["partially checked"] += 1
        else:
            counts["not checked"] += 1
    parts = ", ".join(f"{count} {kind}" for kind, count in counts.items() if count)
    return (
        f"NOT-CHECKED    {ROW}  {tier}: {len(requirements)} controls — {parts} "
        "(run check --verbose to list them)"
    )


def _per_control_lines(root: str) -> list[str]:
    """Today's rendering: one line per line of the row's detail, from the library report."""
    row = next(r for r in run_checks(_snapshot(root)).organizational if r.check_id == ROW)
    return [f"{row.verdict.value.upper():<14} {ROW}  {line}" for line in row.detail.splitlines()]


def _tier_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if ROW in line]


def _without_tier_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if ROW not in line]


@pytest.mark.parametrize("tier", TIERS)
def test_check_prints_one_summary_line_for_the_tier_controls(tmp_path: Path, tier: str) -> None:
    """R1."""
    root = _store(tmp_path, tier)

    _code, out, err = _run("check", "--root", root)

    assert _tier_lines(out) == [_expected_summary(tier)], out + err


@pytest.mark.parametrize("tier", TIERS)
def test_check_verbose_lists_every_control(tmp_path: Path, tier: str) -> None:
    """R2."""
    root = _store(tmp_path, tier)

    _code, out, err = _run("check", "--verbose", "--root", root)

    lines = _tier_lines(out)
    assert "error:" not in err, err
    assert lines == _per_control_lines(root)
    assert len(lines) > 1


@pytest.mark.parametrize(
    ("ci", "failing"),
    (((), False), (("--ci",), False), (("--ci",), True)),
    ids=("plain", "ci", "ci-failing"),
)
def test_verbose_changes_nothing_but_the_tier_control_lines(
    tmp_path: Path, ci: tuple[str, ...], failing: bool
) -> None:
    """R3: the plain output is the verbose output with the control block replaced by the summary."""
    root = _store(tmp_path, "standard", failing=failing)

    plain = _run("check", *ci, "--root", root)
    verbose = _run("check", *ci, "--verbose", "--root", root)

    assert plain[0] == verbose[0] == (1 if failing else 0), (plain, verbose)
    assert plain[2] == verbose[2]
    lines = verbose[1].split("\n")
    block = [i for i, line in enumerate(lines) if ROW in line]
    assert block == list(range(block[0], block[0] + len(block))), "the control lines are contiguous"
    assert [lines[i] for i in block] == _per_control_lines(root)
    expected = lines[: block[0]] + [_expected_summary("standard")] + lines[block[-1] + 1 :]
    assert plain[1] == "\n".join(expected)

    order = [r.check_id for r in run_checks(_snapshot(root)).organizational]
    at = order.index(ROW)
    before = lines[block[0] - 1]
    after = lines[block[-1] + 1]
    if at == 0:
        assert before == "organizational (never mechanically provable)"
    else:
        assert f" {order[at - 1]}" in before, before
    if at == len(order) - 1:
        assert after.startswith("composed: "), after
    else:
        assert f" {order[at + 1]}" in after, after


def test_json_is_unchanged_by_verbose_and_lists_every_control(tmp_path: Path) -> None:
    """R4."""
    root = _store(tmp_path, "standard")

    plain = _run("check", "--json", "--root", root)
    verbose = _run("check", "--json", "--verbose", "--root", root)

    assert plain[0] == verbose[0] == 0, (plain, verbose)
    assert verbose[1] == plain[1]
    assert verbose[2] == plain[2] == ""
    report = json.loads(plain[1])
    assert plain[1] == json.dumps(report, sort_keys=True) + "\n"
    library = run_checks(_snapshot(root))
    assert report["revision"] == library.revision
    assert report["organizational"] == [
        {"check_id": r.check_id, "verdict": r.verdict.value, "detail": r.detail}
        for r in library.organizational
    ]
    row = next(r for r in report["organizational"] if r["check_id"] == ROW)
    assert len(row["detail"].splitlines()) > 1


def test_check_help_names_verbose() -> None:
    """R5."""
    code, out, _err = _run("check", "--help")

    assert code == 0
    text = re.sub(r"\s+", " ", out)
    match = re.search(r"--verbose (.*?)(?= --[a-z]|$)", text)
    assert match, text
    assert "tier control" in match.group(1) and "text output" in match.group(1), match.group(1)


def test_the_dogfood_store_prints_one_summary_line() -> None:
    """R6: mk2's own store, at its own tier."""
    root = str(ROOT / ".vouch")
    with Store.open(root, read_only=True) as store:
        tier = store.read_snapshot().tier.value

    _code, out, _err = _run("check", "--root", root)

    assert _tier_lines(out) == [_expected_summary(tier)]


def test_claim_boundary_states_the_summary() -> None:
    """R7."""
    text = (ROOT / "docs" / "claim-boundary.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| Residual tier controls |")]
    assert len(rows) == 1, rows
    row = re.sub(r"\s+", " ", rows[0])
    assert "`org.tier-controls` lists active cells without full coverage" in row
    assert (
        "In `check`'s text output the row is one summary line with a count of each kind present; "
        "`check --verbose` and `check --json` list every control." in row
    )


def test_the_built_lock_accepts_verbose(tmp_path: Path) -> None:
    """R8: the packaged entry point, not only the source CLI."""
    from vouch_mk2.lock import build_lock

    root = _store(tmp_path, "standard")
    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))

    def lock_check(*extra: str) -> str:
        result = subprocess.run(
            [sys.executable, str(lock), "check", *extra, "--root", root],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    assert _tier_lines(lock_check()) == [_expected_summary("standard")]
    assert _tier_lines(lock_check("--verbose")) == _per_control_lines(root)
