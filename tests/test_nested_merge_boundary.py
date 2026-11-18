"""Frozen acceptance pack for self-hosted unit nested-merge-boundary@1 (D-38).

Found by Sol and Fable reviewing D-33 (``vouch_mk2-bd4.4.11``): A<-B and C<-A replay, then B<-C
refused ``merge.theirs-history-invalid``. A replayed ``MergeRecorded`` kept its source ``ours_head``,
a digest that is not on the receiving chain, so a later merge could not locate the block it
bounded. Every test here failed against the pre-change tree, except tests marked ``# guard:``.
The implementer may not edit this file.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import commands
from vouch_mk2.core.decide import Accepted, Refused, decide
from vouch_mk2.core.events import EventRecord, MergeRecorded, UnitInvalidated, UnitSpecified
from vouch_mk2.core.model import CiPolicy, DeviationType, UnitRef
from vouch_mk2.store import Store, decode_event_record, encode_document
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "nested-merge" / "legacy-untranslated.json"
V1_MERGES = ROOT / "tests" / "fixtures" / "nested-merge" / "legacy-v1-merges.json"
T = "2026-11-18T00:00:00+00:00"
_CI_RUN_ID_VARIABLES = ("GITHUB_RUN_ID", "CI_PIPELINE_ID", "BUILDKITE_BUILD_ID", "CIRCLE_WORKFLOW_ID")


@pytest.fixture(autouse=True)
def _no_ci_run_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common rule: no CI run-id variable is set."""
    for name in _CI_RUN_ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@dataclass(frozen=True)
class _Logged:
    digest: str
    record: EventRecord


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _cmd(root: Path, verb: str, *argv: str) -> str:
    code, output = _run(verb, "--root", str(root), "--actor", root.name, *argv)
    assert code == 0, (root.name, verb, argv, output)
    return output


def _merge(ours: Path, theirs: Path) -> tuple[int, str]:
    return _run("merge", "--root", str(ours), "--other", str(theirs), "--actor", ours.name)


