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
from collections.abc import Iterator
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.events import EventRecord, MergeRecorded
from vouch_mk2.store import Store, decode_event_record
from vouch_mk2.store.digests import ArtifactKind

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "nested-merge" / "legacy-untranslated.json"
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
    before = len(_chain(c))
    assert _merge(c, a)[0] == 0
    chain = _chain(c)
    digests = [item.digest for item in chain]
    replayed = _merge_markers(c, before)[:-1]  # the last marker is C<-A's own merge record
    assert len(replayed) == 1
    marker = replayed[0]
    event = marker.record.folded_event
    assert isinstance(event, MergeRecorded)
    assert event.theirs_head == source_event.theirs_head
    assert event.ours_head in digests[: digests.index(marker.digest)]
    assert marker.record.actor == source[0].record.actor


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


def _legacy(tmp_path: Path) -> tuple[Path, Path, _Logged]:
    """Stores B and C written by the pre-change code after A<-B and C<-A, and C's offending record."""
    document = json.loads(FIXTURE.read_text())
    roots: list[Path] = []
    for name in ("B", "C"):
        root = tmp_path / name
        for relative, encoded in document[name].items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded))
        roots.append(root)
    b, c = roots
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
