"""Frozen acceptance pack for pr-merge-check@1 (D-50).

A pull request's store must equal its head replayed onto its base. ``check-merge`` recomputes that
merge on a private copy of the base and compares states, and both workflows run it on every pull
request. Every test here fails before the change, except tests marked ``# guard:``. The
implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import yaml

from vouch_mk2.cli import main
from vouch_mk2.init import generated_workflow
from vouch_mk2.store import ArtifactKind, Store, decode_event_record, digest_of
from vouch_mk2.store.codec import decode_raw_document, encode_document, encode_snapshot

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
CLAIM_BOUNDARY = ROOT / "docs" / "claim-boundary.md"
COMMANDS = ROOT / "docs" / "commands.md"
CONDITION = "github.event_name == 'pull_request'"

Json = dict[str, object]


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _must(*argv: str) -> str:
    code, out, err = _run(*argv)
    assert code == 0, (argv, out, err)
    return out


def _store(root: Path) -> Path:
    Store.init(str(root)).close()
    _must("specify", "--root", str(root), "--name", "x", "--version", "1")
    return root


def _copy(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def _specify(root: Path, name: str) -> None:
    _must("specify", "--root", str(root), "--name", name, "--version", "1")


def _merge(ours: Path, theirs: Path) -> str:
    """Merge THEIRS into OURS with the CLI and return the mode."""
    document: object = json.loads(
        _must("merge", "--root", str(ours), "--other", str(theirs), "--json", "--actor", "t")
    )
    assert isinstance(document, dict)
    mode = cast(Json, document)["mode"]
    assert isinstance(mode, str)
    return mode


def _check(base: Path, head: Path, merged: Path) -> tuple[int, str, str]:
    return _run(
        "check-merge", "--base", str(base), "--head", str(head), "--merged", str(merged)
    )


def _first_line(text: str) -> str:
    lines = text.splitlines()
    assert lines, "no output"
    return lines[0]


def _passes(base: Path, head: Path, merged: Path, mode: str) -> None:
    code, out, err = _check(base, head, merged)
    assert code == 0, (out, err)
    assert _first_line(out).startswith("PASS check-merge"), out
    assert mode in _first_line(out), out


def _fails(base: Path, head: Path, merged: Path, *named: str) -> str:
    code, out, err = _check(base, head, merged)
    assert code == 1, (out, err)
    assert _first_line(out).startswith("FAIL check-merge"), out
    for token in named:
        assert token in out, (token, out)
    return out


def _units(root: Path) -> list[str]:
    with Store.open(str(root), read_only=True) as store:
        return [unit.ref().label() for unit in store.read_snapshot().units]


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _diverged(tmp_path: Path) -> tuple[Path, Path]:
    """BASE and HEAD share x@1; BASE then specifies b@1 and HEAD a@1."""
    base = _store(tmp_path / "base")
    head = _copy(base, tmp_path / "head")
    _specify(base, "b")
    _specify(head, "a")
    return base, head


def _rewrite_head_record(root: Path, change: Callable[[dict[str, object]], None]) -> None:
    """Rewrite the head event record as a hand editor would: re-hash it and point state at it."""
    with Store.open(str(root), read_only=True) as store:
        snapshot = store.read_snapshot()
    objects = root / "objects" / "sha256"
    raw = decode_raw_document((objects / snapshot.event_head).read_bytes())
    change(raw)
    data = encode_document(raw)
    decode_event_record(data)
    digest = digest_of(ArtifactKind.EVENT, data)
    (objects / digest).write_bytes(data)
    (root / "state.json").write_bytes(encode_snapshot(replace(snapshot, event_head=digest)))


def _forged_head(tmp_path: Path) -> tuple[Path, Path, Path]:
    """HEAD merged an earlier BASE; BASE gains b2@1; HEAD's merge record then names BASE's new head.

    Returns (BASE, forged HEAD, unforged control HEAD).
    """
    base, head = _diverged(tmp_path)
    assert _merge(head, base) == "replayed"
    control = _copy(head, tmp_path / "control")
    _specify(base, "b2")
    with Store.open(str(base), read_only=True) as store:
        new_head = store.read_snapshot().event_head

    def retarget(raw: dict[str, object]) -> None:
        payload = raw["payload"]
        assert isinstance(payload, dict)
        cast(dict[str, object], payload)["theirs_head"] = new_head

    _rewrite_head_record(head, retarget)
    return base, head, control


def _tier_scenario(tmp_path: Path) -> tuple[Path, Path]:
    """At standard, HEAD lowers the tier while x@1 is specified; BASE then freezes x@1."""
    base = _store(tmp_path / "base")
    _must("set-tier", "--root", str(base), "--tier", "standard")
    head = _copy(base, tmp_path / "head")
    _must("set-tier", "--root", str(head), "--tier", "lightweight")
    _must("freeze", "--root", str(base), "--name", "x", "--pack-version", "x-pack/1")
    return base, head


# R1


def test_an_unchanged_base_fast_forwards_and_passes(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    head = _copy(base, tmp_path / "head")
    _specify(head, "a")
    _passes(base, head, head, "fast-forward")


# R2


def test_an_honest_replayed_merge_passes(tmp_path: Path) -> None:
    base, head = _diverged(tmp_path)
    assert _merge(head, base) == "replayed"
    assert _units(head) == ["x@1", "a@1", "b@1"]
    _passes(base, head, head, "replayed")


# R3


def test_a_head_the_base_already_contains_is_a_no_op_pass(tmp_path: Path) -> None:
    head = _store(tmp_path / "head")
    base = _copy(head, tmp_path / "base")
    _specify(base, "b")
    _passes(base, head, base, "no-op")


# R4


def test_a_merged_store_missing_base_work_fails_and_names_it(tmp_path: Path) -> None:
    base, head = _diverged(tmp_path)
    _fails(base, head, head, "b@1")


# R5


def test_a_forged_theirs_head_fails(tmp_path: Path) -> None:
    base, head, control = _forged_head(tmp_path)
    _fails(base, head, head, "b2@1")

    assert _merge(control, base) == "replayed"
    _passes(base, control, control, "replayed")


# R6


def test_a_merged_store_missing_head_work_names_the_unit_and_the_setting(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    head = _copy(base, tmp_path / "head")
    _specify(head, "h")
    _must("set-tier", "--root", str(head), "--tier", "standard")
    _fails(base, head, base, "h@1", "tier", "standard", "lightweight")


# R7


def test_a_head_whose_work_the_base_refuses_fails(tmp_path: Path) -> None:
    base, head = _tier_scenario(tmp_path)
    assert _merge(head, base) == "replayed"
    _fails(base, head, head, "merge.conflict", "tier.lowering-refused")


# R8


def test_an_unreadable_head_history_fails(tmp_path: Path) -> None:
    base, head = _diverged(tmp_path)
    assert _merge(head, base) == "replayed"
    with Store.open(str(head), read_only=True) as store:
        head_digest = store.read_snapshot().event_head
    (head / "objects" / "sha256" / head_digest).unlink()
    _fails(base, head, head)


# R14


def test_a_changed_unit_and_a_changed_effects_requirement_are_named(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    head = _copy(base, tmp_path / "head")
    _must("freeze", "--root", str(head), "--name", "x", "--pack-version", "x-pack/1")
    _must("require-effects", "--root", str(head))
    assert _units(head) == _units(base)
    _fails(base, head, base, "x@1", "effects_required")


# R15


def test_an_unreadable_merged_history_fails(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    head = _copy(base, tmp_path / "head")
    _specify(head, "a")
    merged = _copy(head, tmp_path / "merged")
    with Store.open(str(merged), read_only=True) as store:
        tip = store.read_snapshot().event_head
        predecessor = decode_event_record(store.get_object(ArtifactKind.EVENT, tip)).prev
    assert predecessor != "GENESIS"
    (merged / "objects" / "sha256" / predecessor).unlink()
    _fails(base, head, merged)


# R9


def test_a_path_without_a_store_exits_two(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    missing = tmp_path / "no-store-here"
    missing.mkdir()
    code, out, err = _check(base, base, missing)
    assert code == 2, (out, err)
    assert str(missing) in err


# R10


def test_check_merge_writes_nothing_and_leaves_no_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, head = _diverged(tmp_path)
    merged = _copy(head, tmp_path / "merged")
    assert _merge(merged, base) == "replayed"
    roots = (base, head, merged)
    for root in roots:
        # A leftover temporary file that a writable open's cleanup would remove.
        (root / ".state-interrupted.tmp").write_bytes(b"partial")
    before = [_tree(root) for root in roots]
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))

    assert _check(base, merged, merged)[0] == 0
    assert _check(base, head, head)[0] == 1
    assert _check(base, head, tmp_path / "absent")[0] == 2

    assert [_tree(root) for root in roots] == before
    assert list(scratch.iterdir()) == []


# R11


def _normalised(path: Path) -> str:
    return " ".join(path.read_text().split())


def test_claim_boundary_states_the_check_and_its_boundary() -> None:
    text = _normalised(CLAIM_BOUNDARY)
    for sentence in (
        "`check-merge` replays HEAD onto a private copy of BASE and passes only when the result "
        "has the same state as MERGED.",
        "Someone who can push to the default branch directly is outside this check: the base is "
        "trusted.",
        "A forged claim that changes no state can pass undetected; a later check fails if the "
        "claim makes its replay refuse or reach a state different from MERGED.",
        "A base commit with no store passes as an adoption pull request, with no replay.",
        "The check runs code from the pull request, so a pull request that edits the workflow, "
        "the lock or the source the lock is built from can remove or change the check.",
        "An honest pull request also fails when its work cannot be replayed onto the base.",
        "Requiring the job to pass before a merge is the repository's choice.",
    ):
        assert sentence in text, sentence
    assert any(
        line.startswith("#") and "check-merge" in line
        for line in COMMANDS.read_text().splitlines()
    )


# R12 and R13: the workflows


def _document(text: str) -> dict[str, object]:
    document: object = yaml.safe_load(text)
    assert isinstance(document, dict)
    return cast(dict[str, object], document)


def _merge_job(text: str) -> dict[str, object]:
    jobs = _document(text)["jobs"]
    assert isinstance(jobs, dict)
    job = cast(dict[str, object], jobs).get("merge_check")
    assert isinstance(job, dict), "no merge_check job"
    return cast(dict[str, object], job)


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job["steps"]
    assert isinstance(steps, list)
    result: list[dict[str, object]] = []
    for step in cast(list[object], steps):
        assert isinstance(step, dict)
        result.append(cast(dict[str, object], step))
    return result


def _run_text(step: dict[str, object]) -> str:
    run = step.get("run", "")
    assert isinstance(run, str)
    return run


def _check_step(job: dict[str, object]) -> tuple[int, dict[str, object]]:
    matches = [
        (index, step) for index, step in enumerate(_steps(job)) if "check-merge" in _run_text(step)
    ]
    assert len(matches) == 1, matches
    return matches[0]


WORKFLOWS: dict[str, tuple[Callable[[], str], str, str]] = {
    "generated": (
        generated_workflow,
        "python vouch.lock self-check --expect @.vouch/config.json",
        "python vouch.lock check-merge",
    ),
    "mk2": (
        CI.read_text,
        'uv run vouch-mk2 build-lock --output "$RUNNER_TEMP/vouch.lock"',
        'python "$RUNNER_TEMP/vouch.lock" check-merge',
    ),
}


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_both_workflows_have_a_read_only_pull_request_merge_check_job(workflow: str) -> None:
    text_of, before, invocation = WORKFLOWS[workflow]
    job = _merge_job(text_of())
    assert job["if"] == CONDITION
    assert job["permissions"] == {"contents": "read"}
    checkouts = [
        step for step in _steps(job)
        if isinstance(step.get("uses"), str) and "actions/checkout@" in cast(str, step["uses"])
    ]
    assert len(checkouts) == 1
    options = checkouts[0]["with"]
    assert isinstance(options, dict)
    assert options.get("persist-credentials") is False
    assert options.get("fetch-depth") == 0
    assert "ref" not in options, "the job checks out the pull request's merge commit"
    index, step = _check_step(job)
    assert step.get("shell") == "bash"
    script = _run_text(step)
    assert "${{" not in script
    assert "git archive" not in script
    assert invocation in " ".join(script.split())
    earlier = [
        position for position, other in enumerate(_steps(job))
        if before in " ".join(_run_text(other).split())
    ]
    assert earlier and earlier[0] < index


def _git(repo: Path, *argv: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    return completed.stdout.strip()


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


def _commit_store(
    repo: Path, store: Path | None, message: str, attributes: str | None = None,
) -> str:
    """Make the working tree's .vouch equal STORE (or absent), set .gitattributes, and commit."""
    target = repo / ".vouch"
    if target.exists():
        shutil.rmtree(target)
    if store is not None:
        shutil.copytree(store, target)
    gitattributes = repo / ".gitattributes"
    if attributes is None:
        gitattributes.unlink(missing_ok=True)
    else:
        gitattributes.write_text(attributes)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _merge_commit(repo: Path, tree_of: str, base: str, head: str) -> str:
    tree = _git(repo, "rev-parse", f"{tree_of}^{{tree}}")
    return _git(repo, "commit-tree", tree, "-p", base, "-p", head, "-m", "merge")


