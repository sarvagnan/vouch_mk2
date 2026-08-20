"""Frozen acceptance pack for self-hosted unit evidence-command-safety@1 (D-21).

Every test here failed against the pre-change tree except the test marked ``guard``.
"""

from __future__ import annotations

import io
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core import EvidenceEvent
from vouch_mk2.store import ArtifactKind, Store, decode_evidence_event

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _frozen(tmp_path: Path) -> str:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_u.py").write_text(
        "def test_u():\n    assert False\n"
    )
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
        "tests/test_u.py",
    )[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    return root


def _evidence_event(root: str) -> EvidenceEvent:
    with Store.open(root, read_only=True) as store:
        head = store.read_snapshot().evidence_head("u")
        assert head is not None
        return decode_evidence_event(
            store.get_object(ArtifactKind.EVIDENCE, head.head)
        )


def test_invalid_command_descriptor_refuses_before_child_runs(tmp_path: Path) -> None:
    root = _frozen(tmp_path)
    marker = tmp_path / "spawned"
    child = [
        sys.executable,
        "-c",
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('spawned'); raise SystemExit(1)",
        str(marker),
        "x" * 4096,
    ]

    code, output = _run(
        "record-evidence",
        "--root",
        root,
        "--unit",
        "u@v1",
        "--kind",
        "red",
        "--",
        *child,
    )

    assert code == 2, output
    assert "evidence.command-line-invalid" in output
    assert not marker.exists(), "an unrecordable command must never be spawned"
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().evidence_head("u") is None


def test_redacted_argument_executes_original_argv_but_is_absent_from_the_store(
    tmp_path: Path,
) -> None:
    root = _frozen(tmp_path)
    marker = tmp_path / "child-saw"
    secret = "SECRET_SHOULD_NEVER_BE_STORED_123456789"
    child = [
        sys.executable,
        "-c",
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); raise SystemExit(1)",
        str(marker),
        secret,
    ]

    code, output = _run(
        "record-evidence",
        "--root",
        root,
        "--unit",
        "u@v1",
        "--kind",
        "red",
        "--redact-arg",
        "4",
        "--",
        *child,
    )

    assert code == 0, output
    assert marker.read_text() == secret, "redaction must not rewrite the spawned argv"
    event = _evidence_event(root)
    assert "<redacted>" in event.command_line
    assert secret not in event.command_line
    assert event.command_line == shlex.join([*child[:4], "<redacted>"])
    for path in Path(root).rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path


def test_invalid_redaction_positions_refuse_before_child_runs(tmp_path: Path) -> None:
    for case, selections in (
        ("executable", ("0",)),
        ("negative", ("-1",)),
        ("out-of-range", ("4",)),
        ("duplicate", ("3", "3")),
    ):
        case_dir = tmp_path / case
        case_dir.mkdir()
        root = _frozen(case_dir)
        marker = case_dir / "spawned"
        child = [
            sys.executable,
            "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('spawned'); raise SystemExit(1)",
            str(marker),
        ]
        redactions = [item for index in selections for item in ("--redact-arg", index)]

        code, output = _run(
            "record-evidence",
            "--root",
            root,
            "--unit",
            "u@v1",
            "--kind",
            "red",
            *redactions,
            "--",
            *child,
        )

        assert code == 2, (case, output)
        assert "evidence.redaction-invalid" in output, (case, output)
        assert not marker.exists(), case
        with Store.open(root, read_only=True) as store:
            assert store.read_snapshot().evidence_head("u") is None


def test_unredacted_valid_command_preserves_the_exact_descriptor(tmp_path: Path) -> None:
    # guard: the opt-in safety seam must not change the ordinary descriptor.
    root = _frozen(tmp_path)
    child = [sys.executable, "-c", "raise SystemExit(1)"]

    code, output = _run(
        "record-evidence",
        "--root",
        root,
        "--unit",
        "u@v1",
        "--kind",
        "red",
        "--",
        *child,
    )

    assert code == 0, output
    assert _evidence_event(root).command_line == shlex.join(child)


def test_docs_define_the_redaction_boundary() -> None:
    text = (
        (ROOT / "docs" / "claim-boundary.md").read_text()
        + "\n"
        + (ROOT / "docs" / "quickstart.md").read_text()
    ).lower()

    assert "--redact-arg" in text
    assert "zero-based" in text and "argv[0]" in text
    assert "<redacted>" in text
    assert "no digest" in text or "does not retain a digest" in text
    assert "original" in text and "child" in text
    assert "child output" in text and "not redacted" in text
    assert "environment" in text and "secret" in text
