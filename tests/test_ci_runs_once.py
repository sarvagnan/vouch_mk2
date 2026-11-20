"""Frozen acceptance pack for self-hosted unit ci-runs-once@1 (D-43).

The user's CI review (2026-11-20): every pull-request commit ran the gates job twice (once for the
branch push, once for the pull request); a default-branch push ran ``formal`` twice (in the gates
job and in the anchor planner's candidate step); and README § Gates said "Run what CI runs" but
listed four of the gates job's commands. D-43 limits push runs to ``main``, skips the gates job's
``formal`` step exactly when the anchor planner runs (the planner's candidate ``formal`` stays), and
makes README § Gates list every gates command in order. Every test here failed against the
pre-change tree, except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"
FORMAL = "uv run vouch-mk2 formal"


def _workflow() -> dict[object, object]:
    document = yaml.safe_load(CI.read_text())
    assert isinstance(document, dict)
    return document


def _triggers() -> dict[object, object]:
    document = _workflow()
    # PyYAML reads the bare key ``on`` as the boolean True.
    triggers = document.get(True, document.get("on"))
    assert isinstance(triggers, dict), triggers
    return triggers


def _job(name: str) -> dict[object, object]:
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def _steps(name: str) -> list[dict[object, object]]:
    steps = _job(name)["steps"]
    assert isinstance(steps, list)
    result: list[dict[object, object]] = []
    for step in steps:
        assert isinstance(step, dict)
        result.append(step)
    return result


def _normalise(line: str) -> str:
    """One command line, whitespace-collapsed; a leading ``python3`` reads as ``python``."""
    words = " ".join(line.split())
    return re.sub(r"^python3 ", "python ", words)


def _gates_commands() -> list[str]:
    commands: list[str] = []
    for step in _steps("gates"):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        for line in run.splitlines():
            if line.strip() and not line.strip().startswith("#"):
                commands.append(_normalise(line))
    return commands


def _readme_gates_lines() -> list[str]:
    text = README.read_text()
    section = text.split("\n## Gates\n", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```sh\n(.*?)```", section, re.S)
    assert block is not None, "README § Gates has no sh block"
    lines: list[str] = []
    for line in block.group(1).splitlines():
        # Drop a trailing shell comment (README annotates commands); CI lines carry none.
        stripped = re.sub(r"\s+#\s.*$", "", line).strip()
        if stripped and not stripped.startswith("#"):
            lines.append(_normalise(stripped))
    return lines


def _anchor_condition() -> str:
    condition = _job("anchor_plan")["if"]
    assert isinstance(condition, str)
    return condition


# ---- R1: push runs only on main ---------------------------------------------------------------


def test_push_runs_only_on_main() -> None:
    triggers = _triggers()
    assert triggers["push"] == {"branches": ["main"]}, triggers["push"]


def test_pull_requests_still_run_on_every_branch() -> None:  # guard:
    triggers = _triggers()
    assert "pull_request" in triggers and triggers["pull_request"] is None
    assert set(triggers) == {"push", "pull_request"}


# ---- R2: formal runs once per default-branch run -----------------------------------------------


def test_gates_formal_is_skipped_exactly_when_the_anchor_planner_runs() -> None:
    formal = [step for step in _steps("gates") if step.get("run") == FORMAL]
    assert len(formal) == 1, formal
    assert formal[0].get("if") == f"${{{{ !({_anchor_condition()}) }}}}", formal[0].get("if")


def test_the_anchor_planner_still_runs_formal_after_gates() -> None:  # guard:
    planner = _job("anchor_plan")
    assert planner["needs"] == "gates"
    candidate = next(step for step in _steps("anchor_plan") if step.get("id") == "candidate")
    script = candidate["run"]
    assert isinstance(script, str)
    assert script.index('python "$RUNNER_TEMP/vouch.lock" formal') < script.index("git add -- .vouch/")
    assert "if" not in candidate


def test_no_other_gates_step_is_conditional() -> None:  # guard:
    conditional = [step.get("name") for step in _steps("gates") if "if" in step and step.get("run") != FORMAL]
    assert conditional == []


# ---- R3: README § Gates lists every gates command, in order ------------------------------------


def test_readme_gates_lists_every_gates_command_in_order() -> None:
    readme = _readme_gates_lines()
    position = 0
    for command in _gates_commands():
        try:
            position = readme.index(command, position) + 1
        except ValueError:
            raise AssertionError(f"README § Gates lacks (in CI order): {command}") from None


def test_readme_gates_lines_are_shell_parsable() -> None:
    for line in _readme_gates_lines():
        shlex.split(line)


def test_readme_gates_names_the_scratch_directory() -> None:
    lines = _readme_gates_lines()
    assert lines and lines[0].startswith("RUNNER_TEMP="), lines[:1]
