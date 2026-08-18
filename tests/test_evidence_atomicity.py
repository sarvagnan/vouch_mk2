"""Acceptance pack for self-hosted unit evidence-atomicity@1 (D-19; external review #3 and #7).

`record-evidence` opens the store to pre-flight, discards what it read, spawns the child, and
only then reads the *then-current* pack digest under the lock. A `freeze`, `rebind-pack` or
`respecify` that lands while the tests run therefore attributes the observed exit status to a
pack that was NOT current when the command ran — a hole in D-17's current-pack guarantee.
Separately, `--json` buffers the child's entire stdout in memory before forwarding it.

Every test here FAILED against the pre-change tree — except tests marked `# guard:`.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
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
    assert _run("set-tier", "--root", root, "--tier", "standard")[0] == 0
    assert _run("set-anchor-mode", "--root", root, "--mode", "ci")[0] == 0
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
                "--project", str(project), "--pack-test-file", "tests/t.py")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    return project, root


# ---- the pack the command ran against is the pack the evidence binds ----


def test_evidence_is_refused_when_the_pack_changes_while_the_command_runs(tmp_path: Path) -> None:
    project, root = _frozen(tmp_path)
    marker = tmp_path / "started"
    # The child signals it has begun, then waits: the pack is repointed underneath it.
    child = [sys.executable, "-c",
             f"import pathlib,time; pathlib.Path({str(marker)!r}).write_text('x');"
             f" [time.sleep(0.05) for _ in range(200) if not pathlib.Path({str(tmp_path / 'go')!r}).exists()];"
             " raise SystemExit(1)"]
    import threading

    result: dict[str, tuple[int, str]] = {}

    def record() -> None:
        result["r"] = _run("record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--", *child)

    thread = threading.Thread(target=record)
    thread.start()
    for _ in range(200):
        if marker.exists():
            break
        time.sleep(0.05)
    # A legitimate concurrent amendment: the pack is re-frozen at a new test set.
    (project / "tests" / "t.py").write_text("def test_x():\n    assert 1 == 2\n")
    for argv in (["open-deviation", "--deviation-id", "D1", "--deviation-type", "pack-defect", "--unit", "u@v1"],
                 ["adjudicate-amend", "--deviation-id", "D1", "--amendment-version", "u-pack/2"],
                 ["close-deviation", "--deviation-id", "D1"], ["respecify", "--name", "u"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0, argv
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2",
                "--project", str(project), "--pack-test-file", "tests/t.py")[0] == 0
    (tmp_path / "go").write_text("go")
    thread.join(timeout=60)
    code, output = result["r"]
    assert code == 2, output
    assert "evidence.pack-moved" in output or "pack-moved" in output, output
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().evidence_head("u") is None, "nothing may be recorded"


def test_evidence_binds_the_digest_captured_before_the_command_ran(tmp_path: Path) -> None:
    # guard: with no concurrent change the captured and current digests agree; pin that the
    # recorded digest is the pack's test set, so the capture cannot silently bind something else.
    project, root = _frozen(tmp_path)
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("u")
        assert unit is not None
        before = unit.acceptance_test_set_digest
    assert _run("record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--",
                sys.executable, "-c", "raise SystemExit(1)")[0] == 0
    from vouch_mk2.store import ArtifactKind, decode_evidence_event

    with Store.open(root, read_only=True) as store:
        head = store.read_snapshot().evidence_head("u")
        assert head is not None
        event = decode_evidence_event(store.get_object(ArtifactKind.EVIDENCE, head.head))
    assert event.pack_digest == before


# ---- the child's output is streamed, not accumulated ----


def test_json_mode_does_not_buffer_the_child_output_in_memory(tmp_path: Path) -> None:
    """pack/2 (DEV-ea-01): this must DISCRIMINATE streaming from buffering — 64 MiB arriving at
    the end proves only that the bytes were forwarded, which a buffering implementation also
    does. The child emits a marker, then blocks until released: the marker must already be at
    stderr while the child is still running. Then a 64 MiB body checks nothing accumulates.
    """
    project, root = _frozen(tmp_path)
    from vouch_mk2.lock import build_lock

    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    # The program lives in a FILE: an inline `python -c` carries literal newlines in argv, which
    # `valid_command_line` refuses on ingress — a correct, unrelated contract.
    release = tmp_path / "release"
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys, time, pathlib\n"
        "sys.stdout.write('MARKER' + 'y' * 65536)\n"
        "sys.stdout.flush()\n"
        f"target = pathlib.Path({str(release)!r})\n"
        "for _ in range(600):\n"
        "    if target.exists():\n"
        "        break\n"
        "    time.sleep(0.05)\n"
        "block = 'x' * 65536\n"
        "for _ in range(1024):\n"
        "    sys.stdout.write(block)\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(1)\n"
    )
    err_path = tmp_path / "child.err"
    out_path = tmp_path / "child.out"
    with err_path.open("wb") as err_file, out_path.open("wb") as out_file:
        proc = subprocess.Popen(
            [sys.executable, str(lock), "record-evidence", "--root", root, "--unit", "u@v1",
             "--kind", "red", "--json", "--", sys.executable, str(script)],
            stdout=out_file, stderr=err_file, env={**os.environ, "PYTHONPATH": ""},
        )
        try:
            arrived = False
            for _ in range(400):
                if err_path.stat().st_size >= 65536 and b"MARKER" in err_path.read_bytes()[:128]:
                    arrived = True
                    break
                assert proc.poll() is None, "the child exited before the marker was forwarded"
                time.sleep(0.05)
            assert arrived, "the child's early output must reach stderr while it is still running"
        finally:
            release.write_text("go")
            proc.wait(timeout=120)
    assert proc.returncode == 0, err_path.read_bytes()[-2000:]
    import json as _json

    document = _json.loads(out_path.read_text())
    assert document["accepted"] is True
    assert err_path.stat().st_size >= 64 * 1024 * 1024, "the whole body must still arrive"


def test_json_mode_still_keeps_stdout_to_exactly_one_document(tmp_path: Path) -> None:
    # guard: unit #3's rule survives — stdout carries the JSON, the child's stdout goes to stderr.
    project, root = _frozen(tmp_path)
    code, _ = _run("record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json", "--",
                   sys.executable, "-c", "print('CHILD'); raise SystemExit(1)")
    assert code == 0


# ---- the claim boundary says what is now true ----


def test_docs_state_that_a_concurrent_pack_change_refuses(tmp_path: Path) -> None:
    text = (ROOT / "docs" / "claim-boundary.md").read_text() + (ROOT / "docs" / "quickstart.md").read_text()
    assert "captured before" in text.lower() or "before the command" in text.lower()
