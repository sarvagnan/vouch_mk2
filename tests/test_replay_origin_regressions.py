"""D-41 transport, validation and repeat-replay regressions."""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import commands
from vouch_mk2.core.decide import Accepted, Refused, apply, decide
from vouch_mk2.core.events import (
    DeviationAmended, EventRecord, MergeRecorded, OpenInCiMarked, UnitBlocked, UnitInvalidated,
)
from vouch_mk2.core.model import AnchorMode, DeviationType, Snapshot, Tier, UnitRef
from vouch_mk2.store import ArtifactKind, Store, StoreError, decode_event_record, digest_of
from vouch_mk2.store import codec, merge as merge_module
from vouch_mk2.store.codec import decode_raw_document, encode_document, encode_snapshot
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots

TIME = "2026-11-18T00:00:00+00:00"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "state-format"


def _command(
    root: Path, command: commands.Command | commands.RecordMerge, *, actor: str = "author",
) -> None:
    with Store.open(str(root)) as store, store.write_locked():
        before = store.read_snapshot()
        decision = decide(before, command)
        assert isinstance(decision, Accepted), (command, decision)
        commit_events(store, before, decision.events, actor=actor, run_id="", recorded_at=TIME)


def _snapshot(root: Path) -> Snapshot:
    with Store.open(str(root), read_only=True) as store:
        return store.read_snapshot()


def _records(root: Path) -> tuple[tuple[str, EventRecord], ...]:
    chain: list[tuple[str, EventRecord]] = []
    with Store.open(str(root), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            chain.append((digest, record))
            digest = record.prev
    return tuple(reversed(chain))


def _store(root: Path, *, freeze: bool = True) -> Path:
    Store.init(str(root)).close()
    _command(root, commands.SpecifyUnit("x", "1"))
    if freeze:
        _command(root, commands.FreezeAcceptance("x", "pack/1"))
    return root


def _merge(ours: Path, theirs: Path) -> MergeOutcome:
    result = merge_store_roots(str(ours), str(theirs), actor=ours.name, run_id="")
    assert isinstance(result, MergeOutcome), result
    assert result.mode == "replayed"
    return result


def _carry_mark(ours: Path, carrier: Path) -> None:
    _command(ours, commands.MarkOpenInCi("x", "old"))
    _command(carrier, commands.SpecifyUnit("carrier", "1"))
    _merge(carrier, ours)


def _rewrite_record(
    root: Path, changes: dict[str, object], *, recompute: bool,
    remove: tuple[str, ...] = (), offset: int = 2,
) -> None:
    """Rewrite a record and rechain its successors with valid artifact hashes."""
    chain = _records(root)
    target = len(chain) - offset
    before = Snapshot()
    previous = "GENESIS"
    objects = root / "objects" / "sha256"
    for index, (digest, record) in enumerate(chain):
        if index >= target:
            raw = decode_raw_document((objects / digest).read_bytes())
            if index == target:
                raw.update(changes)
                for field in remove:
                    del raw[field]
            else:
                raw["prev"] = previous
            data = encode_document(raw)
            digest = digest_of(ArtifactKind.EVENT, data)
            (objects / digest).write_bytes(data)
            if recompute:
                record = decode_event_record(data)
        if recompute:
            before = apply(
                before, (record.folded_event,), event_digests=(digest,),
                event_run_ids=(record.run_id,),
            )
        previous = digest
    snapshot = before if recompute else replace(_snapshot(root), event_head=previous)
    (root / "state.json").write_bytes(encode_snapshot(snapshot))


def _forbid_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_store: Store) -> None:
        pytest.fail("merge entered the write lock")
    monkeypatch.setattr(Store, "write_locked", forbidden)


def _store_bytes(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _assert_position_refusal(
    ours: Path, theirs: Path, monkeypatch: pytest.MonkeyPatch, *,
    branch: str, field: str, digest: str, source_head: str,
) -> None:
    before = (_store_bytes(ours), _store_bytes(theirs))
    _forbid_lock(monkeypatch)
    with pytest.raises(
        StoreError, match=rf"merge\.{branch}-history-invalid: .*{field} {digest}.*{source_head}",
    ):
        merge_store_roots(str(ours), str(theirs), actor="reviewer", run_id="")
    assert (_store_bytes(ours), _store_bytes(theirs)) == before


@pytest.mark.parametrize(
    "change",
    [
        {"event": "CiPolicySet", "payload": {"policy": "strict"}},
        {"payload": {"name": "y@1", "reason": "old"}},
        {"actor": "different"},
        {"run_id": "123"},
        {"recorded_at": "2026-11-19T00:00:00+00:00"},
    ],
    ids=["kind", "subject", "actor", "run", "time"],
)
def test_a_covered_copy_must_match_each_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, object],
) -> None:
    ours = _store(tmp_path / "ours")
    _command(ours, commands.SpecifyUnit("y", "1"))
    _command(ours, commands.FreezeAcceptance("y", "y-pack/1"))
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _carry_mark(ours, carrier)
    _rewrite_record(carrier, change, recompute=True)
    _forbid_lock(monkeypatch)
    with pytest.raises(StoreError, match=r"merge\.theirs-history-invalid: .* disagrees"):
        merge_store_roots(str(ours), str(carrier), actor="reviewer", run_id="")


