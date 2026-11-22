"""Frozen acceptance pack for test-sets@1 (D-52, vouch_mk2-inv.4).

`record-evidence --junit-report` gives the command a report path inside a private directory Vouch
creates, through the `{junit}` token, and records each test's ID and outcome; `show-evidence`
compares the tests the counted GREEN's report lists with those the counted RED's report lists.
The child here is a script this file writes, so the expected IDs and outcomes are known
independently. Every test here fails before the change, except tests marked ``# guard:``. The
implementer may not edit this file.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main
from vouch_mk2.mcp import serve
from vouch_mk2.store import (
    decode_event_record,
    decode_evidence_event,
    encode_event_record,
    encode_evidence_event,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "fixtures" / "test-sets" / "legacy-store.json"
COMMAND_NOTE = "GREEN ran a different command from RED"
MISSING_NOTE = "GREEN's report lacks tests RED's report lists"
FAILURE_NOTE = "a test that failed in RED's report did not pass in GREEN's report"
BOUND = 64 * 1024 * 1024
REFUSAL = "evidence.test-report-invalid"
HEX64 = re.compile(r"[0-9a-f]{64}")

Json = dict[str, object]
Tests = list[tuple[str, str, str]]

WRITER = r'''import json, os, signal, sys
spec = json.loads(os.environ["VOUCH_TEST_SPEC"])
if spec.get("sentinel"):
    open(spec["sentinel"], "w").close()
print("child output on stdout")
argument = sys.argv[1]
path = argument.split("=", 1)[1] if argument.startswith("--report=") else argument
side = spec.get("side")
if side:
    parent = os.path.dirname(path)
    info = {"path": path, "parent_is_dir": os.path.isdir(parent) if parent else False}
    if info["parent_is_dir"]:
        info["parent_mode"] = os.stat(parent).st_mode & 0o777
        info["exists_at_start"] = os.path.lexists(path)
    with open(side, "w") as handle:
        json.dump(info, handle)

def report(tests):
    parts = ['<?xml version="1.0"?>', '<testsuites><testsuite name="s">']
    for classname, name, outcome in tests:
        child = {"failed": '<failure message="f"/>', "error": '<error message="e"/>',
                 "skipped": "<skipped/>", "passed": ""}[outcome]
        parts.append(f'<testcase classname="{classname}" name="{name}">{child}</testcase>')
    parts.append("</testsuite></testsuites>")
    return "".join(parts)

mode = spec.get("mode", "write")
if mode == "write":
    with open(path, "w") as handle:
        handle.write(report(spec["tests"]))
elif mode == "raw":
    with open(path, "w") as handle:
        handle.write(spec["raw"])
elif mode == "utf16":
    with open(path, "wb") as handle:
        handle.write(spec["raw"].encode("utf-16"))
elif mode == "many":
    with open(path, "w") as handle:
        handle.write("<testsuite>")
        for index in range(spec["count"]):
            handle.write(f'<testcase classname="t" name="n{index}"/>')
        handle.write("</testsuite>")
elif mode == "bigattr":
    with open(path, "w") as handle:
        handle.write('<testsuite><testcase classname="t" name="a"><failure message="')
        handle.write("m" * spec["size"])
        handle.write('"/></testcase></testsuite>')
elif mode == "pad":
    with open(path, "w") as handle:
        prefix = '<testsuite><testcase classname="t" name="a"/></testsuite>'
        handle.write(prefix)
        handle.write(" " * (spec["size"] - len(prefix)))
elif mode == "backslashes":
    with open(path, "w") as handle:
        handle.write("<testsuite>")
        for index in range(spec["count"]):
            name = f"{index:05d}" + "\\" * spec["width"]
            handle.write(f'<testcase classname="t" name="{name}"/>')
        handle.write("</testsuite>")
elif mode == "symlink":
    os.symlink(spec["target"], path)
elif mode == "fifo":
    os.mkfifo(path)
elif mode == "kill":
    with open(path, "w") as handle:
        handle.write(report([["t", "a", "passed"]]))
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
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


def _project(tmp_path: Path, *, tier: str = "lightweight") -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    _must("init", "--project", str(project))
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    (project / "packs" / "u.spec.md").write_text("# u\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}\n')
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    (project / "writer.py").write_text(WRITER)
    root = str(project / ".vouch")
    if tier != "lightweight":
        _must("set-tier", "--root", root, "--tier", tier)
    return project, root


def _started(project: Path, root: str, name: str = "u") -> None:
    _must("specify", "--root", root, "--name", name, "--version", "v1")
    _must(
        "freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/1",
        "--project", str(project),
        "--pack-file", "packs/u.spec.md", "--pack-test-file", "tests/t.py",
        "--pack-effects-file", "packs/u.effects.json",
    )
    _must("mark-ready", "--root", root, "--name", name)
    _must("start", "--root", root, "--name", name)


def _child(project: Path, token: str = "--report={junit}", *extra: str) -> list[str]:
    return [sys.executable, str(project / "writer.py"), token, *extra]


def _spec(monkeypatch: pytest.MonkeyPatch, **spec: object) -> None:
    monkeypatch.setenv("VOUCH_TEST_SPEC", json.dumps(spec))


def _tests(*items: str) -> Tests:
    """Items like 'a:failed' under classname t."""
    result: Tests = []
    for item in items:
        name, outcome = item.split(":")
        result.append(("t", name, outcome))
    return result


def _receipt_evidence(out: str) -> Json:
    event = cast(list[Json], _object(out)["events"])[0]
    return cast(Json, event["evidence"])


def _record(
    project: Path, root: str, kind: str, monkeypatch: pytest.MonkeyPatch, tests: Tests | None,
    *, extra: tuple[str, ...] = (),
) -> Json:
    """Record one run; with tests, through --junit-report. Return the receipt's evidence."""
    exit_code = 1 if kind == "red" else 0
    if tests is None:
        _spec(monkeypatch, mode="none", exit=exit_code)
        option: tuple[str, ...] = ()
    else:
        _spec(monkeypatch, tests=tests, exit=exit_code)
        option = ("--junit-report",)
    out = _must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind, "--json",
        *option, "--", *_child(project, "--report={junit}", *extra),
    )
    return _receipt_evidence(out)


