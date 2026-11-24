"""Frozen acceptance pack for anchor-workflow@1 (D-57, vouch_mk2-b6g.20 and vouch_mk2-b6g.21).

The anchor publish step names every refusal; only anchoring calls share the generated workflow's
concurrency group; and the anchor plan step says when it anchors a head an earlier anchoring run
should have published. The publish and plan steps run here as bash scripts taken from the
workflow text, in scratch git repositories, with a local bare repository standing in for GitHub
(reached through a `url.<path>.insteadOf` rule for the URL the script builds) and a `python` shim
that skips only the minutes-long `vouch.lock formal`. Every test here fails before the change,
except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
import yaml

from vouch_mk2.init import generated_workflow

ROOT = Path(__file__).resolve().parents[1]
MK2_CI = ROOT / ".github" / "workflows" / "ci.yml"
TOKEN = "tok"
REPOSITORY = "o/r"
GROUP = (
    "vouch-anchor-${{ github.repository_id }}-${{ github.ref }}-"
    "${{ inputs.anchoring && 'anchoring' || github.run_id }}"
)
REFUSALS = {
    "checksum": "REFUSED publish: the candidate bundle does not match its checksum",
    "base-shape": "REFUSED publish: the planned base is not a full commit id",
    "candidate-shape": "REFUSED publish: the candidate is not a full commit id",
    "not-in-bundle": "REFUSED publish: the bundle does not hold the planned candidate",
    "two-parents": "REFUSED publish: the candidate does not have exactly one parent",
    "wrong-parent": "REFUSED publish: the candidate's parent is not the planned base",
    "outside-vouch": "REFUSED publish: the candidate changes a path outside .vouch/",
    "symlink": "REFUSED publish: the candidate introduces a non-regular file under .vouch/",
    "moved": (
        "REFUSED publish: trunk moved since this run planned its anchor; a later run will anchor it"
    ),
}

Json = dict[str, object]


def _document(text: str) -> Json:
    return cast(Json, yaml.safe_load(text))


def _step(text: str, job: str, *, step_id: str | None = None, name: str | None = None) -> str:
    jobs = cast(Json, _document(text)["jobs"])
    steps = cast(list[Json], cast(Json, jobs[job])["steps"])
    (step,) = [
        item for item in steps
        if (step_id is not None and item.get("id") == step_id)
        or (name is not None and item.get("name") == name)
    ]
    return cast(str, step["run"])


def _env() -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("GITHUB_", "GIT_"))
    }
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_NOSYSTEM="1",
    )
    return env


def _git(cwd: Path, *argv: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *argv], cwd=cwd, env=env or _env(), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (argv, result.stdout, result.stderr)
    return result.stdout.strip()


def _lock(project: Path, *argv: str) -> None:
    result = subprocess.run(
        [sys.executable, "vouch.lock", *argv], cwd=project, env=_env(),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (argv, result.stdout, result.stderr)


@dataclass(frozen=True)
class Repo:
    project: Path
    shim: Path
    runner_temp: Path


_TEMPLATE: dict[str, Path] = {}


def _adopted(tmp_path: Path) -> Repo:
    """A fresh copy of a project init adopted at lightweight with CI anchoring, committed once
    (no evidence yet). The template is built once per session and copied, to keep the pack fast."""
    template = _TEMPLATE.get("project")
    if template is None:
        template = _build_template(tmp_path.parent / "anchor-workflow-template")
        _TEMPLATE["project"] = template
    project = tmp_path / "proj"
    shutil.copytree(template, project, symlinks=True)
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "vouch.lock" ] && [ "$2" = "formal" ]; then exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    (shim / "python").chmod(0o755)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    return Repo(project, shim, runner_temp)


def _build_template(project: Path) -> Path:
    project.mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    result = subprocess.run(
        [sys.executable, "-c", (
            "from vouch_mk2.cli import main; raise SystemExit(main(['init', '--project', '.', "
            "'--tier', 'lightweight', '--test-job', 'unit-tests']))"
        )],
        cwd=project, env=_env(), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    (project / "packs").mkdir()
    (project / "packs" / "u.spec.md").write_text("# u\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}\n')
    (project / "tests").mkdir()
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    _lock(project, "specify", "--root", ".vouch", "--name", "u", "--version", "v1")
    _lock(
        project, "freeze", "--root", ".vouch", "--name", "u", "--pack-version", "p1",
        "--project", ".", "--pack-file", "packs/u.spec.md", "--pack-test-file", "tests/t.py",
        "--pack-effects-file", "packs/u.effects.json",
    )
    _lock(project, "mark-ready", "--root", ".vouch", "--name", "u")
    _lock(project, "start", "--root", ".vouch", "--name", "u")
    _lock(project, "mark-open-in-ci", "--root", ".vouch", "--name", "u", "--reason", "pending")
    _commit(project, "adopted")
    return project


def _commit(project: Path, message: str) -> str:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "--allow-empty", "-m", message)
    return _git(project, "rev-parse", "HEAD")


def _red(project: Path) -> None:
    _lock(
        project, "record-evidence", "--root", ".vouch", "--unit", "u@v1", "--kind", "red",
        "--", sys.executable, "-c", "raise SystemExit(1)",
    )


def _head(project: Path) -> str:
    state = cast(Json, json.loads((project / ".vouch" / "state.json").read_text()))
    (head,) = [
        cast(Json, item) for item in cast(list[object], state["evidence_heads"])
        if cast(Json, item)["unit_name"] == "u"
    ]
    return cast(str, head["head"])


def _plan(repo: Repo, workflow: str) -> tuple[int, str, dict[str, str]]:
    script = _step(workflow, "anchor_plan", step_id="candidate")
    output = repo.runner_temp / "github-output"
    output.write_text("")
    env = dict(
        _env(), PATH=f"{repo.shim}:{os.environ['PATH']}", RUNNER_TEMP=str(repo.runner_temp),
        GITHUB_OUTPUT=str(output), GITHUB_RUN_ID="4242", GITHUB_ACTIONS="true",
    )
    result = subprocess.run(
        ["bash", "-c", script], cwd=repo.project, env=env,
        capture_output=True, text=True, check=False,
    )
    outputs = dict(
        line.split("=", 1) for line in output.read_text().splitlines() if "=" in line
    )
    return result.returncode, result.stdout + result.stderr, outputs


def _late_line(label: str, head: str) -> str:
    return f"vouch: anchoring {label} late: its head {head[:16]} was already pending at the previous commit"


def _late_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("vouch: anchoring")]


def _head_of(project: Path, name: str) -> str:
    state = cast(Json, json.loads((project / ".vouch" / "state.json").read_text()))
    (head,) = [
        cast(Json, item) for item in cast(list[object], state["evidence_heads"])
        if cast(Json, item)["unit_name"] == name
    ]
    return cast(str, head["head"])


def _anchored_in(project: Path, candidate: str, name: str) -> tuple[object, object]:
    state = cast(Json, json.loads(_git(project, "show", f"{candidate}:.vouch/state.json")))
    (head,) = [
        cast(Json, item) for item in cast(list[object], state["evidence_heads"])
        if cast(Json, item)["unit_name"] == name
    ]
    return head["anchored"], head["anchor_run_id"]


def _second_unit(project: Path) -> None:
    (project / "packs" / "w.spec.md").write_text("# w\n")
    (project / "tests" / "w.py").write_text("def test_w():\n    assert True\n")
    _lock(project, "specify", "--root", ".vouch", "--name", "w", "--version", "v1")
    _lock(
        project, "freeze", "--root", ".vouch", "--name", "w", "--pack-version", "p1",
        "--project", ".", "--pack-file", "packs/w.spec.md", "--pack-test-file", "tests/w.py",
        "--pack-effects-file", "packs/u.effects.json",
    )
    _lock(project, "mark-ready", "--root", ".vouch", "--name", "w")
    _lock(project, "start", "--root", ".vouch", "--name", "w")
    _lock(project, "mark-open-in-ci", "--root", ".vouch", "--name", "w", "--reason", "pending")


def _red_for(project: Path, label: str) -> None:
    _lock(
        project, "record-evidence", "--root", ".vouch", "--unit", label, "--kind", "red",
        "--", sys.executable, "-c", "raise SystemExit(1)",
    )


# R1 — concurrency


def test_only_anchoring_calls_share_the_concurrency_group() -> None:
    document = _document(generated_workflow(("unit-tests",)))
    concurrency = cast(Json, document["concurrency"])
    assert concurrency["group"] == GROUP
    assert concurrency["cancel-in-progress"] is False
    for job in cast(Json, document["jobs"]).values():
        assert "concurrency" not in cast(Json, job)


def test_mk2s_own_ci_keeps_its_group() -> None:  # guard: mk2's single-trigger group is unchanged
    concurrency = cast(Json, _document(MK2_CI.read_text())["concurrency"])
    assert concurrency["group"] == "vouch-anchor-${{ github.repository_id }}-${{ github.ref }}"


# R2 — late anchor signal


def test_a_head_left_pending_at_the_previous_commit_is_signalled(tmp_path: Path) -> None:
    repo = _adopted(tmp_path)
    _second_unit(repo.project)
    _red(repo.project)
    _commit(repo.project, "u's evidence, left pending")
    u_head = _head_of(repo.project, "u")
    _red_for(repo.project, "w@v1")
    (repo.project / "README.md").write_text("later\n")
    _commit(repo.project, "a later push with w's evidence")
    w_head = _head_of(repo.project, "w")
    code, out, outputs = _plan(repo, generated_workflow(("unit-tests",)))
    assert code == 0, out
    assert outputs.get("changed") == "true", out
    assert _late_lines(out) == [_late_line("u@v1", u_head)], out
    candidate = outputs["candidate"]
    assert _anchored_in(repo.project, candidate, "u") == (u_head, "4242")
    assert _anchored_in(repo.project, candidate, "w") == (w_head, "4242")


@pytest.mark.parametrize(
    "case",
    [
        "recorded-in-this-push", "first-commit", "already-anchored", "replaced-while-pending",
        "parent-without-store", "parent-store-malformed",
    ],
)
def test_a_head_not_pending_at_the_previous_commit_is_not_signalled(  # guard: no signal today
    tmp_path: Path, case: str,
) -> None:
    repo = _adopted(tmp_path)
    project = repo.project
    if case == "first-commit":
        shutil.rmtree(project / ".git")
        _git(project, "init", "-q", "-b", "main")
        _red(project)
        _commit(project, "only commit")
    elif case == "already-anchored":
        _red(project)
        code, out, _ = _plan(repo, generated_workflow(("unit-tests",)))
        assert code == 0, out
        _commit(project, "anchored")
        _red(project)
        _commit(project, "new evidence")
    elif case == "replaced-while-pending":
        _red(project)
        _commit(project, "first evidence, left pending")
        _red(project)
        _commit(project, "newer evidence for the same unit")
    elif case == "parent-without-store":
        shutil.rmtree(project / ".git")
        _git(project, "init", "-q", "-b", "main")
        aside = tmp_path / "store-aside"
        shutil.move(str(project / ".vouch"), str(aside))
        (project / "README.md").write_text("before adoption\n")
        _commit(project, "before adoption")
        shutil.move(str(aside), str(project / ".vouch"))
        _red(project)
        _commit(project, "adopted with evidence")
    elif case == "parent-store-malformed":
        state = project / ".vouch" / "state.json"
        good = state.read_bytes()
        state.write_text("{")
        _commit(project, "a broken store")
        state.write_bytes(good)
        _red(project)
        _commit(project, "repaired, with evidence")
    else:
        _red(project)
        _commit(project, "evidence in this push")
    head = _head(project)
    code, out, outputs = _plan(repo, generated_workflow(("unit-tests",)))
    assert code == 0, out
    assert outputs.get("changed") == "true", out
    assert _late_lines(out) == [], out
    assert _anchored_in(project, outputs["candidate"], "u") == (head, "4242")


# R3 — publish refusals


@dataclass(frozen=True)
class Publish:
    origin: Path
    base: str
    candidate: str
    env: dict[str, str]


def _publishable(tmp_path: Path) -> tuple[Repo, Publish]:
    repo = _adopted(tmp_path)
    _red(repo.project)
    base = _commit(repo.project, "evidence")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(origin))
    _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(repo.project, "push", "-q", str(origin), "main:trunk")
    code, out, outputs = _plan(repo, generated_workflow(("unit-tests",)))
    assert code == 0 and outputs.get("changed") == "true", out
    transport = repo.runner_temp / "vouch-candidate"
    transport.mkdir()
    for name in ("vouch-candidate.bundle", "vouch-candidate.bundle.sha256"):
        shutil.copy2(repo.runner_temp / name, transport / name)
    config = tmp_path / "gitconfig"
    config.write_text(
        f'[url "{origin}"]\n'
        f"\tinsteadOf = https://x-access-token:{TOKEN}@github.com/{REPOSITORY}.git\n"
    )
    env = dict(
        _env(), RUNNER_TEMP=str(repo.runner_temp), GIT_CONFIG_GLOBAL=str(config),
        VOUCH_PUSH_TOKEN=TOKEN, PLANNED_BASE=base, CANDIDATE=outputs["candidate"],
        DEFAULT_BRANCH="trunk",
    )
    shutil.rmtree(repo.runner_temp / "vouch-publish", ignore_errors=True)
    return repo, Publish(origin, base, outputs["candidate"], env)


def _publish(repo: Repo, publish: Publish, workflow: str, **overrides: str) -> tuple[int, str]:
    script = _step(workflow, "anchor_publish", name="Validate candidate and publish")
    script = script.replace("${{ github.repository }}", REPOSITORY)
    assert "${{" not in script
    empty = repo.runner_temp.parent / "publisher-cwd"
    empty.mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", "-c", script], cwd=empty, env={**publish.env, **overrides},
        capture_output=True, text=True, check=False,
    )
    return result.returncode, result.stdout + result.stderr


def _rebundle(repo: Repo, commit: str) -> None:
    """Replace the transported bundle with one holding `commit` as vouch-candidate."""
    project = repo.project
    _git(project, "branch", "-f", "vouch-candidate", commit)
    transport = repo.runner_temp / "vouch-candidate"
    _git(project, "bundle", "create", str(transport / "vouch-candidate.bundle"),
         "refs/heads/vouch-candidate")
    _git(project, "branch", "-D", "vouch-candidate")
    digest = subprocess.run(
        ["shasum", "-a", "256", "vouch-candidate.bundle"], cwd=transport,
        capture_output=True, text=True, check=True,
    ).stdout
    (transport / "vouch-candidate.bundle.sha256").write_text(digest)


WORKFLOWS = {
    "generated": lambda: generated_workflow(("unit-tests",)),
    "mk2-ci": lambda: MK2_CI.read_text(),
}


@pytest.mark.parametrize("source", sorted(WORKFLOWS))
def test_a_valid_candidate_is_published(  # guard: the harness publishes before and after
    tmp_path: Path, source: str,
) -> None:
    repo, publish = _publishable(tmp_path)
    code, out = _publish(repo, publish, WORKFLOWS[source]())
    assert code == 0, out
    assert _git(publish.origin, "rev-parse", "trunk") == publish.candidate
    assert "REFUSED publish:" not in out


@pytest.mark.parametrize("source", sorted(WORKFLOWS))
@pytest.mark.parametrize("reason", sorted(REFUSALS))
def test_each_refusal_names_its_reason(tmp_path: Path, source: str, reason: str) -> None:
    repo, publish = _publishable(tmp_path)
    project = repo.project
    overrides: dict[str, str] = {}
    if reason == "checksum":
        (repo.runner_temp / "vouch-candidate" / "vouch-candidate.bundle.sha256").write_text(
            "0" * 64 + "  vouch-candidate.bundle\n"
        )
    elif reason == "base-shape":
        overrides["PLANNED_BASE"] = "not-a-commit"
    elif reason == "candidate-shape":
        overrides["CANDIDATE"] = "not-a-commit"
    elif reason == "not-in-bundle":
        overrides["CANDIDATE"] = publish.base
    elif reason == "two-parents":
        _git(project, "checkout", "-q", "-b", "side", f"{publish.base}^")
        side = _commit(project, "side")
        _git(project, "checkout", "-q", "--detach", publish.candidate)
        _git(project, "merge", "-q", "--no-ff", "-m", "merge", side)
        merged = _git(project, "rev-parse", "HEAD")
        _rebundle(repo, merged)
        overrides["CANDIDATE"] = merged
    elif reason == "wrong-parent":
        overrides["PLANNED_BASE"] = _git(project, "rev-parse", f"{publish.base}^")
    elif reason in {"outside-vouch", "symlink"}:
        _git(project, "checkout", "-q", "--detach", publish.base)
        if reason == "outside-vouch":
            (project / "README.md").write_text("not a store file\n")
        else:
            os.symlink("state.json", project / ".vouch" / "alias.json")
        crafted = _commit(project, "crafted")
        _rebundle(repo, crafted)
        overrides["CANDIDATE"] = crafted
    else:
        clone = tmp_path / "other"
        _git(tmp_path, "clone", "-q", str(publish.origin), str(clone))
        (clone / "moved.txt").write_text("moved\n")
        _commit(clone, "moved")
        _git(clone, "push", "-q", "origin", "HEAD:trunk")
    before = _git(publish.origin, "rev-parse", "trunk")
    code, out = _publish(repo, publish, WORKFLOWS[source](), **overrides)
    assert code == 1, out
    assert REFUSALS[reason] in out.splitlines(), out
    assert _git(publish.origin, "rev-parse", "trunk") == before
    assert len(re.findall(r"^REFUSED publish:", out, re.MULTILINE)) == 1, out
    # The step's own old messages are replaced, not repeated beside the new line.
    assert "candidate changes a path outside .vouch/" not in out.replace(REFUSALS["outside-vouch"], "")
    assert "candidate introduces non-regular mode" not in out