def test_a_covered_companion_must_match_its_generated_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ours = _store(tmp_path / "ours")
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _command(ours, commands.OpenDeviation("dev", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    _command(carrier, commands.SpecifyUnit("carrier", "1"))
    _merge(carrier, ours)
    root, original = _records(ours)[-1]
    copy = _records(carrier)[-2][1]
    assert isinstance(original.folded_event, UnitBlocked) and not original.generated
    assert copy.origin == root and not copy.generated
    _rewrite_record(carrier, {"generated": True}, recompute=True)
    _forbid_lock(monkeypatch)
    with pytest.raises(StoreError, match=r"merge\.theirs-history-invalid: .* disagrees"):
        merge_store_roots(str(ours), str(carrier), actor="reviewer", run_id="")


@pytest.mark.parametrize("exact_no_op", [False, True], ids=["origin-covered", "exact-no-op"])
def test_an_incoming_origin_cannot_name_a_known_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exact_no_op: bool,
) -> None:
    source = _store(tmp_path / "source")
    ours = Path(shutil.copytree(source, tmp_path / "ours"))
    theirs = Path(shutil.copytree(source, tmp_path / "theirs"))
    _command(source, commands.MarkOpenInCi("x", "old"))
    for branch in (ours, theirs):
        _command(branch, commands.SpecifyUnit(branch.name, "1"))
        _merge(branch, source)
    digest, copy = _records(ours)[-2]
    assert isinstance(copy.folded_event, OpenInCiMarked)
    assert copy.origin and copy.origin != digest
    _rewrite_record(theirs, {"origin": digest}, recompute=True)
    if exact_no_op:
        _command(ours, commands.RecordMerge(_snapshot(ours).event_head, _snapshot(theirs).event_head))
    _forbid_lock(monkeypatch)
    before = (_store_bytes(ours), _store_bytes(theirs))
    with pytest.raises(StoreError, match=rf"merge\.theirs-history-invalid: .*origin {digest}.*copy"):
        merge_store_roots(str(ours), str(theirs), actor="reviewer", run_id="")
    assert (_store_bytes(ours), _store_bytes(theirs)) == before


@pytest.mark.parametrize("change", ["strip-origin", "unrelated-origin"])
def test_a_replayed_mark_cannot_restore_a_clear_with_invalid_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    change: str,
) -> None:
    source = _store(tmp_path / "source")
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    _command(source, commands.MarkOpenInCi("x", "old"))
    ours = Path(shutil.copytree(source, tmp_path / "ours"))
    _command(ours, commands.ClearOpenInCi("x"))
    _command(source, commands.SpecifyUnit("source-only", "1"))
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    unrelated = _snapshot(carrier).event_head
    _merge(carrier, source)
    honest = Path(shutil.copytree(ours, tmp_path / "honest"))
    assert not _merge(honest, carrier).snapshot.ci_exceptions

    if change == "strip-origin":
        _rewrite_record(carrier, {}, recompute=True, remove=("origin", "origin_format"), offset=3)
    else:
        _rewrite_record(carrier, {"origin": unrelated}, recompute=True, offset=3)
    assert main(["check", "--root", str(carrier)]) == 1
    assert any(
        line.startswith("FAIL") and "check.event-log" in line and "origin" in line
        for line in capsys.readouterr().out.splitlines()
    )
    _forbid_lock(monkeypatch)
    before = (_store_bytes(ours), _store_bytes(carrier))
    with pytest.raises(StoreError, match=r"merge\.theirs-history-invalid: .*origin"):
        merge_store_roots(str(ours), str(carrier), actor="reviewer", run_id="")
    assert (_store_bytes(ours), _store_bytes(carrier)) == before
    assert not _snapshot(ours).ci_exceptions


def test_an_origin_cannot_name_a_same_chain_mark_for_another_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    ours = _store(tmp_path / "ours")
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _command(carrier, commands.SpecifyUnit("y", "1"))
    _command(carrier, commands.FreezeAcceptance("y", "pack/1"))
    _command(carrier, commands.MarkOpenInCi("y", "old"))
    wrong_subject = _snapshot(carrier).event_head
    _carry_mark(ours, carrier)
    _rewrite_record(carrier, {"origin": wrong_subject}, recompute=True)

    assert main(["check", "--root", str(carrier)]) == 1
    assert any(
        line.startswith("FAIL") and "check.event-log" in line and wrong_subject in line
        for line in capsys.readouterr().out.splitlines()
    )
    _forbid_lock(monkeypatch)
    with pytest.raises(StoreError, match=rf"merge\.theirs-history-invalid: .*{wrong_subject}"):
        merge_store_roots(str(ours), str(carrier), actor="reviewer", run_id="")


@pytest.mark.parametrize("resolution", ["same-chain", "receiver-chain", "source-chain"])
def test_a_satisfied_entry_cannot_name_an_unrelated_specification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    resolution: str,
) -> None:
    source = _store(tmp_path / "source")
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    _command(source, commands.SpecifyUnit("source-only", "1"))
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    unrelated = _snapshot(carrier if resolution == "same-chain" else source).event_head
    _merge(carrier, source)
    _rewrite_record(carrier, {"satisfied": [unrelated]}, recompute=True, offset=1)

    main(["check", "--root", str(carrier)])
    verdict = "FAIL" if resolution == "same-chain" else "PASS"
    assert any(
        line.startswith(verdict) and "check.event-log" in line
        for line in capsys.readouterr().out.splitlines()
    )
    ours, theirs = (carrier, source) if resolution == "source-chain" else (source, carrier)
    branch = "ours" if resolution == "source-chain" else "theirs"
    _forbid_lock(monkeypatch)
    with pytest.raises(StoreError, match=rf"merge\.{branch}-history-invalid: .*satisfied.*{unrelated}"):
        merge_store_roots(str(ours), str(theirs), actor="reviewer", run_id="")


