"""Acceptance pack for self-hosted unit adoption-seam@1 (D-18 B2/B4; adoption reviews A3-A8).

Every test here FAILED against the pre-change tree (RED recorded before implementation) and must
PASS at completion -- except tests marked `# guard:`. The implementer may not edit this file; a
finding that this pack is wrong is a DEVIATION adjudicated by the owner.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main

ROOT = Path(__file__).resolve().parents[1]
OK = [sys.executable, "-c", "raise SystemExit(0)"]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _init(tmp_path: Path, *extra: str) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    code, out = _run("init", "--project", str(project), *extra)
    assert code == 0, out
    return project, str(project / ".vouch")


# ---- R1 init ----


def test_init_refuses_an_existing_store_and_never_clobbers_a_workflow(tmp_path: Path) -> None:
    # guard: both behaviours already hold and must keep holding.
    project, _root = _init(tmp_path)
    code, out = _run("init", "--project", str(project))
    assert code != 0 and "already exists" in out
    other = tmp_path / "other"
    (other / ".github" / "workflows").mkdir(parents=True)
    marker = "existing: do not clobber\n"
    (other / ".github" / "workflows" / "vouch.yml").write_text(marker)
    code, out = _run("init", "--project", str(other))
    # pack/3 (DEV-as-02): D-19 supersedes the exit code here — a differing workflow is a
    # collision requiring operator action (exit 3), not silent success. The no-clobber and
    # suggestion assertions this guard exists for are unchanged.
    assert code == 3, out
    assert "suggested" in out
    assert (other / ".github" / "workflows" / "vouch.yml").read_text() == marker


def test_init_tier_sets_the_store_tier_at_creation(tmp_path: Path) -> None:
    _project, root = _init(tmp_path, "--tier", "standard")
    code, out = _run("status", "--root", root)
    assert code == 0, out
    assert "standard" in out


# ---- R2 check must not re-explore ----


def test_check_does_not_re_explore_the_bounded_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project, root = _init(tmp_path)
    assert _run("record-formal-certificate", "--root", root)[0] in (0, 2)
    import vouch_mk2.verify as verify

    calls: list[str] = []
    real = verify.run_formal

    def spy(*args: object, **kwargs: object) -> object:
        calls.append("explored")
        return real(*args, **kwargs)

    monkeypatch.setattr(verify, "run_formal", spy)
    code, _out = _run("check", "--root", root)
    assert code in (0, 1)
    assert calls == [], f"check re-explored the bounded model {len(calls)} time(s)"


def test_formal_verb_still_explores(monkeypatch: pytest.MonkeyPatch) -> None:
    # guard: `formal` is the one entry point that may explore; it must keep exploring.
    import vouch_mk2.cli as cli_module

    code, out = _run("formal")
    assert code == 0, out
    assert "states:" in out and "transitions:" in out
    assert cli_module is not None


def test_hook_never_explores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # guard: holds today and must keep holding.
    _project, root = _init(tmp_path)
    import vouch_mk2.verify as verify

    calls: list[str] = []
    real = verify.run_formal
    monkeypatch.setattr(
        verify, "run_formal", lambda *a, **k: (calls.append("x"), real(*a, **k))[1]
    )
    _run("hook", "--edit-target", str(tmp_path / "f.py"))
    assert calls == [], "hook re-explored the bounded model"


# ---- R3 workflow ----


def test_generated_workflow_pins_python_314_and_passes_project(tmp_path: Path) -> None:
    project, _root = _init(tmp_path)
    workflow = (project / ".github" / "workflows" / "vouch.yml").read_text()
    assert "actions/setup-python" in workflow
    assert "3.14" in workflow
    assert "--project" in workflow


# ---- R4 emitted commands ----


def test_lock_invocation_emits_its_own_program_name(tmp_path: Path) -> None:
    import subprocess

    project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    lock = project / "vouch.lock"
    assert lock.exists(), "init must vendor a lock"
    result = subprocess.run(
        [sys.executable, str(lock), "next", "--root", root, "--json"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["actions"], "expected at least one next action"
    for action in document["actions"]:
        assert action["argv"][0] == "vouch.lock", (
            f"a lock-invoked store emitted {action['argv'][0]!r}; an operator must be able to "
            "copy-paste the command they actually ran"
        )


def test_emitted_program_name_does_not_depend_on_unrelated_store_facts(tmp_path: Path) -> None:
    import subprocess

    project, root = _init(tmp_path)
    # The CI policy has nothing to do with how the program was invoked.
    assert _run("set-ci-policy", "--root", root, "--policy", "ledger")[0] == 0
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    lock = project / "vouch.lock"
    result = subprocess.run(
        [sys.executable, str(lock), "next", "--root", root, "--json"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["actions"], "expected at least one next action"
    for action in document["actions"]:
        assert action["argv"][0] == "vouch.lock", (
            f"emitted {action['argv'][0]!r} under ci_policy=ledger; the invocation name must not "
            "depend on the CI policy or any other unrelated recorded fact"
        )


# ---- R5 bootstrap ----


def test_next_emits_bootstrap_actions_on_an_empty_store(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    code, out = _run("next", "--root", root)
    assert code == 0, out
    assert "no next actions" not in out.lower()
    assert "specify" in out.lower()


# ---- R6 ci policy (D-18 B2) ----


def test_init_defaults_to_strict(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    code, out = _run("check", "--root", root, "--ci")
    assert code in (0, 1), out
    assert any(
        "ci policy" in line.lower() and "strict" in line.lower() for line in out.splitlines()
    ), f"check --ci must name the policy it enforced; got:\n{out}"


def test_strict_ci_policy_fails_a_nonterminal_unit_and_an_exception_clears_it(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    assert _run("set-ci-policy", "--root", root, "--policy", "strict")[0] == 0
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")[0] == 0
    code, out = _run("check", "--root", root, "--ci")
    assert code == 1 and "ci-policy" in out and "u" in out
    code, out = _run("mark-open-in-ci", "--root", root, "--name", "u", "--reason", "")
    assert code == 2 and "ci-policy.reason-required" in out
    assert _run(
        "mark-open-in-ci", "--root", root, "--name", "u", "--reason", "in flight this week"
    )[0] == 0
    code, out = _run("check", "--root", root, "--ci")
    assert "u" in out and "in flight this week" in out
    assert not any(
        line.startswith("FAIL") and "ci-policy" in line for line in out.splitlines()
    ), out
    assert _run("clear-open-in-ci", "--root", root, "--name", "u")[0] == 0
    code, out = _run("check", "--root", root, "--ci")
    assert code == 1 and "ci-policy" in out


def test_ledger_policy_is_todays_behaviour_and_is_named(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    assert _run("set-ci-policy", "--root", root, "--policy", "ledger")[0] == 0
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")[0] == 0
    code, out = _run("check", "--root", root, "--ci")
    assert "ledger" in out.lower()
    assert not any(
        line.startswith("FAIL") and "ci-policy" in line for line in out.splitlines()
    ), out


# ---- R7 status --json ----


def test_status_json_matches_the_text_facts(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    code, out = _run("status", "--root", root, "--json")
    assert code == 0, out
    document = json.loads(out)
    assert isinstance(document, dict)
    assert "unit_counts" in document and "evidence_chains" in document


# ---- R8 no vacuous PASS ----


def test_a_check_with_nothing_to_verify_is_not_a_vacuous_pass(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    code, out = _run("check", "--root", root)
    assert code in (0, 1)
    for line in out.splitlines():
        if line.startswith("PASS") and "check.evidence-chains" in line:
            pytest.fail(
                "check.evidence-chains PASSes on a store with no evidence chains at all; "
                "a check that verified nothing must be NOT_APPLICABLE or NOT_CHECKED"
            )


# ---- R9 legible org row ----


def test_org_tier_controls_renders_one_row_per_control(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    code, out = _run("check", "--root", root)
    assert code in (0, 1)
    org_lines = [line for line in out.splitlines() if "org.tier-controls" in line]
    assert org_lines, out
    assert max(len(line) for line in org_lines) < 400, (
        "org.tier-controls is emitted as one unreadable line; render one row per control"
    )


# ---- R10 build-lock mkdir ----


def test_build_lock_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "deeper" / "vouch.lock"
    code, out = _run("build-lock", "--output", str(target))
    assert code == 0, out
    assert target.exists()


# ---- R11 verb help ----


def test_every_verb_has_human_help_text() -> None:
    import argparse

    from vouch_mk2.cli import _parser  # noqa: PLC2701

    parser = _parser()
    subparsers = [
        action
        for action in parser._actions  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert subparsers, "no subcommands found"
    offenders: list[str] = []
    for action in subparsers:
        for choice in action._choices_actions:  # noqa: SLF001
            text = (choice.help or "").strip()
            # A reflected dataclass repr looks like "AmendDeviation(deviation_id: 'str', ...)".
            if text.endswith(")") and "(" in text and ": '" in text:
                offenders.append(choice.dest)
    assert not offenders, f"verbs whose help is a dataclass repr: {offenders}"


# ---- R12 secrets warning ----


def test_record_evidence_warns_about_a_secret_looking_command_line(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    code, out = _run(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red",
        "--", *FAIL, "--password", "hunter2-hunter2-hunter2",
    )
    assert code == 0, out
    assert "secret" in out.lower() or "password" in out.lower()


# ---- R13 python 3.14 ----


def test_python_314_is_stated_everywhere(tmp_path: Path) -> None:
    project, _root = _init(tmp_path)
    readme = (ROOT / "README.md").read_text()
    quickstart = (ROOT / "docs" / "quickstart.md").read_text()
    workflow = (project / ".github" / "workflows" / "vouch.yml").read_text()
    assert "3.14" in readme and "3.14" in quickstart and "3.14" in workflow
    lock = (project / "vouch.lock").read_bytes()
    assert b"3.14" in lock, "the lock must state the Python requirement in its bootstrap message"


# ---- R14 bundles ----


def test_next_recommends_bind_bundle_for_an_unbound_release(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0
    assert _run("prepare-release", "--root", root, "--release-id", "r1", "--include", "u")[0] == 0
    code, out = _run("next", "--root", root)
    assert code == 0, out
    assert "bind-bundle" in out
