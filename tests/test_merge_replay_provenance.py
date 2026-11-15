"""Frozen acceptance pack for self-hosted unit merge-replay-provenance@1 (D-33).

Settles ``vouch_mk2-bd4.4.2`` and ``vouch_mk2-bd4.4.6``. A divergent semantic merge must
replay each incoming command onto the unit reference its source acted on, check each
unit-bearing companion fact the same way, require an anchor's source head on ours, and keep
each replayed fact's source actor, run claim and recorded time. A refusal is
``merge.conflict`` and writes nothing. Requirements R1-R16 are in
``packs/merge-replay-provenance-1.spec.md``. The implementer may not edit this file.
"""

from __future__ import annotations

import datetime
import io
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    AcceptanceFrozen,
    AcceptancePackBound,
    AcceptancePackRebound,
    AdvanceAnchor,
    AmendDeviation,
    AnchorAdvanced,
    BeginReverify,
    BindPack,
    EventRecord,
    FreezeAcceptance,
    MarkDone,
    MarkReady,
    MergeRecorded,
    PreLogHistoryRecorded,
    PrepareRelease,
    Publish,
    RebindPack,
    ReleasePrepared,
    Respecify,
    Snapshot,
    StartWork,
    Supersede,
    UnitBlocked,
    UnitInvalidated,
    UnitRef,
    UnitSpecified,
    apply,
    decide,
)
from vouch_mk2.core import commands as core_commands
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    decode_event_record,
    decode_pack_inventory,
    digest_of,
    encode_document,
)
from vouch_mk2.store.event_log import _CI_RUN_ID_VARIABLES

ROOT = Path(__file__).resolve().parents[1]
OK = (sys.executable, "-c", "raise SystemExit(0)")
FAIL = (sys.executable, "-c", "raise SystemExit(1)")
SHARED = "shared-agent"
OURS = "ours-agent"
THEIRS = "theirs-agent"
HEX64 = re.compile(r"[0-9a-f]{64}")


# --------------------------------------------------------------------------- harness