def _show(root: str) -> Json:
    return _object(_must("show-evidence", "--root", root, "--json"))


def _unit(document: Json, label: str = "u@v1") -> Json:
    (unit,) = [
        cast(Json, item) for item in cast(list[object], document["units"])
        if cast(Json, item)["unit"] == label
    ]
    return unit


def _comparison(root: str) -> object:
    return _unit(_show(root))["test_comparison"]


def _notes(root: str) -> list[str]:
    return cast(list[str], _unit(_show(root))["notes"])


def _text(root: str) -> str:
    return _must("show-evidence", "--root", root)


def _store_files(root: str) -> dict[str, bytes]:
    base = Path(root)
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.name != ".lock"
    }


def _row(root: str, check_id: str) -> str:
    _, out, _ = _run("check", "--root", root)
    rows = [line.split() for line in out.splitlines() if check_id in line.split()]
    assert len(rows) == 1, out
    return rows[0][0]


@pytest.fixture
def scratch_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return scratch


# R1


def test_a_junit_report_is_recorded_with_its_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    _spec(
        monkeypatch,
        tests=[["t", "a", "failed"], ["t", "b", "passed"], ["t", "x&amp;y", "passed"]],
        exit=1,
    )
    code, out, err = _run(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json",
        "--junit-report", "--", *_child(project),
    )
    assert code == 0, (out, err)
    evidence = _receipt_evidence(out)
    assert evidence["evidence_format"] == 3
    digest = evidence["test_report"]
    assert isinstance(digest, str) and HEX64.fullmatch(digest)
    assert (Path(root) / "objects" / "sha256" / digest).is_file()
    command = evidence["command_line"]
    assert isinstance(command, str) and "--report={junit}" in command
    (record,) = cast(list[Json], _unit(_show(root))["records"])
    assert record["test_report"] == digest
    stored = _object((Path(root) / "objects" / "sha256" / digest).read_bytes().decode())
    assert "t::x&y" in json.dumps(stored), stored

    _spec(monkeypatch, tests=[["t", "a", "passed"]], exit=0)
    redacted = _receipt_evidence(_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green", "--json",
        "--junit-report", "--redact-arg", "2", "--", *_child(project),
    ))
    assert "<redacted>" in cast(str, redacted["command_line"])
    assert "{junit}" not in cast(str, redacted["command_line"])
    assert redacted["evidence_format"] == 3
    assert HEX64.fullmatch(cast(str, redacted["test_report"]))


# R2