SHIM = """import sys
sys.path.insert(0, {src!r})
from vouch_mk2.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


def _repository(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A scratch repository with the commits R13 checks out, keyed by scenario."""
    stores = tmp_path / "stores"
    stores.mkdir()
    common = _store(stores / "common")
    base_store = _copy(common, stores / "base")
    head_store = _copy(common, stores / "head")
    _specify(base_store, "b")
    _specify(head_store, "a")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("scratch\n")

    no_store = _commit_store(repo, None, "no store")
    adopted = _commit_store(repo, common, "adopt")
    _git(repo, "reset", "-q", "--hard", no_store)
    shared = _commit_store(repo, common, "shared")
    head_only = _commit_store(repo, head_store, "head")
    _git(repo, "reset", "-q", "--hard", shared)
    base_moved = _commit_store(repo, base_store, "base")
    honest_store = _copy(head_store, stores / "honest")
    assert _merge(honest_store, base_store) == "replayed"
    honest = _commit_store(repo, honest_store, "honest head")
    _git(repo, "reset", "-q", "--hard", base_moved)
    hostile = _commit_store(
        repo, honest_store, "honest head with attributes", ".vouch/** export-ignore -text\n"
    )
    _git(repo, "reset", "-q", "--hard", base_moved)
    crlf = _commit_store(
        repo, honest_store, "honest head with crlf checkout", ".vouch/** text eol=crlf\n"
    )
    _git(repo, "reset", "-q", "--hard", base_moved)
    deleted = _commit_store(repo, None, "head deletes the store")
    commits = {
        "honest": _merge_commit(repo, honest, base_moved, honest),
        "attributes": _merge_commit(repo, hostile, base_moved, hostile),
        "crlf": _merge_commit(repo, crlf, base_moved, crlf),
        "dropped": _merge_commit(repo, head_only, base_moved, head_only),
        "lossy-merge": _merge_commit(repo, base_moved, base_moved, honest),
        "deleted": _merge_commit(repo, base_moved, base_moved, deleted),
        "adoption": _merge_commit(repo, adopted, no_store, adopted),
        "single-parent": honest,
    }
    return repo, commits