@pytest.fixture(autouse=True)
def _no_ci_run_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common rule: no CI run-id variable is set unless a requirement names a run claim."""
    for name in _CI_RUN_ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)


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


def _root(project: Path) -> str:
    return str(project / ".vouch")


def _cmd(project: Path, verb: str, *argv: str, actor: str = SHARED) -> str:
    return _must(verb, "--root", _root(project), "--actor", actor, *argv)


def _new(tmp_path: Path, name: str = "ours") -> Path:
    """A fresh project from ``init`` (effects required) with one bindable pack."""
    project = tmp_path / name
    project.mkdir()
    _must("init", "--project", str(project))
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    (project / "packs" / "u.spec.md").write_text("# synthetic pack\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}\n')
    (project / "tests" / "t.py").write_text("def test_u() -> None:\n    assert True\n")
    return project


def _fork(project: Path, name: str = "theirs") -> Path:
    other = project.parent / name
    shutil.copytree(project, other)
    return other


def _pack_args(project: Path) -> tuple[str, ...]:
    return (
        "--project", str(project),
        "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py",
        "--pack-effects-file", "packs/u.effects.json",
    )


def _specify(
    project: Path, name: str, version: str, *depends_on: str, actor: str = SHARED
) -> None:
    extra: list[str] = []
    for dependency in depends_on:
        extra += ["--depends-on", dependency]
    _cmd(project, "specify", "--name", name, "--version", version, *extra, actor=actor)


def _freeze(
    project: Path, name: str, pack_version: str, *, bound: bool = True, actor: str = SHARED
) -> None:
    extra = _pack_args(project) if bound else ()
    _cmd(project, "freeze", "--name", name, "--pack-version", pack_version, *extra, actor=actor)


def _deviation(project: Path, deviation_id: str, ref: str, *, actor: str = SHARED) -> None:
    _cmd(
        project, "open-deviation", "--deviation-id", deviation_id,
        "--deviation-type", "pack-defect", "--unit", ref, actor=actor,
    )


def _reject_and_close(project: Path, deviation_id: str, *, actor: str = SHARED) -> None:
    _cmd(project, "adjudicate-reject", "--deviation-id", deviation_id, actor=actor)
    _cmd(project, "close-deviation", "--deviation-id", deviation_id, actor=actor)


def _evidence(
    project: Path, ref: str, kind: str, command: tuple[str, ...], *, actor: str = SHARED
) -> None:
    _cmd(project, "record-evidence", "--unit", ref, "--kind", kind, "--", *command, actor=actor)


def _publish(project: Path, name: str, version: str, *, actor: str = SHARED) -> None:
    """Specify NAME@VERSION and take it to published at the lightweight tier."""
    _specify(project, name, version, actor=actor)
    _freeze(project, name, f"{name}-pack/{version}", actor=actor)
    for verb in ("mark-ready", "start", "mark-done", "publish"):
        _cmd(project, verb, "--name", name, actor=actor)


def _snapshot(project: Path) -> Snapshot:
    with Store.open(_root(project), read_only=True) as store:
        return store.read_snapshot()


def _units(project: Path) -> dict[str, str]:
    return {unit.ref().label(): unit.status.value for unit in _snapshot(project).units}


def _current(project: Path, name: str) -> str:
    unit = _snapshot(project).unit(name)
    assert unit is not None, name
    return unit.ref().label()


@dataclass(frozen=True)
class _Logged:
    digest: str
    payload: bytes
    record: EventRecord


def _chain(project: Path) -> list[_Logged]:
    """The store's event chain, oldest first."""
    found: list[_Logged] = []
    with Store.open(_root(project), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest and digest != "GENESIS":
            payload = store.get_object(ArtifactKind.EVENT, digest)
            record = decode_event_record(payload)
            found.append(_Logged(digest, payload, record))
            digest = record.prev
    return list(reversed(found))


def _kind(item: _Logged) -> str:
    return type(item.record.folded_event).__name__


def _subject(item: _Logged) -> str:
    event = item.record.folded_event
    if isinstance(event, MergeRecorded):
        return f"{event.ours_head}:{event.theirs_head}"
    for field in ("name", "deviation_id", "release_id", "unit_name"):
        value = getattr(event, field, None)
        if isinstance(value, str):
            return value
    return ""


def _last(project: Path, kind: str, subject: str | None = None) -> _Logged:
    """The newest record of KIND (and SUBJECT) in PROJECT's chain."""
    matches = [
        item for item in _chain(project)
        if _kind(item) == kind and (subject is None or _subject(item) == subject)
    ]
    assert matches, (kind, subject)
    return matches[-1]


def _added_by_merge(project: Path, before_head: str) -> list[_Logged]:
    chain = _chain(project)
    heads = [item.digest for item in chain]
    start = heads.index(before_head) + 1 if before_head in heads else 0
    return chain[start:]


def _merge(ours: Path, theirs: Path, *argv: str) -> tuple[int, str]:
    return _run("merge", "--root", _root(ours), "--other", _root(theirs), *argv)


def _assert_replayed(code: int, output: str) -> None:
    assert code == 0, output
    assert "merge: replayed;" in output, output


def _files(project: Path) -> dict[str, bytes]:
    root = project / ".vouch"
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _next_second() -> None:
    """Cross a wall-clock second boundary, so the next record's time differs."""
    start = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    while datetime.datetime.now(datetime.UTC).replace(microsecond=0) == start:
        time.sleep(0.01)


def _assert_accepted(snapshot: Snapshot, command: core_commands.Command) -> Accepted:
    """Precondition: decide() alone accepts the recovered command on ours."""
    decision = decide(snapshot, command)
    assert isinstance(decision, Accepted), decision
    return decision


def _assert_refused(
    monkeypatch: pytest.MonkeyPatch,
    ours: Path,
    perform: Callable[[], tuple[int, str]],
    *,
    source: _Logged,
    names: tuple[str, ...],
) -> str:
    """The spec's "Refused" definition, applied to one merge attempt."""
    (ours / ".vouch" / ".lock").unlink(missing_ok=True)
    before = _files(ours)
    entered: list[str] = []
    original = Store.write_locked

    def spy(self: Store) -> AbstractContextManager[None]:
        entered.append("write_locked")
        return original(self)

    monkeypatch.setattr(Store, "write_locked", spy)
    try:
        code, output = perform()
    finally:
        monkeypatch.setattr(Store, "write_locked", original)

    assert code != 0, output
    assert "merge.conflict" in output, output
    assert _kind(source) in output, (_kind(source), output)
    assert f"revision {source.record.revision}" in output, (source.record.revision, output)
    for name in names:
        assert name in output, (name, output)
    assert _files(ours) == before, "a refused merge changed or added a store file"
    assert not (ours / ".vouch" / ".lock").exists(), "a refused merge created the lock"
    assert entered == [], "a refused merge entered the store write lock"
    return output


def _record_with_run(
    monkeypatch: pytest.MonkeyPatch, run_id: str, action: Callable[[], object]
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    try:
        action()
    finally:
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)


# --------------------------------------------------------------------------- R1


_R1_OPS = (
    "freeze", "bind-pack", "mark-ready", "start", "mark-done",
    "publish", "respecify", "begin-reverify", "supersede",
)
_R1_EVENTS = {
    "freeze": "AcceptanceFrozen",
    "bind-pack": "AcceptancePackBound",
    "mark-ready": "UnitMarkedReady",
    "start": "WorkStarted",
    "mark-done": "UnitMarkedDone",
    "publish": "UnitPublished",
    "respecify": "UnitRespecified",
    "begin-reverify": "ReverificationStarted",
    "supersede": "UnitSuperseded",
}
_R1_PRE_STATES = {
    "freeze": "specified",
    "bind-pack": "acceptance-frozen",
    "mark-ready": "acceptance-frozen",
    "start": "ready",
    "mark-done": "in-progress",
    "publish": "done-pending-acceptance",
    "respecify": "blocked",
    "begin-reverify": "blocked",
    "supersede": "blocked",
}


def _pre_state(project: Path, op: str, version: str, *, actor: str) -> None:
    """Bring x@VERSION to OP's pre-state with its own deviation ids and pack versions."""
    _specify(project, "x", version, actor=actor)
    if op == "freeze":
        return
    _freeze(project, "x", f"x-pack/{version}", bound=op != "bind-pack", actor=actor)
    if op in ("bind-pack", "mark-ready"):
        return
    if op in ("respecify", "begin-reverify", "supersede"):
        _deviation(project, f"pre-{version}", f"x@{version}", actor=actor)
        if op != "supersede":
            _reject_and_close(project, f"pre-{version}", actor=actor)
        return
    _cmd(project, "mark-ready", "--name", "x", actor=actor)
    if op == "start":
        return
    _cmd(project, "start", "--name", "x", actor=actor)
    if op == "mark-done":
        return
    _cmd(project, "mark-done", "--name", "x", actor=actor)


def _run_op(project: Path, op: str, *, actor: str) -> None:
    if op == "freeze":
        _freeze(project, "x", "x-pack/theirs", actor=actor)
    elif op == "bind-pack":
        _cmd(project, "bind-pack", "--name", "x", *_pack_args(project), actor=actor)
    else:
        _cmd(project, op, "--name", "x", actor=actor)


def _recovered(op: str, source: _Logged) -> core_commands.Command:
    event = source.record.folded_event
    match op:
        case "freeze":
            assert isinstance(event, AcceptanceFrozen)
            return FreezeAcceptance(
                "x", event.pack_version, event.pack_digest, event.test_set_digest,
                effects_declared=True,
            )
        case "bind-pack":
            assert isinstance(event, AcceptancePackBound)
            return BindPack("x", event.pack_digest, event.test_set_digest, effects_declared=True)
        case "mark-ready":
            return MarkReady("x")
        case "start":
            return StartWork("x")
        case "mark-done":
            return MarkDone("x")
        case "publish":
            return Publish("x")
        case "respecify":
            return Respecify("x")
        case "begin-reverify":
            return BeginReverify("x")
        case _:
            return Supersede("x")


def _r1_fixture(tmp_path: Path, op: str) -> tuple[Path, Path, _Logged]:
    ours = _new(tmp_path)
    _pre_state(ours, op, "1", actor=SHARED)
    assert _units(ours) == {"x@1": _R1_PRE_STATES[op]}
    theirs = _fork(ours)
    _run_op(theirs, op, actor=THEIRS)
    if _units(ours)["x@1"] != "blocked":
        _deviation(ours, "ours-move", "x@1", actor=OURS)
    _cmd(ours, "supersede", "--name", "x", actor=OURS)
    _pre_state(ours, op, "2", actor=OURS)
    assert _units(ours) == {"x@1": "superseded", "x@2": _R1_PRE_STATES[op]}
    assert _current(theirs, "x") == "x@1"
    source = _last(theirs, _R1_EVENTS[op], "x")
    _assert_accepted(_snapshot(ours), _recovered(op, source))
    return ours, theirs, source


@pytest.mark.parametrize("op", _R1_OPS, ids=_R1_OPS)
def test_lifecycle_replay_refuses_another_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str
) -> None:
    """R1: a lifecycle command recorded for x@1 never replays onto ours' x@2."""
    ours, theirs, source = _r1_fixture(tmp_path, op)

    _assert_refused(
        monkeypatch, ours, lambda: _merge(ours, theirs),
        source=source, names=("x@1", "x@2"),
    )


