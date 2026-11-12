"""Guard the decision log's pinned bytes, allowing only section-tail appends.

This catches drift, not deliberate tampering: an edit together with a hand-edited
pins file passes. Only review of the diff catches that case (D-29).
The guard uses top-level section and fence rules rather than rendering Markdown;
crafted lines on which it and a renderer disagree can create editable boundaries
inside entries, and review of the diff is the control for those lines too (D-29).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Never, cast


ROOT = Path(__file__).resolve().parents[1]
ENTRY = re.compile(r"^## D-(\d+)\b", re.MULTILINE)
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class Pin:
    key: str
    size: int
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "bytes": self.size, "sha256": self.digest}


def split_sections(raw: bytes) -> tuple[list[tuple[str, bytes]], str | None]:
    # Decode without newline translation or BOM stripping; re-encoding is lossless.
    text = raw.decode("utf-8")
    sections: list[tuple[str, bytes]] = []
    key, start = "header", 0
    offset = 0
    closing: re.Pattern[str] | None = None
    # Split only at LF, as with ENTRY's multiline anchors, preserving section
    # bytes through lossless UTF-8 re-encoding. Strip CR only for fence recognition.
    for line in text.split("\n"):
        content = line.removesuffix("\r")
        if closing is not None:
            if closing.fullmatch(content) is not None:
                closing = None
        elif (
            (fence := FENCE.match(content)) is not None
            and (fence.group(1)[0] != "`" or "`" not in content[fence.end() :])
        ):
            run = fence.group(1)
            closing = re.compile(rf" {{0,3}}{run[0]}{{{len(run)},}}[ \t]*")
        elif (match := ENTRY.match(line)) is not None:
            sections.append((key, text[start:offset].encode("utf-8")))
            key, start = f"D-{match.group(1)}", offset
        offset += len(line) + 1
    sections.append((key, text[start:].encode("utf-8")))
    return sections, key if closing is not None else None


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> Never:
    raise ValueError(f"invalid JSON constant: {value}")


def read_pins(path: Path, *, allow_missing: bool) -> list[Pin]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if allow_missing:
            return []
        raise
    data: object = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
    )
    if not isinstance(data, dict):
        raise ValueError("pins must be a JSON object")
    document = cast(dict[str, object], data)
    if type(document.get("format")) is not int or document["format"] != 1:
        raise ValueError("pins format must be 1")
    items = document.get("sections")
    if not isinstance(items, list):
        raise ValueError("pins sections must be a list")
    pins: list[Pin] = []
    seen: set[str] = set()
    for item in cast(list[object], items):
        if not isinstance(item, dict):
            raise ValueError("each pin must be an object")
        entry = cast(dict[str, object], item)
        key, size, digest = entry.get("key"), entry.get("bytes"), entry.get("sha256")
        if not isinstance(key, str) or re.fullmatch(r"header|D-\d+", key) is None:
            raise ValueError("pin key must name header or D-<n>")
        if key in seen:
            raise ValueError(f"duplicate pin: {key}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(f"{key}: pin bytes must be a nonnegative integer")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"{key}: pin sha256 must be 64 lowercase hex digits")
        seen.add(key)
        pins.append(Pin(key, size, digest))
    return pins


def problems(sections: list[tuple[str, bytes]], pins: list[Pin]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for number, (key, _) in enumerate(sections[1:], start=1):
        if key != f"D-{number}":
            found.append((key, f"out of order (expected D-{number})"))
    keys = {key for key, _ in sections}
    for pin in pins:
        if pin.key not in keys:
            found.append((pin.key, "missing"))
    by_key = {pin.key: pin for pin in pins}
    for key, raw in sections:
        pin = by_key.get(key)
        if pin is None:
            found.append((key, "not pinned"))
            continue
        if len(raw) < pin.size or hashlib.sha256(raw[: pin.size]).hexdigest() != pin.digest:
            found.append((key, "pinned text changed"))
        if len(raw) > pin.size:
            found.append((key, "unpinned text"))
    return found


def write_pins(path: Path, sections: list[tuple[str, bytes]]) -> None:
    pins = [Pin(key, len(raw), hashlib.sha256(raw).hexdigest()) for key, raw in sections]
    data = {"format": 1, "sections": [pin.as_dict() for pin in pins]}
    raw = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "pin"))
    parser.add_argument("--log", type=Path, default=ROOT / "DECISIONS.md")
    parser.add_argument("--pins", type=Path, default=ROOT / "DECISIONS.pins.json")
    arguments = parser.parse_args(argv)
    log = cast(Path, arguments.log)
    pins_path = cast(Path, arguments.pins)
    pinning = arguments.command == "pin"
    try:
        with ExitStack() as stack:
            if pinning:
                # The directory stays stable across atomic saves of the log or
                # pins. Close on success or failure, after any pins replacement.
                directory = os.open(pins_path.parent, os.O_RDONLY)
                stack.callback(os.close, directory)
                fcntl.flock(directory, fcntl.LOCK_EX)
            stream = stack.enter_context(log.open("rb"))
            sections, unclosed = split_sections(stream.read())
            pins = read_pins(pins_path, allow_missing=pinning)
            found = problems(sections, pins)
            if unclosed is not None:
                found.append((unclosed, "unclosed code fence"))
            if pinning:
                refused = [item for item in found if item[1] not in ("unpinned text", "not pinned")]
                if not refused:
                    if found:
                        write_pins(pins_path, sections)
                    return 0
                found = refused
            for key, reason in found:
                print(f"{key}: {reason}")
            return 1 if found else 0
    except (OSError, ValueError, RecursionError) as error:
        print(f"{parser.prog}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
