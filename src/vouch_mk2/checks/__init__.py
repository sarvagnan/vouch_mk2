"""Public surface for pure Phase 3 structured checks."""

from vouch_mk2.checks.registry import (
    MECHANICAL_CHECKS,
    ORGANIZATIONAL_CHECKS,
    CheckEvaluator,
    run_checks,
)
from vouch_mk2.checks.results import CheckReport, CheckResult
from vouch_mk2.checks.verdicts import compose

__all__ = [
    "MECHANICAL_CHECKS",
    "ORGANIZATIONAL_CHECKS",
    "CheckEvaluator",
    "CheckReport",
    "CheckResult",
    "compose",
    "run_checks",
]