# --------------------------------------------------------------------------- R2, R3


def test_rebind_replay_refuses_another_version_with_the_same_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: a rebind recorded for x@1 never lands on x@2 bound to the same inventory."""
    ours = _new(tmp_path)
    _publish(ours, "x", "1")
    theirs = _fork(ours)
    (theirs / "packs" / "u.spec.md").write_text("# synthetic pack, revised prose\n")
    _cmd(theirs, "rebind-pack", "--name", "x", "--reason", "prose", *_pack_args(theirs),
         actor=THEIRS)
    _cmd(ours, "supersede", "--name", "x", actor=OURS)
    _publish(ours, "x", "2", actor=OURS)
    ours_snapshot = _snapshot(ours)
    one = next(u for u in ours_snapshot.units if u.version == "1")
    two = next(u for u in ours_snapshot.units if u.version == "2")
    assert one.acceptance_pack_digest == two.acceptance_pack_digest

    source = _last(theirs, "AcceptancePackRebound", "x")
    event = source.record.folded_event
    assert isinstance(event, AcceptancePackRebound)
    with Store.open(_root(theirs), read_only=True) as store:
        old = decode_pack_inventory(store.get_object(ArtifactKind.ACCEPTANCE_PACK,
                                                     event.old_pack_digest))
        new = decode_pack_inventory(store.get_object(ArtifactKind.ACCEPTANCE_PACK,
                                                     event.new_pack_digest))
    assert [entry.path for entry in old.entries] == [entry.path for entry in new.entries]
    assert old.effects == new.effects
    _assert_accepted(ours_snapshot, RebindPack(
        name="x", pack_digest=event.new_pack_digest, reason=event.reason,
        test_set_digest=event.new_test_set_digest, old_inventory=old, new_inventory=new,
        old_pack_digest=event.old_pack_digest,
    ))

    _assert_refused(
        monkeypatch, ours, lambda: _merge(ours, theirs),
        source=source, names=("x@1", "x@2"),
    )


def test_release_replay_refuses_a_different_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: a release recorded with x@1 is never regenerated with x@2."""
    ours = _new(tmp_path)
    _publish(ours, "x", "1")
    theirs = _fork(ours)
    _cmd(theirs, "prepare-release", "--release-id", "r", "--includes", "x", actor=THEIRS)
    _cmd(ours, "supersede", "--name", "x", actor=OURS)
    _publish(ours, "x", "2", actor=OURS)

    source = _last(theirs, "ReleasePrepared", "r")
    event = source.record.folded_event
    assert isinstance(event, ReleasePrepared)
    assert [ref.label() for ref in event.includes] == ["x@1"]
    decision = _assert_accepted(_snapshot(ours), PrepareRelease("r", ("x",)))
    regenerated = decision.events[0]
    assert isinstance(regenerated, ReleasePrepared)
    assert [ref.label() for ref in regenerated.includes] == ["x@2"]

    _assert_refused(
        monkeypatch, ours, lambda: _merge(ours, theirs),
        source=source, names=("x@1", "x@2"),
    )


