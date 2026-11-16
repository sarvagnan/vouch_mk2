"""Frozen acceptance pack for self-hosted unit option-safe-identifiers@1 (D-35).

Found by GPT 6 Astra reviewing anchor-unit-ref@1 (``vouch_mk2-dww``): a unit name or version
that begins with ``-`` was accepted, but every CLI option reads a separate ``-v1`` token as an
option, so the commands ``next`` suggests for such a unit cannot run. Every test here failed
against the pre-change tree, except tests marked ``# guard:``. The implementer may not edit this
file.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core import Accepted, Refused, Snapshot, SpecifyUnit, UnitSpecified, decide
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
CODE = "identifier.leading-dash"
DETAIL = "may not begin with '-'"


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _root(tmp_path: Path) -> str:
    project = tmp_path / "proj"
    project.mkdir()
    code, output = _run("init", "--project", str(project))
    assert code == 0, output
    return str(project / ".vouch")


def _units(root: str) -> tuple[str, ...]:
    with Store.open(root, read_only=True) as store:
        return tuple(unit.name for unit in store.read_snapshot().units)


def _refusal(command: SpecifyUnit) -> tuple[tuple[str, str], ...]:
    decision = decide(Snapshot(), command)
    assert isinstance(decision, Refused), decision
    return tuple((violation.code, violation.detail) for violation in decision.violations)


def test_decide_refuses_a_unit_name_that_begins_with_a_dash() -> None:
    """R1."""
    violations = _refusal(SpecifyUnit("-x", "v1"))
    assert [code for code, _ in violations] == [CODE]
    assert DETAIL in violations[0][1]


def test_decide_refuses_a_unit_version_that_begins_with_a_dash() -> None:
    """R2."""
    violations = _refusal(SpecifyUnit("u", "-v1"))
    assert [code for code, _ in violations] == [CODE]
    assert DETAIL in violations[0][1]


def test_cli_specify_refuses_a_dash_led_name_and_records_nothing(tmp_path: Path) -> None:
    """R3: text output."""
    root = _root(tmp_path)
    code, output = _run("specify", "--root", root, "--name=-x", "--version", "v1")
    assert code == 2, output
    assert CODE in output
    assert DETAIL in output
    assert _units(root) == ()


def test_cli_specify_refuses_a_dash_led_version_as_json(tmp_path: Path) -> None:
    """R3: JSON output."""
    root = _root(tmp_path)
    code, output = _run("specify", "--root", root, "--name", "u", "--version=-v1", "--json")
    assert code == 2, output
    result = json.loads(output)
    assert result["accepted"] is False
    assert [violation["code"] for violation in result["violations"]] == [CODE]
    assert _units(root) == ()


def test_inner_and_trailing_dashes_are_still_accepted() -> None:
    """R4. # guard: these were legal before and stay legal."""
    for name, version in (("a-b", "v1-rc"), ("x-", "1-")):
        decision = decide(Snapshot(), SpecifyUnit(name, version))
        assert isinstance(decision, Accepted), decision
        assert len(decision.events) == 1
        event = decision.events[0]
        assert isinstance(event, UnitSpecified)
        assert (event.name, event.version) == (name, version)


def test_other_invalid_identifiers_keep_the_identifier_invalid_code() -> None:
    """R5. # guard: the existing rule is reported first and alone."""
    for name in ("", "a@b", "-a@b"):
        violations = _refusal(SpecifyUnit(name, "v1"))
        assert [code for code, _ in violations] == ["identifier.invalid"], name


def test_quickstart_states_that_unit_identifiers_may_not_begin_with_a_dash() -> None:
    """R6."""
    text = re.sub(r"\s+", " ", (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8"))
    assert "A unit name or version may not begin with `-`" in text