@pytest.mark.parametrize("branch", ["ours", "theirs"])
def test_a_satisfied_entry_must_come_from_the_complete_source_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str,
) -> None:
    carrier = _store(tmp_path / "carrier")
    _command(carrier, commands.MarkOpenInCi("x", "old"))
    source = Path(shutil.copytree(carrier, tmp_path / "source"))
    _command(carrier, commands.ClearOpenInCi("x"))
    clear = _snapshot(carrier).event_head
    _command(source, commands.SpecifyUnit("b", "1"))
    source_head = _snapshot(source).event_head
    _merge(carrier, source)
    _, marker = _records(carrier)[-1]
    assert isinstance(marker.folded_event, MergeRecorded)
    assert marker.folded_event.theirs_head == source_head and not marker.satisfied
    assert merge_store_roots(str(carrier), str(source), actor="reviewer", run_id="") == (
        MergeOutcome("no-op", _snapshot(carrier), 0)
    )

    _rewrite_record(carrier, {"satisfied": [clear]}, recompute=True, offset=1)
    ours, theirs = (carrier, source) if branch == "ours" else (source, carrier)
    _assert_position_refusal(
        ours, theirs, monkeypatch, branch=branch, field="satisfied entry",
        digest=clear, source_head=source_head,
    )


def test_an_origin_must_come_from_the_complete_source_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    carrier = _store(tmp_path / "carrier")
    source = Path(shutil.copytree(carrier, tmp_path / "source"))
    _command(carrier, commands.MarkOpenInCi("x", "old"))
    same_claim, independent = _records(carrier)[-1]
    _command(source, commands.SpecifyUnit("b", "1"))
    _command(source, commands.MarkOpenInCi("x", "old"))
    source_head, original = _records(source)[-1]
    assert same_claim != source_head
    assert replace(independent, prev=original.prev, revision=original.revision) == original
    _merge(carrier, source)
    assert _records(carrier)[-2][1].origin == source_head

    _rewrite_record(carrier, {"origin": same_claim}, recompute=True)
    _assert_position_refusal(
        carrier, source, monkeypatch, branch="ours", field="origin",
        digest=same_claim, source_head=source_head,
    )


@pytest.mark.parametrize("field", ["satisfied", "origin"])
def test_a_complete_source_history_rejects_an_unresolved_claim_before_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    ours = _store(tmp_path / "ours")
    _command(ours, commands.MarkOpenInCi("x", "old"))
    source = Path(shutil.copytree(ours, tmp_path / "source"))
    _command(ours, commands.SpecifyUnit("a", "1"))
    _command(source, commands.SpecifyUnit("b", "1"))
    source_head = _snapshot(source).event_head
    _merge(ours, source)
    _, copied = _records(ours)[-2]
    _, marker = _records(ours)[-1]
    assert copied.origin == source_head
    assert isinstance(marker.folded_event, MergeRecorded)
    assert marker.folded_event.theirs_head == source_head and not marker.satisfied
    assert merge_store_roots(str(ours), str(source), actor="reviewer", run_id="") == (
        MergeOutcome("no-op", _snapshot(ours), 0)
    )

    unavailable = "0" * 64
    assert all(unavailable not in dict(_records(root)) for root in (ours, source))
    if field == "satisfied":
        _rewrite_record(ours, {"satisfied": [unavailable]}, recompute=True, offset=1)
    else:
        _rewrite_record(ours, {"origin": unavailable}, recompute=True)

    _assert_position_refusal(
        ours, source, monkeypatch, branch="ours",
        field="satisfied entry" if field == "satisfied" else field,
        digest=unavailable, source_head=source_head,
    )


@pytest.mark.parametrize("parent_available", [True, False], ids=["complete", "missing-parent"])
def test_satisfaction_checks_source_ancestry_through_merge_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parent_available: bool,
) -> None:
    carrier = _store(tmp_path / "carrier")
    _command(carrier, commands.MarkOpenInCi("x", "old"))
    source = Path(shutil.copytree(carrier, tmp_path / "source"))
    parent = carrier if parent_available else Path(shutil.copytree(carrier, tmp_path / "parent"))
    _command(parent, commands.SpecifyUnit("a", "1"))
    _command(source, commands.SpecifyUnit("b", "1"))
    _merge(source, parent)
    source_head, source_marker = _records(source)[-1]
    assert isinstance(source_marker.folded_event, MergeRecorded)
    parent_head = source_marker.folded_event.theirs_head
    assert parent_head not in dict(_records(source))
    assert (parent_head in dict(_records(carrier))) == parent_available

    _command(carrier, commands.ClearOpenInCi("x"))
    clear = _snapshot(carrier).event_head
    _merge(carrier, source)
    _rewrite_record(carrier, {"satisfied": [clear]}, recompute=True, offset=1)
    if parent_available:
        _assert_position_refusal(
            carrier, source, monkeypatch, branch="ours", field="satisfied entry",
            digest=clear, source_head=source_head,
        )
    else:
        before = (_store_bytes(carrier), _store_bytes(source))
        _forbid_lock(monkeypatch)
        assert merge_store_roots(str(carrier), str(source), actor="reviewer", run_id="") == (
            MergeOutcome("no-op", _snapshot(carrier), 0)
        )
        assert (_store_bytes(carrier), _store_bytes(source)) == before


@pytest.mark.parametrize("branch", ["ours", "theirs"])
def test_a_satisfied_entry_cannot_name_a_clear_after_the_source_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str,
) -> None:
    carrier = _store(tmp_path / "carrier")
    _command(carrier, commands.MarkOpenInCi("x", "old"))
    source = Path(shutil.copytree(carrier, tmp_path / "source"))
    _command(carrier, commands.SpecifyUnit("a", "1"))
    _command(source, commands.SpecifyUnit("b", "1"))
    source_head = _snapshot(source).event_head
    _merge(carrier, source)
    marker = _records(carrier)[-1][1]
    assert isinstance(marker.folded_event, MergeRecorded)
    assert marker.folded_event.theirs_head == source_head and not marker.satisfied
    original_exceptions = _snapshot(carrier).ci_exceptions
    assert original_exceptions
    control = Path(shutil.copytree(carrier, tmp_path / "control"))

    _command(source, commands.ClearOpenInCi("x"))
    clear, clear_record = _records(source)[-1]
    assert clear_record.prev == source_head
    assert not _merge(control, source).snapshot.ci_exceptions

    _rewrite_record(carrier, {"satisfied": [clear]}, recompute=True, offset=1)
    ours, theirs = (carrier, source) if branch == "ours" else (source, carrier)
    _assert_position_refusal(
        ours, theirs, monkeypatch, branch=branch, field="satisfied entry",
        digest=clear, source_head=source_head,
    )
    assert _snapshot(carrier).ci_exceptions == original_exceptions


