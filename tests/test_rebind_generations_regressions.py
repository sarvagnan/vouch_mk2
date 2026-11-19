"""Owner regressions for rebind-generations@1 (D-39), from Astra's review and re-check.

HIGH: a commit that kept the on-disk revision could replace a valid event head with a rebind that
does not name the unit's current pack, because only revision-advancing commits had their supplied
records checked; the revision-preserving upgrade writer had the same gap. LOW: the frozen pack checks a refused commit's state bytes but not that it writes
no object, and reaches the pre-log refusal only while the checkpoint is being built.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.events import AcceptancePackRebound, EventRecord, PreLogHistoryRecorded
from vouch_mk2.core.model import PackRebound, Snapshot, Unit, UnitStatus
from vouch_mk2.store import Store
from vouch_mk2.store.codec import encode_event_record, encode_snapshot
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.errors import StoreError
from vouch_mk2.store.event_log import build_event_records, event_pack_digest

CODE = "rebind.history-disconnected"
RECORDED_AT = "2026-11-19T00:00:00+00:00"


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


class _Project:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.root = str(path / ".vouch")

    def call(self, verb: str, *argv: str) -> None:
        code, output = _run(verb, "--root", self.root, *argv)
        assert code == 0, (verb, argv, output)

    def evidence(self, kind: str) -> None:
        exit_code = "1" if kind == "red" else "0"
        self.call(
            "record-evidence", "--unit", "u@1", "--kind", kind, "--",
            sys.executable, "-c", f"raise SystemExit({exit_code})",
        )

    def write_test(self, marker: str) -> None:
        (self.path / "t.py").write_text(f"assert True  # {marker}\n")

    def freeze_and_publish(self, pack_version: str, marker: str) -> None:
        self.write_test(marker)
        self.call(
            "freeze", "--name", "u", "--pack-version", pack_version,
            "--project", str(self.path), "--pack-test-file", "t.py",
        )
        for verb in ("mark-ready", "start"):
            self.call(verb, "--name", "u")
        self.evidence("red")
        self.evidence("green")
        self.call("advance-anchor", "--unit-name", "u")
        for verb in ("mark-done", "publish"):
            self.call(verb, "--name", "u")

    def rebind(self, marker: str, reason: str) -> None:
        self.write_test(marker)
        self.call(
            "rebind-pack", "--name", "u", "--project", str(self.path),
            "--pack-test-file", "t.py", "--reason", reason,
        )

    def snapshot(self) -> Snapshot:
        with Store.open(self.root, read_only=True) as store:
            return store.read_snapshot()

    def event_log_verdict(self) -> str:
        code, output = _run("check", "--root", self.root, "--json")
        assert code in (0, 1), output
        rows = json.loads(output)["mechanical"]
        return next(row["verdict"] for row in rows if row["check_id"] == "check.event-log")

    def objects(self) -> list[str]:
        return sorted(item.name for item in (self.path / ".vouch" / "objects" / "sha256").iterdir())


def _second_generation(tmp_path: Path) -> _Project:
    path = tmp_path / "project"
    path.mkdir()
    Store.init(str(path / ".vouch")).close()
    project = _Project(path)
    project.call("set-ci-policy", "--policy", "ledger")
    project.call("specify", "--name", "u", "--version", "1")
    project.freeze_and_publish("p/1", "A")
    project.rebind("B", "generation 1")
    project.evidence("green")
    project.call("advance-anchor", "--unit-name", "u")
    for verb, argv in (
        ("open-deviation", ("--unit", "u@1", "--deviation-id", "D1", "--deviation-type",
                            "pack-defect")),
        ("adjudicate-amend", ("--deviation-id", "D1", "--amendment-version", "p/2")),
        ("close-deviation", ("--deviation-id", "D1")),
        ("respecify", ("--name", "u")),
    ):
        project.call(verb, *argv)
    project.freeze_and_publish("p/2", "C")
    return project


def _stale_extension(
    snapshot: Snapshot,
) -> tuple[Snapshot, tuple[tuple[ArtifactKind, bytes], ...]]:
    """Records decided against a copy of the unit that still names generation 1's pack (B)."""
    unit = snapshot.unit("u")
    assert unit is not None
    first = unit.rebound_history[0]
    stale_unit = replace(
        unit,
        acceptance_pack_digest=first.new_inventory_digest,
        acceptance_test_set_digest=first.new_test_set_digest,
    )
    stale = replace(
        snapshot, units=tuple(stale_unit if item.name == "u" else item for item in snapshot.units)
    )
    head = snapshot.evidence_head_ref(unit.ref())
    assert head is not None
    event = AcceptancePackRebound(
        "u", first.new_inventory_digest, first.old_inventory_digest,
        first.new_test_set_digest, first.old_test_set_digest, "stale", head.head_sequence,
    )
    candidate, objects, _records = build_event_records(
        stale, (event,), actor="forger", run_id="", recorded_at=RECORDED_AT
    )
    return candidate, objects


