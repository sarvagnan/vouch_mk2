"""Pure evidence-policy predicates shared by recording and verification."""

from __future__ import annotations

from vouch_mk2.core.model import EvidenceKind, EvidencePolicy, TestOutcome, TestReport
from vouch_mk2.ir.schema import Violation


JUNIT_TOKEN = "{junit}"
REPORT_REQUIRED = Violation(
    "evidence.report-required", "the bound evidence policy requires a JUnit report",
)
_FAILED_OUTCOMES = frozenset({TestOutcome.FAILED, TestOutcome.ERROR})


def requires_report(policy: EvidencePolicy, kind: EvidenceKind) -> bool:
    return any(JUNIT_TOKEN in argument for argument in policy.command) or (
        policy.reports_required and kind in {EvidenceKind.RED, EvidenceKind.GREEN}
    )


def report_policy_violation(
    kind: EvidenceKind, report: TestReport, comparison_red: TestReport | None = None,
) -> Violation | None:
    """Check the report contents for a pack that requires reports."""
    if kind is EvidenceKind.RED and not _FAILED_OUTCOMES.intersection(report.values()):
        return Violation(
            "evidence.red-report-no-failure", "the RED report lists no failed or error test",
        )
    if kind is EvidenceKind.GREEN:
        if comparison_red is None:
            return Violation("evidence.report-no-red", "no comparison RED has a report")
        if not comparison_red.keys() <= report.keys() or any(
            outcome in _FAILED_OUTCOMES and report[test] is not TestOutcome.PASSED
            for test, outcome in comparison_red.items()
        ):
            return Violation(
                "evidence.report-incomplete",
                "the GREEN report must list all comparison RED tests and list its failures as passed",
            )
    return None
