"""Acceptance pack for ``pending-anchor-locality@1``."""

from __future__ import annotations

import inspect
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2 import verify
from vouch_mk2.checks import CheckReport, CheckResult
from vouch_mk2.cli import main
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import Store


def _result(check_id: str, verdict: Verdict) -> CheckResult:
    return CheckResult(check_id, verdict, "detail")


def _report(
    *mechanical: CheckResult,
    organizational: tuple[CheckResult, ...] = (),
) -> CheckReport:
    return CheckReport(1, tuple(mechanical), organizational)


def test_verify_owns_the_report_only_pending_anchor_classifier() -> None:
    classifier = getattr(verify, "pending_anchor_is_only_unchecked", None)
    assert callable(classifier)
    assert tuple(inspect.signature(classifier).parameters) == ("report",)

    pending = _result(mk2.CHECK_EVIDENCE_ANCHORS, Verdict.NOT_CHECKED)
    passed = _result("check.other", Verdict.PASS)
    failed = _result("check.failed", Verdict.FAIL)
    other_pending = _result("check.other", Verdict.NOT_CHECKED)
    assert classifier(_report(passed, pending))
    assert not classifier(_report(passed))
    assert not classifier(_report(pending, other_pending))
    assert not classifier(_report(pending, failed))
    assert not classifier(
        _report(pending, organizational=(_result("claim.owner", Verdict.FAIL),))
    )


def test_cli_does_not_reinterpret_evidence_heads() -> None:
    source = Path(inspect.getsourcefile(main) or "").read_text()
    assert "def _only_pending_anchor_is_not_checked" not in source
    assert "snapshot.evidence_heads" not in source
    assert "pending_anchor_is_only_unchecked(report)" in source


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _published_never_anchored(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "acceptance.py").write_text("def test_acceptance():\n    assert False\n")
    root = str(project / ".vouch")
    Store.init(root).close()
    commands = (
        ("set-tier", "--tier", "standard"),
        ("set-anchor-mode", "--mode", "ci"),
        ("specify", "--name", "u", "--version", "v1"),
    )
    for argv in commands:
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0
    assert _run(
        "freeze",
        "--root",
        root,
        "--name",
        "u",
        "--pack-version",
        "u-pack/1",
        "--project",
        str(project),
        "--pack-test-file",
        "tests/acceptance.py",
    )[0] == 0
    for argv in (("mark-ready", "--name", "u"), ("start", "--name", "u")):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0
    for kind, code in (("red", 1), ("green", 0)):
        assert _run(
            "record-evidence",
            "--root",
            root,
            "--unit",
            "u@v1",
            "--kind",
            kind,
            "--",
            sys.executable,
            "-c",
            f"raise SystemExit({code})",
        )[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    return project, root


def test_pending_anchor_behavior_remains_visible_and_narrow(tmp_path: Path) -> None:
    # guard: behavior stays the same while ownership moves into verification.
    project, root = _published_never_anchored(tmp_path)
    assert _run("check", "--ci", "--root", root, "--project", str(project))[0] == 1
    code, text = _run(
        "check",
        "--ci",
        "--pending-anchor-ok",
        "--root",
        root,
        "--project",
        str(project),
    )
    assert code == 0
    assert mk2.CHECK_EVIDENCE_ANCHORS in text and "NOT-CHECKED" in text

    code, output = _run(
        "check",
        "--ci",
        "--pending-anchor-ok",
        "--json",
        "--root",
        root,
        "--project",
        str(project),
    )
    assert code == 0
    payload = json.loads(output)
    row = next(item for item in payload["mechanical"] if item["check_id"] == mk2.CHECK_EVIDENCE_ANCHORS)
    assert row["verdict"] == "not-checked"

    (project / "tests/acceptance.py").write_text("changed after freeze\n")
    assert _run(
        "check",
        "--ci",
        "--pending-anchor-ok",
        "--root",
        root,
        "--project",
        str(project),
    )[0] == 1