# --------------------------------------------------------------------------- R4


# guard:
def test_same_version_lifecycle_replays(tmp_path: Path) -> None:
    """R4: a same-version lifecycle replays."""
    ours = _new(tmp_path)
    _specify(ours, "shared", "1")
    theirs = _fork(ours)
    _publish(theirs, "y", "1", actor=THEIRS)
    _specify(ours, "z", "1", actor=OURS)

    _assert_replayed(*_merge(ours, theirs))

    assert _units(ours)["y@1"] == "published"


# guard:
def test_incoming_supersede_and_successor_replay(tmp_path: Path) -> None:
    """R4: theirs' own supersede and successor resolve step by step on ours."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    _freeze(ours, "x", "x-pack/1")
    _deviation(ours, "d", "x@1")
    theirs = _fork(ours)
    _cmd(theirs, "supersede", "--name", "x", actor=THEIRS)
    _specify(theirs, "x", "2", actor=THEIRS)
    _freeze(theirs, "x", "x-pack/2", actor=THEIRS)
    _specify(ours, "z", "1", actor=OURS)

    _assert_replayed(*_merge(ours, theirs))

    units = _units(ours)
    assert units["x@1"] == "superseded"
    assert units["x@2"] == "acceptance-frozen"


# guard:
def test_by_name_dependency_on_another_version_still_replays(tmp_path: Path) -> None:
    """R4: identity does not version-pin by-name dependencies (D-33)."""
    ours = _new(tmp_path)
    _publish(ours, "x", "1")
    _specify(ours, "y", "1", "x")
    _freeze(ours, "y", "y-pack/1")
    for verb in ("mark-ready", "start", "mark-done"):
        _cmd(ours, verb, "--name", "y")
    theirs = _fork(ours)
    _cmd(theirs, "publish", "--name", "y", actor=THEIRS)
    _cmd(ours, "supersede", "--name", "x", actor=OURS)
    _publish(ours, "x", "2", actor=OURS)

    _assert_replayed(*_merge(ours, theirs))

    assert _units(ours)["y@1"] == "published"


# --------------------------------------------------------------------------- R5, R6


def _invalidated(decision: Accepted) -> list[str]:
    return [event.name for event in decision.events if isinstance(event, UnitInvalidated)]


def test_amendment_replay_refuses_invalidating_another_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5: an amendment's invalidation of x@1 never lands on x@2."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    _deviation(ours, "d", "x@1")
    theirs = _fork(ours)
    _cmd(theirs, "adjudicate-amend", "--deviation-id", "d", "--amendment-version", "am1",
         actor=THEIRS)
    _cmd(ours, "supersede", "--name", "x", actor=OURS)
    _specify(ours, "x", "2", actor=OURS)
    assert _units(ours) == {"x@1": "superseded", "x@2": "specified"}

    decision = _assert_accepted(_snapshot(ours), AmendDeviation("d", "am1"))
    assert _invalidated(decision) == ["x"]
    assert _current(ours, "x") == "x@2"
    source = _last(theirs, "UnitInvalidated", "x")

    _assert_refused(
        monkeypatch, ours, lambda: _merge(ours, theirs),
        source=source, names=("x@1", "x@2"),
    )


def test_amendment_replay_checks_every_source_companion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6: a later companion's version is checked even when the first one matches."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    _specify(ours, "z", "1", "x")
    _deviation(ours, "d", "x@1")
    theirs = _fork(ours)
    _cmd(theirs, "adjudicate-amend", "--deviation-id", "d", "--amendment-version", "am1",
         actor=THEIRS)
    _deviation(ours, "z-move", "z@1", actor=OURS)
    _cmd(ours, "supersede", "--name", "z", actor=OURS)
    _specify(ours, "z", "2", "x", actor=OURS)
    assert _current(ours, "x") == "x@1"
    assert _current(ours, "z") == "z@2"

    decision = _assert_accepted(_snapshot(ours), AmendDeviation("d", "am1"))
    assert sorted(_invalidated(decision)) == ["x", "z"]
    source_x = _last(theirs, "UnitInvalidated", "x")
    source = _last(theirs, "UnitInvalidated", "z")
    assert source_x.record.revision < source.record.revision

    _assert_refused(
        monkeypatch, ours, lambda: _merge(ours, theirs),
        source=source, names=("z@1", "z@2"),
    )


# --------------------------------------------------------------------------- R7


# guard:
def test_redundant_block_replays_without_its_companion(tmp_path: Path) -> None:
    """R7: a source UnitBlocked with no replayed counterpart produces no record."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    _freeze(ours, "x", "x-pack/1")
    theirs = _fork(ours)
    _deviation(theirs, "theirs-d", "x@1", actor=THEIRS)
    _deviation(ours, "ours-d", "x@1", actor=OURS)
    assert any(_kind(item) == "UnitBlocked" for item in _chain(theirs)[-2:])
    before_head = _snapshot(ours).event_head

    _assert_replayed(*_merge(ours, theirs))

    statuses = {item.deviation_id: item.status.value for item in _snapshot(ours).deviations}
    assert statuses == {"ours-d": "open", "theirs-d": "open"}
    added = _added_by_merge(ours, before_head)
    assert not any(isinstance(item.record.folded_event, UnitBlocked) for item in added)


