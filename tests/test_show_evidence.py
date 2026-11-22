"""Frozen acceptance pack for show-evidence@1 (D-49).

Every evidence record stores the command line it ran, but nothing showed it. ``show-evidence`` is
a read-only view of each unit's evidence chain with the stored command lines, the RED and GREEN
behind a true current-pack evidence result marked, and a note (never a refusal) when those two
have different stored command lines. Every test here fails before the change, except tests marked
``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import re
import shlex
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

from vouch_mk2.cli import main
from vouch_mk2.mcp import serve
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]

# Literal argv and their stored command lines: the oracle is the text an operator typed.
FAIL = ["python3", "-c", "raise SystemExit(1)"]
FAIL_LINE = "python3 -c 'raise SystemExit(1)'"
OK = ["python3", "-c", "raise SystemExit(0)"]
OK_LINE = "python3 -c 'raise SystemExit(0)'"
OK_OTHER = ["python3", "-c", "pass"]
OK_OTHER_LINE = "python3 -c pass"
NOTE = "GREEN ran a different command from RED"

TOP_KEYS = {"revision", "units"}
UNIT_KEYS = {
    "unit",
    "status",
    "current_test_set",
    "current_pack_green",
    "records",
    "notes",
    "chain_error",
    "test_comparison",
}
RECORD_KEYS = {
    "sequence",
    "kind",
    "outcome",
    "test_set",
    "command",
    "recorded_at",
    "digest",
    "counts",
    "test_report",
}

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


def _object(text: str) -> Json:
    document: object = json.loads(text)
    assert isinstance(document, dict)
    return cast(Json, document)


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir()
    _must("init", "--project", str(project))
    return project, str(project / ".vouch")


def _write_pack(project: Path, test_body: str, spec: str = "# u acceptance pack\n") -> None:
    (project / "packs").mkdir(exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    (project / "packs" / "u.spec.md").write_text(spec)
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}')
    (project / "tests" / "t.py").write_text(f"def test_x():\n    {test_body}\n")


_FILES = (
    "--pack-file", "packs/u.spec.md",
    "--pack-test-file", "tests/t.py",
    "--pack-effects-file", "packs/u.effects.json",
)


def _started(
    project: Path, root: str, name: str = "u", *, version: str = "v1", bound: bool = True
) -> None:
    _must("specify", "--root", root, "--name", name, "--version", version)
    files = ("--project", str(project), *_FILES) if bound else ()
    _must("freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/1", *files)
    _must("mark-ready", "--root", root, "--name", name)
    _must("start", "--root", root, "--name", name)


def _evidence(root: str, kind: str, argv: list[str], unit: str = "u@v1", *extra: str) -> Json:
    """Record one run and return the receipt's evidence object plus its digest and revision."""
    out = _must(
        "record-evidence", "--root", root, "--unit", unit, "--kind", kind, "--json",
        *extra, "--", *argv,
    )
    receipt = _object(out)
    event = cast(list[Json], receipt["events"])[0]
    evidence = dict(cast(Json, event["evidence"]))
    evidence["digest"] = event["event_digest"]
    evidence["revision"] = receipt["revision"]
    return evidence


def _toggle_script(project: Path, exit_code: int) -> list[str]:
    """One command line whose exit code the test changes between runs."""
    script = project / "check.py"
    script.write_text(f"raise SystemExit({exit_code})\n")
    return ["python3", str(script)]


def _show(root: str, *extra: str) -> Json:
    return _object(_must("show-evidence", "--root", root, "--json", *extra))


def _units(document: Json) -> list[Json]:
    return cast(list[Json], document["units"])


def _unit(document: Json, label: str) -> Json:
    matches = [unit for unit in _units(document) if unit["unit"] == label]
    assert len(matches) == 1, (label, document)
    return matches[0]


def _records(unit: Json) -> list[Json]:
    return cast(list[Json], unit["records"])


def _counts(unit: Json) -> list[object]:
    return [record["counts"] for record in _records(unit)]


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _mcp(root: str, *calls: tuple[str, Json]) -> list[Json]:
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})]
    for index, (name, arguments) in enumerate(calls, 2):
        lines.append(json.dumps({
            "jsonrpc": "2.0", "id": index, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }))
    return [_object(line) for line in serve(root, lines)]


