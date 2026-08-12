"""Permanently NOT_CHECKED organizational claim rows."""

from __future__ import annotations

from vouch_mk2.checks.results import CheckResult
from vouch_mk2.core.model import Snapshot
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict


def check_acceptance_ownership(_snapshot: Snapshot) -> CheckResult:
    return CheckResult(
        check_id=mk2.ORG_ACCEPTANCE_OWNERSHIP,
        verdict=Verdict.NOT_CHECKED,
        detail=(
            "a recorded owner identity proves a string was recorded, not that a "
            "person with authority and time validated intent"
        ),
    )


def check_intent_validation(_snapshot: Snapshot) -> CheckResult:
    return CheckResult(
        check_id=mk2.ORG_INTENT_VALIDATION,
        verdict=Verdict.NOT_CHECKED,
        detail=(
            "whether the frozen pack tests *the right thing* is human judgment; "
            "red proves cases CAN fail, not that they should"
        ),
    )


def check_independence(_snapshot: Snapshot) -> CheckResult:
    return CheckResult(
        check_id=mk2.ORG_INDEPENDENCE,
        verdict=Verdict.NOT_CHECKED,
        detail=(
            "identity comparison is a format heuristic (the 0.6 commit-author "
            "lesson), never proof of distinct people"
        ),
    )


def check_causal_red_history(_snapshot: Snapshot) -> CheckResult:
    return CheckResult(
        check_id=mk2.ORG_CAUSAL_RED_HISTORY,
        verdict=Verdict.NOT_CHECKED,
        detail=(
            "an anchored chain proves ordering and anchor timing; no anchor mode "
            "proves *why* the red occurred"
        ),
    )


def check_protective_closure_truthfulness(_snapshot: Snapshot) -> CheckResult:
    return CheckResult(
        check_id=mk2.ORG_PROTECTIVE_CLOSURE_TRUTHFULNESS,
        verdict=Verdict.NOT_CHECKED,
        detail=(
            "a closure record proves a trigger and roster snapshot were recorded, "
            "not that the trigger was real or the response appropriate"
        ),
    )