@pytest.mark.parametrize("branch", ["ours", "theirs"])
def test_an_origin_cannot_name_a_matching_mark_after_its_block_source_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str,
) -> None:
    source = _store(tmp_path / "source")
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    _carry_mark(source, carrier)
    source_head, original = _records(source)[-1]
    control = Path(shutil.copytree(carrier, tmp_path / "control"))

    _command(source, commands.MarkOpenInCi("x", "old"))
    later, later_record = _records(source)[-1]
    assert later_record.prev == source_head
    assert later_record == replace(original, prev=source_head, revision=original.revision + 1)
    assert _merge(control, source).replayed_records == 1

    _rewrite_record(carrier, {"origin": later}, recompute=True)
    ours, theirs = (carrier, source) if branch == "ours" else (source, carrier)
    _assert_position_refusal(
        ours, theirs, monkeypatch, branch=branch, field="origin",
        digest=later, source_head=source_head,
    )


@pytest.mark.parametrize(
    ("branch", "source_advanced"),
    [("ours", False), ("theirs", False), ("ours", True)],
    ids=["ours", "theirs", "transitive"],
)
def test_a_satisfied_entry_cannot_name_a_clear_authored_after_incorporating_the_source_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str, source_advanced: bool,
) -> None:
    witness = _store(tmp_path / "witness")
    _command(witness, commands.MarkOpenInCi("x", "old"))
    source = Path(shutil.copytree(witness, tmp_path / "source"))
    _command(witness, commands.SpecifyUnit("a", "1"))
    _command(source, commands.SpecifyUnit("b", "1"))
    source_head = _snapshot(source).event_head
    if source_advanced:
        _command(source, commands.SpecifyUnit("c", "1"))
        assert _records(source)[-1][1].prev == source_head
    merged_head = _snapshot(source).event_head
    _merge(witness, source)
    witness_digest, witness_record = _records(witness)[-1]
    assert isinstance(witness_record.folded_event, MergeRecorded)
    assert witness_record.folded_event.theirs_head == merged_head

    _command(witness, commands.ClearOpenInCi("x"))
    clear, clear_record = _records(witness)[-1]
    assert clear_record.prev == witness_digest
    _command(source, commands.ClearOpenInCi("x"))
    assert source_head not in dict(_records(witness))
    assert clear not in dict(_records(source))

    # In the mirror, OURS supplies the witness and THEIRS makes the claim.
    carrier, incoming = (witness, source) if branch == "ours" else (source, witness)
    assert _merge(carrier, incoming).skipped_records == 1
    marker = _records(carrier)[-1][1].folded_event
    assert isinstance(marker, MergeRecorded)
    _rewrite_record(carrier, {
        "satisfied": [clear],
        "payload": {"ours_head": marker.ours_head, "theirs_head": source_head},
    }, recompute=True, offset=1)

    _assert_position_refusal(
        witness, source, monkeypatch, branch=branch, field="satisfied entry",
        digest=clear, source_head=source_head,
    )


def test_an_origin_cannot_name_a_mark_authored_after_incorporating_the_source_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ours = _store(tmp_path / "ours")
    source = Path(shutil.copytree(ours, tmp_path / "source"))
    _command(ours, commands.SpecifyUnit("a", "1"))
    _command(source, commands.MarkOpenInCi("x", "old"))
    source_head = _snapshot(source).event_head
    _merge(ours, source)
    witness = _snapshot(ours).event_head
    _command(ours, commands.MarkOpenInCi("x", "old"))
    later, later_record = _records(ours)[-1]
    assert later_record.prev == witness
    _command(source, commands.MarkOpenInCi("x", "old"))
    assert source_head not in dict(_records(ours))
    assert later not in dict(_records(source))

    _merge(ours, source)
    marker = _records(ours)[-1][1].folded_event
    assert isinstance(marker, MergeRecorded)
    _rewrite_record(ours, {"origin": later}, recompute=True)
    _rewrite_record(ours, {
        "payload": {"ours_head": marker.ours_head, "theirs_head": source_head},
    }, recompute=True, offset=1)

    _assert_position_refusal(
        ours, source, monkeypatch, branch="ours", field="origin",
        digest=later, source_head=source_head,
    )


@pytest.mark.parametrize("field", ["satisfied", "origin"])
@pytest.mark.parametrize("resolves", [True, False], ids=["resolves", "unresolved"])
def test_check_event_log_requires_claims_in_complete_same_chain_source_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], field: str,
    resolves: bool,
) -> None:
    source = _store(tmp_path / "source")
    _command(source, commands.MarkOpenInCi("x", "old"))
    shared_head = _snapshot(source).event_head
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    if field == "satisfied":
        _command(carrier, commands.ClearOpenInCi("x"))
        _command(source, commands.SpecifyUnit("b", "1"))
    else:
        _command(carrier, commands.SpecifyUnit("a", "1"))
        _command(carrier, commands.MarkOpenInCi("x", "old"))
        _command(source, commands.MarkOpenInCi("x", "old"))
    target = _snapshot(carrier).event_head if resolves else "0" * 64
    _merge(carrier, source)
    marker = _records(carrier)[-1][1].folded_event
    assert isinstance(marker, MergeRecorded)
    changes: dict[str, object] = {
        "payload": {"ours_head": marker.ours_head, "theirs_head": shared_head},
    }
    if field == "satisfied":
        changes["satisfied"] = [target]
    else:
        _rewrite_record(carrier, {"origin": target}, recompute=True)
    _rewrite_record(carrier, changes, recompute=True, offset=1)

    assert main(["check", "--root", str(carrier)]) == 1
    assert any(
        line.startswith("FAIL") and "check.event-log" in line
        and field in line and target in line and shared_head in line
        for line in capsys.readouterr().out.splitlines()
    )
    _assert_position_refusal(
        source, carrier, monkeypatch, branch="theirs",
        field="satisfied entry" if field == "satisfied" else field,
        digest=target, source_head=shared_head,
    )