def _tool_names(responses: list[Json]) -> list[object]:
    listed = cast(list[Json], cast(Json, responses[0]["result"])["tools"])
    return [tool["name"] for tool in listed]


# R1


def test_show_evidence_lists_every_record_as_recorded(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    receipts = [
        _evidence(root, "red", FAIL),
        _evidence(root, "green", OK),
        _evidence(root, "refactor", OK_OTHER),
    ]
    with Store.open(root, read_only=True) as store:
        bound = store.read_snapshot().unit("u")
    assert bound is not None

    document = _show(root)

    assert set(document) == TOP_KEYS
    assert document["revision"] == receipts[-1]["revision"]
    unit = _unit(document, "u@v1")
    assert set(unit) == UNIT_KEYS
    assert unit["status"] == "in-progress"
    assert unit["chain_error"] == ""
    assert unit["current_test_set"] == bound.acceptance_test_set_digest != ""
    records = _records(unit)
    assert [set(record) for record in records] == [RECORD_KEYS] * 3
    assert [record["command"] for record in records] == [FAIL_LINE, OK_LINE, OK_OTHER_LINE]
    for record, receipt in zip(records, receipts, strict=True):
        assert record["sequence"] == receipt["sequence"]
        assert record["kind"] == receipt["kind"]
        assert record["outcome"] == receipt["outcome"]
        assert record["test_set"] == receipt["pack_digest"] == bound.acceptance_test_set_digest
        assert record["recorded_at"] == receipt["recorded_at"]
        assert record["digest"] == receipt["digest"]
    assert [record["sequence"] for record in records] == [1, 2, 3]


# R2


def test_the_latest_green_and_the_red_before_it_count(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", OK_OTHER[:2] + ["raise SystemExit(3)"])
    _evidence(root, "red", _toggle_script(project, 1))
    _evidence(root, "green", OK)
    _evidence(root, "green", _toggle_script(project, 0))
    _evidence(root, "refactor", OK_OTHER)

    unit = _unit(_show(root), "u@v1")

    assert _counts(unit) == ["", "red", "", "green", ""]
    assert unit["current_pack_green"] is True
    assert unit["notes"] == []


# R3


def test_a_later_red_leaves_nothing_counted(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _evidence(root, "red", FAIL)

    unit = _unit(_show(root), "u@v1")

    assert _counts(unit) == ["", "", ""]
    assert unit["current_pack_green"] is False
    assert unit["notes"] == []


# R4


def test_a_green_with_no_red_before_it_counts_nothing(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "green", OK)

    unit = _unit(_show(root), "u@v1")

    assert _counts(unit) == [""]
    assert unit["current_pack_green"] is False
    assert unit["notes"] == []


# R5


def test_a_different_green_command_is_noted_and_never_blocks(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _must("set-tier", "--root", root, "--tier", "standard")
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK_OTHER)

    unit = _unit(_show(root), "u@v1")
    assert _counts(unit) == ["red", "green"]
    assert unit["notes"] == [NOTE]
    assert unit["current_pack_green"] is True

    code, out, err = _run("show-evidence", "--root", root)
    assert code == 0, err
    assert "u@v1" in out and NOTE in out
    green_lines = [line for line in out.splitlines() if OK_OTHER_LINE in line]
    assert len(green_lines) == 1 and "green" in green_lines[0] and "counts" in green_lines[0]
    red_lines = [line for line in out.splitlines() if FAIL_LINE in line]
    assert len(red_lines) == 1 and "red" in red_lines[0] and "counts" in red_lines[0]

    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")


# R6


def test_a_spec_only_refreeze_counts_nothing_until_a_fresh_green(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _must("mark-done", "--root", root, "--name", "u")
    _must(
        "open-deviation", "--root", root, "--deviation-id", "DEV-1",
        "--deviation-type", "pack-defect", "--unit", "u@v1",
    )
    _write_pack(project, "assert True", spec="# u acceptance pack\n\nAmended.\n")
    _must(
        "adjudicate-amend", "--root", root, "--deviation-id", "DEV-1",
        "--amendment-version", "u-pack/2",
    )
    _must("close-deviation", "--root", root, "--deviation-id", "DEV-1")
    _must("respecify", "--root", root, "--name", "u")
    _must(
        "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2",
        "--project", str(project), *_FILES,
    )
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")

    before = _unit(_show(root), "u@v1")
    assert before["current_pack_green"] is False
    assert _counts(before) == ["", ""]

    _evidence(root, "refactor", OK)
    after_refactor = _unit(_show(root), "u@v1")
    assert after_refactor["current_pack_green"] is False
    assert _counts(after_refactor) == ["", "", ""]

    _evidence(root, "green", OK)
    after_green = _unit(_show(root), "u@v1")
    assert after_green["current_pack_green"] is True
    assert _counts(after_green) == ["red", "", "", "green"]
    assert after_green["notes"] == [NOTE]


# R7


def test_marks_follow_the_current_test_set_after_a_changed_refreeze(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    first = _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _must("mark-done", "--root", root, "--name", "u")
    _must(
        "open-deviation", "--root", root, "--deviation-id", "DEV-1",
        "--deviation-type", "pack-defect", "--unit", "u@v1",
    )
    _write_pack(project, "assert 1 == 1")
    _must(
        "adjudicate-amend", "--root", root, "--deviation-id", "DEV-1",
        "--amendment-version", "u-pack/2",
    )
    _must("close-deviation", "--root", root, "--deviation-id", "DEV-1")
    _must("respecify", "--root", root, "--name", "u")
    _must(
        "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2",
        "--project", str(project), *_FILES,
    )
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")
    second = _evidence(root, "red", _toggle_script(project, 1))
    _evidence(root, "green", _toggle_script(project, 0))

    unit = _unit(_show(root), "u@v1")
    records = _records(unit)

    assert [record["test_set"] for record in records] == [
        first["pack_digest"], first["pack_digest"], second["pack_digest"], second["pack_digest"],
    ]
    assert first["pack_digest"] != second["pack_digest"]
    assert unit["current_test_set"] == second["pack_digest"]
    assert _counts(unit) == ["", "", "red", "green"]
    assert unit["current_pack_green"] is True
    assert unit["notes"] == []


# R8


def test_a_rebound_pack_counts_a_green_with_no_red(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")
    _write_pack(project, "assert 2 == 2")
    _must(
        "rebind-pack", "--root", root, "--name", "u", "--reason", "owner adaptation",
        "--project", str(project), *_FILES,
    )

    before = _unit(_show(root), "u@v1")
    assert before["current_pack_green"] is False
    assert _counts(before) == ["", ""]

    _evidence(root, "green", OK_OTHER)
    after = _unit(_show(root), "u@v1")
    assert _counts(after) == ["", "", "green"]
    assert after["current_pack_green"] is True
    assert after["notes"] == []


# R9


def test_units_are_ordered_and_unbound_or_empty_units_are_unmarked(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root, "labelled", bound=False)
    _evidence(root, "red", FAIL, "labelled@v1")
    _evidence(root, "green", OK, "labelled@v1")
    _must("specify", "--root", root, "--name", "empty", "--version", "v1")
    _must("specify", "--root", root, "--name", "alpha", "--version", "v1")

    document = _show(root)

    assert [unit["unit"] for unit in _units(document)] == ["alpha@v1", "empty@v1", "labelled@v1"]
    labelled = _unit(document, "labelled@v1")
    assert labelled["current_test_set"] == ""
    assert [record["test_set"] for record in _records(labelled)] == ["", ""]
    assert _counts(labelled) == ["", ""]
    assert labelled["current_pack_green"] is False
    assert labelled["notes"] == []
    empty = _unit(document, "empty@v1")
    assert empty == {
        "unit": "empty@v1",
        "status": "specified",
        "current_test_set": "",
        "current_pack_green": False,
        "records": [],
        "notes": [],
        "chain_error": "",
        "test_comparison": None,
    }


# R10


def test_superseded_versions_are_hidden_by_default_and_selectable_by_exact_version(
    tmp_path: Path,
) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _must("mark-done", "--root", root, "--name", "u")
    _must("publish", "--root", root, "--name", "u")
    _must("supersede", "--root", root, "--name", "u")
    _must("specify", "--root", root, "--name", "u", "--version", "v2")
    _must("specify", "--root", root, "--name", "w", "--version", "v1")

    listed = _show(root)
    assert [unit["unit"] for unit in _units(listed)] == ["u@v2", "w@v1"]
    assert _records(_unit(listed, "u@v2")) == []

    old = _show(root, "--unit", "u@v1")
    assert [unit["unit"] for unit in _units(old)] == ["u@v1"]
    assert _unit(old, "u@v1")["status"] == "superseded"
    assert [record["command"] for record in _records(_unit(old, "u@v1"))] == [FAIL_LINE, OK_LINE]

    only = _show(root, "--unit", "w@v1")
    assert [unit["unit"] for unit in _units(only)] == ["w@v1"]

    code, out, err = _run("show-evidence", "--root", root, "--unit", "nope@v1")
    assert code == 2, (out, err)
    assert "nope@v1" in err


# R11


def test_redacted_arguments_stay_redacted(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    script = _toggle_script(project, 1)
    _evidence(root, "red", [*script, "secret-token-123"], "u@v1", "--redact-arg", "2")

    document = _show(root)
    (record,) = _records(_unit(document, "u@v1"))
    assert record["command"] == f"python3 {shlex.quote(script[1])} '<redacted>'"
    _code, text, _err = _run("show-evidence", "--root", root)
    assert "secret-token-123" not in json.dumps(document)
    assert "secret-token-123" not in text


# R12


def test_an_unreadable_chain_is_reported_and_exits_one(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _started(project, root, "w")
    _evidence(root, "red", FAIL)
    head = _evidence(root, "green", OK)
    _evidence(root, "red", FAIL, "w@v1")
    digest = head["digest"]
    assert isinstance(digest, str)
    (Path(root) / "objects" / "sha256" / digest).unlink()

    code, out, err = _run("show-evidence", "--root", root, "--json")

    assert code == 1, (out, err)
    document = _object(out)
    broken = _unit(document, "u@v1")
    assert isinstance(broken["chain_error"], str) and broken["chain_error"]
    assert broken["records"] == [] and broken["notes"] == []
    assert broken["current_pack_green"] is True
    healthy = _unit(document, "w@v1")
    assert healthy["chain_error"] == ""
    assert [record["command"] for record in _records(healthy)] == [FAIL_LINE]

    responses = _mcp(root, ("evidence", {}))
    result = cast(Json, responses[1]["result"])
    assert result["structuredContent"] == document
    assert result.get("isError") is True


# R13


def test_show_evidence_and_the_mcp_tool_use_a_read_only_open(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    # A leftover temporary file that a writable open's cleanup would remove.
    leftover = Path(root) / ".state-interrupted.tmp"
    leftover.write_bytes(b"partial")
    before = _tree(Path(root))

    _show(root)
    _must("show-evidence", "--root", root)
    _mcp(root, ("evidence", {}))

    assert leftover.exists()
    assert _tree(Path(root)) == before


# R14


def test_the_mcp_evidence_tool_is_listed_and_matches_the_cli(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    assert "evidence" in _tool_names(_mcp(root))

    _write_pack(project, "assert True")
    _started(project, root)
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK_OTHER)
    expected = _show(root)

    responses = _mcp(root, ("evidence", {}), ("evidence", {"unit": "u@v1"}))
    result = cast(Json, responses[1]["result"])
    assert result["structuredContent"] == expected
    assert result.get("isError") is not True
    assert "error" in responses[2], "the evidence tool takes no arguments"


# R15


def test_docs_name_show_evidence_and_its_limits() -> None:
    boundary = " ".join((ROOT / "docs" / "claim-boundary.md").read_text().split())
    assert (
        "`show-evidence` lists the stored command line of every RED and GREEN and notes when "
        "the GREEN that counts has a different stored command line from the RED that counts; "
        "the note never blocks publication."
    ) in boundary
    reviewer = (ROOT / "docs" / "agents" / "roles" / "reviewer.md").read_text()
    assert "`show-evidence`" in reviewer
    commands = (ROOT / "docs" / "commands.md").read_text()
    assert re.search(r"^#+ .*show-evidence", commands, re.M), "commands.md has no show-evidence entry"


# R16


def test_the_json_reference_shows_a_worked_red_and_green(tmp_path: Path) -> None:
    raw = (ROOT / "docs" / "schemas.md").read_text()
    match = re.search(r"^## `show-evidence --json`\n(.*?)(?=^## |\Z)", raw, re.S | re.M)
    assert match is not None, "docs/schemas.md has no ## `show-evidence --json` section"
    block = re.search(r"```json\n(.*?)\n```", match.group(1), re.S)
    assert block is not None, "the section has no JSON block"
    doc = _object(block.group(1))
    assert set(doc) == TOP_KEYS
    units = cast(list[Json], doc["units"])
    assert units
    worked: list[dict[str, Json]] = []
    for unit in units:
        assert set(unit) == UNIT_KEYS
        counted: dict[str, Json] = {}
        for record in cast(list[Json], unit["records"]):
            assert set(record) == RECORD_KEYS
            if record["counts"]:
                counted[cast(str, record["counts"])] = record
                assert record["test_set"] == unit["current_test_set"] != ""
                assert unit["current_pack_green"] is True
        if set(counted) == {"red", "green"}:
            worked.append(counted)
    assert worked, "show one unit with a counted RED and a counted GREEN"
    counted = worked[0]
    assert counted["red"]["kind"] == "red" and counted["red"]["outcome"] == "tests-failed"
    assert counted["green"]["kind"] == "green" and counted["green"]["outcome"] == "tests-passed"
    red_sequence = counted["red"]["sequence"]
    green_sequence = counted["green"]["sequence"]
    assert isinstance(red_sequence, int) and isinstance(green_sequence, int)
    assert red_sequence < green_sequence


# R17


def test_a_format_one_record_is_listed_with_no_test_set_and_no_mark(tmp_path: Path) -> None:
    from vouch_mk2.core import (
        EvidenceEvent, EvidenceHead, EvidenceKind, EvidenceOutcome, Snapshot, Tier, Unit,
        UnitRef, UnitStatus,
    )
    from vouch_mk2.store import (
        STATE_FORMAT_HIGH_WATER_FILE, ArtifactKind, digest_of, encode_evidence_event,
        encode_snapshot,
    )

    # A format-2 store holding a format-1 record (written before test-set digests existed),
    # upgraded through the CLI the way a real historical store is.
    root = tmp_path / "historical"
    legacy = EvidenceEvent(
        UnitRef("u", "v1"), 1, "GENESIS", EvidenceKind.RED, "pytest -q",
        EvidenceOutcome.TESTS_FAILED, "2026-01-02T03:04:05+00:00", evidence_format=1,
    )
    data = encode_evidence_event(legacy)
    digest = digest_of(ArtifactKind.EVIDENCE, data)
    snapshot = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(
            Unit(
                "u", "v1", UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="pack/1", acceptance_pack_digest="a" * 64,
            ),
        ),
        evidence_heads=(EvidenceHead("u", digest, 1, "GENESIS", "v1"),),
    )
    with Store.init(str(root)) as store, store.write_locked():
        store.put_object(ArtifactKind.EVIDENCE, data)
    (root / STATE_FORMAT_HIGH_WATER_FILE).unlink()
    document = _object(encode_snapshot(snapshot).decode())
    document["state_format"] = 2
    del document["event_head"]
    for head in cast(list[Json], document["evidence_heads"]):
        for field in (
            "unit_version", "pack_summaries", "current_pack_digest",
            "current_pack_red_seen", "current_pack_green",
        ):
            head.pop(field, None)
    (root / "state.json").write_bytes(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    _must("upgrade", "--root", str(root))

    unit = _unit(_show(str(root)), "u@v1")

    assert unit["chain_error"] == ""
    assert _records(unit) == [{
        "sequence": 1,
        "kind": "red",
        "outcome": "tests-failed",
        "test_set": "",
        "command": "pytest -q",
        "recorded_at": "2026-01-02T03:04:05+00:00",
        "digest": digest,
        "counts": "",
        "test_report": "",
    }]
    assert unit["current_pack_green"] is False
    assert unit["notes"] == []


def test_guard_the_pack_fixtures_produce_a_bound_in_progress_unit(tmp_path: Path) -> None:
    # guard: the fixtures this pack relies on work with today's CLI.
    project, root = _project(tmp_path)
    _write_pack(project, "assert True")
    _started(project, root)
    receipt = _evidence(root, "red", FAIL)
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("u")
    assert unit is not None
    assert unit.status.value == "in-progress"
    assert unit.acceptance_test_set_digest
    assert receipt["pack_digest"] == unit.acceptance_test_set_digest
    assert receipt["command_line"] == FAIL_LINE