def _run_step(tmp_path: Path, repo: Path, commit: str, script: str) -> subprocess.CompletedProcess[str]:
    # A fresh checkout, as on a runner: Git rewrites only files it finds missing or changed.
    shutil.rmtree(repo / ".vouch", ignore_errors=True)
    (repo / ".gitattributes").unlink(missing_ok=True)
    _git(repo, "checkout", "-q", "--force", "--detach", commit)
    _git(repo, "clean", "-q", "-fdx")
    shim = SHIM.format(src=str(ROOT / "src"))
    (repo / "vouch.lock").write_text(shim)
    runner_temp = Path(tempfile.mkdtemp(dir=tmp_path))
    (runner_temp / "vouch.lock").write_text(shim)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("python", "python3"):
        link = bin_dir / name
        if not link.exists():
            link.symlink_to(sys.executable)
    script_file = tmp_path / "step.sh"
    script_file.write_text(script)
    env = _git_env() | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "PYTHONPATH": "",
    }
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script_file)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_the_merge_check_step_passes_honest_and_fails_dropped_merges(
    tmp_path: Path, workflow: str,
) -> None:
    text_of, _, _ = WORKFLOWS[workflow]
    _, step = _check_step(_merge_job(text_of()))
    script = _run_text(step)
    repo, commits = _repository(tmp_path)

    honest = _run_step(tmp_path, repo, commits["honest"], script)
    assert honest.returncode == 0, honest.stdout + honest.stderr

    attributes = _run_step(tmp_path, repo, commits["attributes"], script)
    assert attributes.returncode == 0, attributes.stdout + attributes.stderr

    crlf = _run_step(tmp_path, repo, commits["crlf"], script)
    assert crlf.returncode == 0, crlf.stdout + crlf.stderr
    committed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", "HEAD:.vouch/state.json"],
        check=True, capture_output=True, env=_git_env(),
    ).stdout
    assert (repo / ".vouch" / "state.json").read_bytes() != committed, "the checkout must differ"

    dropped = _run_step(tmp_path, repo, commits["dropped"], script)
    assert dropped.returncode != 0, dropped.stdout + dropped.stderr
    assert "FAIL check-merge" in dropped.stdout + dropped.stderr

    lossy = _run_step(tmp_path, repo, commits["lossy-merge"], script)
    assert lossy.returncode != 0, lossy.stdout + lossy.stderr
    assert "FAIL check-merge" in lossy.stdout + lossy.stderr
    assert "a@1" in lossy.stdout + lossy.stderr

    deleted = _run_step(tmp_path, repo, commits["deleted"], script)
    assert deleted.returncode != 0, deleted.stdout + deleted.stderr

    adoption = _run_step(tmp_path, repo, commits["adoption"], script)
    assert adoption.returncode == 0, adoption.stdout + adoption.stderr

    single = _run_step(tmp_path, repo, commits["single-parent"], script)
    assert single.returncode != 0, single.stdout + single.stderr


