"""Acceptance pack for decisions-append-only@1 (D-29).

The decision log is append-only within each entry. `scripts/decisions_guard.py` pins a digest
of every section of the log and fails when pinned text changes. These tests drive the script
as a black box on fixture logs, and once on mk2's own log.
"""

from __future__ import annotations

import ast
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "decisions_guard.py"

HEADER = "# Example decision log\n\nAppend-only.\n\n"
E1 = "## D-1 — First (2026-01-01)\n\n- **Decision.** One.\n\n"
E2 = "## D-2 — Second (2026-01-02)\n\n- **Decision.** Two, with an — em dash.\n\n"
E3 = "## D-3 — Third (2026-01-03)\n\n- **Decision.** Three.\n"
BASE = HEADER + E1 + E2 + E3

CHANGED = "pinned text changed"


def _run(command: str, log: Path, pins: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--log", str(log), "--pins", str(pins)],
        capture_output=True,
        text=True,
        cwd=log.parent,
        check=False,
    )


def _out(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _sections(pins: Path) -> list[dict[str, object]]:
    data = cast(dict[str, object], json.loads(pins.read_text(encoding="utf-8")))
    assert data["format"] == 1
    return cast(list[dict[str, object]], data["sections"])


def _expected(key: str, text: str) -> dict[str, object]:
    raw = text.encode("utf-8")
    return {"key": key, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def pinned(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "DECISIONS.md"
    pins = tmp_path / "DECISIONS.pins.json"
    log.write_text(BASE, encoding="utf-8")
    result = _run("pin", log, pins)
    assert result.returncode == 0, _out(result)
    return log, pins


# R1
def test_a_pinned_log_verifies_and_the_pins_cover_every_section(
    pinned: tuple[Path, Path],
) -> None:
    log, pins = pinned
    result = _run("verify", log, pins)
    assert result.returncode == 0, _out(result)
    assert _sections(pins) == [
        _expected("header", HEADER),
        _expected("D-1", E1),
        _expected("D-2", E2),
        _expected("D-3", E3),
    ]
    before = pins.read_bytes()
    again = _run("pin", log, pins)
    assert again.returncode == 0, _out(again)
    assert pins.read_bytes() == before


# R2
@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("Two, with", "Too, with", "D-2"),
        ("One.\n", "One. \n", "D-1"),
        ("(2026-01-02)\n\n", "(2026-01-02)\n\nInserted line.\n\n", "D-2"),
        ("- **Decision.** Three.\n", "", "D-3"),
        ("Append-only.", "Append only.", "header"),
    ],
    ids=["word", "whitespace", "insertion", "truncation", "header"],
)
def test_changed_pinned_text_fails_naming_only_that_section(
    pinned: tuple[Path, Path], old: str, new: str, key: str
) -> None:
    log, pins = pinned
    assert BASE.count(old) == 1
    log.write_text(BASE.replace(old, new), encoding="utf-8")
    result = _run("verify", log, pins)
    out = _out(result)
    assert result.returncode == 1, out
    assert f"{key}: {CHANGED}" in out
    assert out.count(CHANGED) == 1


# R3
def test_an_amendment_appended_to_an_entry_is_accepted_once_pinned(
    pinned: tuple[Path, Path],
) -> None:
    log, pins = pinned
    amendment = "**D-2 amendment (2026-01-05, user decision):** Two and a half.\n\n"
    log.write_text(HEADER + E1 + E2 + amendment + E3, encoding="utf-8")
    before_pin = _run("verify", log, pins)
    assert before_pin.returncode == 1, _out(before_pin)
    assert "D-2: unpinned text" in _out(before_pin)
    assert CHANGED not in _out(before_pin)
    pinning = _run("pin", log, pins)
    assert pinning.returncode == 0, _out(pinning)
    after_pin = _run("verify", log, pins)
    assert after_pin.returncode == 0, _out(after_pin)
    assert _sections(pins)[2] == _expected("D-2", E2 + amendment)


# R4
def test_a_new_entry_fails_until_it_is_pinned(pinned: tuple[Path, Path]) -> None:
    log, pins = pinned
    e4 = "\n## D-4 — Fourth (2026-01-04)\n\n- **Decision.** Four.\n"
    log.write_text(BASE + e4, encoding="utf-8")
    before_pin = _run("verify", log, pins)
    assert before_pin.returncode == 1, _out(before_pin)
    assert "D-4: not pinned" in _out(before_pin)
    assert CHANGED not in _out(before_pin)
    pinning = _run("pin", log, pins)
    assert pinning.returncode == 0, _out(pinning)
    after_pin = _run("verify", log, pins)
    assert after_pin.returncode == 0, _out(after_pin)
    assert [section["key"] for section in _sections(pins)] == ["header", "D-1", "D-2", "D-3", "D-4"]


# R5
def test_pin_refuses_changed_text_and_writes_nothing(pinned: tuple[Path, Path]) -> None:
    log, pins = pinned
    before = pins.read_bytes()
    e4 = "\n## D-4 — Fourth (2026-01-04)\n\n- **Decision.** Four.\n"
    log.write_text(BASE.replace("One.", "Uno.") + e4, encoding="utf-8")
    refused = _run("pin", log, pins)
    assert refused.returncode == 1, _out(refused)
    assert f"D-1: {CHANGED}" in _out(refused)
    assert pins.read_bytes() == before
    still = _run("verify", log, pins)
    assert still.returncode == 1, _out(still)


# R6
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (HEADER + E1 + E3, "D-2: missing"),
        (HEADER + E1 + E3 + "\n" + E2, "out of order"),
        (HEADER + E1 + E2 + E2 + E3, "out of order"),
        (BASE.replace("## D-3 ", "## D-4 "), "out of order"),
    ],
    ids=["removed", "swapped", "duplicated", "renumbered"],
)
def test_a_missing_reordered_or_misnumbered_entry_fails(
    pinned: tuple[Path, Path], text: str, expected: str
) -> None:
    log, pins = pinned
    before = pins.read_bytes()
    log.write_text(text, encoding="utf-8")
    result = _run("verify", log, pins)
    assert result.returncode == 1, _out(result)
    assert expected in _out(result)
    refused = _run("pin", log, pins)
    assert refused.returncode == 1, _out(refused)
    assert pins.read_bytes() == before


