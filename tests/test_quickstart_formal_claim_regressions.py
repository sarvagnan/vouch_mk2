"""Regressions for quickstart-formal-claim@1: every false verdict a review round showed passing.

Each case is one of the counterexamples GPT 6 Astra found against an earlier pack (DEV-qfc-01 to
-05). The frozen pack's R1 must reject every one of them, so a later rebind cannot loosen it
without failing here.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "test_quickstart_formal_claim.py"
MARKER = "[claim-boundary.md](claim-boundary.md#formal-certificate). "
END_OF_FORMAL_PARAGRAPH = "that expensive exploration.\n"
SECTION_8 = "## 8. Check and read the report"


def _pack() -> ModuleType:
    spec = importlib.util.spec_from_file_location("quickstart_formal_claim_pack", PACK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_paragraph(text: str, extra: str) -> str:
    assert text.count(MARKER) == 1
    return text.replace(MARKER, MARKER + extra)


def _after_paragraph(text: str, extra: str) -> str:
    assert text.count(END_OF_FORMAL_PARAGRAPH) == 1
    return text.replace(END_OF_FORMAL_PARAGRAPH, END_OF_FORMAL_PARAGRAPH + "\n" + extra + "\n")


def _end_of_section_7(text: str, extra: str) -> str:
    assert text.count(SECTION_8) == 1
    return text.replace(SECTION_8, extra + "\n\n" + SECTION_8)


CASES: dict[str, Callable[[str], str]] = {
    # DEV-qfc-01: the verdict inverted in the pinned sentence itself.
    "inverted": lambda text: text.replace(
        "reports `PASS` at every tier", "reports `FAIL` rather than `PASS` at every tier"
    ),
    # DEV-qfc-02: a second sentence naming another verdict.
    "second-sentence-pass": lambda text: _in_paragraph(
        text,
        "Without a recorded certificate, `check.formal-certificate` also reports `PASS` at "
        "every tier. ",
    ),
    "second-sentence-fail": lambda text: _in_paragraph(
        text,
        "In practice, a valid recorded certificate makes `check.formal-certificate` report "
        "`FAIL` at every tier. ",
    ),
    # DEV-qfc-03: an abbreviation splitting the sentence; other dashes; HTML entities.
    "abbreviation": lambda text: _in_paragraph(
        text,
        "With a valid recorded certificate (e.g. after running `formal --record`), the row "
        "reports `FAIL` at every tier. ",
    ),
    "en-dash": lambda text: _end_of_section_7(
        text,
        "With a valid recorded certificate, `check.formal-certificate` reports NOT–CHECKED "
        "at every tier.",
    ),
    "html-entity": lambda text: _end_of_section_7(text, "A stale certificate FA&#73;LS nothing."),
    # DEV-qfc-04: a list lead-in or heading carrying the condition inside §8.
    "list-lead-in": lambda text: _after_paragraph(
        text, "Without a recorded certificate:\n\n- The row reports `PASS` at every tier."
    ),
    "heading": lambda text: _after_paragraph(
        text, "### Without a recorded certificate\n\nThe row reports `PASS` at every tier."
    ),
    # DEV-qfc-05: inline Markdown inside a verdict word.
    "emphasis": lambda text: _end_of_section_7(
        text,
        "With a valid recorded certificate, `check.formal-certificate` reports **NOT**-CHECKED "
        "at every tier.",
    ),
    "emphasis-two-words": lambda text: _end_of_section_7(
        text,
        "With a valid recorded certificate, `check.formal-certificate` reports **NOT** "
        "APPLICABLE at every tier.",
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_pack_r1_rejects_each_reviewed_counterexample(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = _pack()
    current = pack.QUICKSTART.read_text()
    false_text = CASES[name](current)
    assert false_text != current, name
    monkeypatch.setattr(pack, "QUICKSTART", SimpleNamespace(read_text=lambda: false_text))
    with pytest.raises(AssertionError):
        pack.test_quickstart_names_the_formal_certificate_row_with_the_verdict_check_reports(
            tmp_path
        )


def test_pack_r1_accepts_the_published_quickstart(tmp_path: Path) -> None:
    # guard: the substitution harness above is not vacuous; the real text passes R1.
    _pack().test_quickstart_names_the_formal_certificate_row_with_the_verdict_check_reports(
        tmp_path
    )
