"""Frozen acceptance pack for evidence-policy@1 (D-54, vouch_mk2-inv.3).

A pack may bind an evidence-policy file that fixes the command RED and GREEN must run, requires
JUnit reports, or both. `record-evidence` refuses what the policy forbids, `decide` refuses a
rebind that changes it, and `check.evidence-chains` verifies every record for the current test
set. The child here is a script this file writes, so the expected IDs and outcomes are known
independently. Every test here fails before the change, except tests marked ``# guard:``. The
implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import commands
from vouch_mk2.core.decide import Refused, decide
from vouch_mk2.core.model import (
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    TestOutcome,
    UnitRef,
)
from vouch_mk2.store import Store
from vouch_mk2.store.codec import decode_pack_inventory, encode_test_report
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.verify import record_evidence

ROOT = Path(__file__).resolve().parents[1]
INVALID = "pack.evidence-policy-invalid"
NOT_BOUND = "evidence.command-not-bound"
REDACTION = "evidence.redaction-refused"
REQUIRED = "evidence.report-required"
NO_FAILURE = "evidence.red-report-no-failure"
INCOMPLETE = "evidence.report-incomplete"
NO_RED = "evidence.report-no-red"
REBIND = "rebind.evidence-policy-changed"
TOKEN_INVALID = "evidence.test-report-invalid"
BOUND = 64 * 1024 * 1024
ORPHAN = ".state-victim.tmp"

# The digests the code before this unit gives `_project`'s pack (packs/u.spec.md, tests/t.py and
# packs/u.effects.json), from its `freeze --json` receipt; a pack without a policy keeps them.
LEGACY_PACK = "bc1db125ed0a40242780c3e0aeb44e6a1e75fb1342930321a90f59f6497c390f"
LEGACY_TEST_SET = "3cec8523348123a4d691f51b0cd4cec311b9ecbb333a5cc755eba8db086834a7"

REPORTS = {"evidence_format": 1, "reports": "required"}

Json = dict[str, object]

WRITER = r'''import json, os, sys
spec = json.loads(os.environ["VOUCH_TEST_SPEC"])
if spec.get("sentinel"):
    open(spec["sentinel"], "w").close()
tests = spec.get("tests")
if tests is not None:
    args = sys.argv[1:]
    path = None
    for index, item in enumerate(args):
        if item.startswith("--report="):
            path = item.split("=", 1)[1]
        elif item == "--report" and index + 1 < len(args):
            path = args[index + 1]
    parts = ['<?xml version="1.0"?>', '<testsuite name="s">']
    for classname, name, outcome in tests:
        child = {"failed": '<failure message="f"/>', "error": '<error message="e"/>',
                 "skipped": "<skipped/>", "passed": ""}[outcome]
        parts.append(f'<testcase classname="{classname}" name="{name}">{child}</testcase>')
    parts.append("</testsuite>")
    with open(path, "w") as handle:
        handle.write("".join(parts))
raise SystemExit(spec.get("exit", 0))
'''


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


def _object(text: str) -> Json:
    document: object = json.loads(text)
    assert isinstance(document, dict)
    return cast(Json, document)


def _project(tmp_path: Path, name: str = "proj") -> tuple[Path, str]:
    project = tmp_path / name
    project.mkdir(parents=True)
    _must("init", "--project", str(project))
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    (project / "packs" / "u.spec.md").write_text("# u\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}\n')
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    (project / "writer.py").write_text(WRITER)
    return project, str(project / ".vouch")


def _writer(project: Path) -> list[str]:
    return [sys.executable, str(project / "writer.py")]


def _child(project: Path, *extra: str) -> list[str]:
    """The command the tests run: the writer, given the report token."""
    return [*_writer(project), "--report={junit}", *extra]


def _write_policy(project: Path, policy: object, name: str = "u.evidence.json") -> str:
    path = project / "packs" / name
    if isinstance(policy, bytes):
        path.write_bytes(policy)
    else:
        path.write_text(policy if isinstance(policy, str) else json.dumps(policy))
    return f"packs/{name}"


def _pack_args(
    project: Path, policy_file: str | None, *, tests: bool = True,
) -> tuple[str, ...]:
    return (
        "--project", str(project), "--pack-file", "packs/u.spec.md",
        *(("--pack-test-file", "tests/t.py") if tests else ()),
        "--pack-effects-file", "packs/u.effects.json",
        *(() if policy_file is None else ("--pack-evidence-file", policy_file)),
    )


def _freeze_args(
    project: Path, root: str, policy_file: str | None, *, tests: bool = True,
) -> tuple[str, ...]:
    return (
        "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
        *_pack_args(project, policy_file, tests=tests),
    )


def _started(project: Path, root: str, policy: object | None) -> None:
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    policy_file = None if policy is None else _write_policy(project, policy)
    _must(*_freeze_args(project, root, policy_file))
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")


def _spec(monkeypatch: pytest.MonkeyPatch, **spec: object) -> None:
    monkeypatch.setenv("VOUCH_TEST_SPEC", json.dumps(spec))


def _tests(*items: str) -> list[list[str]]:
    """Items like 'a:failed' under classname t."""
    return [["t", *item.split(":")] for item in items]


def _record(
    root: str, kind: str, argv: list[str], *options: str,
) -> tuple[int, str, str]:
    return _run(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind, "--json",
        *options, "--", *argv,
    )


def _recorded(root: str, kind: str, argv: list[str], *options: str) -> Json:
    code, out, err = _record(root, kind, argv, *options)
    assert code == 0, (kind, out, err)
    event = cast(list[Json], _object(out)["events"])[0]
    return cast(Json, event["evidence"])


def _red_green(
    project: Path, root: str, monkeypatch: pytest.MonkeyPatch,
    red: list[list[str]], green: list[list[str]],
) -> None:
    _spec(monkeypatch, tests=red, exit=1)
    _recorded(root, "red", _child(project), "--junit-report")
    _spec(monkeypatch, tests=green, exit=0)
    _recorded(root, "green", _child(project), "--junit-report")


def _seed_orphan(root: str) -> None:
    """A leftover temporary file that a writable store open sweeps."""
    (Path(root) / ORPHAN).write_bytes(b"orphan")


def _store_files(root: str, *, sweep_allowed: bool = False) -> dict[str, bytes]:
    base = Path(root)
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file()
        and path.name != ".lock"
        and not (sweep_allowed and path.name == ORPHAN)
    }


def _records(root: str) -> list[Json]:
    document = _object(_must("show-evidence", "--root", root, "--json"))
    (unit,) = [
        cast(Json, item) for item in cast(list[object], document["units"])
        if cast(Json, item)["unit"] == "u@v1"
    ]
    return cast(list[Json], unit["records"])


def _refused(
    root: str, result: tuple[int, str, str], code: str, *, sweep_allowed: bool = False,
    before: dict[str, bytes],
) -> None:
    """The refusal contract: exit 2, no payload on stdout, `vouch-mk2: <code>` on stderr.

    The writer child prints nothing, so stdout is empty."""
    status, out, err = result
    assert status == 2, (out, err)
    assert out == "", (out, err)
    assert any(line.startswith(f"vouch-mk2: {code}") for line in err.splitlines()), err
    assert _store_files(root, sweep_allowed=sweep_allowed) == {
        name: data for name, data in before.items() if not (sweep_allowed and name == ORPHAN)
    }
    if sweep_allowed:
        orphan = Path(root) / ORPHAN
        assert not orphan.exists() or orphan.read_bytes() == before[ORPHAN]


def _snapshot_before(root: str) -> dict[str, bytes]:
    _seed_orphan(root)
    return _store_files(root)


def _row(root: str, check_id: str) -> str:
    _, out, _ = _run("check", "--root", root)
    rows = [line.split() for line in out.splitlines() if check_id in line.split()]
    assert len(rows) == 1, out
    return rows[0][0]


def _receipt(out: str) -> Json:
    return cast(list[Json], _object(out)["events"])[0]


# R1


def test_a_pack_without_a_policy_keeps_its_digests_and_commands(  # guard: unchanged today
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    frozen = _receipt(_must(*_freeze_args(project, root, None), "--json"))
    assert frozen["pack_digest"] == LEGACY_PACK
    assert frozen["test_set_digest"] == LEGACY_TEST_SET
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")
    _spec(monkeypatch, exit=1)
    red = _recorded(root, "red", [sys.executable, "-c", "raise SystemExit(1)"])
    _spec(monkeypatch, exit=0)
    green = _recorded(root, "green", [sys.executable, "-c", "pass"], "--redact-arg", "2")
    assert red["pack_digest"] == LEGACY_TEST_SET
    assert green["pack_digest"] == LEGACY_TEST_SET
    assert green["evidence_format"] == 2


# R2


def _frozen_digests(
    tmp_path: Path, name: str, policy: object | None, *, tests: bool,
    spec: str = "# u\n", effects: str = '{"effects_format": 1, "effects": []}\n',
) -> tuple[object, object]:
    project, root = _project(tmp_path, name)
    (project / "packs" / "u.spec.md").write_text(spec)
    (project / "packs" / "u.effects.json").write_text(effects)
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    policy_file = None if policy is None else _write_policy(project, policy)
    frozen = _receipt(_must(*_freeze_args(project, root, policy_file, tests=tests), "--json"))
    return frozen["pack_digest"], frozen["test_set_digest"]


def test_the_policy_is_part_of_both_test_set_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_policy = {"evidence_format": 1, "reports": "required", "command": ["run", "{junit}"]}
    for tests in (True, False):
        label = "explicit" if tests else "fallback"
        _, base = _frozen_digests(tmp_path, f"{label}-base", REPORTS, tests=tests)
        _, no_policy = _frozen_digests(tmp_path, f"{label}-none", None, tests=tests)
        _, policy = _frozen_digests(tmp_path, f"{label}-policy", other_policy, tests=tests)
        _, effects = _frozen_digests(
            tmp_path, f"{label}-effects", REPORTS, tests=tests,
            effects='{"effects_format": 1,  "effects": []}\n',
        )
        _, spec = _frozen_digests(tmp_path, f"{label}-spec", REPORTS, tests=tests, spec="# v\n")
        _, spaced = _frozen_digests(
            tmp_path, f"{label}-spaced", '{ "evidence_format": 1, "reports": "required" }', tests=tests,
        )
        assert base != no_policy, label
        assert base != spaced, label
        assert base != policy, label
        assert base == effects, label
        assert (base == spec) is tests, label
    digests: list[object] = []
    for index, policy_text in enumerate((
        REPORTS,
        {"evidence_format": 1, "command": ["run", "{junit}"]},
        {"evidence_format": 1, "command": ["run", "--fast", "{junit}"]},
    )):
        digests.append(_frozen_digests(tmp_path, f"three{index}", policy_text, tests=True)[1])
    assert LEGACY_TEST_SET not in digests
    assert len(set(map(str, digests))) == 3, digests
    project, root = _project(tmp_path, "recorded")
    _started(project, root, REPORTS)
    _spec(monkeypatch, tests=_tests("a:failed"), exit=1)
    red = _recorded(root, "red", _child(project), "--junit-report")
    assert red["pack_digest"] == digests[0]


# R3


@pytest.mark.parametrize(
    "policy",
    [
        "{",
        b'{"evidence_format": 1, "reports": "requir\xff"}',
        '[{"evidence_format": 1, "reports": "required"}]',
        '{"evidence_format": 1, "reports": "required", "reports": "required"}',
        {"evidence_format": 2, "reports": "required"},
        {"evidence_format": True, "reports": "required"},
        '{"evidence_format": 1.0, "reports": "required"}',
        {"evidence_format": 1},
        {"evidence_format": 1, "reports": "required", "extra": 1},
        {"evidence_format": 1, "reports": "optional"},
        {"evidence_format": 1, "command": []},
        {"evidence_format": 1, "command": "pytest"},
        {"evidence_format": 1, "command": ["pytest", 1]},
        {"evidence_format": 1, "command": ["pytest", ""]},
        {"evidence_format": 1, "command": ["pytest", "{junit}", "--x={junit}"]},
        {"evidence_format": 1, "command": ["pytest"], "reports": "required"},
        {"evidence_format": 1, "command": ["pytest", "a\nb"]},
        {"evidence_format": 1, "command": ["pytest", "x" * 4100]},
    ],
    ids=[
        "not-json", "invalid-utf8", "non-object", "duplicate-key", "format-2", "format-bool",
        "format-float", "no-opt-in", "unknown-key", "reports-value", "empty-command",
        "command-string", "command-int", "empty-argument", "token-twice",
        "reports-without-token", "nonprintable", "overlong",
    ],
)
def test_freeze_refuses_a_malformed_policy(tmp_path: Path, policy: object) -> None:
    project, root = _project(tmp_path)
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    policy_file = _write_policy(project, policy)
    before = _snapshot_before(root)
    _refused(root, _run(*_freeze_args(project, root, policy_file)), INVALID, before=before)


@pytest.mark.parametrize("misuse", ["twice", "alone"])
def test_freeze_refuses_a_misused_option(tmp_path: Path, misuse: str) -> None:
    project, root = _project(tmp_path)
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    policy_file = _write_policy(project, REPORTS)
    if misuse == "twice":
        argv = (*_freeze_args(project, root, policy_file), "--pack-evidence-file", policy_file)
    else:
        argv = (
            "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
            "--project", str(project), "--pack-evidence-file", policy_file,
        )
    before = _snapshot_before(root)
    _refused(root, _run(*argv), INVALID, before=before)


@pytest.mark.parametrize("case", ["symlink", "shared-path"])
def test_a_policy_file_is_read_like_any_pack_file(tmp_path: Path, case: str) -> None:
    project, root = _project(tmp_path)
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    if case == "symlink":
        _write_policy(project, REPORTS, "real.evidence.json")
        os.symlink(project / "packs" / "real.evidence.json", project / "packs" / "u.evidence.json")
        policy_file = "packs/u.evidence.json"
    else:
        policy_file = "tests/t.py"
    before = _snapshot_before(root)
    status, out, err = _run(*_freeze_args(project, root, policy_file))
    assert status == 2, (out, err)
    assert out == "", out
    assert policy_file in err, err
    if case == "symlink":
        assert "cannot open" in err, err
    else:
        assert "duplicate path" in err, err
    assert _store_files(root) == before


# R4


def test_the_bound_command_is_accepted_for_every_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    plain = [*_writer(project), "plain run"]
    _started(project, root, {"evidence_format": 1, "command": plain})
    lines: list[object] = []
    for kind, exit_code in (("red", 1), ("green", 0), ("refactor", 0)):
        _spec(monkeypatch, exit=exit_code)
        lines.append(_recorded(root, kind, plain)["command_line"])
    assert lines == [shlex.join(plain)] * 3
    assert _row(root, "check.evidence-chains") == "PASS"


# R5


@pytest.mark.parametrize("kind", ["red", "green", "refactor"])
@pytest.mark.parametrize("change", ["extra", "different", "dropped", "reordered"])
def test_another_command_is_refused_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, change: str,
) -> None:
    project, root = _project(tmp_path)
    plain = [*_writer(project), "plain", "run"]
    _started(project, root, {"evidence_format": 1, "command": plain})
    if kind != "red":
        _spec(monkeypatch, exit=1)
        _recorded(root, "red", plain)
        _spec(monkeypatch, exit=0)
        _recorded(root, "green", plain)
    argv = {
        "extra": [*plain, "-x"],
        "different": [*plain[:3], "walk"],
        "dropped": plain[:3],
        "reordered": [*plain[:2], "run", "plain"],
    }[change]
    sentinel = tmp_path / "started"
    _spec(monkeypatch, exit=0 if kind != "red" else 1, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, kind, argv), NOT_BOUND, before=before)
    assert not sentinel.exists()


# R6


def test_redaction_is_refused_for_a_bound_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    plain = [*_writer(project), "secret"]
    _started(project, root, {"evidence_format": 1, "command": plain})
    sentinel = tmp_path / "started"
    _spec(monkeypatch, exit=1, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, "red", plain, "--redact-arg", "2"), REDACTION, before=before)
    assert not sentinel.exists()


def test_reports_only_allows_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _spec(monkeypatch, tests=_tests("a:failed"), exit=1)
    red = _recorded(root, "red", _child(project, "secret"), "--junit-report", "--redact-arg", "3")
    assert "<redacted>" in str(red["command_line"])
    assert "secret" not in str(red["command_line"])


# R7


@pytest.mark.parametrize("token", ["inside", "whole"])
def test_a_bound_command_with_the_token_needs_the_report_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str,
) -> None:
    project, root = _project(tmp_path)
    bound = _child(project) if token == "inside" else [*_writer(project), "--report", "{junit}"]
    _started(project, root, {"evidence_format": 1, "command": bound})
    sentinel = tmp_path / "started"
    _spec(monkeypatch, tests=_tests("a:failed"), exit=1, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, "red", bound), REQUIRED, before=before)
    assert not sentinel.exists()
    red = _recorded(root, "red", bound, "--junit-report")
    assert red["evidence_format"] == 3
    assert red["command_line"] == shlex.join(bound)


def test_a_bound_token_applies_to_a_refactor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    bound = _child(project)
    _started(project, root, {"evidence_format": 1, "command": bound})
    _red_green(project, root, monkeypatch, _tests("a:failed"), _tests("a:passed"))
    sentinel = tmp_path / "started"
    _spec(monkeypatch, tests=_tests("a:passed"), exit=0, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, "refactor", bound), REQUIRED, before=before)
    assert not sentinel.exists()
    assert _recorded(root, "refactor", bound, "--junit-report")["evidence_format"] == 3


def test_a_bound_command_without_the_token_refuses_the_report_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    plain = [*_writer(project), "plain"]
    _started(project, root, {"evidence_format": 1, "command": plain})
    sentinel = tmp_path / "started"
    _spec(monkeypatch, exit=1, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, "red", plain, "--junit-report"), TOKEN_INVALID, before=before)
    assert not sentinel.exists()


def test_the_command_is_checked_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    bound = _child(project)
    _started(project, root, {"evidence_format": 1, "command": bound, "reports": "required"})
    sentinel = tmp_path / "started"
    _spec(monkeypatch, exit=1, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, "red", [*bound, "-x"]), NOT_BOUND, before=before)
    assert not sentinel.exists()


# R8


@pytest.mark.parametrize("kind", ["red", "green"])
def test_required_reports_refuse_a_run_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    if kind == "green":
        _spec(monkeypatch, tests=_tests("a:failed"), exit=1)
        _recorded(root, "red", _child(project), "--junit-report")
    sentinel = tmp_path / "started"
    _spec(monkeypatch, exit=1 if kind == "red" else 0, sentinel=str(sentinel))
    before = _snapshot_before(root)
    _refused(root, _record(root, kind, _writer(project)), REQUIRED, before=before)
    assert not sentinel.exists()


def test_a_refactor_needs_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _red_green(project, root, monkeypatch, _tests("a:failed"), _tests("a:passed"))
    _spec(monkeypatch, exit=0)
    refactor = _recorded(root, "refactor", _writer(project))
    assert refactor["evidence_format"] == 2


# R9


@pytest.mark.parametrize(
    "red", [("a:passed",), ("a:skipped", "b:passed")], ids=["all-passed", "skipped-only"],
)
def test_a_red_report_must_list_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, red: tuple[str, ...],
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _spec(monkeypatch, tests=_tests(*red), exit=1)
    before = _snapshot_before(root)
    _refused(
        root, _record(root, "red", _child(project), "--junit-report"), NO_FAILURE,
        sweep_allowed=True, before=before,
    )
    _spec(monkeypatch, tests=_tests("a:error", "b:passed"), exit=1)
    assert _recorded(root, "red", _child(project), "--junit-report")["evidence_format"] == 3


# R10


@pytest.mark.parametrize(
    ("red", "green", "accepted"),
    [
        (("a:failed", "b:passed"), ("a:passed",), False),
        (("a:failed",), ("a:skipped",), False),
        (("a:failed", "b:error"), ("a:passed", "b:failed"), False),
        (("a:failed",), ("b:passed",), False),
        (("a:failed",), ("a:passed", "b:passed"), True),
        (("a:error", "b:passed"), ("a:passed", "b:skipped"), True),
        (("a:failed", "b:failed"), ("b:passed", "a:passed"), True),
    ],
    ids=[
        "missing-test", "failure-skipped", "failure-still-failing", "disjoint", "superset",
        "error-passed-other-skipped", "same-tests-reordered",
    ],
)
def test_a_green_report_must_cover_the_red_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    red: tuple[str, ...], green: tuple[str, ...], accepted: bool,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _spec(monkeypatch, tests=_tests(*red), exit=1)
    _recorded(root, "red", _child(project), "--junit-report")
    _spec(monkeypatch, tests=_tests(*green), exit=0)
    if accepted:
        _recorded(root, "green", _child(project), "--junit-report")
        assert [record["kind"] for record in _records(root)] == ["red", "green"]
    else:
        before = _snapshot_before(root)
        _refused(
            root, _record(root, "green", _child(project), "--junit-report"), INCOMPLETE,
            sweep_allowed=True, before=before,
        )


# R11


def test_the_latest_red_is_the_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    for red in (_tests("a:failed", "b:failed"), _tests("a:failed")):
        _spec(monkeypatch, tests=red, exit=1)
        _recorded(root, "red", _child(project), "--junit-report")
    _spec(monkeypatch, tests=_tests("a:passed"), exit=0)
    _recorded(root, "green", _child(project), "--junit-report")

    other, other_root = _project(tmp_path, "other")
    _started(other, other_root, REPORTS)
    for red in (_tests("a:failed"), _tests("a:failed", "b:failed")):
        _spec(monkeypatch, tests=red, exit=1)
        _recorded(other_root, "red", _child(other), "--junit-report")
    _spec(monkeypatch, tests=_tests("a:passed"), exit=0)
    before = _snapshot_before(other_root)
    _refused(
        other_root, _record(other_root, "green", _child(other), "--junit-report"), INCOMPLETE,
        sweep_allowed=True, before=before,
    )


# R12


def test_a_green_with_no_red_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _spec(monkeypatch, tests=_tests("a:passed"), exit=0)
    before = _snapshot_before(root)
    _refused(
        root, _record(root, "green", _child(project), "--junit-report"), NO_RED,
        sweep_allowed=True, before=before,
    )


# R13


def _published(project: Path, root: str, monkeypatch: pytest.MonkeyPatch, policy: object) -> None:
    _started(project, root, policy)
    if policy is None:
        _spec(monkeypatch, exit=1)
        _recorded(root, "red", _writer(project))
        _spec(monkeypatch, exit=0)
        _recorded(root, "green", _writer(project))
    else:
        _red_green(project, root, monkeypatch, _tests("a:failed", "b:passed"),
                   _tests("a:passed", "b:passed"))
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")


def _rebind(
    project: Path, root: str, policy_file: str | None, *options: str,
) -> tuple[int, str, str]:
    return _run(
        "rebind-pack", "--root", root, "--name", "u", "--reason", "interface changed",
        *options, *_pack_args(project, policy_file),
    )


def _change_tests(project: Path, text: str = "def test_x():\n    assert 1\n") -> None:
    (project / "tests" / "t.py").write_text(text)


@pytest.mark.parametrize("output", ["text", "json"])
@pytest.mark.parametrize("change", ["changed", "argument", "removed", "added"])
def test_rebind_cannot_change_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, output: str,
) -> None:
    project, root = _project(tmp_path)
    combined = {"evidence_format": 1, "reports": "required", "command": _child(project)}
    published = {"added": None, "argument": combined}.get(change, REPORTS)
    _published(project, root, monkeypatch, published)
    _change_tests(project)
    if change == "removed":
        policy_file = None
    elif change == "argument":
        policy_file = _write_policy(
            project, {**combined, "command": _child(project, "-x")},
        )
    else:
        policy_file = _write_policy(project, combined)
    before = _store_files(root)
    options = ("--json",) if output == "json" else ()
    status, out, err = _rebind(project, root, policy_file, *options)
    assert status == 2, (out, err)
    if output == "json":
        document = _object(out)
        assert document["accepted"] is False, out
        codes = [cast(Json, item)["code"] for item in cast(list[object], document["violations"])]
        assert codes == [REBIND], out
    else:
        assert out == "", out
        assert any(line.startswith(f"{REBIND}:") for line in err.splitlines()), err
    assert _store_files(root) == before


def _frozen_pack(root: str) -> tuple[str, str]:
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("u")
        assert unit is not None
        return unit.acceptance_pack_digest, unit.acceptance_test_set_digest


def test_decide_refuses_a_rebind_that_changes_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    combined = {"evidence_format": 1, "reports": "required", "command": _child(project)}
    _published(project, root, monkeypatch, combined)
    other, other_root = _project(tmp_path, "other")
    _write_policy(other, {**combined, "command": _child(project, "-x")})
    (other / "tests" / "t.py").write_text("def test_x():\n    assert 1\n")
    _must("specify", "--root", other_root, "--name", "u", "--version", "v1")
    _must(*_freeze_args(other, other_root, "packs/u.evidence.json"))
    old_digest, _ = _frozen_pack(root)
    new_digest, new_test_set = _frozen_pack(other_root)
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        old_inventory = decode_pack_inventory(
            store.get_object(ArtifactKind.ACCEPTANCE_PACK, old_digest)
        )
    with Store.open(other_root, read_only=True) as store:
        new_inventory = decode_pack_inventory(
            store.get_object(ArtifactKind.ACCEPTANCE_PACK, new_digest)
        )
    decision = decide(
        snapshot,
        commands.RebindPack(
            name="u", pack_digest=new_digest, reason="interface changed",
            test_set_digest=new_test_set, old_inventory=old_inventory,
            new_inventory=new_inventory, old_pack_digest=old_digest,
        ),
    )
    assert isinstance(decision, Refused), decision
    assert [violation.code for violation in decision.violations] == [REBIND]


def test_rebind_compares_what_the_policy_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _published(project, root, monkeypatch, REPORTS)
    _change_tests(project)
    (project / "packs" / "u.evidence.json").write_text(
        '{ "evidence_format" : 1 , "reports" : "required" }\n'
    )
    assert _rebind(project, root, "packs/u.evidence.json")[0] == 0
    _change_tests(project, "def test_x():\n    assert 2\n")
    data = (project / "packs" / "u.evidence.json").read_bytes()
    (project / "packs" / "moved.evidence.json").write_bytes(data)
    status, out, err = _rebind(project, root, "packs/moved.evidence.json")
    assert status == 2, (out, err)
    assert "rebind.paths-removed" in err, err
    assert REBIND not in err, err


def _forge(
    root: str, *, kind: EvidenceKind = EvidenceKind.GREEN, command_line: str,
    report: dict[str, TestOutcome] | None,
) -> None:
    """Write a record through the library, standing for a writer that skipped the policy."""
    data = None if report is None else encode_test_report(report, max_bytes=BOUND)
    with Store.open(root) as store:
        snapshot = store.read_snapshot()
        unit = UnitRef(name="u", version="v1")
        head = snapshot.evidence_head_ref(unit)
        assert head is not None
        resolved = snapshot.unit_ref(unit)
        assert resolved is not None
        event = EvidenceEvent(
            unit=unit,
            sequence=head.head_sequence + 1,
            parent=head.head,
            kind=kind,
            command_line=command_line,
            outcome=(
                EvidenceOutcome.TESTS_FAILED if kind is EvidenceKind.RED
                else EvidenceOutcome.TESTS_PASSED
            ),
            recorded_at="2026-11-23T00:00:00+00:00",
            pack_digest=resolved.acceptance_test_set_digest,
            evidence_format=2 if data is None else 3,
            test_report="" if data is None else digest_of(ArtifactKind.TEST_REPORT, data),
        )
        record_evidence(store, event, actor="forger", test_report_data=data)


def test_after_a_rebind_green_is_compared_with_the_red_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _published(project, root, monkeypatch, REPORTS)
    _forge(root, command_line=shlex.join(_child(project)), report={"t::a": TestOutcome.PASSED})
    assert _row(root, "check.evidence-chains") == "FAIL"
    _change_tests(project)
    assert _rebind(project, root, "packs/u.evidence.json")[0] == 0
    _spec(monkeypatch, tests=_tests("b:passed"), exit=0)
    before = _snapshot_before(root)
    _refused(
        root, _record(root, "green", _child(project), "--junit-report"), INCOMPLETE,
        sweep_allowed=True, before=before,
    )
    _spec(monkeypatch, tests=_tests("a:passed", "b:passed"), exit=0)
    _recorded(root, "green", _child(project), "--junit-report")
    assert _row(root, "check.evidence-chains") == "PASS"


def test_a_red_for_the_current_test_set_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _red_green(project, root, monkeypatch, _tests("a:failed"), _tests("a:passed"))
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")
    original = (project / "tests" / "t.py").read_text()
    _change_tests(project)
    assert _rebind(project, root, "packs/u.evidence.json")[0] == 0
    _spec(monkeypatch, tests=_tests("a:failed", "c:failed"), exit=1)
    _recorded(root, "red", _child(project), "--junit-report")
    _change_tests(project, original)
    assert _rebind(project, root, "packs/u.evidence.json")[0] == 0
    _spec(monkeypatch, tests=_tests("a:passed"), exit=0)
    _recorded(root, "green", _child(project), "--junit-report")
    assert _row(root, "check.evidence-chains") == "PASS"


def test_a_reportless_fallback_red_refuses_the_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _published(project, root, monkeypatch, REPORTS)
    _forge(root, kind=EvidenceKind.RED, command_line=shlex.join(_writer(project)), report=None)
    _change_tests(project)
    assert _rebind(project, root, "packs/u.evidence.json")[0] == 0
    _spec(monkeypatch, tests=_tests("a:passed", "b:passed"), exit=0)
    before = _snapshot_before(root)
    _refused(
        root, _record(root, "green", _child(project), "--junit-report"), NO_RED,
        sweep_allowed=True, before=before,
    )


# R14


@pytest.mark.parametrize(
    "forgery",
    [
        "other-command", "no-report", "short-report", "failure-skipped", "bad-then-good",
        "red-no-failure", "refactor-other-command",
    ],
)
def test_check_walks_every_record_against_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forgery: str,
) -> None:
    project, root = _project(tmp_path)
    bound = _child(project)
    line = shlex.join(bound)
    _started(project, root, {"evidence_format": 1, "command": bound, "reports": "required"})
    _red_green(project, root, monkeypatch, _tests("a:failed", "b:passed"),
               _tests("a:passed", "b:passed"))
    assert _row(root, "check.evidence-chains") == "PASS"
    full = {"t::a": TestOutcome.PASSED, "t::b": TestOutcome.PASSED}
    if forgery == "other-command":
        _forge(root, command_line=shlex.join([*bound, "-x"]), report=full)
    elif forgery == "no-report":
        _forge(root, command_line=line, report=None)
    elif forgery == "short-report":
        _forge(root, command_line=line, report={"t::a": TestOutcome.PASSED})
    elif forgery == "failure-skipped":
        _forge(root, command_line=line,
               report={"t::a": TestOutcome.SKIPPED, "t::b": TestOutcome.PASSED})
    elif forgery == "bad-then-good":
        _forge(root, command_line=line, report={"t::a": TestOutcome.PASSED})
        _spec(monkeypatch, tests=_tests("a:passed", "b:passed"), exit=0)
        _recorded(root, "green", bound, "--junit-report")
    elif forgery == "red-no-failure":
        _forge(root, kind=EvidenceKind.RED, command_line=line,
               report={"t::a": TestOutcome.PASSED})
    else:
        _forge(root, kind=EvidenceKind.REFACTOR, command_line=shlex.join([*bound, "-x"]),
               report=full)
    assert _row(root, "check.evidence-chains") == "FAIL"


def test_check_needs_a_report_for_a_token_bound_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    bound = _child(project)
    _started(project, root, {"evidence_format": 1, "command": bound})
    _red_green(project, root, monkeypatch, _tests("a:failed"), _tests("a:passed"))
    assert _row(root, "check.evidence-chains") == "PASS"
    _forge(root, kind=EvidenceKind.REFACTOR, command_line=shlex.join(bound), report=None)
    assert _row(root, "check.evidence-chains") == "FAIL"


def test_check_needs_reports_when_they_are_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _red_green(project, root, monkeypatch, _tests("a:failed"), _tests("a:passed"))
    assert _row(root, "check.evidence-chains") == "PASS"
    _forge(root, kind=EvidenceKind.RED, command_line=shlex.join(_writer(project)), report=None)
    assert _row(root, "check.evidence-chains") == "FAIL"


def test_check_compares_each_green_with_its_own_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root, REPORTS)
    _red_green(project, root, monkeypatch, _tests("a:failed", "b:failed"),
               _tests("a:passed", "b:passed"))
    _red_green(project, root, monkeypatch, _tests("c:failed"), _tests("c:passed"))
    assert _row(root, "check.evidence-chains") == "PASS"


# R15


def test_docs_state_the_policy() -> None:
    evidence = " ".join((ROOT / "docs" / "claim-boundary.md").read_text().split())
    assert (
        "A pack may bind an evidence-policy file (D-54) that fixes the command RED and GREEN "
        "must run, requires JUnit reports, or both."
    ) in evidence
    assert (
        "With reports required, `record-evidence` refuses a RED whose report lists no failure "
        "and a GREEN whose report omits a test the RED's report lists or does not list one of "
        "RED's failures as passed."
    ) in evidence
    assert (
        "The policy is part of the test set, so a different policy is a different test set, "
        "and `rebind-pack` cannot change a published pack's policy."
    ) in evidence
    for relative in ("README.md", "docs/concepts.md"):
        text = " ".join((ROOT / relative).read_text().split())
        assert (
            "A pack that binds a command in its evidence-policy file fixes that command."
        ) in text, relative
    errors = (ROOT / "docs" / "errors.md").read_text()
    for code in (INVALID, NOT_BOUND, REDACTION, REQUIRED, NO_FAILURE, INCOMPLETE, NO_RED, REBIND):
        assert f"`{code}`" in errors, code


# R16


def test_the_option_is_on_freeze_and_rebind_only() -> None:
    for command in ("freeze", "rebind-pack"):
        assert "--pack-evidence-file" in _run(command, "--help")[1], command
    assert "--pack-evidence-file" not in _run("bind-pack", "--help")[1]


# R17


def test_the_frozen_policy_is_used_not_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    plain = [*_writer(project), "plain"]
    _started(project, root, {"evidence_format": 1, "command": plain})
    other = [*_writer(project), "other"]
    _write_policy(project, {"evidence_format": 1, "command": other})
    _spec(monkeypatch, exit=1)
    _recorded(root, "red", plain)
    before = _snapshot_before(root)
    _refused(root, _record(root, "green", other), NOT_BOUND, before=before)
    (project / "packs" / "u.evidence.json").unlink()
    _spec(monkeypatch, exit=0)
    _recorded(root, "green", plain)
    assert _row(root, "check.evidence-chains") == "PASS"
    _forge(root, command_line=shlex.join(other), report=None)
    assert _row(root, "check.evidence-chains") == "FAIL"
