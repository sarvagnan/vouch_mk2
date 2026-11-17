"""Non-pack checks for D-37's replay state, atomicity and source claims."""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import commands, events
from vouch_mk2.core.decide import Accepted, Refused, apply, decide
from vouch_mk2.core.model import DeviationType, Snapshot, UnitRef
from vouch_mk2.store import Store, decode_event_record
from vouch_mk2.store.digests import ArtifactKind
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots


def _execute(root: Path, command: commands.Command, *, actor: str = "source") -> None:
    with Store.open(str(root)) as store:
        with store.write_locked():
            before = store.read_snapshot()
            decision = decide(before, command)
            assert isinstance(decision, Accepted), decision
            commit_events(
                store, before, decision.events, actor=actor, run_id=f"{actor}-run",
                recorded_at=f"{actor}-time",
            )


def _snapshot(root: Path) -> Snapshot:
    with Store.open(str(root), read_only=True) as store:
        return store.read_snapshot()


def _chain(root: Path) -> list[tuple[str, events.EventRecord]]:
    result: list[tuple[str, events.EventRecord]] = []
    with Store.open(str(root), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest and digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            result.append((digest, record))
            digest = record.prev
    return result[::-1]


def _branches(tmp_path: Path, *, diverge: bool = True) -> tuple[Path, Path]:
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    Store.init(str(ours)).close()
    _execute(ours, commands.SpecifyUnit("u", "1"))
    _execute(ours, commands.FreezeAcceptance("u", "pack/1"))
    _execute(ours, commands.MarkOpenInCi("u", "shared reason"))
    shutil.copytree(ours, theirs)
    if diverge:
        _execute(ours, commands.SpecifyUnit("ours-only", "1"), actor="ours")
        _execute(ours, commands.ClearOpenInCi("u"), actor="ours")
    return ours, theirs


def _merge(ours: Path, theirs: Path) -> MergeOutcome | Refused:
    return merge_store_roots(str(ours), str(theirs), actor="merger", run_id="merge-run")


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("remark", (False, True))
def test_skipped_clear_writes_no_record_and_the_chain_derives_the_snapshot(
    tmp_path: Path, remark: bool,
) -> None:
    """R1/R3's intended chain checks, stopping at the format's GENESIS sentinel."""
    ours, theirs = _branches(tmp_path)
    _execute(theirs, commands.ClearOpenInCi("u"))
    if remark:
        _execute(theirs, commands.MarkOpenInCi("u", "re-marked on theirs"))
    before = _snapshot(ours)
    source_head = _snapshot(theirs).event_head

    result = _merge(ours, theirs)

    assert isinstance(result, MergeOutcome)
    assert result.mode == "replayed"
    assert result.skipped_records == 1
    assert result.replayed_records == int(remark)
    assert result.snapshot.revision == before.revision + int(remark) + 1
    assert {item.unit_name: item.reason for item in result.snapshot.ci_exceptions} == (
        {"u@1": "re-marked on theirs"} if remark else {}
    )
    chain = _chain(ours)
    kinds = [type(record.folded_event).__name__ for _, record in chain]
    assert kinds.count("OpenInCiCleared") == 1
    if remark:
        assert kinds[-3:] == ["OpenInCiCleared", "OpenInCiMarked", "MergeRecorded"]
    else:
        assert kinds[-2:] == ["OpenInCiCleared", "MergeRecorded"]
    merge_event = chain[-1][1].folded_event
    assert isinstance(merge_event, events.MergeRecorded)
    assert (merge_event.ours_head, merge_event.theirs_head) == (before.event_head, source_head)
    folded = Snapshot()
    for digest, record in chain:
        folded = apply(
            folded, (record.folded_event,), event_digests=(digest,),
            event_run_ids=(record.run_id,),
        )
    assert folded == result.snapshot == _snapshot(ours)


def test_skip_does_not_shift_claims_on_later_leading_or_companion_records(tmp_path: Path) -> None:
    ours, theirs = _branches(tmp_path)
    _execute(theirs, commands.ClearOpenInCi("u"), actor="clearer")
    _execute(theirs, commands.MarkOpenInCi("u", "new reason"), actor="marker")
    _execute(
        theirs, commands.OpenDeviation("d", DeviationType.PACK_DEFECT, UnitRef("u", "1")),
        actor="deviation-author",
    )
    source = [record for _, record in _chain(theirs)[-3:]]
    assert [type(record.folded_event).__name__ for record in source] == [
        "OpenInCiMarked", "DeviationOpened", "UnitBlocked",
    ]
    before_count = len(_chain(ours))

    result = _merge(ours, theirs)

    assert isinstance(result, MergeOutcome)
    assert (result.skipped_records, result.replayed_records) == (1, 3)
    added = [record for _, record in _chain(ours)[before_count:]]
    assert len(added) == 4
    for original, replayed in zip(source, added[:-1], strict=True):
        assert replayed.folded_event == original.folded_event
        assert (replayed.actor, replayed.run_id, replayed.recorded_at) == (
            original.actor, original.run_id, original.recorded_at,
        )
    assert (added[-1].actor, added[-1].run_id) == ("merger", "merge-run")
    assert all(record.actor != "clearer" for record in added)


def test_later_clears_use_the_evolving_replay_state(tmp_path: Path) -> None:
    ours, theirs = _branches(tmp_path)
    _execute(theirs, commands.ClearOpenInCi("u"))
    for reason in ("first re-mark", "second re-mark"):
        _execute(theirs, commands.MarkOpenInCi("u", reason))
        _execute(theirs, commands.ClearOpenInCi("u"))

    result = _merge(ours, theirs)

    assert isinstance(result, MergeOutcome)
    assert (result.skipped_records, result.replayed_records) == (1, 4)
    assert not result.snapshot.ci_exceptions
    assert sum(isinstance(record.folded_event, events.OpenInCiCleared)
               for _, record in _chain(ours)) == 3


@pytest.mark.parametrize("skip", (False, True))
def test_source_companions_are_still_omitted_when_ours_is_already_blocked(
    tmp_path: Path, skip: bool,
) -> None:
    ours, theirs = _branches(tmp_path, diverge=skip)
    if skip:
        _execute(theirs, commands.ClearOpenInCi("u"), actor="clearer")
    _execute(
        theirs, commands.OpenDeviation("theirs-d", DeviationType.PACK_DEFECT, UnitRef("u", "1")),
        actor="deviation-author",
    )
    original = _chain(theirs)[-2][1]
    _execute(
        ours, commands.OpenDeviation("ours-d", DeviationType.PACK_DEFECT, UnitRef("u", "1")),
        actor="ours",
    )
    before_count = len(_chain(ours))

    result = _merge(ours, theirs)

    assert isinstance(result, MergeOutcome)
    assert result.skipped_records == int(skip)
    added = [record for _, record in _chain(ours)[before_count:]]
    assert [type(record.folded_event).__name__ for record in added] == [
        "DeviationOpened", "MergeRecorded",
    ]
    assert (added[0].actor, added[0].run_id, added[0].recorded_at) == (
        original.actor, original.run_id, original.recorded_at,
    )
    assert {deviation.deviation_id for deviation in result.snapshot.deviations} == {
        "ours-d", "theirs-d",
    }


def test_a_later_refusal_after_a_skip_writes_nothing(tmp_path: Path) -> None:
    ours, theirs = _branches(tmp_path)
    _execute(theirs, commands.ClearOpenInCi("u"))
    _execute(theirs, commands.SpecifyUnit("clash", "theirs"))
    _execute(ours, commands.SpecifyUnit("clash", "ours"), actor="ours")
    (ours / ".lock").unlink()
    before_ours, before_theirs = _files(ours), _files(theirs)

    result = _merge(ours, theirs)

    assert isinstance(result, Refused)
    assert result.violations[0].code == "merge.conflict"
    assert _files(ours) == before_ours
    assert _files(theirs) == before_theirs


def test_a_legacy_bare_name_clear_is_not_skipped_when_the_exception_is_absent(
    tmp_path: Path,
) -> None:
    ours, theirs = _branches(tmp_path)
    with Store.open(str(theirs)) as store:
        with store.write_locked():
            commit_events(
                store, store.read_snapshot(), (events.OpenInCiCleared("u"),),
                actor="legacy", run_id="",
            )
    (ours / ".lock").unlink()
    before = _files(ours)

    result = _merge(ours, theirs)

    assert isinstance(result, Refused)
    assert result.violations[0].code == "merge.conflict"
    assert _files(ours) == before


@pytest.mark.parametrize("mode", ("no-op", "fast-forward", "replayed"))
def test_json_always_reports_zero_skips_when_no_clear_is_skipped(tmp_path: Path, mode: str) -> None:
    ours, theirs = _branches(tmp_path, diverge=mode == "replayed")
    if mode != "no-op":
        _execute(theirs, commands.SpecifyUnit("theirs-only", "1"))
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(["merge", "--root", str(ours), "--other", str(theirs), "--json"])
    assert code == 0
    document = json.loads(output.getvalue())
    assert document["mode"] == mode
    assert document["skipped_records"] == 0
    assert document["replayed_records"] == int(mode == "replayed")
