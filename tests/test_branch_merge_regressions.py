"""Non-pack regressions for branch-merge@1's atomic and replay seams."""

from __future__ import annotations

import io
import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import Accepted, RecordMerge, Refused, Snapshot, apply, decide
from vouch_mk2.store import ArtifactKind, Store, StoreError, digest_of


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _branch(tmp_path: Path) -> tuple[Path, Path]:
    ours_project = tmp_path / "ours"
    ours_project.mkdir()
    assert _run("init", "--project", str(ours_project))[0] == 0
    ours = ours_project / ".vouch"
    assert _run(
        "specify", "--root", str(ours), "--name", "shared", "--version", "v1"
    )[0] == 0
    theirs_project = tmp_path / "theirs"
    theirs_project.mkdir()
    theirs = theirs_project / ".vouch"
    shutil.copytree(ours, theirs)
    return ours, theirs


def _files(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        sorted(
            (str(path.relative_to(root)), path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _snapshot(root: Path) -> Snapshot:
    with Store.open(str(root), read_only=True) as store:
        return store.read_snapshot()


def _unit_names(root: Path) -> set[str]:
    return {unit.name for unit in _snapshot(root).units}


def test_repeating_the_same_merge_is_a_no_op(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    assert _run("merge", "--root", str(ours), "--other", str(theirs))[0] == 0
    before = _files(ours)

    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 0 and "no-op" in output
    assert _files(ours) == before
    assert _unit_names(ours) == {"shared", "mine", "yours"}


def test_reverse_merge_skips_the_segment_replayed_from_its_own_head(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    assert _run("merge", "--root", str(ours), "--other", str(theirs))[0] == 0

    code, output = _run("merge", "--root", str(theirs), "--other", str(ours))

    assert code == 0, output
    assert "replayed 1 source record(s)" in output
    assert _unit_names(theirs) == {"shared", "mine", "yours"}
    check_code, check_output = _run("check", "--root", str(theirs))
    assert check_code == 0, check_output


def test_merging_an_advanced_branch_replays_only_records_after_its_merged_head(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    assert _run("merge", "--root", str(ours), "--other", str(theirs))[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "later", "--version", "v1"
    )[0] == 0

    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 0, output
    assert "replayed 1 source record(s)" in output
    assert _unit_names(ours) == {"shared", "mine", "yours", "later"}


def test_refused_merge_does_not_create_a_missing_lock_or_change_any_store_file(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "clash", "--version", "ours"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "clash", "--version", "theirs"
    )[0] == 0
    (ours / ".lock").unlink()
    before = _files(ours)

    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 2 and "merge.conflict" in output
    assert _files(ours) == before
    assert not (ours / ".lock").exists()


def test_merge_replays_a_multi_event_command_as_one_decision(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    for argv in (
        ("specify", "--name", "victim", "--version", "v1"),
        ("freeze", "--name", "victim", "--pack-version", "p1"),
        ("mark-ready", "--name", "victim"),
        ("start", "--name", "victim"),
        (
            "open-deviation",
            "--deviation-id",
            "DEV-merge",
            "--deviation-type",
            "pack-defect",
            "--unit",
            "victim@v1",
        ),
    ):
        assert _run(argv[0], "--root", str(theirs), *argv[1:])[0] == 0

    assert _run("merge", "--root", str(ours), "--other", str(theirs))[0] == 0
    with Store.open(str(ours), read_only=True) as store:
        snapshot = store.read_snapshot()
        victim = snapshot.unit("victim")
        deviation = snapshot.deviation("DEV-merge")
    assert victim is not None and victim.status.value == "blocked"
    assert deviation is not None and deviation.status.value == "open"
    code, output = _run("check", "--root", str(ours))
    assert code == 0, output
    assert "PASS           check.event-log" in output


def test_fast_forward_copies_pack_objects_referenced_by_the_adopted_snapshot(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    pack_file = tmp_path / "theirs" / "acceptance.txt"
    pack_file.write_text("frozen\n", encoding="utf-8")
    assert _run(
        "freeze",
        "--root",
        str(theirs),
        "--name",
        "shared",
        "--pack-version",
        "p1",
        "--project",
        str(pack_file.parent),
        "--pack-file",
        pack_file.name,
    )[0] == 0

    assert _run("merge", "--root", str(ours), "--other", str(theirs))[0] == 0
    with Store.open(str(ours), read_only=True) as store:
        unit = store.read_snapshot().unit("shared")
        assert unit is not None
        store.get_object(ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest)


def test_merge_does_not_import_an_unreachable_foreign_object(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    payload = b"unreachable foreign acceptance pack"
    digest = digest_of(ArtifactKind.ACCEPTANCE_PACK, payload)
    with Store.open(str(theirs)) as store:
        with store.write_locked():
            assert store.put_object(ArtifactKind.ACCEPTANCE_PACK, payload) == digest
    assert not (ours / "objects" / "sha256" / digest).exists()

    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 0, output
    assert not (ours / "objects" / "sha256" / digest).exists()


def test_caught_merge_placement_error_removes_every_new_canonical_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    pack_file = tmp_path / "theirs" / "acceptance.txt"
    pack_file.write_text("frozen\n", encoding="utf-8")
    assert _run(
        "freeze",
        "--root",
        str(theirs),
        "--name",
        "shared",
        "--pack-version",
        "p1",
        "--project",
        str(pack_file.parent),
        "--pack-file",
        pack_file.name,
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    before = _files(ours)
    original = Store.put_object
    placements = 0

    def fail_after_second_placement(
        store: Store, kind: ArtifactKind, payload: bytes
    ) -> str:
        nonlocal placements
        digest = original(store, kind, payload)
        placements += 1
        if placements == 2:
            raise StoreError("INJECT after second canonical placement")
        return digest

    monkeypatch.setattr(Store, "put_object", fail_after_second_placement)
    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 2 and "INJECT after second canonical placement" in output
    assert placements == 2
    assert _files(ours) == before


def test_exception_before_merge_state_replacement_removes_all_staged_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    before = _files(ours)

    def fail_before_replacement(store: Store, temp_name: str) -> None:
        raise StoreError("INJECT before merge state replacement")

    monkeypatch.setattr(Store, "_replace_state", fail_before_replacement)
    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 2 and "INJECT before merge state replacement" in output
    assert _files(ours) == before


def test_writable_open_recovers_an_interrupted_merge_transaction(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "mine", "--version", "v1"
    )[0] == 0
    pack_file = tmp_path / "theirs" / "acceptance.txt"
    pack_file.write_text("frozen\n", encoding="utf-8")
    assert _run(
        "freeze",
        "--root",
        str(theirs),
        "--name",
        "shared",
        "--pack-version",
        "p1",
        "--project",
        str(pack_file.parent),
        "--pack-file",
        pack_file.name,
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "yours", "--version", "v1"
    )[0] == 0
    before = _files(ours)

    child = os.fork()
    if child == 0:
        def exit_before_replacement(self: Store, temp_name: str) -> None:
            os._exit(77)

        Store._replace_state = exit_before_replacement
        _run("merge", "--root", str(ours), "--other", str(theirs))
        os._exit(78)
    waited, status = os.waitpid(child, 0)

    assert waited == child and os.waitstatus_to_exitcode(status) == 77
    assert _files(ours) != before
    with Store.open(str(ours)):
        pass
    assert _files(ours) == before


def test_merge_conflict_json_names_origin_revision_event_and_decide_code(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run(
        "specify", "--root", str(ours), "--name", "clash", "--version", "v1"
    )[0] == 0
    assert _run(
        "specify", "--root", str(theirs), "--name", "clash", "--version", "v2"
    )[0] == 0

    code, output = _run(
        "merge", "--root", str(ours), "--other", str(theirs), "--json"
    )

    document = json.loads(output)
    assert code == 2
    assert document["violations"][0]["code"] == "merge.conflict"
    detail = document["violations"][0]["detail"]
    assert all(word in detail for word in ("UnitSpecified", "clash", "revision", "unit.exists"))


def test_merge_fact_changes_only_revision_when_folded() -> None:
    invalid = decide(Snapshot(), RecordMerge("bad", "also-bad"))
    assert isinstance(invalid, Refused)
    assert invalid.violations[0].code == "merge.head-invalid"

    accepted = decide(Snapshot(), RecordMerge("a" * 64, "b" * 64))
    assert isinstance(accepted, Accepted)
    after = apply(Snapshot(), accepted.events)
    assert after == Snapshot(revision=1)


def test_identical_merge_is_read_only_even_when_the_lock_file_is_absent(
    tmp_path: Path,
) -> None:
    ours, theirs = _branch(tmp_path)
    (ours / ".lock").unlink()
    before = _files(ours)

    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 0 and "no-op" in output
    assert _files(ours) == before
    assert not (ours / ".lock").exists()


def test_two_empty_stores_are_unrelated_histories(tmp_path: Path) -> None:
    projects = (tmp_path / "empty-ours", tmp_path / "empty-theirs")
    for project in projects:
        project.mkdir()
        assert _run("init", "--project", str(project))[0] == 0
    ours, theirs = (project / ".vouch" for project in projects)
    before = _files(ours)

    code, output = _run("merge", "--root", str(ours), "--other", str(theirs))

    assert code == 2 and "merge.unrelated-histories" in output
    assert _files(ours) == before