# R7
def test_an_unreadable_log_or_pins_file_is_a_usage_error(tmp_path: Path) -> None:
    assert SCRIPT.is_file(), "a missing script also exits 2"
    log = tmp_path / "DECISIONS.md"
    pins = tmp_path / "DECISIONS.pins.json"
    log.write_text(BASE, encoding="utf-8")

    missing_pins = _run("verify", log, pins)
    assert missing_pins.returncode == 2, _out(missing_pins)
    assert not pins.exists()

    for bad in ("{", json.dumps({"format": 2, "sections": []}), json.dumps([1, 2])):
        pins.write_text(bad, encoding="utf-8")
        for command in ("verify", "pin"):
            result = _run(command, log, pins)
            assert result.returncode == 2, (bad, command, _out(result))
            assert pins.read_text(encoding="utf-8") == bad
    pins.unlink()

    log.unlink()
    for command in ("verify", "pin"):
        result = _run(command, log, pins)
        assert result.returncode == 2, (command, _out(result))
        assert not pins.exists()


# R8
def test_mk2_own_log_is_pinned_and_verifies_from_any_directory(tmp_path: Path) -> None:
    pins = ROOT / "DECISIONS.pins.json"
    assert pins.is_file()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "verify"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, _out(result)
    log = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    numbers = [int(n) for n in re.findall(r"(?m)^## D-(\d+)\b", log)]
    assert numbers == list(range(1, len(numbers) + 1))
    assert len(numbers) >= 29
    assert [section["key"] for section in _sections(pins)] == ["header"] + [
        f"D-{n}" for n in numbers
    ]


# R9
def test_the_guard_uses_only_the_standard_library() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported, "the guard imports nothing"
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# R10
def test_the_log_header_states_the_rule_and_d29_records_it() -> None:  # guard:
    log = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    header = log[: log.index("\n## D-1 ")]
    assert "Text already in the log never changes." in header
    assert "appended to the end of the entry it amends" in header
    assert "uv run python scripts/decisions_guard.py pin" in header
    assert re.search(r"(?m)^## D-29 — The decision log is append-only within each entry", log)


# R11
FENCED_NOTE = (
    "**D-3 note (2026-01-06, user):** An example heading for a later entry:\n\n"
    "```markdown\n## D-4 — Example only\n```\n\nThe example stays part of D-3.\n"
)


def test_a_fenced_heading_is_content_of_the_entry_that_quotes_it(
    pinned: tuple[Path, Path],
) -> None:
    log, pins = pinned
    log.write_text(BASE + "\n" + FENCED_NOTE, encoding="utf-8")
    pinning = _run("pin", log, pins)
    assert pinning.returncode == 0, _out(pinning)
    assert [section["key"] for section in _sections(pins)] == ["header", "D-1", "D-2", "D-3"]
    assert _run("verify", log, pins).returncode == 0
    inserted = (BASE + "\n" + FENCED_NOTE).replace(
        "```markdown\n## D-4", "```markdown\nInserted.\n## D-4"
    )
    log.write_text(inserted, encoding="utf-8")
    result = _run("verify", log, pins)
    assert result.returncode == 1, _out(result)
    assert f"D-3: {CHANGED}" in result.stdout