def test_a_d37_skip_and_inherited_satisfaction_merge_when_the_clear_resolves(
    tmp_path: Path,
) -> None:
    source = _store(tmp_path / "source")
    _command(source, commands.MarkOpenInCi("x", "old"))
    ours = Path(shutil.copytree(source, tmp_path / "ours"))
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    _command(ours, commands.SpecifyUnit("a", "1"))
    _command(ours, commands.ClearOpenInCi("x"))
    _command(source, commands.ClearOpenInCi("x"))
    clear = _snapshot(source).event_head
    assert _merge(ours, source).skipped_records == 1
    assert _records(ours)[-1][1].satisfied == (clear,)

    _command(carrier, commands.SpecifyUnit("c", "1"))
    assert not _merge(carrier, ours).snapshot.ci_exceptions
    assert _records(carrier)[-1][1].satisfied == (clear,)
    _command(carrier, commands.MarkOpenInCi("x", "fresh"))
    _command(source, commands.SpecifyUnit("new", "1"))
    result = _merge(carrier, source)
    assert result.snapshot.unit("new") is not None
    assert tuple(item.reason for item in result.snapshot.ci_exceptions) == ("fresh",)


def test_a_d37_skip_keeps_a_clear_that_predates_the_source_head(tmp_path: Path) -> None:
    source = _store(tmp_path / "source")
    _command(source, commands.MarkOpenInCi("x", "old"))
    ours = Path(shutil.copytree(source, tmp_path / "ours"))
    _command(ours, commands.SpecifyUnit("a", "1"))
    _command(ours, commands.ClearOpenInCi("x"))
    _command(source, commands.ClearOpenInCi("x"))
    clear = _snapshot(source).event_head
    _command(source, commands.SpecifyUnit("b", "1"))
    source_head, source_record = _records(source)[-1]
    assert source_record.prev == clear

    result = _merge(ours, source)
    assert result.skipped_records == 1 and not result.snapshot.ci_exceptions
    marker_digest, marker = _records(ours)[-1]
    assert isinstance(marker.folded_event, MergeRecorded)
    assert marker.folded_event.theirs_head == source_head
    assert marker.satisfied == (clear,)

    # The reverse merge inherits the clear, then incorporates the head that carried it.
    _merge(source, ours)
    _, inherited = _records(source)[-1]
    assert isinstance(inherited.folded_event, MergeRecorded)
    assert inherited.folded_event.theirs_head == marker_digest
    assert clear in inherited.satisfied
    assert marker_digest not in dict(_records(source)) and clear not in dict(_records(ours))

    _command(source, commands.SpecifyUnit("c", "1"))
    assert _merge(ours, source).snapshot.unit("c") is not None


def test_a_third_store_can_verify_inherited_satisfaction_with_the_original_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    skipper = _store(tmp_path / "skipper")
    _command(skipper, commands.MarkOpenInCi("x", "old"))
    source = Path(shutil.copytree(skipper, tmp_path / "source"))
    _command(source, commands.ClearOpenInCi("x"))
    clear = _snapshot(source).event_head
    third = Path(shutil.copytree(source, tmp_path / "third"))

    _command(skipper, commands.SpecifyUnit("a", "1"))
    _command(skipper, commands.ClearOpenInCi("x"))
    assert _merge(skipper, source).skipped_records == 1
    assert _records(skipper)[-1][1].satisfied == (clear,)
    assert clear not in dict(_records(skipper))

    _command(third, commands.SpecifyUnit("c", "1"))
    result = _merge(third, skipper)
    assert result.snapshot.unit("a") is not None and not result.snapshot.ci_exceptions
    assert clear in _records(third)[-1][1].satisfied
    assert main(["check", "--root", str(third)]) == 0
    assert any(
        line.startswith("PASS") and "check.event-log" in line
        for line in capsys.readouterr().out.splitlines()
    )

    # Now both stores together resolve the third store's source and its inherited clear.
    before = (_store_bytes(third), _store_bytes(skipper))
    _forbid_lock(monkeypatch)
    assert merge_store_roots(str(third), str(skipper), actor="reviewer", run_id="") == (
        MergeOutcome("no-op", result.snapshot, 0)
    )
    assert (_store_bytes(third), _store_bytes(skipper)) == before