def test_a_revision_preserving_commit_cannot_replace_the_head_with_a_stale_rebind(
    tmp_path: Path,
) -> None:
    project = _second_generation(tmp_path)
    candidate, objects = _stale_extension(project.snapshot())
    project.rebind("D", "generation 2")
    assert project.snapshot().revision == candidate.revision
    state = project.path / ".vouch" / "state.json"
    before = state.read_bytes()
    with Store.open(project.root) as store:
        with pytest.raises(StoreError, match="must keep the on-disk event head"):
            store.commit(candidate, objects=objects, expected_revision=candidate.revision)
    assert state.read_bytes() == before
    assert project.event_log_verdict() == "pass"


def test_the_upgrade_writer_cannot_replace_the_head(tmp_path: Path) -> None:
    """Astra's re-check: the revision-preserving upgrade writer must keep the raw head too."""
    project = _second_generation(tmp_path)
    candidate, _objects = _stale_extension(project.snapshot())
    project.rebind("D", "generation 2")
    assert project.snapshot().revision == candidate.revision
    state = project.path / ".vouch" / "state.json"
    before = state.read_bytes()
    with Store.open(project.root) as store, store.write_locked():
        with pytest.raises(StoreError, match="preserve the current raw event head"):
            store.replace_state_for_upgrade(encode_snapshot(candidate))
    assert state.read_bytes() == before
    assert project.event_log_verdict() == "pass"


def test_a_commit_cannot_move_the_store_back(tmp_path: Path) -> None:
    project = _second_generation(tmp_path)
    earlier = project.snapshot()
    project.rebind("D", "generation 2")
    current = project.snapshot()
    state = project.path / ".vouch" / "state.json"
    before = state.read_bytes()
    with Store.open(project.root) as store:
        with pytest.raises(StoreError, match="cannot move the store back"):
            store.commit(earlier, expected_revision=current.revision)
    assert state.read_bytes() == before


def test_a_refused_rebind_commit_writes_no_object(tmp_path: Path) -> None:
    project = _second_generation(tmp_path)
    snapshot = project.snapshot()
    candidate, objects = _stale_extension(snapshot)
    before = project.objects()
    with Store.open(project.root) as store:
        with pytest.raises(StoreError, match=CODE):
            store.commit(candidate, objects=objects, expected_revision=snapshot.revision)
        assert project.objects() == before


def test_the_raw_commit_refuses_a_disconnected_pre_log_baseline(tmp_path: Path) -> None:
    a, b, c, d = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
    unit = Unit(
        "u", "1", UnitStatus.PUBLISHED, acceptance_pack_version="p/2", published_once=True,
        acceptance_pack_digest=d, acceptance_test_set_digest=d, pack_history=("p/1",),
        pack_binding="rebound",
        rebound_history=(
            PackRebound(a, b, a, b, "generation 1", 2),
            PackRebound(c, d, c, d, "unwitnessed generation break", 4),
        ),
    )
    baseline = Snapshot(revision=20, units=(unit,))
    marker = PreLogHistoryRecorded(description="pre-log revisions 1..20", baseline=baseline)
    record = EventRecord(
        marker, RECORDED_AT, "GENESIS", baseline.revision, "importer", "",
        event_pack_digest(marker),
    )
    payload = encode_event_record(record)
    candidate = replace(baseline, event_head=digest_of(ArtifactKind.EVENT, payload))
    root = tmp_path / ".vouch"
    Store.init(str(root)).close()
    before = sorted(item.name for item in (root / "objects" / "sha256").iterdir())
    with Store.open(str(root)) as store:
        with pytest.raises(StoreError, match=CODE):
            store.commit(candidate, objects=((ArtifactKind.EVENT, payload),), expected_revision=0)
        assert store.read_snapshot() == Snapshot()
    assert sorted(item.name for item in (root / "objects" / "sha256").iterdir()) == before
