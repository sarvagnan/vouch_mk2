"""Pure verdict composition for structured checks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import assert_never

from vouch_mk2.ir.schema import Verdict


def compose(verdicts: Iterable[Verdict]) -> Verdict:
    """Compose verdicts using FAIL > NOT_CHECKED > PASS > NOT_APPLICABLE.

    Empty input is explicitly ``NOT_CHECKED``: an empty check set must never gain
    a vacuous ``PASS`` and falsely claim coverage. A non-empty input containing
    only ``NOT_APPLICABLE`` verdicts composes to ``NOT_APPLICABLE``.
    """
    saw_verdict = False
    saw_not_checked = False
    saw_pass = False
    for verdict in verdicts:
        saw_verdict = True
        match verdict:
            case Verdict.FAIL:
                return Verdict.FAIL
            case Verdict.NOT_CHECKED:
                saw_not_checked = True
            case Verdict.PASS:
                saw_pass = True
            case Verdict.NOT_APPLICABLE:
                pass
            case _:
                assert_never(verdict)
    if not saw_verdict or saw_not_checked:
        return Verdict.NOT_CHECKED
    if saw_pass:
        return Verdict.PASS
    return Verdict.NOT_APPLICABLE
