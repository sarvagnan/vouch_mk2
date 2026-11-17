"""D-38 boundary cases outside the frozen acceptance pack."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from vouch_mk2 import git_merge
from vouch_mk2.cli import main
from vouch_mk2.core import commands, events
from vouch_mk2.core.decide import Accepted, decide
from vouch_mk2.core.model import DeviationType, UnitRef
from vouch_mk2.store import Store, decode_event_record
from vouch_mk2.store.digests import ArtifactKind
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots


@dataclass(frozen=True)
class _Logged:
    digest: str
    record: events.EventRecord


def _execute(root: Path, command: commands.Command) -> None:
    with Store.open(str(root)) as store, store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, command)
        assert isinstance(decision, Accepted), decision
        commit_events(
            store, snapshot, decision.events, actor=root.name,
            run_id=f"{root.name}-run", recorded_at=f"{root.name}-time",
        )


def _specify(root: Path, name: str, *depends_on: str) -> None:
    _execute(root, commands.SpecifyUnit(name, "1", depends_on))


def _open_deviation(root: Path, name: str) -> None:
    _execute(root, commands.OpenDeviation(name, DeviationType.PACK_DEFECT, UnitRef("x", "1")))


def _base(tmp_path: Path) -> Path:
    root = tmp_path / "a"
    Store.init(str(root)).close()
    _specify(root, "x")
    return root


def _fork(root: Path, name: str) -> Path:
    other = root.with_name(name)
    shutil.copytree(root, other)
    return other


def _chain(root: Path) -> list[_Logged]:
    chain: list[_Logged] = []
    with Store.open(str(root), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            chain.append(_Logged(digest, record))
            digest = record.prev
    return chain[::-1]


def _merge(ours: Path, theirs: Path) -> MergeOutcome:
    result = merge_store_roots(str(ours), str(theirs), actor=ours.name, run_id=f"{ours.name}-merge")
    assert isinstance(result, MergeOutcome), result
    assert result.mode == "replayed", result
    return result


def _marker(item: _Logged) -> events.MergeRecorded:
    event = item.record.folded_event
    assert isinstance(event, events.MergeRecorded), event
    return event


def _claims(item: _Logged) -> tuple[str, str, str]:
    return item.record.actor, item.record.run_id, item.record.recorded_at


def _assert_translation(original: _Logged, translated: _Logged, boundary: _Logged) -> None:
    assert _marker(translated) == events.MergeRecorded(boundary.digest, _marker(original).theirs_head)
    assert _claims(translated) == _claims(original)


def _assert_specified_once(root: Path, *names: str) -> None:
    assert sorted(
        event.name for item in _chain(root)
        if isinstance(event := item.record.folded_event, events.UnitSpecified)
    ) == sorted(names)


def test_boundary_in_the_shared_prefix_uses_receiving_head(tmp_path: Path) -> None:
    a = _base(tmp_path)
    b = _fork(a, "b")
    _specify(a, "shared-later")
    c = _fork(a, "c")
    shared = _chain(c)[-1]
    _specify(b, "b")
    _specify(c, "c")
    c_head = _chain(c)[-1]
    _merge(a, b)
    original = _chain(a)[-1]
    assert _marker(original).ours_head == shared.digest

    _merge(c, a)

    _assert_translation(original, _chain(c)[-2], c_head)
    result = _merge(b, c)
    assert result.replayed_records == 3  # shared-later, c, and C<-A
    _assert_specified_once(b, "x", "b", "c", "shared-later")


def test_boundary_in_a_filtered_block_uses_head_at_that_position(tmp_path: Path) -> None:
    a = _base(tmp_path)
    b, c, d = (_fork(a, name) for name in ("b", "c", "d"))
    for root in (a, b, c, d):
        _specify(root, root.name)
    _merge(a, b)
    _merge(c, a)
    filtered = _chain(c)[-1]
    _merge(c, d)
    original = _chain(c)[-1]
    assert _marker(original).ours_head == filtered.digest
    prefix = _chain(a)

    _merge(a, c)

    chain = _chain(a)
    assert chain[:len(prefix)] == prefix
    replayed_c, replayed_d, translated, _final = chain[len(prefix):]
    assert replayed_c.record.folded_event == events.UnitSpecified("c", "1", ())
    assert replayed_d.record.folded_event == events.UnitSpecified("d", "1", ())
    _assert_translation(original, translated, replayed_c)
    _merge(d, a)
    _assert_specified_once(d, "x", "a", "b", "c", "d")


def test_empty_replayed_block_points_to_marker_predecessor(tmp_path: Path) -> None:
    a = _base(tmp_path)
    _execute(a, commands.FreezeAcceptance("x", "pack/1"))
    _execute(a, commands.MarkOpenInCi("x", "shared exception"))
    b, c = (_fork(a, name) for name in ("b", "c"))
    _specify(a, "before-block")
    _execute(b, commands.ClearOpenInCi("x"))
    _execute(c, commands.ClearOpenInCi("x"))
    _specify(c, "receiving-prefix")
    prefix = _chain(c)
    _merge(a, b)
    original = _chain(a)[-1]

    result = _merge(c, a)

    assert result.skipped_records == 1
    before_block, translated, _final = _chain(c)[len(prefix):]
    assert before_block.record.folded_event == events.UnitSpecified("before-block", "1", ())
    _assert_translation(original, translated, before_block)
    assert translated.record.prev == before_block.digest
    reverse = _merge(b, c)
    assert reverse.replayed_records == 3  # receiving-prefix, before-block, and C<-A
    _assert_specified_once(b, "x", "before-block", "receiving-prefix")


def test_overlapping_nested_blocks_filter_the_union_and_keep_adjacent_work(tmp_path: Path) -> None:
    a = _base(tmp_path)
    b, c, d = (_fork(a, name) for name in ("b", "c", "d"))
    for root in (a, b, c, d):
        _specify(root, root.name)
    _merge(a, b)
    _merge(c, a)
    _specify(c, "after-inner")
    _merge(d, c)
    _specify(d, "after-outer")
    d_chain = _chain(d)
    inner, outer = [item for item in d_chain if isinstance(item.record.folded_event, events.MergeRecorded)][:2]
    positions = {item.digest: index for index, item in enumerate(d_chain)}
    assert positions[_marker(outer).ours_head] < positions[_marker(inner).ours_head]
    assert positions[inner.digest] < positions[outer.digest]
    prefix = _chain(a)

    result = _merge(a, d)  # a incorporates both auxiliary parents of the nested intervals

    chain = _chain(a)
    assert chain[:len(prefix)] == prefix
    assert result.replayed_records == 5  # d, c, after-inner, D<-C and after-outer
    _assert_specified_once(a, "x", "a", "b", "c", "d", "after-inner", "after-outer")


def test_generated_companion_after_boundary_stays_with_its_command(tmp_path: Path) -> None:
    a = _base(tmp_path)
    _open_deviation(a, "d")
    b, c = (_fork(a, name) for name in ("b", "c"))
    _execute(a, commands.AmendDeviation("d", "am1"))
    _specify(b, "b")
    _specify(c, "z", "x")
    a_boundary = _chain(a)[-1]
    c_prefix = _chain(c)
    _merge(a, b)
    original = _chain(a)[-1]
    assert _marker(original).ours_head == a_boundary.digest

    _merge(c, a)

    _amended, replayed_x, generated_z, _replayed_b, translated, final = _chain(c)[len(c_prefix):]
    assert isinstance(replayed_x.record.folded_event, events.UnitInvalidated)
    assert replayed_x.record.folded_event.name == "x"
    assert isinstance(generated_z.record.folded_event, events.UnitInvalidated)
    assert generated_z.record.folded_event.name == "z"
    assert _claims(replayed_x) == _claims(a_boundary)
    assert _claims(generated_z) == _claims(final)
    _assert_translation(original, translated, replayed_x)
    result = _merge(b, c)
    _assert_specified_once(b, "x", "b", "z")
    assert result.replayed_records == 5  # z, the three-fact amendment, and C<-A


def test_omitted_companion_boundary_uses_head_after_the_remaining_facts(tmp_path: Path) -> None:
    a = _base(tmp_path)
    b, c = (_fork(a, name) for name in ("b", "c"))
    _open_deviation(a, "a-d")
    _open_deviation(c, "c-d")
    _specify(b, "b")
    a_boundary = _chain(a)[-1]
    c_prefix = _chain(c)
    assert isinstance(a_boundary.record.folded_event, events.UnitBlocked)
    _merge(a, b)
    original = _chain(a)[-1]
    assert _marker(original).ours_head == a_boundary.digest

    _merge(c, a)

    opened, _replayed_b, translated, _final = _chain(c)[len(c_prefix):]
    assert isinstance(opened.record.folded_event, events.DeviationOpened)
    assert opened.record.folded_event.deviation_id == "a-d"
    _assert_translation(original, translated, opened)
    result = _merge(b, c)
    assert {deviation.deviation_id for deviation in result.snapshot.deviations} == {"a-d", "c-d"}
    _assert_specified_once(b, "x", "b")


def test_git_index_recovery_translates_the_same_boundary_as_directory_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _base(tmp_path)
    b, c = (_fork(a, name) for name in ("b", "c"))
    for root in (a, b, c):
        _specify(root, root.name)
    _merge(a, b)
    original = _chain(a)[-1]
    prefix = _chain(c)
    expected = _fork(c, "expected")
    _merge(expected, a)

    blobs: dict[bytes, bytes] = {}
    index: list[bytes] = []

    def add_entry(path: str, payload: bytes, stage: int) -> None:
        oid = hashlib.sha256(payload).hexdigest().encode()
        blobs[oid] = payload
        index.append(b"100644 %s %d\tc/%s\0" % (oid, stage, path.encode()))

    common_files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for root in (a, c)
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"state.json", ".lock"}
    }
    for path, payload in sorted(common_files.items()):
        add_entry(path, payload, 0)
    for root, stage in ((c, 2), (a, 3)):
        add_entry("state.json", (root / "state.json").read_bytes(), stage)
    index_reads = 0

    def git_output(repo: str, *argv: bytes) -> bytes:
        nonlocal index_reads
        assert Path(repo) == c.parent
        if argv == (b"rev-parse", b"--show-prefix"):
            return b"\n"
        if argv == (b"ls-files", b"--stage", b"-z", b"--", b"c"):
            index_reads += 1
            return b"".join(index)
        assert argv[:2] == (b"cat-file", b"blob") and len(argv) == 3, argv
        return blobs[argv[2]]

    monkeypatch.setattr(git_merge, "_git_output", git_output)
    assert main(["merge-git-stages", "--root", str(c), "--actor", "recovery"]) == 0

    chain = _chain(c)
    assert index_reads == 2
    assert chain[:-1] == _chain(expected)[:-1]
    assert chain[:len(prefix)] == prefix
    replayed_a, _replayed_b, translated, final = chain[len(prefix):]
    _assert_translation(original, translated, replayed_a)
    assert _marker(final) == events.MergeRecorded(prefix[-1].digest, original.digest)
    assert final.record.actor == "recovery"
    _merge(b, c)
    _assert_specified_once(b, "x", "a", "b", "c")