def _dependent_amendment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Shared x@1 with open deviation d; ours adds dependent a; theirs amends d."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    _deviation(ours, "d", "x@1")
    theirs = _fork(ours)
    _record_with_run(monkeypatch, "theirs-run", lambda: _cmd(
        theirs, "adjudicate-amend", "--deviation-id", "d", "--amendment-version", "am1",
        actor=THEIRS,
    ))
    _specify(ours, "a", "1", "x", actor=OURS)
    return ours, theirs


# guard:
def test_amendment_reaching_an_ours_only_dependent_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7: an amendment whose impact on ours reaches a dependent theirs lacked replays."""
    ours, theirs = _dependent_amendment(tmp_path, monkeypatch)

    _assert_replayed(*_merge(ours, theirs))

    units = _units(ours)
    assert units["x@1"] == "invalidated"
    assert units["a@1"] == "invalidated"


# --------------------------------------------------------------------------- R8, R9


def _anchored_base(tmp_path: Path) -> Path:
    """Shared u@v1, bound, with one GREEN evidence record."""
    ours = _new(tmp_path)
    _specify(ours, "u", "v1")
    _freeze(ours, "u", "u-pack/v1")
    _evidence(ours, "u@v1", "green", OK)
    return ours


def _head(project: Path, ref: str) -> tuple[str, str, str]:
    name, version = ref.split("@", 1)
    head = _snapshot(project).evidence_head_ref(UnitRef(name, version))
    assert head is not None, ref
    return head.head, head.anchored, head.anchor_run_id


def test_anchor_replay_refuses_when_ours_added_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8: an anchor is never replayed onto evidence its source run did not see."""
    ours = _anchored_base(tmp_path)
    theirs = _fork(ours)
    _record_with_run(monkeypatch, "ci-7", lambda: _cmd(
        theirs, "advance-anchor", "--unit-name", "u", "--unit-version", "v1", actor=THEIRS,
    ))
    _evidence(ours, "u@v1", "refactor", OK, actor=OURS)
    theirs_head = _head(theirs, "u@v1")[0]
    ours_head = _head(ours, "u@v1")[0]
    assert theirs_head != ours_head
    assert HEX64.fullmatch(theirs_head) and HEX64.fullmatch(ours_head)
    _assert_accepted(_snapshot(ours), AdvanceAnchor("u", "ci-7", unit_version="v1"))
    source = _last(theirs, "AnchorAdvanced", "u")

    _assert_refused(
        monkeypatch, ours, lambda: _merge(ours, theirs),
        source=source, names=("u@v1", theirs_head, ours_head),
    )


# guard:
def test_anchor_replay_accepts_a_superseded_versions_equal_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8: a superseded version's unchanged head can still be claimed (D-26)."""
    ours = _anchored_base(tmp_path)
    theirs = _fork(ours)
    _record_with_run(monkeypatch, "ci-7", lambda: _cmd(
        theirs, "advance-anchor", "--unit-name", "u", "--unit-version", "v1", actor=THEIRS,
    ))
    _deviation(ours, "move", "u@v1", actor=OURS)
    _cmd(ours, "supersede", "--name", "u", actor=OURS)
    assert _head(ours, "u@v1")[0] == _head(theirs, "u@v1")[0]

    _assert_replayed(*_merge(ours, theirs))

    head, anchored, _run_id = _head(ours, "u@v1")
    assert anchored == head


def _legacy_anchor(project: Path, run_id: str) -> None:
    """Commit the historical AnchorAdvanced wire shape, which records no version."""
    with Store.open(_root(project)) as store:
        with store.write_locked():
            snapshot = store.read_snapshot()
            data = encode_document({
                "actor": "legacy", "event": "AnchorAdvanced", "pack_digest": "",
                "payload": {"unit_name": "u"}, "prev": snapshot.event_head,
                "recorded_at": "2026-08-20T10:34:26+00:00",
                "revision": snapshot.revision + 1, "run_id": run_id,
            })
            digest = digest_of(ArtifactKind.EVENT, data)
            folded = apply(
                snapshot, (AnchorAdvanced("u"),),
                event_digests=(digest,), event_run_ids=(run_id,),
            )
            store.commit(
                folded, objects=((ArtifactKind.EVENT, data),),
                expected_revision=snapshot.revision,
            )
    assert json.loads(data)["payload"] == {"unit_name": "u"}


def test_legacy_anchor_replays_against_its_source_version(tmp_path: Path) -> None:
    """R9: a versionless anchor replays against the version current on its source."""
    ours = _anchored_base(tmp_path)
    theirs = _fork(ours)
    _legacy_anchor(theirs, "legacy-ci-1")
    assert _current(theirs, "u") == "u@v1"
    _deviation(ours, "move", "u@v1", actor=OURS)
    _cmd(ours, "supersede", "--name", "u", actor=OURS)
    _specify(ours, "u", "v2", actor=OURS)
    _freeze(ours, "u", "u-pack/v2", actor=OURS)
    _evidence(ours, "u@v2", "green", OK, actor=OURS)
    v1_before = _head(ours, "u@v1")
    v2_before = _head(ours, "u@v2")
    assert v1_before[0] == _head(theirs, "u@v1")[0]

    _assert_replayed(*_merge(ours, theirs))

    head, anchored, run_id = _head(ours, "u@v1")
    assert head == v1_before[0]
    assert anchored == head
    assert run_id == "legacy-ci-1"
    assert _head(ours, "u@v2") == v2_before


