"""Acceptance pack for ``ci-candidate-validity@1`` (D-21)."""

from __future__ import annotations

import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import AnchorMode, Tier
from vouch_mk2.init import InitError, generated_workflow, init_project
from vouch_mk2.lock import build_lock
from vouch_mk2.store import Store


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


def _job_with(text: str, token: str) -> str:
    matches = tuple(body for body in _jobs(text).values() if token in body)
    assert len(matches) == 1, token
    return matches[0]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _write_lock(path: Path) -> Path:
    path.write_bytes(build_lock(str(ROOT / "src")))
    return path


def _assert_candidate_is_checked_before_staging(
    planner: str,
    *,
    lock: str,
    project_argument: str,
) -> None:
    generate = f'python {lock} generate --root .vouch'
    check = f'python {lock} check --ci --root .vouch{project_argument}'
    formal = f'python {lock} formal'
    stage = "git add -- .vouch/"
    assert planner.index(generate) < planner.index(check)
    assert planner.index(check) < planner.index(formal) < planner.index(stage)
    candidate_validation = planner[planner.index(check) : planner.index(stage)]
    assert "--pending-anchor-ok" not in candidate_validation


def test_generated_and_dogfood_candidates_are_semantically_validated() -> None:
    generated = _job_with(generated_workflow(("unit-tests",)), "advance-anchor")
    _assert_candidate_is_checked_before_staging(
        generated,
        lock="vouch.lock",
        project_argument=" --project .",
    )

    dogfood = _job_with(CI.read_text(), "advance-anchor")
    _assert_candidate_is_checked_before_staging(
        dogfood,
        lock='"$RUNNER_TEMP/vouch.lock"',
        project_argument="",
    )


@pytest.mark.parametrize("tier", ("standard", "high"))
def test_non_lightweight_init_without_a_test_gate_refuses_unchanged(
    tmp_path: Path, tier: str
) -> None:
    project = tmp_path / tier
    project.mkdir()
    lock = _write_lock(tmp_path / f"{tier}.lock")

    code, output = _run(
        "init",
        "--project",
        str(project),
        "--lock",
        str(lock),
        "--tier",
        tier,
    )

    assert code == 2
    assert "--test-job" in output and tier in output
    assert tuple(project.iterdir()) == ()


def test_init_records_a_tier_coherent_anchor_mode(tmp_path: Path) -> None:
    lock_bytes = build_lock(str(ROOT / "src"))

    direct = tmp_path / "direct"
    direct.mkdir()
    with pytest.raises(InitError, match="anchor_mode=ci"):
        init_project(str(direct), lock_bytes, tier=Tier.STANDARD)
    assert tuple(direct.iterdir()) == ()

    direct_ci = tmp_path / "direct-ci"
    direct_ci.mkdir()
    init_project(
        str(direct_ci),
        lock_bytes,
        tier=Tier.STANDARD,
        anchor_mode=AnchorMode.CI,
    )
    with Store.open(str(direct_ci / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.tier is Tier.STANDARD
    assert snapshot.anchor_mode is AnchorMode.CI

    standard = tmp_path / "standard"
    standard.mkdir()
    lock = tmp_path / "vouch.lock"
    lock.write_bytes(lock_bytes)
    code, output = _run(
        "init",
        "--project",
        str(standard),
        "--lock",
        str(lock),
        "--tier",
        "standard",
        "--test-job",
        "unit-tests",
    )
    assert code == 0, output
    with Store.open(str(standard / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.tier is Tier.STANDARD
    assert snapshot.anchor_mode is AnchorMode.CI

    lightweight = tmp_path / "lightweight"
    lightweight.mkdir()
    code, output = _run(
        "init",
        "--project",
        str(lightweight),
        "--lock",
        str(lock),
    )
    assert code == 0, output
    with Store.open(str(lightweight / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.anchor_mode is AnchorMode.NONE

    gated_lightweight = tmp_path / "gated-lightweight"
    gated_lightweight.mkdir()
    code, output = _run(
        "init",
        "--project",
        str(gated_lightweight),
        "--lock",
        str(lock),
        "--test-job",
        "unit-tests",
    )
    assert code == 0, output
    with Store.open(str(gated_lightweight / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.anchor_mode is AnchorMode.CI


def test_concurrency_uses_a_vouch_specific_repository_namespace() -> None:
    for text in (generated_workflow(("unit-tests",)), CI.read_text()):
        header = text.split("\njobs:\n", 1)[0]
        group = re.search(r"(?m)^  group:\s*(.+)$", header)
        assert group is not None
        value = group.group(1)
        assert value.startswith("vouch-anchor-")
        assert "${{ github.repository_id }}" in value
        assert "${{ github.ref }}" in value
        assert "${{ github.workflow }}" not in value


def test_docs_do_not_claim_a_github_token_follow_up_run() -> None:
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text().lower()
    quickstart = (ROOT / "docs" / "quickstart.md").read_text().lower()
    combined = boundary + quickstart
    assert "github_token" in combined
    assert "does not trigger" in combined
    assert "post-anchor" in combined and "check --ci" in combined and "formal" in combined
    assert "github app" in combined and "ruleset" in combined
