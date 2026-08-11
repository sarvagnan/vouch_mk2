from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vouch_mk2.core.model import Snapshot
from vouch_mk2.store import (
    ArtifactKind,
    CasConflict,
    IntegrityError,
    Store,
    StoreError,
)


def test_init_open_read_round_trip(tmp_path: Path) -> None:
    root = str(tmp_path / "store")
    with Store.init(root) as initialized:
        assert initialized.read_snapshot() == Snapshot()
    with Store.open(root) as reopened:
        assert reopened.read_snapshot() == Snapshot()


def test_put_get_object_round_trip(tmp_path: Path) -> None:
    payload = b"evidence bytes"
    with Store.init(str(tmp_path / "store")) as store:
        digest = store.put_object(ArtifactKind.OBJECT, payload)
        assert store.get_object(ArtifactKind.OBJECT, digest) == payload


def test_corrupted_object_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        digest = store.put_object(ArtifactKind.OBJECT, b"original")
        (root / "objects" / "sha256" / digest).write_bytes(b"corrupt")
        with pytest.raises(IntegrityError):
            store.get_object(ArtifactKind.OBJECT, digest)


def test_reput_is_idempotent(tmp_path: Path) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        first = store.put_object(ArtifactKind.OBJECT, b"same")
        second = store.put_object(ArtifactKind.OBJECT, b"same")
        assert second == first


def test_reput_detects_tampered_existing_object(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        digest = store.put_object(ArtifactKind.OBJECT, b"same")
        (root / "objects" / "sha256" / digest).write_bytes(b"tampered")
        with pytest.raises(IntegrityError):
            store.put_object(ArtifactKind.OBJECT, b"same")


def test_cas_conflict_has_no_partial_state_effect(tmp_path: Path) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        committed = Snapshot(revision=1)
        store.commit(committed)
        state_bytes = state_path(tmp_path).read_bytes()

        with pytest.raises(CasConflict):
            store.commit(Snapshot(revision=1))

        assert store.read_snapshot() == committed
        assert state_path(tmp_path).read_bytes() == state_bytes


def state_path(tmp_path: Path) -> Path:
    return tmp_path / "store" / "state.json"


def test_oversized_artifact_is_refused_before_allocation(tmp_path: Path) -> None:
    root = str(tmp_path / "store")
    with Store.init(root):
        pass
    with Store.open(root, max_artifact_bytes=8) as bounded:
        with pytest.raises(IntegrityError, match="exceeds the 8-byte artifact bound"):
            bounded.read_snapshot()


def test_open_sweeps_orphaned_temp_files(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)):
        pass
    orphan_state = root / ".state-deadbeef.tmp"
    orphan_object = root / "objects" / "sha256" / ".object-deadbeef.tmp"
    orphan_state.write_bytes(b"orphan")
    orphan_object.write_bytes(b"orphan")
    with Store.open(str(root)) as store:
        assert store.read_snapshot() == Snapshot()
    assert not orphan_state.exists()
    assert not orphan_object.exists()


def test_permission_flip_fails_typed_and_leaves_state_intact(tmp_path: Path) -> None:
    root = tmp_path / "store"
    sha_dir = root / "objects" / "sha256"
    with Store.init(str(root)) as store:
        sha_dir.chmod(0o500)
        try:
            with pytest.raises(StoreError):
                store.commit(
                    Snapshot(revision=1), ((ArtifactKind.OBJECT, b"payload"),)
                )
        finally:
            sha_dir.chmod(0o700)
        assert store.read_snapshot() == Snapshot()
        store.commit(Snapshot(revision=1), ((ArtifactKind.OBJECT, b"payload"),))
        assert store.read_snapshot().revision == 1


def test_state_symlink_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "store"
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    with Store.init(str(root)) as store:
        os.unlink(root / "state.json")
        os.symlink(target, root / "state.json")
        with pytest.raises(IntegrityError):
            store.read_snapshot()


def test_concurrent_cas_contention_has_no_lost_updates(tmp_path: Path) -> None:
    root = str(tmp_path / "store")
    with Store.init(root):
        pass
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    conflicts = 0
    conflict_lock = threading.Lock()

    # Separate handles exercise filesystem locking rather than Python object
    # locking; the barrier forces every worker to read revision 0 before any
    # commit lands, so contention is an asserted fact, not a scheduling accident.
    def commit_once() -> None:
        nonlocal conflicts
        with Store.open(root) as store:
            current = store.read_snapshot()
            barrier.wait()
            while True:
                try:
                    store.commit(Snapshot(revision=current.revision + 1))
                except CasConflict:
                    with conflict_lock:
                        conflicts += 1
                    current = store.read_snapshot()
                    continue
                return

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(commit_once) for _ in range(worker_count)]
        for future in futures:
            future.result()

    with Store.open(root) as final_store:
        assert final_store.read_snapshot().revision == worker_count
    assert conflicts >= worker_count - 1
