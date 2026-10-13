"""Non-pack regressions for D-25's exact-reference exception replay."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vouch_mk2.core import commands, events
from vouch_mk2.core.decide import Accepted, Refused, decide
from vouch_mk2.core.model import DeviationType, UnitRef
from vouch_mk2.store import Store
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import merge_store_roots


def _record(root: Path, recorded: tuple[events.Event, ...]) -> int:
    with Store.open(str(root)) as store:
        with store.write_locked():
            snapshot = commit_events(
                store, store.read_snapshot(), recorded, actor="test", run_id=""
            )
    return snapshot.revision


def _execute(root: Path, command: commands.Command) -> None:
    with Store.open(str(root), read_only=True) as store:
        decision = decide(store.read_snapshot(), command)
    assert isinstance(decision, Accepted), decision
    _record(root, decision.events)


def _branches(tmp_path: Path) -> tuple[Path, Path]:
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    Store.init(str(ours)).close()
    _execute(ours, commands.SpecifyUnit("moving", "1"))
    _execute(ours, commands.FreezeAcceptance("moving", "pack/1"))
    _execute(ours, commands.MarkOpenInCi("moving", "shared version 1 reason"))
    shutil.copytree(ours, theirs)
    return ours, theirs


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("clear", (False, True), ids=("mark", "clear"))
def test_bare_name_exception_replay_is_refused_without_writes(
    tmp_path: Path, clear: bool
) -> None:
    ours, theirs = _branches(tmp_path)
    # Commit a coherent history in the short-lived pre-fix event encoding.
    source_event = (
        events.OpenInCiCleared("moving")
        if clear
        else events.OpenInCiMarked("moving", "legacy bare-name reason")
    )
    revision = _record(theirs, (source_event,))
    _execute(ours, commands.SpecifyUnit("ours", "1"))
    (ours / ".lock").unlink()
    before_ours, before_theirs = _files(ours), _files(theirs)

    result = merge_store_roots(str(ours), str(theirs), actor="test", run_id="")

    assert isinstance(result, Refused)
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.code == "merge.conflict"
    assert type(source_event).__name__ in violation.detail
    assert f"revision {revision}" in violation.detail
    assert "name='moving'" in violation.detail
    assert "moving@1" in violation.detail
    assert "unit.unknown" not in violation.detail
    assert _files(ours) == before_ours
    assert _files(theirs) == before_theirs


def test_clear_replay_never_removes_a_successor_exception(tmp_path: Path) -> None:
    ours, theirs = _branches(tmp_path)
    _execute(theirs, commands.ClearOpenInCi("moving"))
    with Store.open(str(theirs), read_only=True) as store:
        revision = store.read_snapshot().revision
    _execute(
        ours,
        commands.OpenDeviation("move-on", DeviationType.PACK_DEFECT, UnitRef("moving", "1")),
    )
    _execute(ours, commands.Supersede("moving"))
    _execute(ours, commands.SpecifyUnit("moving", "2"))
    _execute(ours, commands.FreezeAcceptance("moving", "pack/2"))
    _execute(ours, commands.MarkOpenInCi("moving", "keep version 2 reason"))
    (ours / ".lock").unlink()
    before_ours, before_theirs = _files(ours), _files(theirs)

    result = merge_store_roots(str(ours), str(theirs), actor="test", run_id="")

    assert isinstance(result, Refused)
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.code == "merge.conflict"
    assert "OpenInCiCleared" in violation.detail
    assert f"revision {revision}" in violation.detail
    assert "moving@1" in violation.detail
    assert "moving@2" in violation.detail
    assert "unit.unknown" not in violation.detail
    assert _files(ours) == before_ours
    assert _files(theirs) == before_theirs