def test_the_child_writes_to_a_private_path_vouch_removes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_tmp: Path,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    paths: list[Path] = []
    for index, kind in enumerate(("red", "green")):
        side = tmp_path / f"side-{index}.json"
        _spec(
            monkeypatch, tests=[["t", "a", "passed"]], exit=1 if kind == "red" else 0,
            side=str(side),
        )
        _must(
            "record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind,
            "--junit-report", "--", *_child(project),
        )
        info = _object(side.read_text())
        path = Path(cast(str, info["path"]))
        assert path.is_relative_to(scratch_tmp), (path, scratch_tmp)
        assert not path.is_relative_to(project)
        assert info["parent_is_dir"] is True
        assert info["parent_mode"] == 0o700
        assert info["exists_at_start"] is False
        paths.append(path)
    assert paths[0].parent != paths[1].parent
    assert list(scratch_tmp.iterdir()) == []


# R3


def test_without_the_option_the_record_is_format_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    side = tmp_path / "side.json"
    _spec(monkeypatch, mode="none", exit=1, side=str(side))
    evidence = _receipt_evidence(_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json",
        "--", *_child(project, "{junit}"),
    ))
    assert _object(side.read_text())["path"] == "{junit}"
    assert evidence["evidence_format"] == 2
    assert "test_report" in evidence and evidence["test_report"] == ""


# R4


def test_a_green_that_ran_more_tests_is_a_superset_with_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    _record(project, root, "red", monkeypatch, _tests("a:failed", "b:passed"))
    _record(project, root, "green", monkeypatch, _tests("a:passed", "b:passed", "c:passed"))
    assert _comparison(root) == {
        "relation": "superset", "red_only": [], "green_only": ["t::c"],
        "red_failures_not_passed": [],
    }
    assert _notes(root) == []
    assert "tests: superset" in _text(root)


# R5


def test_a_green_missing_tests_is_noted_and_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path, tier="standard")
    _started(project, root)
    _record(project, root, "red", monkeypatch, _tests("a:failed", "b:passed"))
    _record(project, root, "green", monkeypatch, _tests("a:passed"))
    assert _comparison(root) == {
        "relation": "subset", "red_only": ["t::b"], "green_only": [],
        "red_failures_not_passed": [],
    }
    assert _notes(root) == [MISSING_NOTE]
    assert MISSING_NOTE in _text(root)
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")


# R6


def test_red_failures_and_errors_not_passing_are_noted_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    _record(project, root, "red", monkeypatch, _tests("e:error", "a:failed"))
    _record(project, root, "green", monkeypatch, _tests("b:passed", "a:skipped"))
    assert _comparison(root) == {
        "relation": "overlap", "red_only": ["t::e"], "green_only": ["t::b"],
        "red_failures_not_passed": ["t::a", "t::e"],
    }
    assert _notes(root) == [MISSING_NOTE, FAILURE_NOTE]
    text = _text(root)
    assert FAILURE_NOTE in text and "tests: overlap" in text


# R7


@pytest.mark.parametrize(
    ("red", "green", "relation", "red_only", "green_only"),
    [
        (("a", "b"), ("b", "a"), "equal", [], []),
        (("a",), ("a", "b"), "superset", [], ["t::b"]),
        (("a", "b"), ("a",), "subset", ["t::b"], []),
        (("a", "b", "d"), ("b", "c", "e"), "overlap", ["t::a", "t::d"], ["t::c", "t::e"]),
        (("a",), ("c",), "disjoint", ["t::a"], ["t::c"]),
    ],
)
def test_the_relation_is_named_for_each_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    red: tuple[str, ...], green: tuple[str, ...], relation: str,
    red_only: list[str], green_only: list[str],
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    _record(project, root, "red", monkeypatch, [("t", name, "passed") for name in red])
    _record(project, root, "green", monkeypatch, [("t", name, "passed") for name in green])
    assert _comparison(root) == {
        "relation": relation, "red_only": red_only, "green_only": green_only,
        "red_failures_not_passed": [],
    }
    expected_notes: list[str] = (
        [MISSING_NOTE] if relation in ("subset", "overlap", "disjoint") else []
    )
    assert _notes(root) == expected_notes
    assert f"tests: {relation}" in _text(root)


# R8