# --------------------------------------------------------------------------- git helpers


def _git(project: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *argv),
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _must_git(project: Path, *argv: str) -> str:
    result = _git(project, *argv)
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _repository(tmp_path: Path) -> Path:
    project = _new(tmp_path, "project")
    _must_git(project, "init", "-b", "main")
    _must_git(project, "config", "user.name", "Vouch Test")
    _must_git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    return project


def _commit(project: Path, message: str) -> None:
    _must("generate", "--root", _root(project))
    _must_git(project, "add", "-A")
    _must_git(project, "commit", "-m", message)


def _git_diverge(
    project: Path, incoming: Callable[[], None], current: Callable[[], None]
) -> None:
    """Commit shared history, diverge on two branches, and start the conflicted merge."""
    _commit(project, "shared vouch history")
    _must_git(project, "checkout", "-b", "incoming")
    incoming()
    _commit(project, "incoming vouch events")
    _must_git(project, "checkout", "main")
    current()
    _commit(project, "current vouch events")
    merged = _git(project, "merge", "--no-edit", "incoming")
    assert merged.returncode != 0, merged.stdout


def _branch_store(project: Path, ref: str) -> Path:
    """A project directory holding REF's committed .vouch, for reading facts during a conflict.

    The working store holds conflict markers until merge-git-stages runs, so preconditions read
    each side from its commit instead.
    """
    copy = project.parent / f"branch-{ref}"
    copy.mkdir()
    listing = _must_git(project, "ls-tree", "-r", "--name-only", ref, "--", ".vouch")
    for name in listing.splitlines():
        target = copy / name
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = subprocess.run(
            ("git", "-C", str(project), "show", f"{ref}:{name}"),
            check=True, stdout=subprocess.PIPE,
        )
        target.write_bytes(blob.stdout)
    return copy


# --------------------------------------------------------------------------- R10


def test_replayed_ci_anchor_keeps_its_run_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R10 (bd4.4.6): evidence and its CI anchor replayed together keep the run claim."""
    project = _repository(tmp_path)
    _cmd(project, "set-anchor-mode", "--mode", "ci")
    _specify(project, "u", "v1")
    _freeze(project, "u", "u-pack/v1")
    _cmd(project, "mark-ready", "--name", "u")
    _cmd(project, "start", "--name", "u")
    _evidence(project, "u@v1", "red", FAIL)

    def incoming() -> None:
        _evidence(project, "u@v1", "green", OK, actor=THEIRS)
        _cmd(project, "mark-done", "--name", "u", actor=THEIRS)
        _cmd(project, "publish", "--name", "u", actor=THEIRS)
        _record_with_run(monkeypatch, "ci-7", lambda: _cmd(
            project, "advance-anchor", "--unit-name", "u", "--unit-version", "v1",
            actor=THEIRS,
        ))

    def current() -> None:
        _specify(project, "other", "1", actor=OURS)

    _git_diverge(project, incoming, current)

    code, output = _run("merge-git-stages", "--root", _root(project))

    assert code == 0, output
    head, anchored, run_id = _head(project, "u@v1")
    assert run_id == "ci-7"
    assert anchored == head
    _must("generate", "--root", _root(project))
    code, output = _run("check", "--ci", "--root", _root(project), "--project", str(project))
    assert code == 0, output
    status = _must("status", "--root", _root(project))
    assert "anchor: u@v1 advanced by workflow run ci-7" in status, status


# --------------------------------------------------------------------------- R11, R12


def _claims(item: _Logged) -> tuple[str, str, str]:
    return item.record.actor, item.record.run_id, item.record.recorded_at


def _correspondents(
    source: list[_Logged], merged: list[_Logged]
) -> list[tuple[_Logged, _Logged]]:
    """Pair each source record with the merged record of the same kind and subject."""
    pairs: list[tuple[_Logged, _Logged]] = []
    for item in source:
        matches = [
            other for other in merged
            if _kind(other) == _kind(item) and _subject(other) == _subject(item)
        ]
        assert len(matches) == 1, (_kind(item), _subject(item), len(matches))
        pairs.append((item, matches[0]))
    return pairs


def test_replayed_records_keep_source_actor_run_and_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R11: replayed leading records and companions keep their source claims."""
    ours = _new(tmp_path)
    _specify(ours, "shared", "1")
    theirs = _fork(ours)
    ancestor = _snapshot(theirs).event_head
    _record_with_run(monkeypatch, "ci-9", lambda: _specify(theirs, "a", "1", actor=THEIRS))
    _next_second()
    _specify(theirs, "b", "1", actor=THEIRS)
    _deviation(theirs, "theirs-d", "b@1", actor=THEIRS)
    source = _added_by_merge(theirs, ancestor)
    assert [_kind(item) for item in source] == [
        "UnitSpecified", "UnitSpecified", "DeviationOpened", "UnitBlocked",
    ]
    assert source[0].record.run_id == "ci-9"
    assert source[1].record.run_id == ""
    _next_second()
    _specify(ours, "later", "1", actor=OURS)
    ours_latest = _chain(ours)[-1].record.recorded_at
    assert all(item.record.recorded_at < ours_latest for item in source)
    before_head = _snapshot(ours).event_head
    _next_second()
    monkeypatch.setenv("GITHUB_RUN_ID", "merge-run")

    _assert_replayed(*_merge(ours, theirs, "--actor", "merger"))

    monkeypatch.delenv("GITHUB_RUN_ID")
    added = _added_by_merge(ours, before_head)
    final = added[-1]
    assert isinstance(final.record.folded_event, MergeRecorded)
    for original, replayed in _correspondents(source, added[:-1]):
        assert _claims(replayed) == _claims(original), (_kind(original), _subject(original))
    assert final.record.actor == "merger"
    assert final.record.run_id == "merge-run"
    assert final.record.recorded_at not in {item.record.recorded_at for item in source}