def test_a_satisfied_entry_cannot_name_a_companion_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = _store(tmp_path / "source")
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    _command(source, commands.OpenDeviation("dev", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    _merge(carrier, source)
    digest, copy = _records(carrier)[-2]
    assert isinstance(copy.folded_event, UnitBlocked) and copy.origin
    _rewrite_record(carrier, {"satisfied": [digest]}, recompute=True, offset=1)

    assert main(["check", "--root", str(carrier)]) == 1
    assert any(
        line.startswith("FAIL") and "check.event-log" in line
        and "satisfied" in line and digest in line
        for line in capsys.readouterr().out.splitlines()
    )


@pytest.mark.parametrize("field", ["satisfied", "origin"])
def test_an_unresolved_claim_is_accepted_when_source_history_is_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str,
) -> None:
    source = _store(tmp_path / "source")
    ours = Path(shutil.copytree(source, tmp_path / "ours"))
    carrier = Path(shutil.copytree(source, tmp_path / "carrier"))
    _command(ours, commands.SpecifyUnit("ours-only", "1"))
    _carry_mark(source, carrier)
    unavailable = "0" * 64
    source_head = _snapshot(source).event_head
    for root in (ours, carrier):
        records = dict(_records(root))
        assert unavailable not in records and source_head not in records
    if field == "satisfied":
        _rewrite_record(carrier, {"satisfied": [unavailable]}, recompute=True, offset=1)
    else:
        _rewrite_record(carrier, {"origin": unavailable}, recompute=True)

    assert main(["check", "--root", str(carrier)]) == 0
    assert any(
        line.startswith("PASS") and "check.event-log" in line
        for line in capsys.readouterr().out.splitlines()
    )
    assert _merge(ours, carrier).snapshot.unit("carrier") is not None
    records = _records(ours)
    if field == "satisfied":
        assert unavailable in records[-1][1].satisfied
    else:
        assert any(record.origin == unavailable for _, record in records)


@pytest.mark.parametrize(
    "origin",
    ["", "0" * 63, "0" * 65, "A" * 64, "z" * 64, 42],
    ids=["empty", "short", "long", "uppercase", "nonhex", "number"],
)
def test_malformed_origin_fails_merge_and_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], origin: object,
) -> None:
    ours = _store(tmp_path / "ours")
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _carry_mark(ours, carrier)
    _rewrite_record(carrier, {"origin": origin}, recompute=False)
    _forbid_lock(monkeypatch)
    with pytest.raises(StoreError, match=r"merge\.theirs-history-invalid: .*origin"):
        merge_store_roots(str(ours), str(carrier), actor="reviewer", run_id="")
    assert main(["check", "--root", str(carrier)]) == 1
    assert any(
        line.startswith("FAIL") and "check.event-log" in line and "origin" in line
        for line in capsys.readouterr().out.splitlines()
    )


@pytest.mark.parametrize(
    ("mode", "value", "detail"),
    [
        ("direct", True, "outside a version-2 merge block"),
        ("direct", False, "expected true"),
        ("copy", 1, "expected boolean"),
        ("copy", "true", "expected boolean"),
        ("copy", True, "command primary"),
    ],
    ids=["outside", "outside-false", "integer", "string", "command-primary"],
)
def test_invalid_generated_fails_merge_and_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    mode: str, value: object, detail: str,
) -> None:
    ours = _store(tmp_path / "ours")
    theirs = Path(shutil.copytree(ours, tmp_path / "theirs"))
    if mode == "copy":
        _carry_mark(ours, theirs)
        _rewrite_record(theirs, {"generated": value}, recompute=value is True)
    else:
        _command(theirs, commands.SpecifyUnit("theirs", "1"))
        snapshot = _snapshot(theirs)
        objects = theirs / "objects" / "sha256"
        raw = decode_raw_document((objects / snapshot.event_head).read_bytes())
        raw["generated"] = value
        data = encode_document(raw)
        digest = digest_of(ArtifactKind.EVENT, data)
        (objects / digest).write_bytes(data)
        (theirs / "state.json").write_bytes(encode_snapshot(replace(snapshot, event_head=digest)))
    _forbid_lock(monkeypatch)
    with pytest.raises(StoreError, match=rf"merge\.theirs-history-invalid: .*generated.*{detail}"):
        merge_store_roots(str(ours), str(theirs), actor="reviewer", run_id="")
    assert main(["check", "--root", str(theirs)]) == 1
    assert any(
        line.startswith("FAIL") and "check.event-log" in line and "generated" in line and detail in line
        for line in capsys.readouterr().out.splitlines()
    )


def test_a_copied_setting_keeps_the_new_mode_and_later_tier(tmp_path: Path) -> None:
    ours = _store(tmp_path / "ours", freeze=False)
    source = Path(shutil.copytree(ours, tmp_path / "source"))
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _command(ours, commands.SpecifyUnit("ours-only", "1"))
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    _command(source, commands.SetAnchorMode(AnchorMode.CI))
    _command(source, commands.SetTier(Tier.STANDARD))
    _merge(ours, source)
    _command(ours, commands.SetAnchorMode(AnchorMode.NONE))
    _command(ours, commands.SetTier(Tier.HIGH))
    _command(source, commands.SpecifyUnit("new", "1"))
    _merge(carrier, source)
    _merge(ours, carrier)
    after = _snapshot(ours)
    assert after.anchor_mode is AnchorMode.NONE and after.tier is Tier.HIGH
    assert after.unit("new") is not None


def test_copied_lifecycle_steps_do_not_run_after_a_refreeze(tmp_path: Path) -> None:
    ours = _store(tmp_path / "ours", freeze=False)
    source = Path(shutil.copytree(ours, tmp_path / "source"))
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _command(ours, commands.SpecifyUnit("ours-only", "1"))
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    for command in (
        commands.FreezeAcceptance("x", "pack/1"),
        commands.MarkReady("x"),
        commands.StartWork("x"),
    ):
        _command(source, command)
    _merge(ours, source)
    for command in (
        commands.OpenDeviation("dev", DeviationType.PACK_DEFECT, UnitRef("x", "1")),
        commands.AmendDeviation("dev", "pack/2"),
        commands.Respecify("x"),
        commands.FreezeAcceptance("x", "pack/2"),
    ):
        _command(ours, command)
    _command(source, commands.SpecifyUnit("new", "1"))
    _merge(carrier, source)
    assert _merge(ours, carrier).incorporated_batches >= 3
    unit = _snapshot(ours).unit("x")
    assert unit is not None and unit.acceptance_pack_version == "pack/2"
    assert unit.status.value == "acceptance-frozen"
    assert _snapshot(ours).unit("new") is not None