# Guard


def test_guard_the_scenarios_merge_as_described(tmp_path: Path) -> None:  # guard:
    for name in ("r2", "r5", "r7", "r13"):
        (tmp_path / name).mkdir()
    base, head = _diverged(tmp_path / "r2")
    assert _merge(head, base) == "replayed"

    forged_base, forged, control = _forged_head(tmp_path / "r5")
    assert _merge(_copy(forged, tmp_path / "r5-forged-merge"), forged_base) == "no-op"
    assert _merge(_copy(control, tmp_path / "r5-control-merge"), forged_base) == "replayed"

    tier_base, tier_head = _tier_scenario(tmp_path / "r7")
    assert _merge(tier_head, tier_base) == "replayed"
    refused_copy = _copy(tier_base, tmp_path / "r7-base-side")
    code, out, err = _run(
        "merge", "--root", str(refused_copy), "--other", str(tier_head), "--json", "--actor", "t"
    )
    assert code != 0 and "tier.lowering-refused" in out + err, (code, out, err)

    repo, commits = _repository(tmp_path / "r13")
    for commit in commits.values():
        _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    for name, commit in commits.items():
        parents = _git(repo, "rev-list", "--parents", "-n", "1", commit).count(" ")
        assert parents == (1 if name == "single-parent" else 2), name