def test_inserted_companion_does_not_shift_source_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12: a generated companion that sorts first carries the merge's claims."""
    ours, theirs = _dependent_amendment(tmp_path, monkeypatch)
    source_x = _last(theirs, "UnitInvalidated", "x")
    assert _claims(source_x)[:2] == (THEIRS, "theirs-run")
    before_head = _snapshot(ours).event_head
    _next_second()
    monkeypatch.setenv("GITHUB_RUN_ID", "merge-run")

    _assert_replayed(*_merge(ours, theirs, "--actor", "merger"))

    monkeypatch.delenv("GITHUB_RUN_ID")
    added = _added_by_merge(ours, before_head)
    final = added[-1]
    assert isinstance(final.record.folded_event, MergeRecorded)
    invalidated = [item for item in added if _kind(item) == "UnitInvalidated"]
    assert [_subject(item) for item in invalidated] == ["a", "x"]
    generated, replayed = invalidated
    assert _claims(replayed) == _claims(source_x)
    assert _claims(generated) == _claims(final)
    assert _claims(final)[:2] == ("merger", "merge-run")
    assert final.record.recorded_at != source_x.record.recorded_at


def test_incoming_merge_record_keeps_its_source_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12: an incoming MergeRecorded keeps theirs' claims; this merge's carries its own."""
    ours = _new(tmp_path)
    _specify(ours, "shared", "1")
    theirs = _fork(ours)
    third = _fork(ours, "third")
    _specify(theirs, "t", "1", actor=THEIRS)
    _specify(third, "c", "1", actor="third-agent")
    _next_second()
    monkeypatch.setenv("GITHUB_RUN_ID", "theirs-run")
    _assert_replayed(*_merge(theirs, third, "--actor", THEIRS))
    monkeypatch.delenv("GITHUB_RUN_ID")
    source = _last(theirs, "MergeRecorded")
    assert _claims(source)[:2] == (THEIRS, "theirs-run")
    _specify(ours, "o", "1", actor=OURS)
    before_head = _snapshot(ours).event_head
    _next_second()
    monkeypatch.setenv("GITHUB_RUN_ID", "merge-run")

    _assert_replayed(*_merge(ours, theirs, "--actor", "merger"))

    monkeypatch.delenv("GITHUB_RUN_ID")
    added = _added_by_merge(ours, before_head)
    merges = [item for item in added if isinstance(item.record.folded_event, MergeRecorded)]
    assert len(merges) == 2
    incoming, final = merges
    assert final is added[-1]
    assert _subject(incoming) == _subject(source)
    assert _claims(incoming) == _claims(source)
    assert _claims(final)[:2] == ("merger", "merge-run")
    assert final.record.recorded_at != source.record.recorded_at


# --------------------------------------------------------------------------- R13


# guard:
def test_source_reference_uses_provenance_skipped_records(tmp_path: Path) -> None:
    """R13: source references are derived through a block the replay skips."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    _freeze(ours, "x", "x-pack/1")
    theirs = _fork(ours)
    _specify(theirs, "unrelated", "1", actor=THEIRS)
    _deviation(ours, "move", "x@1", actor=OURS)
    _cmd(ours, "supersede", "--name", "x", actor=OURS)
    _specify(ours, "x", "2", actor=OURS)
    _assert_replayed(*_merge(theirs, ours, "--actor", THEIRS))
    assert _current(theirs, "x") == "x@2"
    _freeze(theirs, "x", "x-pack/2", actor=THEIRS)

    _assert_replayed(*_merge(ours, theirs))

    assert _units(ours)["x@2"] == "acceptance-frozen"


def _pre_log_store(tmp_path: Path) -> Path:
    """A legacy format-3 store whose only unit, x@1, exists only in its baseline."""
    project = tmp_path / "ours"
    root = project / ".vouch"
    (root / "objects" / "sha256").mkdir(parents=True)
    golden = json.loads(
        (ROOT / "tests" / "fixtures" / "state-format" / "format3.golden.json").read_text()
    )
    unit = next(item for item in golden["units"] if item["name"] == "docs")
    unit["name"] = "x"
    unit["version"] = "1"
    golden.update(
        units=[unit], evidence_heads=[], deviations=[], releases=[],
        formal_certificate_digest="", tier="lightweight", anchor_mode="none",
    )
    (root / "state.json").write_text(
        json.dumps(golden, sort_keys=True, separators=(",", ":")) + "\n"
    )
    _must("upgrade", "--root", str(root))
    return project