def _chain(root: Path) -> list[_Logged]:
    """Every record of the store's chain, oldest first."""
    items: list[_Logged] = []
    with Store.open(str(root), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest and digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            items.append(_Logged(digest, record))
            digest = record.prev
    return items[::-1]


def _units(root: Path) -> list[str]:
    with Store.open(str(root), read_only=True) as store:
        return sorted(f"{unit.name}@{unit.version}" for unit in store.read_snapshot().units)


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stores(tmp_path: Path, *names: str) -> Iterator[Path]:
    """A fresh store holding ``shared@1``, then one copy per name, each specifying its own unit."""
    first = tmp_path / names[0]
    Store.init(str(first)).close()
    _cmd(first, "specify", "--name", "shared", "--version", "1")
    for name in names[1:]:
        shutil.copytree(first, tmp_path / name)
    for name in names:
        root = tmp_path / name
        _cmd(root, "specify", "--name", name.lower(), "--version", "1")
        yield root


def _merge_markers(root: Path, after: int) -> list[_Logged]:
    return [
        item for item in _chain(root)[after:] if isinstance(item.record.folded_event, MergeRecorded)
    ]


def test_three_store_nested_merges_all_replay(tmp_path: Path) -> None:
    """R1."""
    a, b, c = _stores(tmp_path, "A", "B", "C")
    for ours, theirs in ((a, b), (c, a), (b, c)):
        code, output = _merge(ours, theirs)
        assert code == 0, (ours.name, theirs.name, output)
        assert "merge: replayed" in output, output
    assert _units(b) == ["a@1", "b@1", "c@1", "shared@1"]
    kinds = [type(item.record.folded_event).__name__ for item in _chain(b)]
    assert kinds.count("UnitSpecified") == 4, kinds


def test_a_replayed_merge_record_names_a_boundary_on_the_receiving_chain(tmp_path: Path) -> None:
    """R2."""
    a, b, c = _stores(tmp_path, "A", "B", "C")
    assert _merge(a, b)[0] == 0
    source = _merge_markers(a, 0)
    assert len(source) == 1
    source_event = source[0].record.folded_event
    assert isinstance(source_event, MergeRecorded)
    boundary = next(item for item in _chain(a) if item.digest == source_event.ours_head)
    assert isinstance(boundary.record.folded_event, UnitSpecified)
    assert boundary.record.folded_event.name == "a"
    c_head, a_head = _chain(c)[-1].digest, _chain(a)[-1].digest
    before = len(_chain(c))
    assert _merge(c, a)[0] == 0
    chain = _chain(c)
    markers = _merge_markers(c, before)
    assert len(markers) == 2
    replayed, final = markers
    event = replayed.record.folded_event
    assert isinstance(event, MergeRecorded)
    counterpart = next(
        item for item in chain[before:]
        if isinstance(item.record.folded_event, UnitSpecified) and item.record.folded_event.name == "a"
    )
    assert event.ours_head == counterpart.digest
    assert event.theirs_head == source_event.theirs_head
    assert replayed.record.actor == source[0].record.actor
    final_event = final.record.folded_event
    assert isinstance(final_event, MergeRecorded)
    assert final_event.ours_head == c_head
    assert final_event.theirs_head == a_head


def test_a_filtered_source_boundary_translates_to_the_staging_head(tmp_path: Path) -> None:
    """R26: the source boundary is a record ours already holds, so replay filters it."""
    a, b, c = _stores(tmp_path, "A", "B", "C")
    assert _merge(c, a)[0] == 0
    assert _merge(a, b)[0] == 0
    c_head = _chain(c)[-1].digest
    before = len(_chain(c))
    assert _merge(c, a)[0] == 0
    markers = _merge_markers(c, before)
    assert len(markers) == 2
    event = markers[0].record.folded_event
    assert isinstance(event, MergeRecorded)
    assert event.ours_head == c_head


def test_later_work_on_every_store_merges_again_without_duplicates(tmp_path: Path) -> None:
    """R3 (pack/2, DEV-nmb-01)."""
    a, b, c = _stores(tmp_path, "A", "B", "C")
    for ours, theirs in ((a, b), (c, a), (b, c)):
        assert _merge(ours, theirs)[0] == 0, (ours.name, theirs.name)
    for root in (a, b, c):
        _cmd(root, "specify", "--name", f"{root.name.lower()}2", "--version", "1")
    for ours, theirs in ((c, a), (b, c), (a, b), (c, b)):
        code, output = _merge(ours, theirs)
        assert code == 0, (ours.name, theirs.name, output)
    expected = ["a2@1", "a@1", "b2@1", "b@1", "c2@1", "c@1", "shared@1"]
    for root in (a, b, c):
        assert _units(root) == expected, root.name
        kinds = [type(item.record.folded_event).__name__ for item in _chain(root)]
        assert kinds.count("UnitSpecified") == len(expected), (root.name, kinds)


def test_a_fully_incorporated_repeat_merge_is_a_byte_stable_no_op(tmp_path: Path) -> None:
    """R4."""
    a, b, c = _stores(tmp_path, "A", "B", "C")
    for ours, theirs in ((a, b), (c, a), (b, c)):
        assert _merge(ours, theirs)[0] == 0, (ours.name, theirs.name)
    (b / ".lock").unlink(missing_ok=True)
    before = _files(b)
    code, output = _merge(b, c)
    assert code == 0, output
    assert "merge: no-op" in output, output
    (b / ".lock").unlink(missing_ok=True)
    assert _files(b) == before


def _materialize(tmp_path: Path, fixture: Path, names: tuple[str, ...]) -> tuple[Path, ...]:
    """Write the named fixture stores under tmp_path, then upgrade each to the current format."""
    document = json.loads(fixture.read_text())
    roots: list[Path] = []
    for name in names:
        root = tmp_path / name
        for relative, encoded in document[name].items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded))
        code, output = _run("upgrade", "--root", str(root))
        assert code == 0, (name, output)
        roots.append(root)
    return tuple(roots)


def _legacy(tmp_path: Path) -> tuple[Path, Path, _Logged]:
    """Stores B and C written by the pre-change code after A<-B and C<-A, and C's offending record."""
    b, c = _materialize(tmp_path, FIXTURE, ("B", "C"))
    chain = _chain(c)
    digests = [item.digest for item in chain]
    offending = [
        item
        for index, item in enumerate(chain)
        if isinstance(item.record.folded_event, MergeRecorded)
        and item.record.folded_event.ours_head not in digests[:index]
    ]
    assert len(offending) == 1
    return b, c, offending[0]


