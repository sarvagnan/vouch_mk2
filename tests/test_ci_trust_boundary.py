"""Acceptance pack for ``ci-trust-boundary@1`` (D-20)."""

from __future__ import annotations

import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.init import generated_workflow
from vouch_mk2.lock import build_lock


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n(?=  [a-zA-Z_][\w-]*:\n)", body)
    jobs: dict[str, str] = {}
    for part in parts:
        match = re.match(r"\s*([\w-]+):", part)
        if match is not None:
            jobs[match.group(1)] = part
    return jobs


def _job_with(jobs: dict[str, str], token: str) -> tuple[str, str]:
    matches = tuple((name, body) for name, body in jobs.items() if token in body)
    assert len(matches) == 1, f"expected one job containing {token!r}, got {[name for name, _ in matches]}"
    return matches[0]


def _permission(body: str) -> str:
    match = re.search(r"\n    permissions:\n      contents:\s*([a-z]+)", body)
    assert match is not None, body[:400]
    return match.group(1)


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def test_generated_workflow_is_callable_but_direct_runs_cannot_anchor() -> None:
    text = generated_workflow(("unit-tests", "lint"))
    trigger = text.split("\njobs:\n", 1)[0]
    assert "push:" in trigger and "pull_request:" in trigger and "workflow_call:" in trigger
    assert re.search(r"anchor(?:ing)?\s*:\s*\n(?:\s+.*\n)*?\s+type:\s*boolean", trigger)
    assert re.search(r"default:\s*false", trigger)

    jobs = _jobs(text)
    _, planner = _job_with(jobs, "advance-anchor")
    condition = re.search(r"\n    if:\s*(.+)", planner)
    assert condition is not None
    assert "github.event_name == 'workflow_call'" not in condition.group(1)
    assert "inputs.anchoring == true" in condition.group(1)
    assert "github.event.repository.default_branch" in condition.group(1)
    assert "github.event.repository.fork == false" in condition.group(1)


def test_init_prints_a_real_same_workflow_test_gate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supplied = tmp_path / "vouch.lock"
    supplied.write_bytes(build_lock(str(ROOT / "src")))

    code, output = _run(
        "init",
        "--project",
        str(project),
        "--lock",
        str(supplied),
        "--test-job",
        "unit-tests",
        "--test-job",
        "lint",
    )
    assert code == 0, output
    assert re.search(r"needs:\s*\[unit-tests,\s*lint\]", output)
    assert "uses: ./.github/workflows/vouch.yml" in output
    assert re.search(r"anchor(?:ing)?:\s*true", output)
    assert "if: github.event_name == 'push'" in output
    assert "github.event.repository.default_branch" in output

    generated = (project / ".github/workflows/vouch.yml").read_text()
    for body in _jobs(generated).values():
        needs = re.findall(r"\n    needs:\s*([^\n]+)", body)
        assert all("unit-tests" not in value and "lint" not in value for value in needs)
    assert not (project / ".github/workflows/ci.yml").exists()


def test_no_test_jobs_fail_closed_without_claiming_a_gate(tmp_path: Path) -> None:
    text = generated_workflow()
    assert "project-tests-required" not in text
    assert "No project test job is wired" not in text
    assert "workflow_call:" in text and re.search(r"default:\s*false", text)

    project = tmp_path / "project"
    project.mkdir()
    supplied = tmp_path / "vouch.lock"
    supplied.write_bytes(build_lock(str(ROOT / "src")))
    code, output = _run("init", "--project", str(project), "--lock", str(supplied))
    assert code == 0, output
    assert "anchor" in output.lower() and "disabled" in output.lower()


def test_generated_write_job_executes_no_repository_controlled_code() -> None:
    jobs = _jobs(generated_workflow(("unit-tests",)))
    planner_name, planner = _job_with(jobs, "advance-anchor")
    publisher_name, publisher = _job_with(jobs, "git push")
    assert planner_name != publisher_name
    assert _permission(planner) == "read"
    assert _permission(publisher) == "write"

    write_jobs = tuple(body for body in jobs.values() if _permission(body) == "write")
    assert write_jobs == (publisher,)
    forbidden = ("vouch.lock", "uv sync", "uv run", "./scripts/", "uses: ./")
    for token in forbidden:
        assert token not in publisher, f"write publisher executes repository-controlled token {token!r}"


def test_generated_publisher_fail_closes_on_stale_or_malformed_candidates() -> None:
    jobs = _jobs(generated_workflow(("unit-tests",)))
    _, publisher = _job_with(jobs, "git push")
    normalized = publisher.lower()
    assert "ls-remote" in publisher, "publisher must compare the live remote head with the planned base"
    assert "diff-tree" in publisher or "diff --name-only" in publisher
    assert ".vouch/" in publisher
    assert "parent" in normalized and ("rev-list" in publisher or "cat-file" in publisher)
    assert any(token in normalized for token in ("symlink", "160000", "120000", "regular mode"))
    assert "exit 1" in publisher or "raise systemexit" in normalized
    assert publisher.index("ls-remote") < publisher.index("git push")


def test_generated_workflow_keeps_ci_hardening() -> None:
    text = generated_workflow(("unit-tests",))
    assert re.search(r"^concurrency:", text, re.M)
    for use in re.findall(r"uses:\s*([^\s#]+)", text):
        if use.startswith("./"):
            continue
        assert re.search(r"@[0-9a-f]{40}$", use), f"mutable action reference: {use}"
    checkout_count = len(re.findall(r"uses:\s*actions/checkout@", text))
    assert checkout_count > 0
    assert text.count("persist-credentials: false") == checkout_count


def test_mk2_workflow_has_the_same_privilege_boundary() -> None:
    jobs = _jobs(CI.read_text())
    planner_name, planner = _job_with(jobs, "advance-anchor")
    publisher_name, publisher = _job_with(jobs, "git push")
    assert planner_name != publisher_name
    assert _permission(planner) == "read"
    assert _permission(publisher) == "write"
    for name, body in jobs.items():
        if _permission(body) != "write":
            continue
        for token in ("vouch.lock", "uv sync", "uv run", "./scripts/", "uses: ./"):
            assert token not in body, f"{name} executes {token!r} with contents: write"


def test_docs_describe_the_gate_and_transport_boundary() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text()
    adoption = (
        (ROOT / "docs" / "quickstart.md").read_text()
        + (ROOT / "docs" / "commands.md").read_text()
    )
    assert "D-20" in decisions and "not evidence" in decisions and "second trust root" in decisions
    assert "candidate" in boundary.lower() and "not evidence" in boundary.lower()
    assert "forge" in boundary.lower() and "organizational" in boundary.lower()
    assert "./.github/workflows/vouch.yml" in adoption
    assert "needs:" in adoption and "direct" in adoption.lower() and "never anchor" in adoption.lower()
