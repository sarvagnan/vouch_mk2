"""Acceptance pack for self-hosted unit branch-merge@1 (D-16's semantic merge).

Every test here FAILED against the pre-change tree (RED recorded before implementation) and must PASS
at completion -- except tests marked `# guard:`. The implementer may not edit this file; a finding that
this pack is wrong is a DEVIATION adjudicated by the owner.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _store(tmp_path: Path, name: str) -> str:
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    code, out = _run("init", "--project", str(project))
    assert code == 0, out
    return str(project / ".vouch")


def _head(root: str) -> str:
    from vouch_mk2.store import Store

    with Store.open(root, read_only=True) as store:
        return store.read_snapshot().event_head


def _units(root: str) -> set[str]:
    from vouch_mk2.store import Store

    with Store.open(root, read_only=True) as store:
        return {u.name for u in store.read_snapshot().units}


def _branch(tmp_path: Path) -> tuple[str, str]:
    """A common ancestor, then a copy: OURS and THEIRS share history."""
    ours = _store(tmp_path, "ours")
    assert _run("specify", "--root", ours, "--name", "shared", "--version", "v1")[0] == 0
    theirs = str(tmp_path / "theirs" / ".vouch")
    Path(theirs).parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ours, theirs)
    return ours, theirs


# ---- R1 ----


def test_merge_replays_the_other_branch_onto_ours(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run("specify", "--root", ours, "--name", "mine", "--version", "v1")[0] == 0
    assert _run("specify", "--root", theirs, "--name", "yours", "--version", "v1")[0] == 0
    code, out = _run("merge", "--root", ours, "--other", theirs)
    assert code == 0, out
    assert _units(ours) == {"shared", "mine", "yours"}, _units(ours)


# ---- R2 ----


def test_a_genuine_conflict_refuses_the_whole_merge_and_writes_nothing(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    # Both branches create the SAME unit name at different versions: decide() cannot accept both.
    assert _run("specify", "--root", ours, "--name", "clash", "--version", "v1")[0] == 0
    assert _run("specify", "--root", theirs, "--name", "clash", "--version", "v2")[0] == 0
    before_state = (Path(ours) / "state.json").read_bytes()
    before_head = _head(ours)
    code, out = _run("merge", "--root", ours, "--other", theirs)
    assert code == 2, out
    assert "merge.conflict" in out, out
    assert "clash" in out, out
    assert (Path(ours) / "state.json").read_bytes() == before_state
    assert _head(ours) == before_head


# ---- R3 ----


def test_unrelated_histories_are_refused(tmp_path: Path) -> None:
    ours = _store(tmp_path, "a")
    theirs = _store(tmp_path, "b")
    assert _run("specify", "--root", ours, "--name", "x", "--version", "v1")[0] == 0
    assert _run("specify", "--root", theirs, "--name", "y", "--version", "v1")[0] == 0
    code, out = _run("merge", "--root", ours, "--other", theirs)
    assert code == 2 and "merge.unrelated-histories" in out, out


# ---- R4 ----


def test_fast_forward_when_our_chain_is_a_prefix(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run("specify", "--root", theirs, "--name", "ahead", "--version", "v1")[0] == 0
    their_head = _head(theirs)
    code, out = _run("merge", "--root", ours, "--other", theirs)
    assert code == 0, out
    assert _head(ours) == their_head, "a fast-forward must adopt their head, not rebuild records"
    assert _units(ours) == {"shared", "ahead"}


# ---- R5 ----


def test_merging_an_ancestor_changes_nothing(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run("specify", "--root", ours, "--name", "ahead", "--version", "v1")[0] == 0
    before = _head(ours)
    code, out = _run("merge", "--root", ours, "--other", theirs)
    assert code == 0, out
    assert _head(ours) == before, "merging an ancestor must not append records"


# ---- R6 ----


def test_merged_store_still_replays_to_its_snapshot(tmp_path: Path) -> None:
    ours, theirs = _branch(tmp_path)
    assert _run("specify", "--root", ours, "--name", "mine", "--version", "v1")[0] == 0
    assert _run("specify", "--root", theirs, "--name", "yours", "--version", "v1")[0] == 0
    assert _run("merge", "--root", ours, "--other", theirs)[0] == 0
    assert _run("generate", "--root", ours)[0] == 0
    code, out = _run("check", "--root", ours)
    rows = [line for line in out.splitlines() if "check.event-log" in line]
    assert rows and rows[0].startswith("PASS"), out


# ---- R7 ----


def test_merge_records_both_heads_and_the_actor_claim(tmp_path: Path) -> None:
    from vouch_mk2.store import ArtifactKind, Store

    ours, theirs = _branch(tmp_path)
    assert _run("specify", "--root", ours, "--name", "mine", "--version", "v1")[0] == 0
    assert _run("specify", "--root", theirs, "--name", "yours", "--version", "v1")[0] == 0
    our_head_before, their_head = _head(ours), _head(theirs)
    assert _run("merge", "--root", ours, "--other", theirs, "--actor", "merge-bot")[0] == 0
    with Store.open(ours, read_only=True) as store:
        snapshot = store.read_snapshot()
        digest = snapshot.event_head
        found = None
        while digest and digest != "GENESIS":
            record = json.loads(store.get_object(ArtifactKind.EVENT, str(digest)))
            blob = json.dumps(record)
            if our_head_before in blob and their_head in blob:
                found = record
                break
            digest = str(record["prev"])
    assert found is not None, "no record names both merged heads"
    assert found["actor"] == "merge-bot"


# ---- R8 ----


def test_docs_describe_merge_and_what_it_does_not_prove() -> None:
    quickstart = (ROOT / "docs" / "quickstart.md").read_text().lower()
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text().lower()
    assert "merge" in quickstart and "merge" in boundary
    assert "refus" in boundary
    assert "replay" in boundary
