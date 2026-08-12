"""Frozen structured results shared by check evaluators and transports."""

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.checks.verdicts import compose
from vouch_mk2.ir.schema import Verdict


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check outcome.

    A clean PASS has an empty ``detail``. FAIL detail names the offending units,
    deviations, or ids. Organizational NOT_CHECKED detail gives the one-line
    reason from ``docs/claim-boundary.md``. NOT_APPLICABLE detail must cite the IR
    applicability rule that made the check inapplicable.
    """

    check_id: str
    verdict: Verdict
    detail: str


@dataclass(frozen=True, slots=True)
class CheckReport:
    """A complete check run over one snapshot revision.

    ``composed`` considers mechanical results, plus one mechanism guard: an
    organizational FAIL. Per ``docs/claim-boundary.md``, organizational rows are
    visible honesty about claims no artifact can prove — their NOT_CHECKED never
    gates the exit code. The only organizational FAIL that can exist is the one
    ``run_checks`` synthesizes when an evaluator violates that contract, and a
    violated mechanism must fail the run.
    """

    revision: int
    mechanical: tuple[CheckResult, ...]
    organizational: tuple[CheckResult, ...]

    def composed(self) -> Verdict:
        if any(result.verdict is Verdict.FAIL for result in self.organizational):
            return Verdict.FAIL
        return compose(result.verdict for result in self.mechanical)
