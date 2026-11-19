"""Frozen acceptance pack for self-hosted unit rebind-generations@1 (D-39).

Found by Astra in the effect-declarations@1 final check (``vouch_mk2-0tc``): publish, rebind,
amend, respecify, re-freeze with different bytes, publish, rebind. The second rebind was decided
but its commit was refused with ``rebind.history-disconnected``, because the snapshot invariant
required every rebind row to continue the previous row, across the re-freeze that legitimately
started a new pack generation. D-39 checks each rebind against the unit's current pack instead,
when events are folded, when the event log is checked, before an event extension is committed and
on merge source chains. Every test here failed against the pre-change tree, except tests marked
``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.commands import RebindPack
from vouch_mk2.core.decide import Refused, apply, decide
from vouch_mk2.core.events import AcceptancePackRebound, EventRecord, PreLogHistoryRecorded
from vouch_mk2.core.model import PackRebound, Snapshot, Unit, UnitStatus
from vouch_mk2.store import Store
from vouch_mk2.store.codec import encode_event_record, encode_snapshot
from vouch_mk2.store.digests import ArtifactKind
from vouch_mk2.store.errors import StoreError
from vouch_mk2.store.event_log import (
    build_event_records,
    commit_pre_log_checkpoint,
    event_pack_digest,
)

ROOT = Path(__file__).resolve().parents[1]
CODE = "rebind.history-disconnected"
RECORDED_AT = "2026-11-19T00:00:00+00:00"
A, B, C, D, E = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)
TA, TB, TC, TD = ("1" * 64, "2" * 64, "3" * 64, "4" * 64)


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _must(*argv: str) -> str:
    code, output = _run(*argv)
    assert code == 0, (argv, output)
    return output


class _Project:
    """A scratch project whose store holds one unit ``u@1`` with a one-test-file pack."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.root = str(path / ".vouch")

    def write_test(self, marker: str) -> None:
        (self.path / "t.py").write_text(f"assert True  # {marker}\n")

    def call(self, verb: str, *argv: str) -> str:
        return _must(verb, "--root", self.root, *argv)

    def evidence(self, kind: str) -> None:
        exit_code = "1" if kind == "red" else "0"
        self.call(
            "record-evidence", "--unit", "u@1", "--kind", kind, "--",
            sys.executable, "-c", f"raise SystemExit({exit_code})",
        )

    def freeze_and_publish(self, pack_version: str, marker: str) -> None:
        self.write_test(marker)
        self.call(
            "freeze", "--name", "u", "--pack-version", pack_version,
            "--project", str(self.path), "--pack-test-file", "t.py",
        )
        self.call("mark-ready", "--name", "u")
        self.call("start", "--name", "u")
        self.evidence("red")
        self.evidence("green")
        self.call("advance-anchor", "--unit-name", "u")
        self.call("mark-done", "--name", "u")
        self.call("publish", "--name", "u")

    def rebind(self, marker: str, reason: str) -> tuple[int, str]:
        self.write_test(marker)
        return _run(
            "rebind-pack", "--root", self.root, "--name", "u", "--project", str(self.path),
            "--pack-test-file", "t.py", "--reason", reason,
        )

    def rebind_and_green(self, marker: str, reason: str) -> None:
        code, output = self.rebind(marker, reason)
        assert code == 0, output
        self.evidence("green")
        self.call("advance-anchor", "--unit-name", "u")

    def amend_and_respecify(self, deviation_id: str, amendment_version: str) -> None:
        self.call(
            "open-deviation", "--unit", "u@1", "--deviation-id", deviation_id,
            "--deviation-type", "pack-defect",
        )
        self.call(
            "adjudicate-amend", "--deviation-id", deviation_id,
            "--amendment-version", amendment_version,
        )
        self.call("close-deviation", "--deviation-id", deviation_id)
        self.call("respecify", "--name", "u")

    def snapshot(self) -> Snapshot:
        with Store.open(self.root, read_only=True) as store:
            return store.read_snapshot()

    def unit(self) -> Unit:
        unit = self.snapshot().unit("u")
        assert unit is not None
        return unit

    def verdicts(self) -> dict[str, tuple[str, str]]:
        code, output = _run("check", "--root", self.root, "--json")
        assert code in (0, 1), output
        document = json.loads(output)
        return {
            row["check_id"]: (row["verdict"], row["detail"]) for row in document["mechanical"]
        }


