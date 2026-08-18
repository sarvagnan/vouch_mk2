"""Acceptance pack for self-hosted unit generated-ci@1 (external review, blocker #1 and #2).

Unit #14 fixed the anchor-step ordering in mk2's OWN ci.yml and asserted only that; the
fragment `init` writes for adopters kept the broken shape — `check --ci` before the anchor
step, so any adopter at standard/high with anchor mode `ci` gets a permanently red workflow
that never reaches the step that would resolve it. This pack asserts the GENERATED artifact.

Every test here FAILED against the pre-change tree — except tests marked `# guard:`.
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _fragment() -> str:
    from vouch_mk2.init import generated_workflow

    return generated_workflow()


def _jobs(text: str) -> dict[str, str]:
    """Split the workflow into top-level job blocks by 2-space-indented keys under `jobs:`."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n(?=  [a-z][\w-]*:\n)", body)
    jobs: dict[str, str] = {}
    for part in parts:
        match = re.match(r"\s*([\w-]+):", part)
        if match is not None:
            jobs[match.group(1)] = part
    return jobs


# ---- the generated workflow can actually reach green ----


def test_validation_runs_before_anchoring_and_anchoring_is_a_separate_job() -> None:
    jobs = _jobs(_fragment())
    assert len(jobs) >= 2, f"expected separate validate and anchor jobs, got {list(jobs)}"
    anchor = next(name for name, body in jobs.items() if "advance-anchor" in body)
    validate = next(name for name, body in jobs.items() if "check --ci" in body)
    assert anchor != validate, "anchoring must not share a job with validation"
    assert re.search(rf"needs:\s*(\[[^\]]*{validate}[^\]]*\]|{validate})", jobs[anchor]), "the anchor job must need the validation job"


def test_validation_tolerates_a_pending_anchor_so_the_first_run_is_not_a_deadlock() -> None:
    validate = next(body for body in _jobs(_fragment()).values() if "check --ci" in body)
    assert "--pending-anchor-ok" in validate, (
        "the validation job must accept a store whose only mechanical non-PASS row is a pending "
        "CI anchor, or a standard-tier adopter can never reach the anchor job"
    )


def test_pending_anchor_ok_accepts_only_that_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "t.py").write_text("def test_x():\n    assert False\n")
    root = str(project / ".vouch")
    Store.init(root).close()
    for argv in (["set-tier", "--tier", "standard"], ["set-anchor-mode", "--mode", "ci"],
                 ["specify", "--name", "u", "--version", "v1"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0
    assert main(["freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
                 "--project", str(project), "--pack-test-file", "tests/t.py"]) == 0
    for argv in (["mark-ready", "--name", "u"], ["start", "--name", "u"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0
    for kind, code in (("red", 1), ("green", 0)):
        assert main(["record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind, "--",
                     sys.executable, "-c", f"raise SystemExit({code})"]) == 0
    assert main(["advance-anchor", "--root", root, "--unit-name", "u"]) == 0
    assert main(["mark-done", "--root", root, "--name", "u"]) == 0
    assert main(["publish", "--root", root, "--name", "u"]) == 0
    assert main(["generate", "--root", root]) == 0
    capsys.readouterr()
    # Without the flag: exit 1 (the anchor has no run claim). With it: exit 0, row still printed.
    assert main(["check", "--ci", "--root", root, "--project", str(project)]) == 1
    assert main(["check", "--ci", "--pending-anchor-ok", "--root", root, "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "no workflow run claim" in out, "the tolerated row must still be reported, not hidden"
    # A real FAIL is never tolerated by the flag.
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    assert main(["check", "--ci", "--pending-anchor-ok", "--root", root, "--project", str(project)]) == 1


# ---- write credentials are scoped to the job that needs them ----


def test_validation_job_has_no_write_credentials() -> None:
    text = _fragment()
    jobs = _jobs(text)
    validate = next(body for body in jobs.values() if "check --ci" in body)
    anchor = next(body for body in jobs.values() if "advance-anchor" in body)
    assert not re.search(r"^permissions:\s*\n\s+contents:\s*write", text, re.M), "no job-wide write permission"
    assert "contents: read" in validate and "contents: write" not in validate
    assert "contents: write" in anchor
    assert "persist-credentials: false" in validate, "validation must not keep a usable git credential"


def test_third_party_actions_are_pinned_to_commit_shas() -> None:
    for use in re.findall(r"uses:\s*(\S+)", _fragment()):
        assert re.search(r"@[0-9a-f]{40}$", use), f"{use} must be pinned to a commit SHA"


# ---- the anchor job cannot fight itself or strand a required check ----


def test_anchor_job_only_runs_on_default_branch_pushes_and_never_on_pull_requests() -> None:
    anchor = next(body for body in _jobs(_fragment()).values() if "advance-anchor" in body)
    assert re.search(r"if:.*github\.event_name\s*==\s*'push'", anchor)
    assert re.search(r"github\.ref\s*==\s*format\('refs/heads/\{0\}',\s*github\.event\.repository\.default_branch\)|github\.ref\s*==\s*'refs/heads/", anchor)
    assert "[skip ci]" not in anchor, (
        "a skipped bot commit on a PR head can leave a required check pending; anchoring on "
        "default-branch pushes needs no skip marker because the re-run finds nothing to do"
    )


def test_workflow_has_concurrency_control() -> None:
    assert re.search(r"^concurrency:", _fragment(), re.M)


def test_anchor_step_advances_only_heads_that_lack_a_run_claim() -> None:
    anchor = next(body for body in _jobs(_fragment()).values() if "advance-anchor" in body)
    assert "anchor_run_id" in anchor, "advance only heads whose current anchor has no run claim"


def test_fragment_tells_the_adopter_to_wire_their_own_tests() -> None:
    text = _fragment()
    assert re.search(r"#.*needs:", text) or "your test job" in text.lower()


# ---- mk2's own workflow keeps the property, stated once ----


def test_mk2_own_ci_still_anchors_before_asserting_its_store_is_clean() -> None:
    # guard: unit #14's property must survive this unit.
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert text.index("advance-anchor") < text.index("check --ci --root .vouch")
