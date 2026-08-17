from __future__ import annotations

import json
import shlex
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, cast

import pytest

import vouch_mk2.cli as cli_module
import vouch_mk2.verify as verify_module
from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    AnchorMode,
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    RecordEvidence,
    Refused,
    Snapshot,
    SpecifyUnit,
    Tier,
    Unit,
    UnitRef,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.formal.run import FormalRun
from vouch_mk2.ir.schema import Violation
from vouch_mk2.store import (
    ArtifactKind,
    DecodeError,
    Store,
    StoreError,
    decode_evidence_event,
    digest_of,
    encode_evidence_event,
)
from vouch_mk2.verify import (
    RefusedError,
    emit_context,
    emit_system_map,
    record_evidence,
    record_formal_certificate,
)

ROOT = Path(__file__).resolve().parents[1]


def _init_store(root: Path, *, unit_name: str | None = None) -> None:
    with Store.init(str(root)) as store:
        if unit_name is not None:
            store.commit(
                Snapshot(
                    revision=1,
                    units=(
                        Unit(
                            unit_name,
                            "v1",
                            UnitStatus.ACCEPTANCE_FROZEN,
                            acceptance_pack_version="pack/1",
                        ),
                    ),
                )
            )


def _event(command_line: str = "pytest") -> EvidenceEvent:
    return EvidenceEvent(
        unit=UnitRef("u", "v1"),
        sequence=1,
        parent="GENESIS",
        kind=EvidenceKind.RED,
        command_line=command_line,
        outcome=EvidenceOutcome.TESTS_FAILED,
        recorded_at="t1",
    )


