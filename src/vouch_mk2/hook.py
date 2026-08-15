"""Warn-only edit hook support, rooted exclusively from the edited target."""

from __future__ import annotations

import json
import os
import stat
from typing import cast

from vouch_mk2.checks import run_checks
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import Store, StoreError
from vouch_mk2.verify import run_artifact_checks


class HookError(StoreError):
    """A hook target or hook payload could not be resolved safely."""


def resolve_root(edit_target: str) -> str | None:
    """Find the nearest store above an edit target without consulting cwd.

    The walk stops at the first repository boundary (a directory containing
    ``.git``) it crosses WITHOUT finding a store: a store belongs to a
    repository, so an unrelated ``.vouch`` planted higher up (``$HOME/.vouch``,
    ``/tmp/.vouch``) must never be picked up for an edit inside a project that
    has none. A ``.vouch`` found at or below the boundary wins."""
    target = os.path.abspath(edit_target)
    start = target if os.path.isdir(target) else os.path.dirname(target)
    directory = start
    while True:
        candidate = os.path.join(directory, ".vouch")
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise HookError(f"{candidate}: cannot inspect store: {error}") from error
        else:
            if stat.S_ISLNK(info.st_mode):
                raise HookError(f"{candidate}: symlinked .vouch store refused")
            if stat.S_ISDIR(info.st_mode):
                state_path = os.path.join(candidate, "state.json")
                try:
                    state_info = os.lstat(state_path)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise HookError(
                        f"{state_path}: cannot inspect state: {error}"
                    ) from error
                else:
                    if stat.S_ISREG(state_info.st_mode):
                        return candidate
        # Repository boundary without a store: stop here, do not climb into
        # ancestor projects or the home directory.
        if os.path.lexists(os.path.join(directory, ".git")):
            return None
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def edit_target_from_json(data: str) -> str | None:
    """Decode Claude Code's PostToolUse ``tool_input.file_path`` payload."""
    if not data.strip():
        return None
    try:
        decoded: object = json.loads(data)
    except json.JSONDecodeError as error:
        raise HookError(f"invalid hook JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise HookError("hook JSON must be an object")
    document = cast(dict[object, object], decoded)
    tool_input = document.get("tool_input")
    if tool_input is None:
        return None
    if not isinstance(tool_input, dict):
        raise HookError("hook JSON tool_input must be an object")
    fields = cast(dict[object, object], tool_input)
    path = fields.get("file_path")
    if path is None:
        return None
    if not isinstance(path, str):
        raise HookError("hook JSON tool_input.file_path must be a string")
    return path or None


def hook_message(edit_target: str) -> str:
    root = resolve_root(edit_target)
    if root is None:
        return f"vouch: no store above {edit_target}; skipping"
    # Read-only: no orphan sweep, no exclusive lock — a store held by a
    # concurrent writer must never wedge the editor ("never blocks", D-7).
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        report = run_checks(
            snapshot,
            artifact_results=run_artifact_checks(store, snapshot),
        )
    failures = sum(
        result.verdict is Verdict.FAIL for result in report.mechanical
    )
    return (
        f"vouch: {report.composed().value} ({failures} mechanical FAIL rows) "
        f"— run `vouch-mk2 check --root {root}` for detail"
    )


__all__ = [
    "HookError",
    "edit_target_from_json",
    "hook_message",
    "resolve_root",
]