def test_a_legacy_untranslated_record_stays_readable(tmp_path: Path) -> None:
    """R5. # guard:"""
    _b, c, _offending = _legacy(tmp_path)
    assert _units(c) == ["a@1", "b@1", "c@1", "shared@1"]
    code, output = _run("check", "--root", str(c))
    rows = [line for line in output.splitlines() if "check.event-log" in line]
    assert rows and rows[0].startswith("PASS"), output


def test_a_merge_needing_a_legacy_boundary_refuses_naming_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6."""
    b, c, offending = _legacy(tmp_path)
    before_b, before_c = _files(b), _files(c)
    entered: list[str] = []
    original = Store.write_locked

    def spy(self: Store) -> AbstractContextManager[None]:
        entered.append("write_locked")
        return original(self)

    monkeypatch.setattr(Store, "write_locked", spy)
    code, output = _merge(b, c)
    monkeypatch.setattr(Store, "write_locked", original)
    assert code == 2, output
    assert "merge.theirs-history-invalid" in output, output
    assert offending.digest[:16] in output, output
    assert entered == []
    assert _files(b) == before_b
    assert _files(c) == before_c


def test_quickstart_states_the_replayed_merge_boundary() -> None:
    """R7."""
    text = " ".join((ROOT / "docs" / "quickstart.md").read_text().split())
    assert (
        "A replayed merge record names, as its OURS head, the record on the receiving chain where"
        " its replayed block begins" in text
    )


# --------------------------------------------------------------------------- D-41 (pack/3)
# Library-level scenarios with a fixed recorded time, so copies and originals are comparable.


def _ex(root: Path, command: commands.Command, *, actor: str | None = None) -> None:
    with Store.open(str(root)) as store, store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, command)
        assert isinstance(decision, Accepted), (root.name, command, decision)
        commit_events(
            store, snapshot, decision.events,
            actor=actor or root.name, run_id="", recorded_at=T,
        )


def _spec(root: Path, name: str, *depends_on: str) -> None:
    _ex(root, commands.SpecifyUnit(name, "1", depends_on))


def _base(tmp_path: Path, *forks: str) -> tuple[Path, ...]:
    """Store A with x@1 specified and label-frozen, and one copy of it per fork name."""
    a = tmp_path / "A"
    Store.init(str(a)).close()
    _spec(a, "x")
    _ex(a, commands.FreezeAcceptance("x", "x-pack/1"))
    copies = []
    for name in forks:
        shutil.copytree(a, tmp_path / name)
        (tmp_path / name / ".lock").unlink(missing_ok=True)
        copies.append(tmp_path / name)
    return (a, *copies)


def _lmerge(ours: Path, theirs: Path) -> MergeOutcome | Refused:
    return merge_store_roots(str(ours), str(theirs), actor=ours.name, run_id="")


def _replayed(ours: Path, theirs: Path) -> MergeOutcome:
    outcome = _lmerge(ours, theirs)
    assert isinstance(outcome, MergeOutcome), (ours.name, theirs.name, outcome)
    assert outcome.mode == "replayed", (ours.name, theirs.name, outcome)
    return outcome


def _exceptions(root: Path) -> dict[str, str]:
    with Store.open(str(root), read_only=True) as store:
        return {item.unit_name: item.reason for item in store.read_snapshot().ci_exceptions}


def _names(root: Path) -> set[str]:
    with Store.open(str(root), read_only=True) as store:
        return {unit.name for unit in store.read_snapshot().units}


def _count(root: Path, kind: str) -> int:
    return sum(type(item.record.folded_event).__name__ == kind for item in _chain(root))


def _assert_refused_before_lock(
    monkeypatch: pytest.MonkeyPatch, ours: Path, theirs: Path, code: str, *fragments: str
) -> str:
    for root in (ours, theirs):
        (root / ".lock").unlink(missing_ok=True)
    before_ours, before_theirs = _files(ours), _files(theirs)
    entered: list[str] = []
    original = Store.write_locked

    def spy(self: Store) -> AbstractContextManager[None]:
        entered.append("write_locked")
        return original(self)

    monkeypatch.setattr(Store, "write_locked", spy)
    try:
        exit_code, output = _merge(ours, theirs)
    finally:
        monkeypatch.setattr(Store, "write_locked", original)
    assert exit_code == 2, output
    assert code in output, output
    for fragment in fragments:
        assert fragment in output, (fragment, output)
    assert entered == []
    for root in (ours, theirs):
        (root / ".lock").unlink(missing_ok=True)
    assert _files(ours) == before_ours
    assert _files(theirs) == before_theirs
    return output


def test_a_copied_mark_does_not_restore_a_cleared_exception(tmp_path: Path) -> None:
    """R8 (Astra's four-store restore)."""
    a, b, c, d = _base(tmp_path, "B", "C", "D")
    _spec(a, "a")
    _spec(b, "b")
    _spec(d, "d")
    _ex(c, commands.MarkOpenInCi("x", "old"))
    for ours, theirs in ((a, b), (c, a), (b, c)):
        _replayed(ours, theirs)
    _ex(b, commands.ClearOpenInCi("x"))
    _spec(c, "c2")
    _replayed(d, c)
    _replayed(b, d)
    assert _exceptions(b) == {}
    assert {"a", "b", "c2", "d"} <= _names(b)
    assert _count(b, "OpenInCiMarked") == 1


def test_three_stores_without_a_nested_merge_keep_the_clear(tmp_path: Path) -> None:
    """R9 (V4)."""
    _a, b, c, d = _base(tmp_path, "B", "C", "D")
    _spec(b, "b")
    _spec(d, "d")
    _ex(c, commands.MarkOpenInCi("x", "old"))
    _replayed(b, c)
    _ex(b, commands.ClearOpenInCi("x"))
    _spec(c, "c2")
    _replayed(d, c)
    _replayed(b, d)
    assert _exceptions(b) == {}
    assert {"b", "c2", "d"} <= _names(b)


def test_a_receiver_that_never_merged_keeps_its_clear(tmp_path: Path) -> None:
    """R10 (V7, the ordinary two-branch flow)."""
    m, u1 = _base(tmp_path, "U1")
    _spec(u1, "u1")
    _ex(m, commands.MarkOpenInCi("x", "old"))
    u2 = tmp_path / "U2"
    shutil.copytree(m, u2)
    (u2 / ".lock").unlink(missing_ok=True)
    _ex(u2, commands.ClearOpenInCi("x"))
    _spec(m, "m2")
    _replayed(u1, m)
    _replayed(u2, u1)
    assert _exceptions(u2) == {}
    assert {"m2", "u1"} <= _names(u2)


def test_a_copied_clear_does_not_remove_a_later_mark(tmp_path: Path) -> None:
    """R11 (V2)."""
    a = _base(tmp_path)[0]
    _ex(a, commands.MarkOpenInCi("x", "shared"))
    for name in ("B", "C", "D"):
        shutil.copytree(a, tmp_path / name)
        (tmp_path / name / ".lock").unlink(missing_ok=True)
    b, c, d = tmp_path / "B", tmp_path / "C", tmp_path / "D"
    _spec(b, "b")
    _spec(d, "d")
    _ex(c, commands.ClearOpenInCi("x"))
    _replayed(b, c)
    _ex(b, commands.MarkOpenInCi("x", "new-on-B"))
    _spec(c, "c2")
    _replayed(d, c)
    _replayed(b, d)
    assert _exceptions(b) == {"x@1": "new-on-B"}


def test_a_skipped_redundant_clear_does_not_return_as_a_copy(tmp_path: Path) -> None:
    """R12 (V3, with D-37)."""
    a = _base(tmp_path)[0]
    _ex(a, commands.MarkOpenInCi("x", "shared"))
    for name in ("B", "C", "D"):
        shutil.copytree(a, tmp_path / name)
        (tmp_path / name / ".lock").unlink(missing_ok=True)
    b, c, d = tmp_path / "B", tmp_path / "C", tmp_path / "D"
    _spec(b, "b")
    _spec(d, "d")
    _ex(b, commands.ClearOpenInCi("x"))
    _ex(c, commands.ClearOpenInCi("x"))
    assert _replayed(b, c).skipped_records == 1
    _ex(b, commands.MarkOpenInCi("x", "new-on-B"))
    _spec(c, "c2")
    _replayed(d, c)
    _replayed(b, d)
    assert _exceptions(b) == {"x@1": "new-on-B"}


def test_a_source_holding_the_original_directly_is_recognised(tmp_path: Path) -> None:
    """R13 (V6)."""
    _a, b, c = _base(tmp_path, "B", "C")
    _spec(b, "b")
    _ex(c, commands.MarkOpenInCi("x", "old"))
    d = tmp_path / "D"
    shutil.copytree(c, d)
    (d / ".lock").unlink(missing_ok=True)
    _spec(c, "c1")
    _spec(d, "d")
    _replayed(b, c)
    _ex(b, commands.ClearOpenInCi("x"))
    _replayed(b, d)
    assert _exceptions(b) == {}
    assert "d" in _names(b)


def test_a_copied_setting_does_not_undo_a_later_setting(tmp_path: Path) -> None:
    """R14."""
    _a, b, c, d = _base(tmp_path, "B", "C", "D")
    _spec(b, "b")
    _spec(d, "d")
    _ex(c, commands.SetCiPolicy(CiPolicy.LEDGER))
    _replayed(b, c)
    _ex(b, commands.SetCiPolicy(CiPolicy.STRICT))
    _spec(c, "c2")
    _replayed(d, c)
    _replayed(b, d)
    with Store.open(str(b), read_only=True) as store:
        assert store.read_snapshot().ci_policy is CiPolicy.STRICT


def test_a_fourth_store_with_copies_of_an_advanced_history_merges(tmp_path: Path) -> None:
    """R15 (pack/1's R3, restored)."""
    a, b, c, d = _stores(tmp_path, "A", "B", "C", "D")
    for ours, theirs in ((a, b), (c, a), (b, c)):
        assert _merge(ours, theirs)[0] == 0, (ours.name, theirs.name)
    _cmd(a, "specify", "--name", "a2", "--version", "1")
    _cmd(c, "specify", "--name", "c2", "--version", "1")
    for ours, theirs in ((c, a), (d, c), (b, d), (a, b)):
        code, output = _merge(ours, theirs)
        assert code == 0, (ours.name, theirs.name, output)
    expected = ["a2@1", "a@1", "b@1", "c2@1", "c@1", "d@1", "shared@1"]
    for root in (a, b):
        assert _units(root) == expected, root.name
        kinds = [type(item.record.folded_event).__name__ for item in _chain(root)]
        assert kinds.count("UnitSpecified") == len(expected), (root.name, kinds)


def test_five_generations_of_copies_keep_the_clear_and_repeat_as_a_no_op(tmp_path: Path) -> None:
    """R16."""
    stores = _base(tmp_path, "B", "C", "D", "E", "F", "G")
    b, c, d, e, f, g = stores[1:]
    for root in (b, d, e, f, g):
        _spec(root, root.name.lower())
    _ex(c, commands.MarkOpenInCi("x", "old"))
    _replayed(b, c)
    _ex(b, commands.ClearOpenInCi("x"))
    _spec(c, "c2")
    previous = c
    for root in (d, e, f, g):
        _replayed(root, previous)
        previous = root
    _replayed(b, g)
    assert _exceptions(b) == {}
    assert {"b", "c2", "d", "e", "f", "g"} <= _names(b)
    (b / ".lock").unlink(missing_ok=True)
    before = _files(b)
    outcome = _lmerge(b, d)
    assert isinstance(outcome, MergeOutcome) and outcome.mode == "no-op", outcome
    (b / ".lock").unlink(missing_ok=True)
    assert _files(b) == before


def test_an_independent_identical_mark_still_reopens(tmp_path: Path) -> None:
    """R17: same actor, time, reason and unit, recorded independently on another parent. # guard:"""
    _a, b, c, d = _base(tmp_path, "B", "C", "D")
    _spec(b, "b")
    _spec(d, "d")
    _ex(c, commands.MarkOpenInCi("x", "old"))
    _replayed(b, c)
    _ex(b, commands.ClearOpenInCi("x"))
    _ex(d, commands.MarkOpenInCi("x", "old"), actor="C")
    _replayed(b, d)
    assert _exceptions(b) == {"x@1": "old"}


def _companion_stores(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A runs dep (depending on x); B amends a deviation on x; A<-B generates dep's invalidation.

    C is A with dep in progress, forked before A<-B. D forked from the base and specified d,
    then D<-A copied A's history, including the generated invalidation.
    """
    a, b, d = _base(tmp_path, "B", "D")
    _spec(d, "d")
    _spec(a, "dep", "x")
    for command in (
        commands.FreezeAcceptance("dep", "dep-pack/1"),
        commands.MarkReady("dep"),
        commands.StartWork("dep"),
    ):
        _ex(a, command)
    c = tmp_path / "C"
    shutil.copytree(a, c)
    (c / ".lock").unlink(missing_ok=True)
    _ex(b, commands.OpenDeviation("dev1", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    _ex(b, commands.AmendDeviation("dev1", "x-pack/2"))
    _replayed(a, b)
    generated = [
        item for item in _chain(a)
        if isinstance(item.record.folded_event, UnitInvalidated)
        and item.record.folded_event.name == "dep"
    ]
    assert len(generated) == 1 and generated[0].record.actor == "A"
    _replayed(d, a)
    return a, b, c, d


def _status(root: Path, name: str) -> str:
    with Store.open(str(root), read_only=True) as store:
        unit = store.read_snapshot().unit(name)
        assert unit is not None
        return unit.status.value


def test_skipping_a_block_whose_replay_generated_a_companion_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R18 (Fable's H3): the block is excluded by exact digest, and by origin through a copy."""
    a, b, _c, d = _companion_stores(tmp_path)
    _assert_refused_before_lock(monkeypatch, b, a, "merge.conflict", "UnitInvalidated", "dep")
    _assert_refused_before_lock(monkeypatch, b, d, "merge.conflict", "UnitInvalidated", "dep")


def test_a_skipped_companion_whose_effect_ours_already_has_merges(tmp_path: Path) -> None:
    """R27: the effect is already established on ours, so the excluded batch is satisfied."""
    _a, b, c, d = _companion_stores(tmp_path)
    _replayed(c, b)
    assert _status(c, "dep") == "invalidated"
    _replayed(c, d)
    assert "d" in _names(c)
    assert _status(c, "dep") == "invalidated"
    assert _count(c, "DeviationAmended") == 1
    assert _count(c, "UnitInvalidated") == 1


def _wire(root: Path, digest: str) -> dict[str, object]:
    document = json.loads((root / "objects" / "sha256" / digest).read_bytes())
    assert isinstance(document, dict)
    return document


def test_replayed_records_carry_their_origin_on_the_wire(tmp_path: Path) -> None:
    """R19."""
    a, c, d, e = _base(tmp_path, "C", "D", "E")
    _spec(a, "a")
    _spec(d, "d")
    _spec(e, "e")
    _ex(c, commands.MarkOpenInCi("x", "old"))
    original = _chain(c)[-1]
    direct = _wire(c, original.digest)
    assert "origin" not in direct and "satisfied" not in direct
    assert direct["event_format"] == 2
    _replayed(d, c)
    _replayed(e, d)
    for root in (d, e):
        copies = [
            item for item in _chain(root)
            if type(item.record.folded_event).__name__ == "OpenInCiMarked"
        ]
        assert len(copies) == 1, root.name
        assert copies[0].digest != original.digest
        wire = _wire(root, copies[0].digest)
        assert wire["origin"] == original.digest, root.name
        assert wire["origin_format"] == 2, root.name
        assert copies[0].record.origin == original.digest
    replayed_marker = _merge_markers(e, 0)[0]
    assert "origin" in _wire(e, replayed_marker.digest)


def _shared_mark(tmp_path: Path, *forks: str) -> tuple[Path, ...]:
    """A with x@1 marked open in CI once, before the copies are made."""
    a = _base(tmp_path)[0]
    _ex(a, commands.MarkOpenInCi("x", "shared"))
    copies = []
    for name in forks:
        shutil.copytree(a, tmp_path / name)
        (tmp_path / name / ".lock").unlink(missing_ok=True)
        copies.append(tmp_path / name)
    return (a, *copies)


def test_the_merge_record_lists_exactly_the_facts_it_satisfied(tmp_path: Path) -> None:
    """R28: a D-37 skip, its inheritance by a third store, and an omitted D-33 companion."""
    _a, ours, theirs, third, carrier = _shared_mark(tmp_path, "OURS", "THEIRS", "THIRD", "CARRIER")
    _spec(ours, "ours-only")
    _spec(third, "third-only")
    _ex(ours, commands.ClearOpenInCi("x"))
    _ex(theirs, commands.ClearOpenInCi("x"))
    clear = _chain(theirs)[-1]
    assert clear.digest != _chain(ours)[-1].digest
    assert _replayed(ours, theirs).skipped_records == 1
    marker = _chain(ours)[-1]
    assert isinstance(marker.record.folded_event, MergeRecorded)
    assert tuple(marker.record.satisfied) == (clear.digest,)
    # THIRD learns of the satisfied clear only through OURS, then marks afresh; a later copy of
    # THEIRS' clear must not remove that mark.
    _replayed(third, ours)
    _ex(third, commands.MarkOpenInCi("x", "fresh-on-third"))
    _spec(carrier, "carrier-only")
    _replayed(carrier, theirs)
    _replayed(third, carrier)
    assert _exceptions(third) == {"x@1": "fresh-on-third"}
    # An omitted companion: both sides block u with their own deviation; replay of THEIRS'
    # DeviationOpened no longer produces THEIRS' UnitBlocked.
    o2, t2 = _base(tmp_path / "omit", "T2")
    _spec(o2, "ours-only")
    _ex(o2, commands.OpenDeviation("ours-d", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    _ex(t2, commands.OpenDeviation("theirs-d", DeviationType.PACK_DEFECT, UnitRef("x", "1")))
    blocked = next(
        item for item in _chain(t2) if type(item.record.folded_event).__name__ == "UnitBlocked"
    )
    _replayed(o2, t2)
    marker = _chain(o2)[-1]
    assert isinstance(marker.record.folded_event, MergeRecorded)
    assert tuple(marker.record.satisfied) == (blocked.digest,)


def _v1(tmp_path: Path, *names: str) -> tuple[Path, ...]:
    return _materialize(tmp_path, V1_MERGES, names)


def test_a_source_batch_inside_a_version_1_merge_block_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R20 (D-41 rule a)."""
    a, c = _v1(tmp_path, "A", "C")
    _assert_refused_before_lock(monkeypatch, c, a, "merge.ancestry-unproven")
    assert "b" not in _names(c)


def test_a_version_1_source_after_a_version_1_merge_on_ours_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R21 (D-41 rule b)."""
    a, c = _v1(tmp_path, "A", "C")
    _assert_refused_before_lock(monkeypatch, a, c, "merge.ancestry-unproven")
    assert "c" not in _names(a)


def test_version_1_merges_in_the_shared_prefix_still_merge(tmp_path: Path) -> None:
    """R22. # guard:"""
    m, u = _v1(tmp_path, "M", "U")
    _replayed(u, m)
    _replayed(m, u)
    for root in (m, u):
        assert {"a", "b", "m2", "u"} <= _names(root), root.name


def test_a_fast_forward_through_a_version_1_merge_is_unaffected(tmp_path: Path) -> None:
    """R25. # guard:"""
    a, pre = _v1(tmp_path, "A", "P")
    outcome = _lmerge(pre, a)
    assert isinstance(outcome, MergeOutcome) and outcome.mode == "fast-forward", outcome
    assert {"a", "b"} <= _names(pre)
    assert [item.digest for item in _chain(pre)] == [item.digest for item in _chain(a)]
    again = _lmerge(pre, a)
    assert isinstance(again, MergeOutcome) and again.mode == "no-op", again


def test_upgrade_reports_version_1_merge_markers_and_changes_only_the_format(
    tmp_path: Path,
) -> None:
    """R23."""
    document = json.loads(V1_MERGES.read_text())
    for name, expected in (("A", 1), ("C", 0)):
        root = tmp_path / name
        for relative, encoded in document[name].items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded))
        state_before = json.loads((root / "state.json").read_text())
        objects_before = {
            path.name: path.read_bytes() for path in (root / "objects" / "sha256").iterdir()
        }
        code, output = _run("upgrade", "--root", str(root))
        assert code == 0, output
        assert f"version-1 merge markers: {expected}" in output.splitlines(), output
        state_after = json.loads((root / "state.json").read_text())
        assert state_after["state_format"] == 7
        state_before.pop("state_format")
        state_after.pop("state_format")
        assert state_after == state_before
        objects_after = {
            path.name: path.read_bytes() for path in (root / "objects" / "sha256").iterdir()
        }
        assert objects_after == objects_before


def test_the_docs_state_the_origin_rule_and_its_trust_level() -> None:
    """R24."""
    quickstart = " ".join((ROOT / "docs" / "quickstart.md").read_text().split())
    assert "A merge skips a replayed copy of a record OURS already holds" in quickstart
    assert "`upgrade` reports how many version-1 merge markers a store holds" in quickstart
    boundary = " ".join((ROOT / "docs" / "claim-boundary.md").read_text().split())
    assert "`origin` and `satisfied` are claims written by the merging tool" in boundary


def test_a_version_2_copy_of_a_version_1_root_is_still_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R29: rule (b) reads the origin's version, not the copy's."""
    a, c, pre = _v1(tmp_path, "A", "C", "P")
    fresh = tmp_path / "P2"
    shutil.copytree(pre, fresh)
    (fresh / ".lock").unlink(missing_ok=True)
    _spec(fresh, "p2")
    _replayed(pre, c)
    copy = next(
        item for item in _chain(pre)
        if isinstance(item.record.folded_event, UnitSpecified) and item.record.folded_event.name == "c"
    )
    assert _wire(pre, copy.digest)["origin_format"] == 1
    _assert_refused_before_lock(monkeypatch, a, pre, "merge.ancestry-unproven")
    _replayed(a, fresh)
    assert "p2" in _names(a) and "c" not in _names(a)


def _rewrite(root: Path, digest: str, edit: Callable[[dict[str, object]], None]) -> None:
    """Edit one stored record's envelope and re-chain every later record, as a hand edit would."""
    chain = _chain(root)
    index = [item.digest for item in chain].index(digest)
    objects = root / "objects" / "sha256"
    previous = chain[index - 1].digest if index else "GENESIS"
    for position in range(index, len(chain)):
        raw = (objects / chain[position].digest).read_bytes()
        document = json.loads(raw)
        assert encode_document(document) == raw
        if position == index:
            edit(document)
        document["prev"] = previous
        payload = encode_document(document)
        previous = digest_of(ArtifactKind.EVENT, payload)
        (objects / previous).write_bytes(payload)
    state = json.loads((root / "state.json").read_bytes())
    state["event_head"] = previous
    (root / "state.json").write_bytes(encode_document(state))


def test_a_misplaced_or_disagreeing_origin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R30: origin is validated on every THEIRS record after the shared cut."""
    a, ours, theirs, carrier = _base(tmp_path, "OURS", "THEIRS", "CARRIER")
    _spec(ours, "ours-only")
    _spec(theirs, "t")
    direct = _chain(theirs)[-1]
    shared_first = _chain(theirs)[0].digest

    def misplace(document: dict[str, object]) -> None:
        document["origin"] = shared_first
        document["origin_format"] = 2

    _rewrite(theirs, direct.digest, misplace)
    _assert_refused_before_lock(monkeypatch, ours, theirs, "merge.theirs-history-invalid")
    code, output = _run("check", "--root", str(theirs))
    rows = [line for line in output.splitlines() if "check.event-log" in line]
    assert rows and rows[0].startswith("FAIL"), output
    # A copy whose claims disagree with OURS' original of the same origin.
    _ex(a, commands.MarkOpenInCi("x", "old"))
    _spec(carrier, "carrier-only")
    _replayed(carrier, a)
    copy = next(
        item for item in _chain(carrier)
        if type(item.record.folded_event).__name__ == "OpenInCiMarked"
    )

    def disagree(document: dict[str, object]) -> None:
        document["actor"] = "mallory"

    _rewrite(carrier, copy.digest, disagree)
    _assert_refused_before_lock(monkeypatch, a, carrier, "merge.theirs-history-invalid")

