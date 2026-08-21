"""Acceptance pack for ``ci-test-hermeticity@1``; Codex may not edit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CI_RUN_ID_VARIABLES = (
    "GITHUB_RUN_ID",
    "CI_PIPELINE_ID",
    "CIRCLE_WORKFLOW_ID",
    "BUILDKITE_BUILD_ID",
    "BUILD_BUILDID",
    "JENKINS_BUILD_ID",
    "BUILD_ID",
    "TRAVIS_BUILD_ID",
    "BITBUCKET_PIPELINE_UUID",
    "CODEBUILD_BUILD_ID",
    "DRONE_BUILD_NUMBER",
    "TEAMCITY_BUILD_ID",
)

LOCAL_ANCHOR_SCENARIOS = (
    "tests/test_ci_anchor.py::test_local_anchor_records_no_run_id",
    "tests/test_ci_anchor.py::test_anchor_run_claim_is_displayed_when_present_and_absent_when_not",
    "tests/test_ci_anchor.py::test_ci_anchor_mode_does_not_claim_ci_for_locally_anchored_units",
    "tests/test_ci_anchor_closure.py::test_a_locally_advanced_anchor_is_not_checked_and_a_ci_run_resolves_it",
    "tests/test_ci_anchor_regressions.py::test_ci_anchor_check_names_every_locally_advanced_unit",
    "tests/test_generated_ci.py::test_pending_anchor_ok_accepts_only_that_row",
)


def _pytest_with_ambient_ci(*scenarios: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {name: f"ambient-{index}" for index, name in enumerate(CI_RUN_ID_VARIABLES, 1)}
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *scenarios],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_local_anchor_scenarios_are_hermetic_under_ambient_ci_identity() -> None:
    result = _pytest_with_ambient_ci(*LOCAL_ANCHOR_SCENARIOS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_intentional_ci_anchor_identity_remains_observable() -> None:
    # guard: this scenario explicitly sets its own GITHUB_RUN_ID after test setup.
    result = _pytest_with_ambient_ci(
        "tests/test_ci_anchor.py::test_advance_anchor_records_the_run_id_claim"
    )
    assert result.returncode == 0, result.stdout + result.stderr