def test_the_comparison_follows_the_marked_pair_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path / "a")
    _started(project, root)
    _record(project, root, "red", monkeypatch, None)
    _record(project, root, "green", monkeypatch, _tests("a:passed"))
    assert _comparison(root) is None
    assert _notes(root) == []

    project, root = _project(tmp_path / "b")
    _started(project, root)
    _record(project, root, "red", monkeypatch, _tests("a:failed"))
    _record(project, root, "green", monkeypatch, _tests("a:passed"))
    _record(project, root, "red", monkeypatch, _tests("a:failed"))
    assert _comparison(root) is None
    assert _notes(root) == []

    project, root = _project(tmp_path / "c")
    _started(project, root)
    _record(project, root, "red", monkeypatch, None)
    _record(project, root, "green", monkeypatch, _tests("a:passed"), extra=("other",))
    assert _comparison(root) is None
    assert _notes(root) == [COMMAND_NOTE]


# R9

_VALID = '<testsuite><testcase classname="t" name="a"/></testsuite>'
_PASSING: Json = {"mode": "write", "tests": [["t", "a", "passed"]]}
_BAD: dict[str, tuple[str, Json]] = {
    "no-token": ("none", _PASSING),
    "token-twice": ("twice", _PASSING),
    "token-twice-in-one-argument": ("twice-inline", _PASSING),
    "no-report": ("ok", {"mode": "none"}),
    "malformed": ("ok", {"mode": "raw", "raw": "<testsuite><testcase"}),
    "dtd": ("ok", {"mode": "raw", "raw": (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "v">]>'
        '<testsuite><testcase classname="t" name="&e;"/></testsuite>'
    )}),
    "dtd-utf16": ("ok", {"mode": "utf16", "raw": (
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY e "v">]>'
        '<testsuite><testcase classname="t" name="&e;"/></testsuite>'
    )}),
    "duplicate-ids": ("ok", {"mode": "raw", "raw": (
        '<testsuite><testcase classname="t" name="a"/>'
        '<testcase classname="t" name="a"/></testsuite>'
    )}),
    "no-testcase": ("ok", {"mode": "raw", "raw": "<testsuite></testsuite>"}),
    "symlink": ("ok", {"mode": "symlink"}),
    "id-too-long": ("ok", {"mode": "write", "tests": [["t", "n" * 4094, "passed"]]}),
    "too-many-testcases": ("ok", {"mode": "many", "count": 100_001}),
    "file-over-the-bound": ("ok", {"mode": "pad", "size": BOUND + 1}),
    "encoded-object-over-the-bound": ("ok", {"mode": "backslashes", "count": 16_384, "width": 4000}),
}


@pytest.mark.parametrize("case", sorted(_BAD))
def test_a_bad_report_or_token_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_tmp: Path, case: str,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    before = _store_files(root)
    token, spec = _BAD[case]
    sentinel = tmp_path / "child-started"
    spec = {**spec, "sentinel": str(sentinel)}
    if case == "symlink":
        target = tmp_path / "elsewhere.xml"
        target.write_text(_VALID)
        spec = {**spec, "target": str(target)}
    _spec(monkeypatch, **spec)
    argv = _child(project)
    if token == "none":
        argv = _child(project, "--report=" + str(tmp_path / "report.xml"))
    elif token == "twice":
        argv = _child(project, "--report={junit}", "{junit}")
    elif token == "twice-inline":
        argv = _child(project, "--report={junit}{junit}")
    code, out, err = _run(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green",
        "--junit-report", "--", *argv,
    )
    assert code == 2, (case, out, err)
    assert REFUSAL in err, (case, err)
    if token in ("none", "twice", "twice-inline"):
        assert not sentinel.exists(), case
    assert _store_files(root) == before
    assert list(scratch_tmp.iterdir()) == []


# R10


