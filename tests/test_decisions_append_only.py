"""Acceptance pack for decisions-append-only@1 (D-29).

The decision log is append-only within each entry. `scripts/decisions_guard.py` pins a digest
of every section of the log and fails when pinned text changes. These tests drive the script
as a black box on fixture logs, and once on mk2's own log.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
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
    assert "never an edit" not in header
    assert re.search(r"(?m)^## D-29 — The decision log is append-only within each entry", log)
