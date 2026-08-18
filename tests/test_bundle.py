from __future__ import annotations

import os
from pathlib import Path

import pytest

from vouch_mk2.bundle import (
    InventoryError,
    InvalidExclusionPattern,
    bind_bundle,
    build_inventory,
    matches_exclusion,
)
from vouch_mk2.core import (
    Accepted,
    BindBundle,
    BundleInventory,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Refused,
    Snapshot,
    Unit,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import ArtifactKind, RelPath, Store, decode_bundle_inventory
from vouch_mk2.verify import run_artifact_checks
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


def test_d_pruning_exclusion_examples() -> None:
    assert not matches_exclusion(RelPath.parse("a/x"), "a/*/**")
    assert matches_exclusion(RelPath.parse("a/x/y"), "a/*/**")
    assert matches_exclusion(RelPath.parse("p/q/golden/r/s"), "**/golden/**")
    assert matches_exclusion(RelPath.parse("a/b"), "a/b")
    assert not matches_exclusion(RelPath.parse("a/b/c"), "a/b")


def test_mixed_double_star_is_refused() -> None:
    with pytest.raises(InvalidExclusionPattern):
        matches_exclusion(RelPath.parse("a/x"), "a**b")


def test_single_star_matches_within_one_path_segment() -> None:
    assert matches_exclusion(RelPath.parse("notes.txt"), "*.txt")
    assert matches_exclusion(RelPath.parse("build/app.min.js"), "build/*.js")
    assert not matches_exclusion(RelPath.parse("build/nested/app.js"), "build/*.js")


def test_build_inventory_truly_prunes_before_fifo_inspection(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    excluded = root / "x"
    excluded.mkdir(parents=True)
    os.mkfifo(excluded / "would-block")
    (root / "kept.txt").write_bytes(b"kept")
    inventory = build_inventory(str(root), ("x/**",))
    assert tuple(entry.path for entry in inventory.entries) == ("kept.txt",)


def test_build_inventory_refuses_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    target = root / "target"
    target.write_bytes(b"data")
    (root / "link").symlink_to(target)
    with pytest.raises(InventoryError, match="symlink refused"):
        build_inventory(str(root), ())


def test_build_and_bind_bundle_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"alpha")
    (source / "nested").mkdir()
    (source / "nested" / "b.txt").write_bytes(b"beta")
    inventory = build_inventory(str(source), ("ignored/**",))

    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        unit = Unit("u", "v1", UnitStatus.SPECIFIED)
        release = Release(
            release_id="r1",
            archetype=ReleaseArchetype.OPERATIONAL,
            state=ReleaseState.PREPARED,
            includes=(unit.ref(),),
            criteria=None,
            closure=None,
        )
        commit_snapshot_fixture(
            store, Snapshot(revision=1, units=(unit,), releases=(release,))
        )
        digest = bind_bundle(store, "r1", inventory)
        decoded = decode_bundle_inventory(
            store.get_object(ArtifactKind.BUNDLE_INVENTORY, digest)
        )
        assert decoded == inventory
        snapshot = store.read_snapshot()
        bound = snapshot.release("r1")
        assert bound is not None
        assert bound.bundle_inventory_digest == digest
        result = next(
            result
            for result in run_artifact_checks(store, snapshot)
            if result.check_id == mk2.CHECK_BUNDLE_INVENTORIES
        )
        assert result.verdict is Verdict.PASS


def test_corrupt_bound_bundle_inventory_fails_with_release(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a").write_bytes(b"a")
    inventory: BundleInventory = build_inventory(str(source), ())
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        unit = Unit("u", "v1", UnitStatus.SPECIFIED)
        commit_snapshot_fixture(
            store,
            Snapshot(
                revision=1,
                units=(unit,),
                releases=(
                    Release(
                        "r1",
                        ReleaseArchetype.OPERATIONAL,
                        ReleaseState.PREPARED,
                        (unit.ref(),),
                        None,
                        None,
                    ),
                ),
            ),
        )
        digest = bind_bundle(store, "r1", inventory)
    (root / "objects" / "sha256" / digest).write_bytes(b"corrupt")
    with Store.open(str(root)) as store:
        result = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_BUNDLE_INVENTORIES
        )
        assert result.verdict is Verdict.FAIL
        assert "r1" in result.detail


def test_bind_bundle_decision_legality_digest_and_rebinding() -> None:
    release = Release(
        "r1", ReleaseArchetype.OPERATIONAL, ReleaseState.PREPARED, (), None, None
    )
    snapshot = Snapshot(releases=(release,))
    first = decide(snapshot, BindBundle("r1", "a" * 64))
    assert isinstance(first, Accepted)
    bound = apply(snapshot, first.events)
    second = decide(bound, BindBundle("r1", "b" * 64))
    assert isinstance(second, Accepted)
    rebound = apply(bound, second.events).release("r1")
    assert rebound is not None and rebound.bundle_inventory_digest == "b" * 64

    bad = decide(snapshot, BindBundle("r1", "BAD"))
    assert isinstance(bad, Refused)
    assert bad.violations[0].code == "release.bundle-digest-invalid"
    missing = decide(snapshot, BindBundle("missing", "a" * 64))
    assert isinstance(missing, Refused)
    assert missing.violations[0].code == "release.unknown"
    progressed = Snapshot(
        releases=(
            Release(
                "r1",
                ReleaseArchetype.OPERATIONAL,
                ReleaseState.DEPLOYING,
                (),
                None,
                None,
            ),
        )
    )
    illegal = decide(progressed, BindBundle("r1", "a" * 64))
    assert isinstance(illegal, Refused)
    assert illegal.violations[0].code == "transition.illegal"


def test_exclusion_suppresses_symlink_and_fifo_entries(tmp_path: Path) -> None:
    import os

    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "kept.txt").write_bytes(b"kept")
    os.symlink("/etc/hosts", root / "docs" / "app-link")
    os.mkfifo(root / "docs" / "pipe")
    # Without exclusions the walker refuses the symlink (typed).
    with pytest.raises(InventoryError, match="symlink refused"):
        build_inventory(str(root), ())
    # The exclusion list is the escape hatch for exactly this content.
    inventory = build_inventory(str(root), ("docs/app-link", "docs/pipe"))
    assert tuple(entry.path for entry in inventory.entries) == ("docs/kept.txt",)


def test_universal_pattern_prunes_and_survives_unreadable_dirs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    trap = root / "sealed"
    trap.mkdir(parents=True)
    (trap / "inner").mkdir()
    trap.chmod(0o000)
    try:
        inventory = build_inventory(str(root), ("**",))
        assert inventory.entries == ()
    finally:
        trap.chmod(0o700)


def test_deep_trees_do_not_recurse(tmp_path: Path) -> None:
    import sys

    root = tmp_path / "deep"
    current = root
    for index in range(120):
        current = current / f"d{index}"
    current.mkdir(parents=True)
    (current / "leaf.txt").write_bytes(b"leaf")
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(80)
    try:
        inventory = build_inventory(str(root), ())
    finally:
        sys.setrecursionlimit(limit)
    assert len(inventory.entries) == 1


def test_encode_refuses_unsorted_inventory_before_commit(tmp_path: Path) -> None:
    from vouch_mk2.core import BundleInventory, InventoryEntry
    from vouch_mk2.store import StoreError, encode_bundle_inventory

    bad = BundleInventory(
        entries=(
            InventoryEntry(path="b", digest="0" * 64),
            InventoryEntry(path="a", digest="1" * 64),
        ),
        exclusions=(),
    )
    with pytest.raises(StoreError, match="sorted"):
        encode_bundle_inventory(bad)