@contextmanager
def _writer_process(root: Path, tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    ready = tmp_path / "writer-ready"
    release = tmp_path / "writer-release"
    script = """
import pathlib
import sys
import time
from vouch_mk2.store import Store

root, ready, release = sys.argv[1:]
with Store.open(root) as store:
    with store.write_locked():
        pathlib.Path(ready).write_text("ready", encoding="utf-8")
        while not pathlib.Path(release).exists():
            time.sleep(0.01)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(ready), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError("writer process did not acquire the lock")
            time.sleep(0.01)
        assert process.poll() is None
        yield process
    finally:
        release.write_text("release", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)


def test_read_only_store_cannot_mutate_any_store_content(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init_store(root)
    (root / ".lock").unlink()
    before = sorted(
        (path.relative_to(root), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )
    with Store.open(str(root), read_only=True) as store:
        with pytest.raises(StoreError, match="read-only"):
            store.write_generated("map.json", b"mutated")
        with pytest.raises(StoreError, match="read-only"):
            store.put_object(ArtifactKind.OBJECT, b"mutated")
        with pytest.raises(StoreError, match="read-only"):
            store.commit(Snapshot(revision=1))
        with pytest.raises(StoreError, match="read-only"):
            with store.write_locked():
                pass
    after = sorted(
        (path.relative_to(root), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )
    assert after == before
    assert not (root / ".lock").exists()


def test_put_object_requires_the_current_thread_to_hold_write_lock(
    tmp_path: Path,
) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        with pytest.raises(StoreError, match="writer lock"):
            store.put_object(ArtifactKind.OBJECT, b"payload")
        with store.write_locked():
            digest = store.put_object(ArtifactKind.OBJECT, b"payload")
        assert store.get_object(ArtifactKind.OBJECT, digest) == b"payload"


def test_status_next_and_check_finish_while_another_process_holds_writer_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    _init_store(root)
    with _writer_process(root, tmp_path):
        for arguments in (
            ["status", "--root", str(root)],
            ["next", "--root", str(root)],
            ["check", "--root", str(root)],
        ):
            started = time.monotonic()
            assert main(arguments) in (0, 1)
            assert time.monotonic() - started < 1.0
            capsys.readouterr()


def test_writable_open_skips_orphan_sweep_while_writer_lock_is_held(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    _init_store(root)
    with _writer_process(root, tmp_path):
        orphan = root / ".state-orphan.tmp"
        orphan.write_bytes(b"orphan")
        started = time.monotonic()
        with Store.open(str(root)):
            pass
        assert time.monotonic() - started < 1.0
        assert orphan.exists()
    with Store.open(str(root)):
        pass
    assert not orphan.exists()


def test_emit_system_map_reads_before_taking_the_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        original = store.read_snapshot

        def guarded_read() -> Snapshot:
            assert store._write_lock_fd is None
            return original()

        monkeypatch.setattr(store, "read_snapshot", guarded_read)
        emit_system_map(store)


def test_emit_context_reads_before_taking_the_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        original = store.read_snapshot

        def guarded_read() -> Snapshot:
            assert store._write_lock_fd is None
            return original()

        monkeypatch.setattr(store, "read_snapshot", guarded_read)
        emit_context(store, None)


def test_shared_store_threads_serialize_before_revision_cas(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _init_store(root)
    condition = threading.Condition()
    cas_count = 0
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def on_step(step: str) -> None:
        nonlocal cas_count
        if step != "cas-checked":
            return
        with condition:
            cas_count += 1
            condition.notify_all()
            if cas_count == 1:
                condition.wait_for(lambda: cas_count == 2, timeout=0.25)

    def worker(store: Store, name: str) -> None:
        start.wait()
        with store.write_locked():
            snapshot = store.read_snapshot()
            decision = decide(snapshot, SpecifyUnit(name, "1"))
            assert isinstance(decision, Accepted)
            store.commit(apply(snapshot, decision.events))
        outcomes.append(name)

    with Store.open(str(root), on_step=on_step) as store:
        threads = [
            threading.Thread(target=worker, args=(store, "a")),
            threading.Thread(target=worker, args=(store, "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        snapshot = store.read_snapshot()
    assert sorted(outcomes) == ["a", "b"]
    assert cas_count == 2
    assert snapshot.revision == 2
    assert {unit.name for unit in snapshot.units} == {"a", "b"}


def test_evidence_decode_rejects_control_characters_in_command_line() -> None:
    document = {
        "unit": {"name": "u", "version": "v1"},
        "sequence": 1,
        "parent": "GENESIS",
        "kind": "red",
        "command_line": "pytest\nforged-result",
        "outcome": "tests-failed",
        "recorded_at": "t1",
    }
    data = (
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    with pytest.raises(DecodeError, match="command_line"):
        decode_evidence_event(data)


def test_record_evidence_command_line_distinguishes_argv_tokenization(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    first_argv = [sys.executable, "-c", "raise SystemExit(1)", "a b", "c"]
    second_argv = [sys.executable, "-c", "raise SystemExit(1)", "a", "b c"]
    for child in (first_argv, second_argv):
        assert main(
            [
                "record-evidence",
                "--root",
                str(root),
                "--unit",
                "u@v1",
                "--kind",
                "red",
                "--",
                *child,
            ]
        ) == 0
        capsys.readouterr()
    with Store.open(str(root), read_only=True) as store:
        head = store.read_snapshot().evidence_head("u")
        assert head is not None
        second = decode_evidence_event(
            store.get_object(ArtifactKind.EVIDENCE, head.head)
        )
        first = decode_evidence_event(
            store.get_object(ArtifactKind.EVIDENCE, second.parent)
        )
    assert first.command_line == shlex.join(first_argv)
    assert second.command_line == shlex.join(second_argv)
    assert first.command_line != second.command_line


def test_decide_refuses_an_invalid_evidence_command_line() -> None:
    event = _event("pytest\nforged-result")
    decision = decide(
        Snapshot(
            units=(
                Unit(
                    "u",
                    "v1",
                    UnitStatus.ACCEPTANCE_FROZEN,
                    acceptance_pack_version="pack/1",
                ),
            )
        ),
        RecordEvidence(event, digest_of(ArtifactKind.EVIDENCE, encode_evidence_event(event))),
    )
    assert isinstance(decision, Refused)
    assert [violation.code for violation in decision.violations] == [
        "evidence.command-line-invalid"
    ]


def test_stale_label_specified_evidence_cannot_poison_later_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "stale-label"
    with Store.init(str(root)) as store:
        store.commit(
            Snapshot(
                revision=1,
                tier=Tier.STANDARD,
                anchor_mode=AnchorMode.CI,
                units=(
                    Unit(
                        "u",
                        "v1",
                        UnitStatus.SPECIFIED,
                        acceptance_pack_version="stale-label",
                    ),
                ),
            )
        )
    for kind, exit_code in (("red", 1), ("green", 0)):
        assert main(
            [
                "record-evidence",
                "--root",
                str(root),
                "--unit",
                "u@v1",
                "--kind",
                kind,
                "--",
                sys.executable,
                "-c",
                f"raise SystemExit({exit_code})",
            ]
        ) == 2
        assert "evidence.no-pack" in capsys.readouterr().err
    assert main(
        [
            "freeze",
            "--root",
            str(root),
            "--name",
            "u",
            "--pack-version",
            "real-label",
        ]
    ) == 0
    assert main(["mark-ready", "--root", str(root), "--name", "u"]) == 0
    assert main(["start", "--root", str(root), "--name", "u"]) == 0
    assert main(["advance-anchor", "--root", str(root), "--unit-name", "u"]) == 2
    capsys.readouterr()
    assert main(["mark-done", "--root", str(root), "--name", "u"]) == 0
    assert main(["publish", "--root", str(root), "--name", "u"]) == 2
    out = capsys.readouterr().err
    assert "gate.dod.current-pack-green" in out
    with Store.open(str(root), read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.evidence_head("u") is None
    assert snapshot.unit("u") is not None
    assert snapshot.unit("u").status is UnitStatus.DONE_PENDING_ACCEPTANCE  # type: ignore[union-attr]


def test_standard_label_only_pack_cannot_publish_after_red_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "label-only"
    Store.init(str(root)).close()
    assert main(["set-tier", "--root", str(root), "--tier", "standard"]) == 0
    assert main(["set-anchor-mode", "--root", str(root), "--mode", "ci"]) == 0
    assert main(
        ["specify", "--root", str(root), "--name", "u", "--version", "v1"]
    ) == 0
    assert main(
        [
            "freeze",
            "--root",
            str(root),
            "--name",
            "u",
            "--pack-version",
            "label-only/1",
        ]
    ) == 0
    assert main(["mark-ready", "--root", str(root), "--name", "u"]) == 0
    assert main(["start", "--root", str(root), "--name", "u"]) == 0
    for kind, exit_code in (("red", 1), ("green", 0)):
        assert main(
            [
                "record-evidence",
                "--root",
                str(root),
                "--unit",
                "u@v1",
                "--kind",
                kind,
                "--",
                sys.executable,
                "-c",
                f"raise SystemExit({exit_code})",
            ]
        ) == 0
    assert main(["advance-anchor", "--root", str(root), "--unit-name", "u"]) == 0
    assert main(["mark-done", "--root", str(root), "--name", "u"]) == 0
    assert main(["publish", "--root", str(root), "--name", "u"]) == 2
    out = capsys.readouterr().err
    assert "gate.dod.current-pack-green" in out
    assert "label-only" in out
    assert "nothing to bind evidence to" in out
    assert "requires RED followed by GREEN" not in out


def test_verify_record_evidence_raises_typed_refusal_with_all_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    violations = (
        Violation("evidence.first", "first detail"),
        Violation("evidence.second", "second detail"),
    )
    monkeypatch.setattr(
        verify_module, "decide", lambda _snapshot, _command: Refused(violations)
    )
    with Store.open(str(root)) as store:
        with pytest.raises(RefusedError) as raised:
            record_evidence(store, _event())
    assert raised.value.violations == violations


def test_verify_record_formal_certificate_raises_typed_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    _init_store(root)
    violations = (Violation("formal.refused", "formal detail"),)
    monkeypatch.setattr(verify_module, "passed", lambda _run: True)
    monkeypatch.setattr(
        verify_module, "build_certificate", lambda _run: (object(), b"certificate")
    )
    monkeypatch.setattr(
        verify_module, "decide", lambda _snapshot, _command: Refused(violations)
    )
    fake_run = cast(FormalRun, object())
    with Store.open(str(root)) as store:
        with pytest.raises(RefusedError) as raised:
            record_formal_certificate(store, fake_run)
    assert raised.value.violations == violations


def test_cli_presents_typed_evidence_refusal_with_original_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "green",
            "--json",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(1)",
        ]
    ) == 2
    document = json.loads(capsys.readouterr().out)
    assert [row["code"] for row in document["violations"]] == [
        "evidence.kind-outcome-mismatch"
    ]


def test_cli_never_presents_store_errors_as_policy_refusals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")

    def fail_infrastructure(_store: Store, _event: EvidenceEvent) -> str:
        raise StoreError("disk unavailable: refused: not a policy code")

    monkeypatch.setattr(cli_module, "record_evidence", fail_infrastructure)
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "red",
            "--json",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(1)",
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "disk unavailable" in captured.err


def test_record_formal_certificate_json_reports_its_own_commit_revision(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "store"
    _init_store(root)
    recorded = threading.Event()
    competing_done = threading.Event()
    own_revision: list[int] = []
    fake_run = cast(FormalRun, object())
    monkeypatch.setattr(cli_module, "run_formal", lambda: fake_run)
    monkeypatch.setattr(cli_module, "passed", lambda _run: True)

    def fake_record(store: Store, _run: FormalRun) -> str:
        with store.write_locked():
            snapshot = store.read_snapshot()
            own_revision.append(snapshot.revision + 1)
            store.commit(replace(snapshot, revision=snapshot.revision + 1))
        recorded.set()
        competing_done.wait(timeout=0.25)
        return "a" * 64

    monkeypatch.setattr(cli_module, "record_formal_certificate", fake_record)

    def competing_writer() -> None:
        assert recorded.wait(timeout=5)
        with Store.open(str(root)) as store:
            with store.write_locked():
                snapshot = store.read_snapshot()
                store.commit(replace(snapshot, revision=snapshot.revision + 1))
        competing_done.set()

    competitor = threading.Thread(target=competing_writer)
    competitor.start()
    assert main(["record-formal-certificate", "--root", str(root), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    competitor.join(timeout=5)
    assert not competitor.is_alive()
    assert document["revision"] == own_revision[0] == 1


def test_record_evidence_spawn_oserror_reports_stderr_and_records_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "red",
            "--",
            str(tmp_path / "does-not-exist"),
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot execute" in captured.err
    with Store.open(str(root), read_only=True) as store:
        assert store.read_snapshot().evidence_head("u") is None


def test_record_evidence_signal_exit_refuses_without_recording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "red",
            "--",
            sys.executable,
            "-c",
            f"import os; os.kill(os.getpid(), {signal.SIGTERM})",
        ]
    ) == 2
    assert "child produced no verdict" in capsys.readouterr().err
    with Store.open(str(root), read_only=True) as store:
        assert store.read_snapshot().evidence_head("u") is None


def test_record_evidence_requires_separator_before_the_child_argv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "record-evidence",
                "--root",
                str(root),
                "--unit",
                "u@v1",
                "--kind",
                "red",
                sys.executable,
                "-c",
                "raise SystemExit(1)",
                "--",
                "child-option",
            ]
        )
    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    with Store.open(str(root), read_only=True) as store:
        assert store.read_snapshot().evidence_head("u") is None


def test_record_evidence_treats_help_after_separator_as_a_child_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "green",
            "--",
            sys.executable,
            "-c",
            "import sys; assert sys.argv[1] == '--help'",
            "--help",
        ]
    ) == 0
    capsys.readouterr()
    with Store.open(str(root), read_only=True) as store:
        assert store.read_snapshot().evidence_head("u") is not None


def test_record_evidence_json_routes_child_stdout_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "red",
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('child-json-output'); raise SystemExit(1)",
        ]
    ) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["accepted"] is True
    assert "child-json-output" in captured.err


def test_record_evidence_text_mode_inherits_child_stdout_and_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root, unit_name="u")
    assert main(
        [
            "record-evidence",
            "--root",
            str(root),
            "--unit",
            "u@v1",
            "--kind",
            "red",
            "--",
            sys.executable,
            "-c",
            "import sys; print('child-out'); print('child-err', file=sys.stderr); raise SystemExit(1)",
        ]
    ) == 0
    captured = capfd.readouterr()
    assert "child-out" in captured.out
    assert "[evidence] recorded red #1" in captured.out
    assert "child-err" in captured.err


def test_next_shell_line_round_trips_exact_root_and_subject_argv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store with space"
    _init_store(root)
    assert main(
        [
            "specify",
            "--root",
            str(root),
            "--name",
            "unit with space",
            "--version",
            "v1",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["next", "--root", str(root)]) == 0
    line = capsys.readouterr().out.strip()
    command, marker, reason = line.partition("  # ")
    assert marker and reason
    assert shlex.split(command) == [
        "vouch-mk2",
        "freeze",
        "--root",
        str(root),
        "--name",
        "unit with space",
        "--pack-version",
        "<PACK_VERSION>",
    ]


def test_next_json_on_an_empty_snapshot_bootstraps_the_first_unit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    _init_store(root)
    assert main(["next", "--root", str(root), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert [action["command"] for action in document["actions"]] == ["specify"]
    assert document["actions"][0]["argv"][0] == "vouch-mk2"


def test_next_placeholders_stay_readable_while_bound_values_are_shell_safe() -> None:
    from vouch_mk2.cli import invocation_for
    from vouch_mk2.projections import NextAction

    action = NextAction(
        subject_kind="unit",
        subject_id="unit with space",
        command="freeze",
        reason="freeze it",
    )
    command, marker, reason = invocation_for(action, "/tmp/root with space").partition(
        "  # "
    )
    assert marker and reason == "freeze it"
    assert "--pack-version <PACK_VERSION>" in command
    assert shlex.split(command) == [
        "vouch-mk2",
        "freeze",
        "--root",
        "/tmp/root with space",
        "--name",
        "unit with space",
        "--pack-version",
        "<PACK_VERSION>",
    ]


def test_record_evidence_help_states_the_exact_spawned_process_claim(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["record-evidence", "--help"])
    assert raised.value.code == 0
    help_text = " ".join(capsys.readouterr().out.lower().split())
    assert "-- <argv...>" in help_text
    assert (
        "the recorded outcome is the exit status of a process this verb spawned"
        in help_text
    )


def test_set_criteria_requires_all_four_launch_criteria_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    option_pairs = (
        ("--launch-criteria", "healthy"),
        ("--verification-window", "1h"),
        ("--go-no-go-authority", "release-owner"),
        ("--rollback-trigger", "error-rate"),
    )
    for missing_flag, _missing_value in option_pairs:
        arguments = [
            "set-criteria",
            "--root",
            "/nonexistent",
            "--release-id",
            "r1",
        ]
        for flag, value in option_pairs:
            if flag != missing_flag:
                arguments.extend((flag, value))
        with pytest.raises(SystemExit) as raised:
            main(arguments)
        assert raised.value.code == 2
        assert missing_flag in capsys.readouterr().err


def test_docs_state_exact_evidence_claim_and_lock_policy() -> None:
    claim_boundary = (ROOT / "docs" / "claim-boundary.md").read_text()
    readme = (ROOT / "README.md").read_text()
    exact_claim = (
        "the recorded outcome is the exit status of a process this verb "
        "spawned, at the time it spawned it"
    )
    assert exact_claim in " ".join(claim_boundary.split())
    assert exact_claim in " ".join(readme.split())
    assert "inherits the caller's environment and working directory" in claim_boundary
    assert "not a claim" in claim_boundary
    assert "Writers\nwait on a blocking exclusive lock" in claim_boundary
    assert "Read-only handles never acquire that lock" in claim_boundary


def test_every_reflected_invocation_parses_with_the_real_parser_once_placeholders_are_filled() -> None:
    # Review of cli-completeness@1: bind-pack rendered `--pack-digest`, which its specialized
    # parser does not accept. Every subject-bearing reflected verb's rendered invocation must
    # parse once its placeholders are substituted with plausible values.
    import dataclasses
    import shlex

    from vouch_mk2.cli import LIFECYCLE_VERBS, _parser, invocation_for
    from vouch_mk2.core import commands as command_module
    from vouch_mk2.projections import NextAction

    subject_kind_for = {"name": "unit", "unit_name": "evidence", "release_id": "release", "deviation_id": "deviation"}
    parser = _parser()
    fillers = {"<PACK_VERSION>": "p1", "<PACK_FILE>": "tests/x.py", "<AMENDMENT_VERSION>": "p2", "<TRIGGER>": "t",
               "<LAUNCH_CRITERIA>": "c", "<VERIFICATION_WINDOW>": "1h", "<ROLLBACK_TRIGGER>": "r", "<GO_NO_GO_AUTHORITY>": "a",
               "<INVENTORY_DIGEST>": "a" * 64, "<CERTIFICATE_DIGEST>": "b" * 64, "<TIER>": "standard", "<MODE>": "ci",
               "<DEVIATION_TYPE>": "pack-defect", "<UNIT>": "u@1", "<VERSION>": "1", "<INCLUDES>": "u", "<OBSERVATIONS>": "o",
               "<DEPENDS_ON>": "d"}
    checked = 0
    for cls in vars(command_module).values():
        if not (dataclasses.is_dataclass(cls) and hasattr(cls, "ir_command")) or cls.ir_command not in LIFECYCLE_VERBS:
            continue
        names = {f.name for f in dataclasses.fields(cls)}
        subject_field = next((f for f in ("name", "unit_name", "release_id", "deviation_id") if f in names), None)
        if subject_field is None:
            continue
        line = invocation_for(NextAction(subject_kind_for[subject_field], "core", cls.ir_command, "why"), "/tmp/r").split("  # ")[0]
        argv = [fillers.get(token, token) for token in shlex.split(line)]
        assert argv[0] == "vouch-mk2"
        parser.parse_args(argv[1:])  # raises SystemExit(2) on an unknown/missing option
        checked += 1
    assert checked >= 20