def _new_project(tmp_path: Path, name: str = "project") -> _Project:
    path = tmp_path / name
    path.mkdir()
    Store.init(str(path / ".vouch")).close()
    project = _Project(path)
    project.call("set-ci-policy", "--policy", "ledger")
    project.call("specify", "--name", "u", "--version", "1")
    return project


def _second_generation(tmp_path: Path, name: str = "project") -> _Project:
    """Generation 1 (pack A, rebound A->B with a fresh GREEN), then an amendment, a respecify and
    a re-freeze with different bytes C, published."""
    project = _new_project(tmp_path, name)
    project.freeze_and_publish("p/1", "A")
    project.rebind_and_green("B", "adapt shared interface, generation 1")
    project.amend_and_respecify("D1", "p/2")
    project.freeze_and_publish("p/2", "C")
    return project


def _forged_rebind(project: _Project) -> tuple[Snapshot, Snapshot, bytes]:
    """A rebound record naming generation 1's last inventory (B), not the current one (C).

    Returns the store's snapshot, the snapshot the record would fold to under the old adjacency
    rule, and the record's bytes. The forged row continues the previous row, so the pre-change
    snapshot invariant accepted it."""
    snapshot = project.snapshot()
    unit = snapshot.unit("u")
    assert unit is not None
    first = unit.rebound_history[0]
    head = snapshot.evidence_head_ref(unit.ref())
    assert head is not None
    event = AcceptancePackRebound(
        name="u",
        old_pack_digest=first.new_inventory_digest,
        new_pack_digest=first.old_inventory_digest,
        old_test_set_digest=first.new_test_set_digest,
        new_test_set_digest=first.old_test_set_digest,
        reason="forged: names an earlier generation's inventory",
        evidence_head_sequence=head.head_sequence,
    )
    record = EventRecord(
        event, RECORDED_AT, snapshot.event_head, snapshot.revision + 1, "forger", "",
        event_pack_digest(event),
    )
    payload = encode_event_record(record)
    forged_unit = replace(
        unit,
        acceptance_pack_digest=event.new_pack_digest,
        acceptance_test_set_digest=event.new_test_set_digest,
        pack_binding="rebound",
        rebound_history=unit.rebound_history
        + (
            PackRebound(
                event.old_pack_digest,
                event.new_pack_digest,
                event.old_test_set_digest,
                event.new_test_set_digest,
                event.reason,
                event.evidence_head_sequence,
            ),
        ),
    )
    with Store.open(project.root) as store, store.write_locked():
        digest = store.put_object(ArtifactKind.EVENT, payload)
    candidate = replace(
        snapshot,
        revision=snapshot.revision + 1,
        event_head=digest,
        units=tuple(forged_unit if item.name == "u" else item for item in snapshot.units),
    )
    return snapshot, candidate, payload


def _published(pack: str, test_set: str, history: tuple[PackRebound, ...]) -> Snapshot:
    unit = Unit(
        "u",
        "1",
        UnitStatus.PUBLISHED,
        acceptance_pack_version="p/2",
        published_once=True,
        acceptance_pack_digest=pack,
        acceptance_test_set_digest=test_set,
        pack_history=("p/1",),
        pack_binding="at-freeze",
        rebound_history=history,
    )
    return Snapshot(revision=20, units=(unit,))


def _rebound(old: str, new: str, old_tests: str, new_tests: str, sequence: int) -> PackRebound:
    return PackRebound(old, new, old_tests, new_tests, "adapt shared interface", sequence)


