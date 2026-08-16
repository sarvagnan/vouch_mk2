"""Validated relative paths for capability-rooted store operations.

No Phase 2 code path takes a caller-supplied relative path (state.json, the
lock, and digest-named objects are all fixed names), so ``RelPath`` currently
has no production consumer. It is defined and tested here, with the rest of the
capability discipline, because Phase 5 (scope inventories, bundles) and Phase 8
(``vouch init``) take caller paths and MUST route them through this type."""

from __future__ import annotations

from dataclasses import dataclass

from vouch_mk2.store.errors import PathError


# 255 is the common NAME_MAX on the supported Unix filesystems. Rejecting longer
# names here gives callers a deterministic typed failure before touching the disk.
MAX_SEGMENT_LENGTH = 255


@dataclass(frozen=True, slots=True)
class RelPath:
    """A non-empty, slash-separated path that cannot escape a capability root."""

    segments: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> RelPath:
        if text == "":
            raise PathError("path: empty input")
        if text.startswith("/"):
            raise PathError("path: absolute paths are forbidden")
        if "\\" in text:
            raise PathError("path: backslash is forbidden")
        if "\x00" in text:
            raise PathError("path: NUL byte is forbidden")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise PathError("path: must be valid UTF-8 without surrogate code points") from error

        segments = tuple(text.split("/"))
        for index, segment in enumerate(segments):
            path = f"segments[{index}]"
            if segment == "":
                raise PathError(f"{path}: empty segment")
            if segment in {".", ".."}:
                raise PathError(f"{path}: traversal segment {segment!r} is forbidden")
            if len(segment) > MAX_SEGMENT_LENGTH:
                raise PathError(
                    f"{path}: exceeds {MAX_SEGMENT_LENGTH}-character limit"
                )
        return cls(segments=segments)