def test_a_fifo_in_place_of_the_report_is_refused_without_blocking(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    env = {
        **os.environ,
        "VOUCH_TEST_SPEC": json.dumps({"mode": "fifo"}),
        "PYTHONPATH": str(ROOT / "src"),
    }
    completed = subprocess.run(
        [
            sys.executable, "-c", "import sys; from vouch_mk2.cli import main; sys.exit(main())",
            "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green",
            "--junit-report", "--", *_child(project),
        ],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert REFUSAL in completed.stderr


# R11


def test_a_child_killed_by_a_signal_is_refused_with_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_tmp: Path,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    before = _store_files(root)
    _spec(monkeypatch, mode="kill")
    code, out, err = _run(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green",
        "--junit-report", "--", *_child(project),
    )
    assert code == 2, (out, err)
    assert "no verdict" in err
    assert _store_files(root) == before
    assert list(scratch_tmp.iterdir()) == []


# R12


def test_large_unrelated_attributes_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    _spec(monkeypatch, mode="bigattr", size=1_000_000, exit=1)
    evidence = _receipt_evidence(_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json",
        "--junit-report", "--", *_child(project),
    ))
    assert evidence["evidence_format"] == 3
    _record(project, root, "green", monkeypatch, _tests("a:skipped"))
    assert _comparison(root) == {
        "relation": "equal", "red_only": [], "green_only": [],
        "red_failures_not_passed": ["t::a"],
    }


# R13


def test_reports_are_canonical_and_the_command_note_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    first = _record(project, root, "red", monkeypatch, _tests("b:failed", "a:passed"))
    second = _record(project, root, "red", monkeypatch, _tests("a:passed", "b:failed"))
    assert first["test_report"] == second["test_report"]
    _record(project, root, "green", monkeypatch, _tests("b:skipped"), extra=("other",))
    assert _notes(root) == [COMMAND_NOTE, MISSING_NOTE, FAILURE_NOTE]


# R14


@pytest.mark.parametrize("which", ["missing-counted-green", "corrupt-uncounted-red"])
def test_a_missing_or_corrupt_report_object_makes_the_chain_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    early = _record(project, root, "red", monkeypatch, _tests("a:failed", "z:failed"))
    _record(project, root, "red", monkeypatch, _tests("a:failed"))
    green = _record(project, root, "green", monkeypatch, _tests("a:passed"))
    objects = Path(root) / "objects" / "sha256"
    if which == "missing-counted-green":
        (objects / cast(str, green["test_report"])).unlink()
    else:
        (objects / cast(str, early["test_report"])).write_bytes(b"not a report")

    assert _row(root, "check.evidence-chains") == "FAIL"
    code, shown, _ = _run("show-evidence", "--root", root, "--json")
    assert code == 1
    chain_error = _unit(_object(shown))["chain_error"]
    assert isinstance(chain_error, str) and chain_error


# R15


@pytest.mark.parametrize("replayed", [False, True])
def test_a_merge_brings_the_report_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replayed: bool,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    branch = tmp_path / "branch-store"
    shutil.copytree(root, branch)
    _record(project, str(branch), "red", monkeypatch, _tests("a:failed", "b:passed"))
    _record(project, str(branch), "green", monkeypatch, _tests("a:passed", "b:passed", "c:passed"))
    expected = _comparison(str(branch))
    assert expected is not None
    if replayed:
        _must("specify", "--root", root, "--name", "other", "--version", "v1")
    _must("merge", "--root", root, "--other", str(branch), "--actor", "t")
    assert _comparison(root) == expected
    assert _row(root, "check.evidence-chains") == "PASS"


# R16


def _legacy_store(tmp_path: Path) -> tuple[str, Json]:
    fixture = _object(LEGACY.read_text())
    root = tmp_path / "legacy"
    for relative, encoded in cast(dict[str, str], fixture["files"]).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    return str(root), cast(Json, fixture["show_evidence"])


def test_a_store_written_before_reads_replays_and_reencodes_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, then = _legacy_store(tmp_path)
    fixture = _object(LEGACY.read_text())
    objects = Path(root) / "objects" / "sha256"
    event_names = cast(list[str], fixture["event_objects"])
    evidence_names = cast(list[str], fixture["evidence_objects"])
    assert event_names and evidence_names
    for name in event_names:
        data = (objects / name).read_bytes()
        assert encode_event_record(decode_event_record(data)) == data, name
    for name in evidence_names:
        data = (objects / name).read_bytes()
        assert encode_evidence_event(decode_evidence_event(data)) == data, name
    format_one = base64.b64decode(cast(str, fixture["format1_evidence"]))
    decoded = decode_evidence_event(format_one)
    assert decoded.evidence_format == 1
    assert encode_evidence_event(decoded) == format_one
    old_keys = set(_object((objects / evidence_names[0]).read_bytes().decode()))

    assert _row(root, "check.event-log") == "PASS"
    assert _row(root, "check.evidence-chains") == "PASS"
    now = _show(root)
    assert now["revision"] == then["revision"]
    for unit_then, unit_now in zip(
        cast(list[Json], then["units"]), cast(list[Json], now["units"]), strict=True,
    ):
        expected = dict(unit_then)
        expected["records"] = [
            {**record, "test_report": ""} for record in cast(list[Json], unit_then["records"])
        ]
        expected["test_comparison"] = None
        assert unit_now == expected

    project = tmp_path / "proj"
    project.mkdir()
    (project / "writer.py").write_text(WRITER)
    _spec(monkeypatch, mode="none", exit=0)
    new = _receipt_evidence(_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green", "--json",
        "--", *_child(project, "{junit}"),
    ))
    assert new["evidence_format"] == 2
    newest = cast(list[Json], _unit(_show(root))["records"])[-1]
    written = _object((objects / cast(str, newest["digest"])).read_bytes().decode())
    assert set(written) == old_keys
    state = _object((Path(root) / "state.json").read_text())
    head = (objects / cast(str, state["event_head"])).read_bytes()
    assert b"test_report" not in head
    assert _row(root, "check.event-log") == "PASS"
    assert _row(root, "check.evidence-chains") == "PASS"