def test_a_rebind_after_a_refreeze_succeeds_and_keeps_both_generations(tmp_path: Path) -> None:
    """R1."""
    project = _second_generation(tmp_path)
    before = project.unit()
    code, output = project.rebind("D", "adapt shared interface, generation 2")
    assert code == 0, output
    unit = project.unit()
    assert unit.status is UnitStatus.PUBLISHED
    assert unit.acceptance_pack_version == "p/2"
    assert unit.pack_binding == "rebound"
    first, second = unit.rebound_history
    assert first == before.rebound_history[0]
    assert first.reason == "adapt shared interface, generation 1"
    assert second.reason == "adapt shared interface, generation 2"
    assert second.old_inventory_digest == before.acceptance_pack_digest
    assert second.old_test_set_digest == before.acceptance_test_set_digest
    assert second.old_inventory_digest != first.new_inventory_digest
    assert second.new_inventory_digest == unit.acceptance_pack_digest
    assert second.evidence_head_sequence >= first.evidence_head_sequence
    verdict, detail = project.verdicts()["check.evidence-chains"]
    assert verdict == "fail", detail
    assert "fresh GREEN" in detail, detail
    project.evidence("green")
    project.call("advance-anchor", "--unit-name", "u")
    verdicts = project.verdicts()
    for check_id in ("check.event-log", "check.evidence-chains"):
        assert verdicts[check_id][0] == "pass", (check_id, verdicts[check_id])
    assert "rebound after publication: u@1" in verdicts["check.pack-bound"][1]


def test_three_generations_and_a_return_to_earlier_bytes(tmp_path: Path) -> None:
    """R2."""
    project = _new_project(tmp_path)
    project.freeze_and_publish("p/1", "A")
    project.rebind_and_green("B", "generation 1, first adaptation")
    project.rebind_and_green("C", "generation 1, second adaptation")
    project.amend_and_respecify("D1", "p/2")
    project.freeze_and_publish("p/2", "A")
    project.rebind_and_green("D", "generation 2")
    project.amend_and_respecify("D2", "p/3")
    project.freeze_and_publish("p/3", "B")
    project.rebind_and_green("E", "generation 3")
    rows = project.unit().rebound_history
    assert [row.reason for row in rows] == [
        "generation 1, first adaptation",
        "generation 1, second adaptation",
        "generation 2",
        "generation 3",
    ]
    assert rows[1].old_inventory_digest == rows[0].new_inventory_digest
    assert rows[2].old_inventory_digest == rows[0].old_inventory_digest
    assert rows[3].old_inventory_digest == rows[0].new_inventory_digest
    assert project.verdicts()["check.event-log"][0] == "pass"


def test_folding_refuses_a_rebind_that_does_not_name_the_current_pack() -> None:
    """R3."""
    snapshot = _published(C, TC, (_rebound(A, B, TA, TB, 2),))
    current = AcceptancePackRebound("u", C, D, TC, TD, "adapt shared interface", 4)
    folded = apply(snapshot, (current,))
    unit = folded.unit("u")
    assert unit is not None
    assert [row.new_inventory_digest for row in unit.rebound_history] == [B, D]
    for forged in (
        AcceptancePackRebound("u", B, D, TB, TD, "continues generation 1", 4),
        AcceptancePackRebound("u", C, D, TB, TD, "wrong old test set", 4),
        AcceptancePackRebound("u", E, D, TC, TD, "wrong old inventory", 4),
    ):
        with pytest.raises(ValueError, match=CODE):
            apply(snapshot, (forged,))


def test_the_event_log_check_fails_on_a_rebind_that_names_an_earlier_generation(
    tmp_path: Path,
) -> None:
    """R4."""
    project = _second_generation(tmp_path)
    _snapshot, candidate, _payload = _forged_rebind(project)
    (project.path / ".vouch" / "state.json").write_bytes(encode_snapshot(candidate))
    verdict, detail = project.verdicts()["check.event-log"]
    assert verdict == "fail", detail
    assert CODE in detail, detail