def test_an_omitted_companion_root_is_written_and_inherited(tmp_path: Path) -> None:
    ours = _store(tmp_path / "ours")
    source = Path(shutil.copytree(ours, tmp_path / "source"))
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    _command(ours, commands.OpenDeviation("ours", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    _command(source, commands.OpenDeviation("theirs", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    omitted, _ = _records(source)[-1]
    _merge(ours, source)
    assert _records(ours)[-1][1].satisfied == (omitted,)
    _merge(carrier, ours)
    assert _records(carrier)[-1][1].satisfied == (omitted,)


def _start_dependent(base: Path) -> None:
    _command(base, commands.SpecifyUnit("dep", "1", ("x",)))
    _command(base, commands.FreezeAcceptance("dep", "dep-pack/1"))
    _command(base, commands.MarkReady("dep"))
    _command(base, commands.StartWork("dep"))


def _amend_primary(primary: Path, *, actor: str = "author") -> None:
    _command(primary, commands.OpenDeviation("dev", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    _command(primary, commands.AmendDeviation("dev", "pack/2"), actor=actor)


def _dependent_invalidation(root: Path) -> tuple[str, EventRecord]:
    return next(
        (digest, record) for digest, record in _records(root)
        if isinstance(record.folded_event, UnitInvalidated) and record.folded_event.name == "dep"
    )


def test_an_exact_repeat_merge_is_a_no_op_after_respecifying_a_dependent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _store(tmp_path / "base")
    primary = Path(shutil.copytree(base, tmp_path / "primary"))
    ours = Path(shutil.copytree(base, tmp_path / "ours"))
    _start_dependent(base)
    _amend_primary(primary)
    _merge(base, primary)
    assert _dependent_invalidation(base)[1].generated
    _command(ours, commands.SpecifyUnit("ours-only", "1"))
    _merge(ours, base)
    _command(ours, commands.Respecify("dep"))

    _forbid_lock(monkeypatch)
    before = (_store_bytes(ours), _store_bytes(base))
    result = merge_store_roots(str(ours), str(base), actor="reviewer", run_id="")
    assert result == MergeOutcome("no-op", _snapshot(ours), 0)
    assert (_store_bytes(ours), _store_bytes(base)) == before


def test_a_copy_of_a_copy_retains_the_generated_flag_and_root(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    primary = Path(shutil.copytree(base, tmp_path / "primary"))
    carrier = Path(shutil.copytree(base, tmp_path / "carrier"))
    onward = Path(shutil.copytree(base, tmp_path / "onward"))
    _start_dependent(base)
    _amend_primary(primary)
    _merge(base, primary)
    root, generated = _dependent_invalidation(base)
    assert generated.generated and not generated.origin and generated.origin_format is None

    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    _merge(carrier, base)
    _command(onward, commands.SpecifyUnit("onward-only", "1"))
    _merge(onward, carrier)
    for branch in (carrier, onward):
        digest, record = _dependent_invalidation(branch)
        assert digest != root
        assert record.generated and record.origin == root and record.origin_format == 2


def test_skipping_the_amendment_keeps_its_existing_invalidations(tmp_path: Path) -> None:
    base = _store(tmp_path / "base")
    primary = Path(shutil.copytree(base, tmp_path / "primary"))
    carrier = Path(shutil.copytree(base, tmp_path / "carrier"))
    _start_dependent(base)
    ours = Path(shutil.copytree(base, tmp_path / "ours"))
    _amend_primary(primary)
    _merge(base, primary)
    _command(carrier, commands.SpecifyUnit("carrier-only", "1"))
    _merge(carrier, base)
    _merge(ours, primary)
    merge = _merge(ours, carrier)
    assert merge.incorporated_batches > 0
    invalidated = [
        record.folded_event.name for _, record in _records(ours)
        if isinstance(record.folded_event, UnitInvalidated)
    ]
    assert sorted(invalidated) == ["dep", "x"]


def test_skipping_a_batch_checks_generated_effect_when_only_copies_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _store(tmp_path / "base")
    primary = Path(shutil.copytree(base, tmp_path / "primary"))
    ours = Path(shutil.copytree(base, tmp_path / "ours"))
    theirs = Path(shutil.copytree(base, tmp_path / "theirs"))
    _start_dependent(base)
    _amend_primary(primary, actor=base.name)
    with patch.object(merge_module, "datetime") as clock:
        clock.datetime.now.return_value = datetime.datetime.fromisoformat(TIME)
        _merge(base, primary)
    root, _ = _dependent_invalidation(base)
    amendment_root = next(
        digest for digest, record in _records(primary)
        if isinstance(record.folded_event, DeviationAmended)
    )
    for branch in (ours, theirs):
        _command(branch, commands.SpecifyUnit(branch.name, "1"))
        _merge(branch, base)
        digest, companion = _dependent_invalidation(branch)
        amendment = next(
            record for _, record in _records(branch)
            if isinstance(record.folded_event, DeviationAmended)
        )
        assert digest != root and companion.origin == root
        assert amendment.origin == amendment_root
        assert (amendment.actor, amendment.recorded_at, amendment.run_id) == (
            companion.actor, companion.recorded_at, companion.run_id,
        )
    _command(ours, commands.Respecify("dep"))
    _forbid_lock(monkeypatch)
    before = _snapshot(ours)
    result = merge_store_roots(str(ours), str(theirs), actor="reviewer", run_id="")
    assert isinstance(result, Refused)
    assert result.violations[0].code == "merge.conflict"
    assert "UnitInvalidated name='dep'" in result.violations[0].detail
    assert _snapshot(ours) == before


def test_a_mixed_format_store_fences_the_old_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = decode_raw_document((FIXTURES / "format6-direct-event.json").read_bytes())
    state = cast(dict[str, object], fixture["state"])
    event = encode_document(fixture["event"])
    root = tmp_path / "store"
    with Store.init(str(root)) as store, store.write_locked():
        assert store.put_object(ArtifactKind.EVENT, event) == state["event_head"]
    (root / "state.json").write_bytes(encode_document(state))
    (root / "state-format.json").write_text("6\n")
    assert main(["upgrade", "--root", str(root)]) == 0
    assert "version-1 merge markers: 0" in capsys.readouterr().out.splitlines()
    assert decode_raw_document((root / "state.json").read_bytes()) == {**state, "state_format": 7}
    _command(root, commands.SpecifyUnit("after", "1"))
    assert [record.event_format for _, record in _records(root)] == [1, 2]
    before = (root / "state.json").read_bytes()
    events = {item.name: item.read_bytes() for item in (root / "objects" / "sha256").iterdir()}
    # Exercise the format-6 writer's D-13 state guard on the normal CLI path.
    monkeypatch.setattr(codec, "CURRENT_STATE_FORMAT", 6)
    assert main(["specify", "--root", str(root), "--name", "old", "--version", "1"]) == 2
    assert "format 7 was written by a newer vouch" in capsys.readouterr().err
    assert (root / "state.json").read_bytes() == before
    assert {item.name: item.read_bytes() for item in (root / "objects" / "sha256").iterdir()} == events


def _git(project: Path, *args: str, expect: int = 0) -> str:
    result = subprocess.run(
        ("git", "-C", str(project), *args), text=True, capture_output=True, check=False,
    )
    assert result.returncode == expect, (args, result.stdout, result.stderr)
    return result.stdout


@pytest.mark.parametrize("operation", ["merge", "rebase"])
@pytest.mark.parametrize("generations", [1, 5])
def test_git_stage_replay_preserves_the_receiver_clear(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], operation: str, generations: int,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Vouch Test")
    _git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    root = _store(project / ".vouch")
    _git(project, "add", "--", ".gitignore", ".vouch")
    _git(project, "commit", "-m", "Base")
    source = Path(shutil.copytree(root, tmp_path / "source"))
    _command(source, commands.MarkOpenInCi("x", "old"), actor="source")
    origin = _snapshot(source).event_head
    _command(source, commands.SpecifyUnit("independent", "1"))

    _git(project, "checkout", "-b", "incoming")
    intermediate_units = {f"carrier-{generation}" for generation in range(1, generations)}
    for name in sorted(intermediate_units):
        carrier = Path(shutil.copytree(root, tmp_path / name))
        _command(carrier, commands.SpecifyUnit(name, "1"))
        _merge(carrier, source)
        assert [
            record.origin for _, record in _records(carrier)
            if isinstance(record.folded_event, OpenInCiMarked)
        ] == [origin]
        source = carrier
    _command(root, commands.SpecifyUnit("carrier", "1"))
    _merge(root, source)
    _git(project, "add", "--", ".vouch")
    _git(project, "commit", "-m", "Carry the old mark")

    _git(project, "checkout", "main")
    _command(root, commands.MarkOpenInCi("x", "old"), actor="source")
    _command(root, commands.ClearOpenInCi("x"))
    _git(project, "add", "--", ".vouch")
    _git(project, "commit", "-m", "Record and clear the mark")
    ours_prefix = _records(root)
    if operation == "merge":
        _git(project, "merge", "--no-edit", "incoming", expect=1)
    else:
        _git(project, "checkout", "incoming")
        _git(project, "rebase", "main", expect=1)
    index = _git(project, "ls-files", "--stage", "--", ".vouch")
    assert " 2\t.vouch/state.json" in index and " 3\t.vouch/state.json" in index

    assert main(["merge-git-stages", "--root", str(root), "--actor", "recovery"]) == 0
    assert "already-incorporated batch(es)" in capsys.readouterr().out
    assert _git(project, "ls-files", "--stage", "--", ".vouch") == index
    assert _records(root)[:len(ours_prefix)] == ours_prefix
    assert sum(isinstance(record.folded_event, OpenInCiMarked) for _, record in _records(root)) == 1
    after = _snapshot(root)
    assert not after.ci_exceptions
    assert {unit.name for unit in after.units} == {"x", "carrier", "independent"} | intermediate_units
    assert main(["check", "--root", str(root)]) == 0
    assert any(
        line.startswith("PASS") and "check.event-log" in line
        for line in capsys.readouterr().out.splitlines()
    )


@pytest.mark.parametrize("source_advanced", [False, True], ids=["reverse-block", "origin"])
def test_merge_json_reports_batches_newly_excluded_by_origin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source_advanced: bool,
) -> None:
    ours = _store(tmp_path / "ours")
    source = Path(shutil.copytree(ours, tmp_path / "source"))
    carrier = Path(shutil.copytree(ours, tmp_path / "carrier"))
    _command(source, commands.MarkOpenInCi("x", "old"))
    assert main(["merge", "--root", str(ours), "--other", str(source), "--json"]) == 0
    normal = json.loads(capsys.readouterr().out)
    assert "incorporated_batches" not in normal
    if source_advanced:
        _command(source, commands.SpecifyUnit("later", "1"))
    _command(carrier, commands.SpecifyUnit("carrier", "1"))
    _merge(carrier, source)
    _command(ours, commands.ClearOpenInCi("x"))
    assert main(["merge", "--root", str(ours), "--other", str(carrier), "--json"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    if source_advanced:
        assert filtered["incorporated_batches"] == 1
    else:
        # Reverse provenance already excludes the mark and its closing marker.
        assert "incorporated_batches" not in filtered
    assert filtered["skipped_records"] == 0
