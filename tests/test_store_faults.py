from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from vouch_mk2.core import Accepted, Snapshot, SpecifyUnit, decide
from vouch_mk2.store import (
    COMMIT_STEPS,
    ArtifactKind,
    Store,
    digest_of,
)
from vouch_mk2.store.event_log import build_event_records, commit_events


class CrashError(Exception):
    pass


_OLD = Snapshot()
_DECISION = decide(_OLD, SpecifyUnit("unit", "1"))
assert isinstance(_DECISION, Accepted)
_NEW, _EVENT_OBJECTS, _RECORDS = build_event_records(
    _OLD,
    _DECISION.events,
    actor="test",
    run_id="",
    recorded_at="2026-08-18T00:00:00+00:00",
)
_PAYLOAD = _EVENT_OBJECTS[0][1]
_OBJECT_DIGEST = digest_of(ArtifactKind.EVENT, _PAYLOAD)


def _raise_at(step_to_fail: str, error: BaseException) -> Callable[[str], None]:
    def callback(step_id: str) -> None:
        if step_id == step_to_fail:
            raise error

    return callback


def _assert_visible_invariant(root: Path, step: str) -> None:
    after_replace = COMMIT_STEPS.index(step) >= COMMIT_STEPS.index("state-replaced")
    with Store.open(str(root)) as reopened:
        assert reopened.read_snapshot() == (_NEW if after_replace else _OLD)

        object_directory = root / "objects" / "sha256"
        for object_path in object_directory.iterdir():
            payload = object_path.read_bytes()
            assert object_path.name == digest_of(ArtifactKind.EVENT, payload)

        if after_replace:
            assert reopened.get_object(ArtifactKind.EVENT, _OBJECT_DIGEST) == _PAYLOAD

        # Raised exceptions model abrupt failure at completed-operation boundaries.
        # The finally path may remove only an unplaced temp and must release flock;
        # this next commit proves the latter within the same process.
        current = reopened.read_snapshot()
        decision = decide(current, SpecifyUnit("next", "1"))
        assert isinstance(decision, Accepted)
        commit_events(
            reopened,
            current,
            decision.events,
            actor="test",
            run_id="",
        )


def test_normal_commit_emits_the_declared_steps_in_order(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    with Store.init(str(root)):
        pass
    seen: list[str] = []
    with Store.open(str(root), on_step=seen.append) as store:
        store.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
    assert tuple(seen) == COMMIT_STEPS


def test_put_object_emits_its_declared_steps_in_order(tmp_path: Path) -> None:
    seen: list[str] = []
    with Store.init(str(tmp_path / "put"), on_step=seen.append) as store:
        with store.write_locked():
            store.put_object(ArtifactKind.OBJECT, _PAYLOAD)
    assert tuple(seen) == (
        "lock-acquired",
        *COMMIT_STEPS[2:6],
        "lock-released",
    )


@pytest.mark.parametrize("step", COMMIT_STEPS)
def test_crash_at_every_commit_boundary_preserves_invariants(
    tmp_path: Path,
    step: str,
) -> None:
    root = tmp_path / step
    with Store.init(str(root)):
        pass
    with Store.open(
        str(root),
        on_step=_raise_at(step, CrashError(step)),
    ) as failing:
        with pytest.raises(CrashError, match=step):
            failing.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
    _assert_visible_invariant(root, step)


@pytest.mark.parametrize(
    "step",
    tuple(step for step in COMMIT_STEPS if "fsync" in step),
)
def test_fsync_failure_at_every_fsync_boundary_preserves_invariants(
    tmp_path: Path,
    step: str,
) -> None:
    root = tmp_path / step
    with Store.init(str(root)):
        pass
    with Store.open(
        str(root),
        on_step=_raise_at(step, OSError(step)),
    ) as failing:
        with pytest.raises(OSError, match=step):
            failing.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
    _assert_visible_invariant(root, step)


# The on_step seam fires AFTER a successful operation; the tests below instead
# make the primitive itself fail, so a regression that swallowed an fsync error
# or mishandled a short write cannot stay green.


@pytest.mark.parametrize("failing_call", (1, 2, 3, 4))
def test_genuine_fsync_failure_preserves_invariants(
    tmp_path: Path,
    failing_call: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Commit performs exactly four fsyncs in order: object temp, objects dir,
    # state temp, root dir. Fail each one AT the primitive.
    root = tmp_path / f"fsync-{failing_call}"
    with Store.init(str(root)):
        pass

    real_fsync = os.fsync
    calls = 0
    active = False

    def flaky_fsync(fd: int) -> None:
        nonlocal calls
        if active:
            calls += 1
            if calls == failing_call:
                raise OSError(5, "injected fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)
    with Store.open(str(root)) as failing:
        active = True
        with pytest.raises(OSError, match="injected fsync failure"):
            failing.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
        active = False
    monkeypatch.undo()

    replaced = failing_call == 4  # only the root fsync follows the replace
    with Store.open(str(root)) as reopened:
        assert reopened.read_snapshot() == (_NEW if replaced else _OLD)
        for object_path in (root / "objects" / "sha256").iterdir():
            payload = object_path.read_bytes()
            assert object_path.name == digest_of(ArtifactKind.EVENT, payload)
        current = reopened.read_snapshot()
        decision = decide(current, SpecifyUnit("next", "1"))
        assert isinstance(decision, Accepted)
        commit_events(
            reopened,
            current,
            decision.events,
            actor="test",
            run_id="",
        )


def test_short_writes_are_completed_by_the_write_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "short-write"
    with Store.init(str(root)):
        pass

    real_write = os.write

    def dribble(fd: int, data: bytes) -> int:
        return real_write(fd, bytes(memoryview(data)[:1]))

    with Store.open(str(root)) as store:
        monkeypatch.setattr(os, "write", dribble)
        store.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
        monkeypatch.undo()
        assert store.read_snapshot() == _NEW
        assert store.get_object(ArtifactKind.EVENT, _OBJECT_DIGEST) == _PAYLOAD


def test_zero_progress_write_raises_and_leaves_state_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "stalled-write"
    with Store.init(str(root)):
        pass

    def stalled(fd: int, data: bytes) -> int:
        return 0

    with Store.open(str(root)) as store:
        monkeypatch.setattr(os, "write", stalled)
        with pytest.raises(OSError, match="short write made no progress"):
            store.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
        monkeypatch.undo()
        assert store.read_snapshot() == _OLD
        store.commit(_NEW, ((ArtifactKind.EVENT, _PAYLOAD),))
        assert store.read_snapshot() == _NEW