# guard:
def test_source_reference_uses_pre_log_baseline(tmp_path: Path) -> None:
    """R13: a unit known only from the pre-log baseline resolves on the source side."""
    ours = _pre_log_store(tmp_path)
    chain = _chain(ours)
    assert isinstance(chain[0].record.folded_event, PreLogHistoryRecorded)
    assert not any(isinstance(item.record.folded_event, UnitSpecified) for item in chain)
    assert _units(ours) == {"x@1": "specified"}
    theirs = _fork(ours)
    _specify(ours, "unrelated", "1", actor=OURS)
    _freeze(theirs, "x", "x-pack/1", bound=False, actor=THEIRS)

    _assert_replayed(*_merge(ours, theirs))

    assert _units(ours)["x@1"] == "acceptance-frozen"


# --------------------------------------------------------------------------- R14


def _index(project: Path) -> str:
    return _must_git(project, "ls-files", "--stage")


def test_git_stage_merge_refuses_a_version_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R14: merge-git-stages refuses R1's mark-ready case the same way."""
    project = _repository(tmp_path)
    _pre_state(project, "mark-ready", "1", actor=SHARED)

    def incoming() -> None:
        _run_op(project, "mark-ready", actor=THEIRS)

    def current() -> None:
        _deviation(project, "ours-move", "x@1", actor=OURS)
        _cmd(project, "supersede", "--name", "x", actor=OURS)
        _pre_state(project, "mark-ready", "2", actor=OURS)

    _git_diverge(project, incoming, current)
    source_store = _branch_store(project, "incoming")
    ours_store = _branch_store(project, "HEAD")
    source = _last(source_store, "UnitMarkedReady", "x")
    assert _units(ours_store) == {"x@1": "superseded", "x@2": "acceptance-frozen"}
    _assert_accepted(_snapshot(ours_store), MarkReady("x"))
    index = _index(project)

    _assert_refused(
        monkeypatch, project, lambda: _run("merge-git-stages", "--root", _root(project)),
        source=source, names=("x@1", "x@2"),
    )
    assert _index(project) == index


def test_git_stage_merge_refuses_an_unequal_anchor_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R14: merge-git-stages refuses R8's unequal-head anchor the same way."""
    project = _repository(tmp_path)
    _specify(project, "u", "v1")
    _freeze(project, "u", "u-pack/v1")
    _evidence(project, "u@v1", "green", OK)

    def incoming() -> None:
        _record_with_run(monkeypatch, "ci-7", lambda: _cmd(
            project, "advance-anchor", "--unit-name", "u", "--unit-version", "v1",
            actor=THEIRS,
        ))

    def current() -> None:
        _evidence(project, "u@v1", "refactor", OK, actor=OURS)

    _git_diverge(project, incoming, current)
    source_store = _branch_store(project, "incoming")
    ours_store = _branch_store(project, "HEAD")
    source = _last(source_store, "AnchorAdvanced", "u")
    theirs_head = _head(source_store, "u@v1")[0]
    ours_head = _head(ours_store, "u@v1")[0]
    assert theirs_head != ours_head
    _assert_accepted(_snapshot(ours_store), AdvanceAnchor("u", "ci-7", unit_version="v1"))
    index = _index(project)

    _assert_refused(
        monkeypatch, project, lambda: _run("merge-git-stages", "--root", _root(project)),
        source=source, names=("u@v1", theirs_head, ours_head),
    )
    assert _index(project) == index


# --------------------------------------------------------------------------- R15


# guard:
def test_fast_forward_copies_source_records_byte_for_byte(tmp_path: Path) -> None:
    """R15: a fast-forward copies the source records unchanged."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    theirs = _fork(ours)
    _specify(theirs, "y", "1", actor=THEIRS)

    code, output = _merge(ours, theirs, "--actor", "merger")

    assert code == 0, output
    assert "merge: fast-forward;" in output, output
    assert [(item.digest, item.payload) for item in _chain(ours)] == [
        (item.digest, item.payload) for item in _chain(theirs)
    ]


# guard:
def test_no_op_merge_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R15: merging an already-incorporated store writes nothing."""
    ours = _new(tmp_path)
    _specify(ours, "x", "1")
    theirs = _fork(ours)
    _specify(ours, "y", "1", actor=OURS)
    (ours / ".vouch" / ".lock").unlink(missing_ok=True)
    before = _files(ours)
    entered: list[str] = []
    original = Store.write_locked

    def spy(self: Store) -> AbstractContextManager[None]:
        entered.append("write_locked")
        return original(self)

    monkeypatch.setattr(Store, "write_locked", spy)
    code, output = _merge(ours, theirs)
    monkeypatch.setattr(Store, "write_locked", original)

    assert code == 0, output
    assert "merge: no-op;" in output, output
    assert _files(ours) == before
    assert entered == []


# --------------------------------------------------------------------------- R16


def _section(path: Path, heading: str) -> str:
    """HEADING's section: up to the next heading of the same or a higher level."""
    lines = path.read_text().splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = lines.index(heading)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"(#+) ", lines[index])
        if match is not None and len(match.group(1)) <= level:
            end = index
            break
    return " ".join(" ".join(lines[start:end]).split())


def test_merge_docs_state_that_replayed_records_keep_source_claims() -> None:
    """R16: both merge sections state the new claim and drop the old one."""
    sections = (
        _section(ROOT / "docs" / "quickstart.md", "### Concurrent branches"),
        _section(ROOT / "docs" / "claim-boundary.md", "## Merging stores"),
    )
    for text in sections:
        assert (
            "replayed records keep their source records' actor, run claim and recorded time"
            in text
        ), text
        assert "the merge record carries the merge's actor claim" in text, text
        assert "both pre-merge heads and the actor claim" not in text, text
        assert "recording both heads and the actor claim" not in text, text
