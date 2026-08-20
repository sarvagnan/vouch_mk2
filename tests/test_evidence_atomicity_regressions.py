from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core import (
    EvidenceEvent,
    EvidenceKind,
    EvidenceOutcome,
    RecordEvidence,
    Refused,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
    decide,
)
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _frozen(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "t.py").write_text("def test_x():\n    assert False\n")
    root = str(project / ".vouch")
    Store.init(root).close()
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
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
        "tests/t.py",
    )[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    return project, root


def _start_record(root: str, tmp_path: Path) -> tuple[subprocess.Popen[bytes], Path]:
    marker = tmp_path / "record-started"
    release = tmp_path / "release-record"
    child = (
        "import pathlib,time; "
        f"pathlib.Path({str(marker)!r}).write_text('started'); "
        f"go=pathlib.Path({str(release)!r}); "
        "exec(\"while not go.exists():\\n time.sleep(0.01)\"); "
        "raise SystemExit(1)"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from vouch_mk2.cli import main; raise SystemExit(main(sys.argv[1:]))",
            "record-evidence",
            "--root",
            root,
            "--unit",
            "u@v1",
            "--kind",
            "red",
            "--",
            sys.executable,
            "-c",
            child,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for _ in range(500):
        if marker.exists():
            break
        if process.poll() is not None:
            break
        time.sleep(0.01)
    assert marker.exists(), process.communicate(timeout=5)
    return process, release


def _block_and_supersede(root: str) -> None:
    assert _run(
        "open-deviation",
        "--root",
        root,
        "--deviation-id",
        "D-mid-run",
        "--deviation-type",
        "pack-defect",
        "--unit",
        "u@v1",
    )[0] == 0
    assert _run("supersede", "--root", root, "--name", "u")[0] == 0


def test_evidence_is_refused_when_unit_is_superseded_mid_run(tmp_path: Path) -> None:
    _project, root = _frozen(tmp_path)
    process, release = _start_record(root, tmp_path)
    _block_and_supersede(root)
    release.write_text("go")
    stdout, stderr = process.communicate(timeout=30)

    assert process.returncode == 2, (stdout + stderr).decode(errors="replace")
    assert b"evidence.pack-moved" in stdout + stderr
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().evidence_head_ref(UnitRef("u", "v1")) is None


def test_evidence_is_refused_when_unit_is_reversioned_mid_run(tmp_path: Path) -> None:
    _project, root = _frozen(tmp_path)
    process, release = _start_record(root, tmp_path)
    _block_and_supersede(root)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v2")[0] == 0
    release.write_text("go")
    stdout, stderr = process.communicate(timeout=30)

    assert process.returncode == 2, (stdout + stderr).decode(errors="replace")
    assert b"evidence.pack-moved" in stdout + stderr
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        unit = snapshot.unit("u")
        assert unit is not None and unit.version == "v2"
        assert snapshot.evidence_head_ref(UnitRef("u", "v1")) is None


def test_evidence_is_not_refused_for_revision_bump_alone(tmp_path: Path) -> None:
    _project, root = _frozen(tmp_path)
    process, release = _start_record(root, tmp_path)
    with Store.open(root, read_only=True) as store:
        before = store.read_snapshot().revision
    assert _run("set-ci-policy", "--root", root, "--policy", "ledger")[0] == 0
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().revision == before + 1
    release.write_text("go")
    stdout, stderr = process.communicate(timeout=30)

    assert process.returncode == 0, (stdout + stderr).decode(errors="replace")
    with Store.open(root, read_only=True) as store:
        head = store.read_snapshot().evidence_head_ref(UnitRef("u", "v1"))
        assert head is not None and head.head_sequence == 1


def test_decide_names_captured_and_current_digests_when_pack_moved() -> None:
    captured = "a" * 64
    current = "b" * 64
    unit = Unit(
        "u",
        "v1",
        UnitStatus.ACCEPTANCE_FROZEN,
        acceptance_pack_version="pack/2",
        acceptance_pack_digest=current,
        acceptance_test_set_digest=current,
    )
    event = EvidenceEvent(
        UnitRef("u", "v1"),
        1,
        "GENESIS",
        EvidenceKind.RED,
        "pytest",
        EvidenceOutcome.TESTS_FAILED,
        "2026-08-18T00:00:00+00:00",
        pack_digest=captured,
    )
    decision = decide(
        Snapshot(units=(unit,)),
        RecordEvidence(event, "c" * 64, captured_pack_digest=captured),
    )

    assert isinstance(decision, Refused)
    assert decision.violations[0].code == "evidence.pack-moved"
    assert captured[:16] in decision.violations[0].detail
    assert current[:16] in decision.violations[0].detail


def test_json_mode_streams_large_child_stdout_with_printable_argv(
    tmp_path: Path,
) -> None:
    from vouch_mk2.lock import build_lock

    _project, root = _frozen(tmp_path)
    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    noisy = [
        sys.executable,
        "-c",
        "import sys; block='x'*65536; "
        "[sys.stdout.write(block) for _ in range(1024)]; "
        "sys.stdout.flush(); raise SystemExit(1)",
    ]
    err_path = tmp_path / "child.err"
    out_path = tmp_path / "child.out"
    with err_path.open("wb") as err_file, out_path.open("wb") as out_file:
        completed = subprocess.run(
            [
                sys.executable,
                str(lock),
                "record-evidence",
                "--root",
                root,
                "--unit",
                "u@v1",
                "--kind",
                "red",
                "--json",
                "--",
                *noisy,
            ],
            stdout=out_file,
            stderr=err_file,
            env={**os.environ, "PYTHONPATH": ""},
            check=False,
        )

    assert completed.returncode == 0
    assert json.loads(out_path.read_text())["accepted"] is True
    assert err_path.stat().st_size == 64 * 1024 * 1024