def test_a_fenced_example_of_an_earlier_heading_is_a_legal_amendment(
    pinned: tuple[Path, Path],
) -> None:
    log, pins = pinned
    note = "**D-1 note (2026-01-06, user):** Quoting its heading:\n\n~~~\n## D-1 — First\n~~~\n\n"
    log.write_text(HEADER + E1 + note + E2 + E3, encoding="utf-8")
    pinning = _run("pin", log, pins)
    assert pinning.returncode == 0, _out(pinning)
    result = _run("verify", log, pins)
    assert result.returncode == 0, _out(result)
    assert "out of order" not in _out(result)
    assert _sections(pins)[1] == _expected("D-1", E1 + note)


def test_an_unclosed_fence_fails_and_pin_refuses(pinned: tuple[Path, Path]) -> None:
    log, pins = pinned
    before = pins.read_bytes()
    log.write_text(BASE + "\n```\nnever closed\n", encoding="utf-8")
    result = _run("verify", log, pins)
    assert result.returncode == 1, _out(result)
    assert "D-3: unclosed code fence" in result.stdout
    refused = _run("pin", log, pins)
    assert refused.returncode == 1, _out(refused)
    assert pins.read_bytes() == before


# R12
def test_crlf_and_bom_bytes_are_pinned_exactly(tmp_path: Path) -> None:
    log = tmp_path / "DECISIONS.md"
    pins = tmp_path / "DECISIONS.pins.json"
    crlf = b"\xef\xbb\xbf" + BASE.replace("\n", "\r\n").encode("utf-8")
    log.write_bytes(crlf)
    pinning = _run("pin", log, pins)
    assert pinning.returncode == 0, _out(pinning)
    assert _run("verify", log, pins).returncode == 0
    assert _sections(pins)[0]["bytes"] == len(b"\xef\xbb\xbf" + HEADER.replace("\n", "\r\n").encode())
    before = pins.read_bytes()
    log.write_bytes(crlf.replace(b"\r\n", b"\n"))
    for command in ("verify", "pin"):
        result = _run(command, log, pins)
        assert result.returncode == 1, (command, _out(result))
        assert CHANGED in result.stdout
    assert pins.read_bytes() == before


# R13
def test_problems_go_to_stdout_and_usage_errors_to_stderr(pinned: tuple[Path, Path]) -> None:
    log, pins = pinned
    log.write_text(BASE.replace("One.", "Uno."), encoding="utf-8")
    problem = _run("verify", log, pins)
    assert problem.returncode == 1
    assert f"D-1: {CHANGED}" in problem.stdout
    assert problem.stderr == ""
    pins.write_text("{", encoding="utf-8")
    usage = _run("verify", log, pins)
    assert usage.returncode == 2
    assert usage.stdout == ""
    assert usage.stderr.strip() != ""


# R14
def _load_main(monkeypatch: pytest.MonkeyPatch) -> Callable[[Sequence[str] | None], int]:
    name = "decisions_guard_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return cast(Callable[[Sequence[str] | None], int], getattr(module, "main"))


def test_a_failed_replace_keeps_the_old_pins_and_leaves_no_temporary_file(
    pinned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    log, pins = pinned
    main = _load_main(monkeypatch)
    before = pins.read_bytes()
    log.write_text(BASE + "\n## D-4 — Fourth (2026-01-04)\n", encoding="utf-8")

    def refuse(src: object, dst: object) -> None:
        raise OSError("injected replace failure")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refuse)
        assert main(["pin", "--log", str(log), "--pins", str(pins)]) == 2
    assert pins.read_bytes() == before
    assert sorted(path.name for path in log.parent.iterdir()) == [
        "DECISIONS.md",
        "DECISIONS.pins.json",
    ]


# R15
def test_pin_waits_for_the_log_lock(pinned: tuple[Path, Path]) -> None:
    log, pins = pinned
    log.write_text(BASE + "\n## D-4 — Fourth (2026-01-04)\n", encoding="utf-8")
    with log.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "pin", "--log", str(log), "--pins", str(pins)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)
        assert process.poll() is None, "pin finished while another process held the log lock"
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stdout + stderr
    assert _run("verify", log, pins).returncode == 0
