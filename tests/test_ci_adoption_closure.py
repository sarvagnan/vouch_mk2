"""Acceptance pack for self-hosted unit ci-adoption-closure@1 (second external review of B1).

B1 was declared done and was not. Confirmed by reproduction:
  * a fresh adopter's first CI run still deadlocks — `--pending-anchor-ok` rejects a head that
    is at GENESIS ("never anchored"), which is precisely the state every new project is in, so
    validation exits 1 and the anchor job that would resolve it never runs. The B1 acceptance
    test masked this by advancing the anchor locally first — it tested a state no adopter has.
  * mk2's OWN ci.yml still carries every defect B1 fixed in the generated fragment: job-wide
    `contents: write`, mutable action tags, `[skip ci]`, no concurrency, persistent credentials.
    B1 fixed the class and left the instance — the mirror of the error B1 itself was fixing.
  * the anchor job runs repository-controlled code with a persisted write credential.
  * `needs: [your-test-job]` is only a comment, so project tests do not gate anchoring (D-18).
  * `--json` is bounded but not prompt: `read(65536)` hides small child output until exit.

Every test here FAILED against the pre-change tree — except tests marked `# guard:`.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _published_unit_never_anchored(tmp_path: Path) -> tuple[Path, str]:
    """Exactly what a real project looks like on its FIRST CI run: evidence, no anchor ever."""
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "t.py").write_text("def test_x():\n    assert False\n")
    root = str(project / ".vouch")
    Store.init(root).close()
    for argv in (["set-tier", "--tier", "standard"], ["set-anchor-mode", "--mode", "ci"],
                 ["specify", "--name", "u", "--version", "v1"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
                "--project", str(project), "--pack-test-file", "tests/t.py")[0] == 0
    for argv in (["mark-ready", "--name", "u"], ["start", "--name", "u"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0
    for kind, code in (("red", 1), ("green", 0)):
        assert _run("record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind, "--",
                    sys.executable, "-c", f"raise SystemExit({code})")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    return project, root


# ---- the first run must not deadlock ----


def test_a_never_anchored_store_passes_pending_anchor_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, root = _published_unit_never_anchored(tmp_path)
    capsys.readouterr()
    assert _run("check", "--ci", "--root", root, "--project", str(project))[0] == 1
    code, output = _run("check", "--ci", "--pending-anchor-ok", "--root", root, "--project", str(project))
    assert code == 0, output
    assert "GENESIS" in output or "no workflow run claim" in output, "the pending row must still be reported"


def test_pending_anchor_ok_still_refuses_everything_else(tmp_path: Path) -> None:
    project, root = _published_unit_never_anchored(tmp_path)
    (project / "tests" / "t.py").write_text("changed after freezing\n")
    assert _run("check", "--ci", "--pending-anchor-ok", "--root", root, "--project", str(project))[0] == 1
    (project / "tests" / "t.py").write_text("def test_x():\n    assert False\n")
    assert _run("specify", "--root", root, "--name", "open-work", "--version", "v1")[0] == 0
    assert _run("check", "--ci", "--pending-anchor-ok", "--root", root, "--project", str(project))[0] == 1


# ---- mk2's own workflow gets the shape it hands adopters ----


def test_mk2_own_ci_has_the_same_shape_it_generates() -> None:
    text = CI.read_text()
    assert not re.search(r"^permissions:\s*\n\s+contents:\s*write", text, re.M), "no job-wide write permission"
    assert "persist-credentials: false" in text
    assert "[skip ci]" not in text
    assert re.search(r"^concurrency:", text, re.M)
    for use in re.findall(r"uses:\s*(\S+)", text):
        assert re.search(r"@[0-9a-f]{40}$", use), f"{use} must be pinned to a commit SHA"


def test_mk2_own_ci_anchors_only_on_default_branch_pushes() -> None:
    text = CI.read_text()
    anchor_block = text[text.index("advance-anchor") - 2000 : text.index("advance-anchor")]
    assert "pull_request" not in anchor_block.split("if:")[-1] or "event_name == 'push'" in anchor_block


# ---- the anchor job does not run project code under a write credential ----


def test_anchor_jobs_do_not_persist_credentials_while_running_repository_code() -> None:
    from vouch_mk2.init import generated_workflow

    for name, text in (("generated", generated_workflow()), ("mk2", CI.read_text())):
        assert text.count("persist-credentials: false") >= 2, (
            f"{name}: every checkout, including the anchor job's, must refuse persisted credentials"
        )


# ---- project tests gate anchoring, not just Vouch validation ----


def test_init_can_wire_the_projects_own_test_job_into_the_gate(tmp_path: Path) -> None:
    from vouch_mk2.lock import build_lock

    project = tmp_path / "proj"
    project.mkdir()
    lock = project / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    assert _run("init", "--project", str(project), "--lock", str(lock),
                "--test-job", "unit-tests", "--test-job", "lint")[0] == 0
    workflow = (project / ".github" / "workflows" / "vouch.yml").read_text()
    anchor = workflow[workflow.index("anchor:"):]
    assert "unit-tests" in anchor and "lint" in anchor, anchor[:600]


def test_without_a_test_job_the_generated_workflow_fails_loudly_rather_than_silently() -> None:
    from vouch_mk2.init import generated_workflow

    text = generated_workflow()
    # A placeholder the adopter must replace — it must FAIL, not pass silently, so nobody
    # believes their tests gate anchoring when they do not.
    assert re.search(r"exit\s+1", text), "the unwired placeholder job must fail"
    assert "--test-job" in text


# ---- --json forwards promptly, not only when the buffer fills ----


def test_small_child_output_reaches_stderr_before_the_child_exits(tmp_path: Path) -> None:
    from vouch_mk2.lock import build_lock

    project, root = _published_unit_never_anchored(tmp_path)
    assert _run("specify", "--root", root, "--name", "w", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "w", "--pack-version", "w-pack/1",
                "--project", str(project), "--pack-test-file", "tests/t.py")[0] == 0
    for argv in (["mark-ready", "--name", "w"], ["start", "--name", "w"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0
    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    release = tmp_path / "go"
    script = tmp_path / "small.py"
    script.write_text(
        "import sys, time, pathlib\n"
        "sys.stdout.write('SMALL\\n')\n"
        "sys.stdout.flush()\n"
        f"target = pathlib.Path({str(release)!r})\n"
        "for _ in range(400):\n"
        "    if target.exists():\n"
        "        break\n"
        "    time.sleep(0.05)\n"
        "raise SystemExit(1)\n"
    )
    err_path = tmp_path / "e"
    out_path = tmp_path / "o"
    with err_path.open("wb") as err_file, out_path.open("wb") as out_file:
        proc = subprocess.Popen(
            [sys.executable, str(lock), "record-evidence", "--root", root, "--unit", "w@v1",
             "--kind", "red", "--json", "--", sys.executable, str(script)],
            stdout=out_file, stderr=err_file, env={**os.environ, "PYTHONPATH": ""},
        )
        try:
            seen = False
            for _ in range(100):
                if err_path.stat().st_size > 0:
                    seen = True
                    break
                assert proc.poll() is None, "child exited before the probe could observe forwarding"
                time.sleep(0.05)
            assert seen, "six bytes must reach stderr while the child is still running"
        finally:
            release.write_text("go")
            proc.wait(timeout=120)
    assert proc.returncode == 0


# ---- standards items the review named ----


def test_no_dead_captured_revision_in_the_evidence_protocol() -> None:
    from vouch_mk2.core import commands as command_module

    source = Path(command_module.__file__).read_text()
    if "captured_revision" in source:
        used = (ROOT / "src" / "vouch_mk2" / "core" / "decide.py").read_text()
        assert "captured_revision" in used, (
            "captured_revision is carried but never read — either use it in the guarantee or drop it"
        )


def test_pending_anchor_exception_uses_the_ir_constant() -> None:
    text = (ROOT / "src" / "vouch_mk2" / "cli.py").read_text()
    assert '"check.evidence-anchors"' not in text, "use mk2.CHECK_EVIDENCE_ANCHORS, not a literal"


def test_init_does_not_expose_a_test_only_hook_in_its_public_signature() -> None:
    import inspect

    from vouch_mk2.init import init_project

    assert "on_step" not in inspect.signature(init_project).parameters, (
        "fault injection must not widen the production API"
    )
