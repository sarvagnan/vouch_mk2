from __future__ import annotations

from itertools import product

from vouch_mk2.checks import compose
from vouch_mk2.ir.schema import Verdict


def expected(values: tuple[Verdict, ...]) -> Verdict:
    if Verdict.FAIL in values:
        return Verdict.FAIL
    if Verdict.NOT_CHECKED in values:
        return Verdict.NOT_CHECKED
    if Verdict.PASS in values:
        return Verdict.PASS
    return Verdict.NOT_APPLICABLE


def test_composition_is_exhaustive_for_singles_and_pairs() -> None:
    for size in (1, 2):
        for values in product(Verdict, repeat=size):
            assert compose(values) is expected(values), values


def test_empty_composition_is_not_checked() -> None:
    assert compose(()) is Verdict.NOT_CHECKED


def test_mixed_composition_with_fail_is_fail() -> None:
    assert compose(
        (
            Verdict.PASS,
            Verdict.NOT_APPLICABLE,
            Verdict.NOT_CHECKED,
            Verdict.FAIL,
        )
    ) is Verdict.FAIL


def test_mixed_composition_without_fail_is_not_checked() -> None:
    assert compose(
        (Verdict.PASS, Verdict.NOT_APPLICABLE, Verdict.NOT_CHECKED)
    ) is Verdict.NOT_CHECKED