def test_a_commit_refuses_a_rebind_that_does_not_name_the_current_pack(tmp_path: Path) -> None:
    """R5."""
    project = _second_generation(tmp_path)
    snapshot = project.snapshot()
    unit = snapshot.unit("u")
    assert unit is not None
    first = unit.rebound_history[0]
    stale_unit = replace(
        unit,
        acceptance_pack_digest=first.new_inventory_digest,
        acceptance_test_set_digest=first.new_test_set_digest,
    )
    stale = replace(
        snapshot,
        units=tuple(stale_unit if item.name == "u" else item for item in snapshot.units),
    )
    head = snapshot.evidence_head_ref(unit.ref())
    assert head is not None
    event = AcceptancePackRebound(
        "u",
        first.new_inventory_digest,
        first.old_inventory_digest,
        first.new_test_set_digest,
        first.old_test_set_digest,
        "forged: decided against a stale copy of the unit",
        head.head_sequence,
    )
    candidate, objects, _records = build_event_records(
        stale, (event,), actor="forger", run_id="", recorded_at=RECORDED_AT
    )
    state = project.path / ".vouch" / "state.json"
    before = state.read_bytes()
    with Store.open(project.root) as store:
        with pytest.raises(StoreError, match=CODE):
            store.commit(candidate, objects=objects, expected_revision=snapshot.revision)
    assert state.read_bytes() == before
    assert project.verdicts()["check.event-log"][0] == "pass"


def test_a_merge_refuses_a_source_chain_with_a_rebind_that_names_an_earlier_generation(
    tmp_path: Path,
) -> None:
    """R6."""
    ours = _second_generation(tmp_path, "ours")
    theirs_path = tmp_path / "theirs"
    shutil.copytree(ours.path, theirs_path)
    theirs = _Project(theirs_path)
    _snapshot, candidate, _payload = _forged_rebind(theirs)
    (theirs.path / ".vouch" / "state.json").write_bytes(encode_snapshot(candidate))
    state = ours.path / ".vouch" / "state.json"
    before = state.read_bytes()
    code, output = _run("merge", "--root", ours.root, "--other", theirs.root)
    assert code != 0, output
    assert "merge.theirs-history-invalid" in output, output
    assert CODE in output, output
    assert state.read_bytes() == before


def test_a_pre_log_baseline_keeps_the_contiguous_chain(tmp_path: Path) -> None:
    """R7."""
    connected = _published(D, TD, (_rebound(A, B, TA, TB, 2), _rebound(B, D, TB, TD, 4)))
    connected = replace(
        connected, units=(replace(connected.units[0], pack_binding="rebound"),)
    )
    marker = PreLogHistoryRecorded(description="pre-log revisions 1..20", baseline=connected)
    assert apply(Snapshot(), (marker,)).units == connected.units
    disconnected = _published(D, TD, (_rebound(A, B, TA, TB, 2), _rebound(C, D, TC, TD, 4)))
    disconnected = replace(
        disconnected, units=(replace(disconnected.units[0], pack_binding="rebound"),)
    )
    with pytest.raises(ValueError, match=CODE):
        apply(
            Snapshot(),
            (PreLogHistoryRecorded(description="pre-log revisions 1..20", baseline=disconnected),),
        )
    root = str(tmp_path / ".vouch")
    Store.init(root).close()
    with Store.open(root) as store:
        with pytest.raises((StoreError, ValueError), match=CODE):
            commit_pre_log_checkpoint(store, disconnected, actor="importer", run_id="")
        assert store.read_snapshot() == Snapshot()


def test_a_stale_rebind_command_is_still_refused_before_folding() -> None:
    """R8. # guard:"""
    snapshot = _published(C, TC, (_rebound(A, B, TA, TB, 2),))
    decision = decide(
        snapshot,
        RebindPack(name="u", pack_digest=D, reason="adapt", old_pack_digest=B),
    )
    assert isinstance(decision, Refused)
    assert [violation.code for violation in decision.violations] == ["rebind.stale-pack"]


def test_the_claim_boundary_states_the_generation_rule() -> None:
    """R9."""
    text = " ".join((ROOT / "docs" / "claim-boundary.md").read_text().split())
    assert (
        "Each rebind must name the unit's current inventory and test set. A re-freeze starts a "
        "new pack generation, so the first rebind after it does not continue an earlier "
        "generation's rebinds." in text
    )