# R19


def test_json_mode_keeps_stdout_for_the_receipt(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    env = {
        **os.environ,
        "VOUCH_TEST_SPEC": json.dumps({"tests": [["t", "a", "failed"]], "exit": 1}),
        "PYTHONPATH": str(ROOT / "src"),
    }
    completed = subprocess.run(
        [
            sys.executable, "-c", "import sys; from vouch_mk2.cli import main; sys.exit(main())",
            "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json",
            "--junit-report", "--", *_child(project),
        ],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _receipt_evidence(completed.stdout)["evidence_format"] == 3
    assert "child output on stdout" in completed.stderr


# R20


def test_failure_outranks_other_outcome_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    raw = (
        '<testsuite>'
        '<testcase classname="t" name="p"><skipped/><failure message="f"/></testcase>'
        '<testcase classname="t" name="q"><skipped/><error message="e"/></testcase>'
        '</testsuite>'
    )
    _spec(monkeypatch, mode="raw", raw=raw, exit=1)
    _must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red",
        "--junit-report", "--", *_child(project),
    )
    _record(project, root, "green", monkeypatch, _tests("r:passed"))
    assert _comparison(root) == {
        "relation": "disjoint", "red_only": ["t::p", "t::q"], "green_only": ["t::r"],
        "red_failures_not_passed": ["t::p", "t::q"],
    }


# R17


def test_the_mcp_evidence_tool_shows_the_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, root = _project(tmp_path)
    _started(project, root)
    _record(project, root, "red", monkeypatch, _tests("a:failed"))
    _record(project, root, "green", monkeypatch, _tests("a:passed", "b:passed"))
    expected = _show(root)
    assert _unit(expected)["test_comparison"] is not None
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "evidence", "arguments": {}},
    })
    (response,) = [_object(line) for line in serve(root, [request])]
    result = cast(Json, response["result"])
    assert result["structuredContent"] == expected


# R18


def _normalised(path: Path) -> str:
    return " ".join(path.read_text().split())


def test_docs_state_what_the_report_proves() -> None:
    claim = _normalised(ROOT / "docs" / "claim-boundary.md")
    for sentence in (
        "`record-evidence --junit-report` stores the test IDs and outcomes that the JUnit report "
        "at a path only Vouch gave the command lists; the report is the command's claim, like the "
        "exit status, and Vouch does not see which tests ran.",
        "`show-evidence` compares the tests the counted GREEN's report lists with those the "
        "counted RED's report lists and notes when GREEN's report lacks some of RED's; the "
        "comparison never blocks publication.",
    ):
        assert sentence in claim, sentence
    assert "--junit-report" in (ROOT / "docs" / "commands.md").read_text()
    assert REFUSAL in (ROOT / "docs" / "errors.md").read_text()
    schemas = (ROOT / "docs" / "schemas.md").read_text()
    heading = "## `show-evidence --json`"
    assert heading in schemas
    section = schemas.split(heading, 1)[1].split("\n## ", 1)[0]
    assert '"test_report"' in section and '"test_comparison"' in section


# Guard


def test_guard_the_fixtures_record_evidence_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # guard:
    project, root = _project(tmp_path)
    _started(project, root)
    _spec(monkeypatch, mode="none", exit=1)
    evidence = _receipt_evidence(_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json",
        "--", *_child(project, "{junit}"),
    ))
    assert evidence["outcome"] == "tests-failed"
    assert evidence["evidence_format"] == 2
    assert LEGACY.is_file()
