"""Acceptance pack for self-hosted unit lifecycle-cli@1 (D-11; epic bd4.1).

Every test here FAILED against the pre-change tree (RED recorded before implementation)
and must PASS at completion — except tests marked `# guard:`, which pass today and must KEEP
passing. The implementer may not edit this file — a review finding that
says this pack is wrong is a DEVIATION adjudicated by the acceptance owner.

Intent: mk2 has a complete typed lifecycle (27 IR commands) and no way to drive it without
writing Python. Every IR lifecycle command becomes a `vouch-mk2` verb of the SAME name; the
verbs are generated from the IR so they cannot drift; verbs whose IR command carries an
outcome mk2 can compute itself (evidence, formal certificate) compute it and never accept a
hand-typed one; refusals surface decide()'s violation codes and leave the store untouched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.model import UnitStatus
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _init(tmp_path: Path) -> str:
    root = str(tmp_path / "store")
    Store.init(root).close()
    return root


def _revision(root: str) -> int:
    with Store.open(root) as store:
        return store.read_snapshot().revision


# ---- completeness: verbs are the IR command set, both directions ----


def test_every_ir_lifecycle_command_is_a_cli_verb_and_vice_versa() -> None:
    import dataclasses

    import vouch_mk2.ir.mk2 as ir
    from vouch_mk2.cli import LIFECYCLE_VERBS
    from vouch_mk2.core import commands as command_module

    core_commands = {
        cls.ir_command
        for cls in vars(command_module).values()
        if dataclasses.is_dataclass(cls) and hasattr(cls, "ir_command")
    }
    ir_names = {getattr(ir, name) for name in dir(ir) if name.startswith("CMD_")}
    assert core_commands == ir_names, "typed core and IR disagree on the command set"
    assert set(LIFECYCLE_VERBS) == core_commands


def test_every_verb_parses_help(capsys: pytest.CaptureFixture[str]) -> None:
    from vouch_mk2.cli import LIFECYCLE_VERBS

    for verb in LIFECYCLE_VERBS:
        with pytest.raises(SystemExit) as raised:
            main([verb, "--help"])
        assert raised.value.code == 0, verb
        assert "--root" in capsys.readouterr().out, verb


def test_next_action_commands_are_cli_verbs() -> None:
    from vouch_mk2.cli import LIFECYCLE_VERBS
    from vouch_mk2.core.model import Snapshot, Unit
    from vouch_mk2.projections import next_actions

    s = Snapshot(revision=1, units=(Unit("u", "1", UnitStatus.SPECIFIED),))
    actions = next_actions(s)
    assert actions, "expected at least one next action for a specified unit"
    for action in actions:
        assert action.command in LIFECYCLE_VERBS, action


# ---- accepted commands: table-visible result, exit 0 ----


def test_specify_creates_the_unit_and_reports_the_new_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    out = capsys.readouterr().out
    assert "core@v1" in out and "specified" in out
    with Store.open(root) as store:
        unit = store.read_snapshot().unit("core")
        assert unit is not None and unit.status is UnitStatus.SPECIFIED


def test_specify_accepts_repeated_depends_on(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert main(["specify", "--root", root, "--name", "a", "--version", "1"]) == 0
    assert main(["specify", "--root", root, "--name", "b", "--version", "1", "--depends-on", "a", "--depends-on", "a"]) == 0
    with Store.open(root) as store:
        unit = store.read_snapshot().unit("b")
        assert unit is not None and unit.depends_on == ("a",)


def test_json_output_lists_events_and_new_revision(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["accepted"] is True
    assert document["revision"] == _revision(root)
    assert any(event["event"] == "unit-specified" for event in document["events"])


# ---- refusals: decide()'s codes, exit 2, store untouched ----


def test_refusal_prints_violation_codes_exits_2_and_leaves_store_untouched(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    before = _revision(root)
    assert main(["publish", "--root", root, "--name", "core"]) == 2
    err = capsys.readouterr().err
    assert "transition.illegal" in err
    assert _revision(root) == before


def test_refusal_json_carries_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    capsys.readouterr()  # pack/2 (DEV-cli-01): drain the accepted command's R4 line first
    assert main(["publish", "--root", root, "--name", "core", "--json"]) == 2
    document = json.loads(capsys.readouterr().out)
    assert document["accepted"] is False
    assert [v["code"] for v in document["violations"]] == ["transition.illegal"]


# ---- typed arguments: enums are choices, refs are NAME@VERSION ----


def test_enum_fields_are_argparse_choices(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    with pytest.raises(SystemExit) as raised:
        main(["set-tier", "--root", root, "--tier", "platinum"])
    assert raised.value.code == 2
    assert "lightweight" in capsys.readouterr().err
    assert main(["set-tier", "--root", root, "--tier", "standard"]) == 0


def test_unit_ref_is_name_at_version_and_bad_refs_are_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "p1"]) == 0
    assert main(["mark-ready", "--root", root, "--name", "core"]) == 0
    assert main(["start", "--root", root, "--name", "core"]) == 0
    with pytest.raises(SystemExit) as raised:
        main(["open-deviation", "--root", root, "--deviation-id", "DEV-1", "--deviation-type", "pack-defect", "--unit", "core"])
    assert raised.value.code == 2
    assert "NAME@VERSION" in capsys.readouterr().err
    assert main(["open-deviation", "--root", root, "--deviation-id", "DEV-1", "--deviation-type", "pack-defect", "--unit", "core@v1"]) == 0
    with Store.open(root) as store:
        unit = store.read_snapshot().unit("core")
        assert unit is not None and unit.status is UnitStatus.BLOCKED


# ---- computed outcomes: evidence is RUN, never typed ----


def test_record_evidence_runs_the_command_and_records_the_real_outcome(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    for argv in (["specify", "--name", "core", "--version", "v1"], ["freeze", "--name", "core", "--pack-version", "p1"], ["mark-ready", "--name", "core"], ["start", "--name", "core"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0
    failing = [sys.executable, "-c", "raise SystemExit(1)"]
    assert main(["record-evidence", "--root", root, "--unit", "core@v1", "--kind", "red", "--", *failing]) == 0
    out = capsys.readouterr().out
    assert "red #1" in out
    with Store.open(root) as store:
        head = store.read_snapshot().evidence_head("core")
        assert head is not None and head.head_sequence == 1
    passing = [sys.executable, "-c", "raise SystemExit(0)"]
    assert main(["record-evidence", "--root", root, "--unit", "core@v1", "--kind", "green", "--", *passing]) == 0
    with Store.open(root) as store:
        head = store.read_snapshot().evidence_head("core")
        assert head is not None and head.head_sequence == 2


def test_record_evidence_refuses_an_incoherent_kind_and_records_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _init(tmp_path)
    for argv in (["specify", "--name", "core", "--version", "v1"], ["freeze", "--name", "core", "--pack-version", "p1"], ["mark-ready", "--name", "core"], ["start", "--name", "core"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0
    failing = [sys.executable, "-c", "raise SystemExit(1)"]
    assert main(["record-evidence", "--root", root, "--unit", "core@v1", "--kind", "green", "--", *failing]) == 2
    assert "coheren" in capsys.readouterr().err.lower()
    with Store.open(root) as store:
        assert store.read_snapshot().evidence_head("core") is None


def test_record_evidence_json_mode_keeps_stdout_to_exactly_one_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # pack/3 (DEV-cli-02): in --json mode the child's stdout is routed to stderr so
    # stdout carries exactly one JSON document; text mode inherits stdio unchanged.
    root = _init(tmp_path)
    for argv in (["specify", "--name", "core", "--version", "v1"], ["freeze", "--name", "core", "--pack-version", "p1"], ["mark-ready", "--name", "core"], ["start", "--name", "core"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0
    capsys.readouterr()
    chatty = [sys.executable, "-c", "print('CHILD-STDOUT'); raise SystemExit(1)"]
    assert main(["record-evidence", "--root", root, "--unit", "core@v1", "--kind", "red", "--json", "--", *chatty]) == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["accepted"] is True
    assert "CHILD-STDOUT" in captured.err


def test_record_evidence_has_no_typed_outcome_flag() -> None:
    # guard: passes today (no verb) and must keep passing (verb without --outcome).
    with pytest.raises(SystemExit) as raised:
        main(["record-evidence", "--root", "/nonexistent", "--unit", "core@v1", "--kind", "green", "--outcome", "tests-passed"])
    assert raised.value.code == 2


def test_record_formal_certificate_verb_runs_the_checker_not_a_typed_digest(tmp_path: Path) -> None:
    # guard: passes today (no verb) and must keep passing (verb without --certificate-digest).
    root = _init(tmp_path)
    with pytest.raises(SystemExit) as raised:
        main(["record-formal-certificate", "--root", root, "--certificate-digest", "a" * 64])
    assert raised.value.code == 2


# ---- the verbs ship in the lock and the old script is gone ----


def test_lock_exposes_the_lifecycle_verbs(tmp_path: Path) -> None:
    from vouch_mk2.lock import build_lock

    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    result = subprocess.run([sys.executable, str(lock), "specify", "--help"], capture_output=True, text=True, env={"PATH": "", "PYTHONPATH": ""}, check=False)
    assert result.returncode == 0 and "--root" in result.stdout


def test_record_pytest_evidence_script_is_absorbed_by_the_verb() -> None:
    assert not (ROOT / "scripts" / "record_pytest_evidence.py").exists()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "record_pytest_evidence" not in ci
