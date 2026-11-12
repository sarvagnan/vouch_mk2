"""Unbound regressions for the effect-declarations review's ingress scenarios."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from vouch_mk2.bundle import build_pack_inventory, pack_test_set_digest
from vouch_mk2.cli import main
from vouch_mk2.core import Accepted, BindPack, FreezeAcceptance, decide
from vouch_mk2.core.events import AcceptancePackRebound
from vouch_mk2.store import ArtifactKind, Store, digest_of, encode_pack_inventory
from vouch_mk2.store.event_log import commit_events


def _run(project: Path, verb: str, *args: str) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = main([verb, "--root", str(project / ".vouch"), *args])
    return code, output.getvalue()


def _must(project: Path, verb: str, *args: str) -> str:
    code, output = _run(project, verb, *args)
    assert code == 0, (verb, output)
    return output


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "ours"
    project.mkdir()
    Store.init(str(project / ".vouch")).close()
    _must(project, "set-ci-policy", "--policy", "ledger")
    (project / "test_u.py").write_text("assert True\n")
    (project / "effects.json").write_text('{"effects_format":1,"effects":[]}')
    return project


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".lock"
    }


def _publish(project: Path, version: str, *, declared: bool = False) -> None:
    _must(project, "specify", "--name", "u", "--version", version)
    _must(
        project, "freeze", "--name", "u", "--pack-version", f"u-pack/{version}",
        "--project", str(project), "--pack-test-file", "test_u.py",
        *(["--pack-effects-file", "effects.json"] if declared else []),
    )
    _finish_publish(project, version)


def _finish_publish(project: Path, version: str) -> None:
    for verb in ("mark-ready", "start"):
        _must(project, verb, "--name", "u")
    for kind, program in (("red", "raise SystemExit(1)"), ("green", "pass")):
        _must(
            project, "record-evidence", "--unit", f"u@{version}", "--kind", kind,
            "--", sys.executable, "-c", program,
        )
    _must(project, "advance-anchor", "--unit-name", "u")
    for verb in ("mark-done", "publish"):
        _must(project, verb, "--name", "u")


def _rebind(project: Path, body: str) -> None:
    (project / "test_u.py").write_text(body)
    _must(
        project, "rebind-pack", "--name", "u", "--project", str(project),
        "--pack-test-file", "test_u.py", "--reason", "adapt shared interface",
    )


def _effects_row(project: Path) -> str:
    _code, output = _run(project, "check", "--project", str(project))
    return next(line for line in output.splitlines() if "check.effects-declared" in line)


def _mock_git_index(ours: Path, theirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply read-only index/blob responses from real stores, without running Git."""
    ours_files, theirs_files = _files(ours / ".vouch"), _files(theirs / ".vouch")
    blobs: dict[bytes, bytes] = {}
    rows: list[bytes] = []
    for name in sorted(ours_files.keys() | theirs_files.keys()):
        variants = (
            [(2, ours_files[name]), (3, theirs_files[name])]
            if name == "state.json"
            else [(0, ours_files[name] if name in ours_files else theirs_files[name])]
        )
        for stage, payload in variants:
            oid = hashlib.sha1(
                b"blob " + str(len(payload)).encode() + b"\0" + payload,
            ).hexdigest().encode()
            blobs[oid] = payload
            rows.append(
                b"100644 " + oid + b" " + str(stage).encode()
                + b"\t.vouch/" + name.encode() + b"\0",
            )
    index = b"".join(rows)

    def git_output(repository: str, *args: bytes) -> bytes:
        assert repository == str(ours)
        if args[0] == b"rev-parse":
            return b"\n"
        if args[0] == b"ls-files":
            return index
        if args[0] == b"cat-file":
            return blobs[args[-1]]
        raise AssertionError(args)

    monkeypatch.setattr("vouch_mk2.git_merge._git_output", git_output)


@pytest.mark.parametrize("entry_point", ["merge", "merge-git-stages"])
def test_a_predecessors_rebind_cannot_remove_its_successors_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_point: str,
) -> None:
    ours = _project(tmp_path)
    _publish(ours, "1")
    theirs = tmp_path / "theirs"
    shutil.copytree(ours, theirs)
    _must(ours, "require-effects")
    _must(ours, "supersede", "--name", "u")
    _publish(ours, "2", declared=True)
    (theirs / "test_u.py").write_text("assert 1 == 1\n")
    _must(
        theirs, "rebind-pack", "--name", "u", "--project", str(theirs),
        "--pack-test-file", "test_u.py", "--reason", "adapt shared interface",
    )
    if entry_point == "merge-git-stages":
        _mock_git_index(ours, theirs, monkeypatch)
    before_ours, before_theirs = _files(ours / ".vouch"), _files(theirs / ".vouch")
    before_declaration = _must(ours, "show-effects", "--json")
    assert '"unit": "u@2"' in before_declaration
    assert '"declaration": "declared"' in before_declaration

    code, output = _run(
        ours, entry_point,
        *(["--other", str(theirs / ".vouch")] if entry_point == "merge" else []),
    )

    assert code != 0 and "rebind.stale-pack" in output, output
    assert _files(ours / ".vouch") == before_ours
    assert _files(theirs / ".vouch") == before_theirs
    assert _must(ours, "show-effects", "--json") == before_declaration


def test_check_detects_an_undeclared_pack_adopted_by_fast_forward(tmp_path: Path) -> None:
    ours = _project(tmp_path)
    _must(ours, "require-effects")
    _must(ours, "specify", "--name", "u", "--version", "1")
    theirs = tmp_path / "theirs"
    shutil.copytree(ours, theirs)
    inventory = build_pack_inventory(str(theirs), (), ("test_u.py",))
    data = encode_pack_inventory(inventory)
    with Store.open(str(theirs / ".vouch")) as store, store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, FreezeAcceptance(
            name="u", pack_version="u-pack/1",
            pack_digest=digest_of(ArtifactKind.ACCEPTANCE_PACK, data),
            test_set_digest=pack_test_set_digest(inventory), effects_declared=True,
        ))
        assert isinstance(decision, Accepted)
        commit_events(
            store, snapshot, decision.events, actor="crafted", run_id="",
            objects=((ArtifactKind.ACCEPTANCE_PACK, data),),
        )

    assert "fast-forward" in _must(ours, "merge", "--other", str(theirs / ".vouch"))
    code, output = _run(ours, "check", "--project", str(ours))

    assert code != 0
    row = next(line for line in output.splitlines() if "check.effects-declared" in line)
    assert row.startswith("FAIL") and "u@1" in row
    assert "bound after effects were required" in row
    assert next(line for line in output.splitlines() if "check.event-log" in line).startswith("PASS")


@pytest.mark.parametrize("field", ["system", "detail"])
def test_cli_refuses_lone_surrogates_without_writing(tmp_path: Path, field: str) -> None:
    project = _project(tmp_path)
    _must(project, "specify", "--name", "u", "--version", "1")
    action = {"kind": "action", "name": "send", "system": "Portal", "outcome": "confirmed"}
    action[field] = "\ud800"
    (project / "effects.json").write_text(json.dumps({"effects_format": 1, "effects": [action]}))
    before = _files(project / ".vouch")

    code, output = _run(
        project, "freeze", "--name", "u", "--pack-version", "u-pack/1",
        "--project", str(project), "--pack-test-file", "test_u.py",
        "--pack-effects-file", "effects.json",
    )

    assert code == 2 and "UTF-8" in output, output
    assert "Traceback" not in output
    assert _files(project / ".vouch") == before


def test_a_grandfathered_rebind_keeps_its_original_binding(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _publish(project, "1")
    _must(project, "require-effects")
    assert _effects_row(project).startswith("PASS")

    _rebind(project, "assert 1 == 1\n")

    row = _effects_row(project)
    assert row.startswith("PASS") and "not declared: u@1" in row
    # The ordinary D-14 repair is fresh GREEN; no declaration change is needed.
    _must(project, "record-evidence", "--unit", "u@1", "--kind", "green",
          "--", sys.executable, "-c", "pass")
    code, output = _run(project, "check", "--ci", "--project", str(project))
    assert code == 0, output


def test_two_rebinds_preserve_a_grandfathered_late_binding(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _must(project, "specify", "--name", "u", "--version", "1")
    _must(project, "freeze", "--name", "u", "--pack-version", "label-only")
    _must(project, "bind-pack", "--name", "u", "--project", str(project),
          "--pack-test-file", "test_u.py")
    _finish_publish(project, "1")
    _must(project, "require-effects")

    _rebind(project, "assert 1 == 1\n")
    _rebind(project, "assert 2 == 2\n")

    row = _effects_row(project)
    assert row.startswith("PASS") and "not declared: u@1" in row


def test_a_sibling_branch_rebind_stays_grandfathered_after_merge(tmp_path: Path) -> None:
    ours = _project(tmp_path)
    _publish(ours, "1")
    theirs = tmp_path / "theirs"
    shutil.copytree(ours, theirs)
    _must(ours, "require-effects")
    _rebind(theirs, "assert 2 == 2\n")

    _must(ours, "merge", "--other", str(theirs / ".vouch"))

    with Store.open(str(ours / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
        unit = snapshot.unit("u")
        assert snapshot.effects_required and unit is not None
        assert len(unit.rebound_history) == 1
    row = _effects_row(ours)
    assert row.startswith("PASS") and "not declared: u@1" in row
    _code, output = _run(ours, "check", "--project", str(ours))
    assert next(line for line in output.splitlines() if "check.event-log" in line).startswith("PASS")


@pytest.mark.parametrize("reused_predecessor_pack", [False, True])
def test_an_undeclared_late_binding_after_the_requirement_still_fails_after_rebind(
    tmp_path: Path, reused_predecessor_pack: bool,
) -> None:
    project = _project(tmp_path)
    version = "1"
    if reused_predecessor_pack:
        _publish(project, "1")
        _must(project, "supersede", "--name", "u")
        version = "2"
    _must(project, "specify", "--name", "u", "--version", version)
    _must(project, "freeze", "--name", "u", "--pack-version", "label-only")
    _must(project, "require-effects")
    inventory = build_pack_inventory(str(project), (), ("test_u.py",))
    data = encode_pack_inventory(inventory)
    with Store.open(str(project / ".vouch")) as store, store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, BindPack(
            name="u", pack_digest=digest_of(ArtifactKind.ACCEPTANCE_PACK, data),
            test_set_digest=pack_test_set_digest(inventory), effects_declared=True,
        ))
        assert isinstance(decision, Accepted)
        commit_events(store, snapshot, decision.events, actor="crafted", run_id="",
                      objects=((ArtifactKind.ACCEPTANCE_PACK, data),))
    _finish_publish(project, version)
    _rebind(project, "assert 3 == 3\n")
    # Compare with the first requirement, even if a later one follows the rebind.
    _must(project, "require-effects")

    row = _effects_row(project)
    assert row.startswith("FAIL")
    assert f"u@{version}: bound after effects were required" in row
    if reused_predecessor_pack:
        assert "u@1" not in row


@pytest.mark.parametrize("broken_at", ["binding", "version-boundary"])
def test_a_broken_rebind_chain_is_reported(tmp_path: Path, broken_at: str) -> None:
    project = _project(tmp_path)
    _publish(project, "1")
    _must(project, "require-effects")
    if broken_at == "version-boundary":
        # The missing origin must not resolve to the identical digest in u@1.
        _must(project, "supersede", "--name", "u")
        _must(project, "specify", "--name", "u", "--version", "2")
        _must(project, "freeze", "--name", "u", "--pack-version", "label-only")
    with Store.open(str(project / ".vouch")) as store, store.write_locked():
        snapshot = store.read_snapshot()
        old_unit = snapshot.units[0]
        assert old_unit.acceptance_pack_digest
        rebound = AcceptancePackRebound(
            name="u", old_pack_digest=(
                old_unit.acceptance_pack_digest if broken_at == "version-boundary" else "9" * 64
            ), new_pack_digest=old_unit.acceptance_pack_digest,
            old_test_set_digest=old_unit.acceptance_test_set_digest,
            new_test_set_digest=old_unit.acceptance_test_set_digest,
            reason="crafted broken ancestry", evidence_head_sequence=2,
        )
        commit_events(store, snapshot, (rebound,), actor="crafted", run_id="")

    row = _effects_row(project)
    assert row.startswith("FAIL") and "effects binding history unavailable" in row
    assert ("u@2" if broken_at == "version-boundary" else "u@1") in row


def test_a_pre_log_binding_stays_grandfathered_after_rebind(tmp_path: Path) -> None:
    from vouch_mk2.store.event_log import commit_pre_log_checkpoint

    source = _project(tmp_path)
    _publish(source, "1")
    with Store.open(str(source / ".vouch"), read_only=True) as store:
        baseline = store.read_snapshot()
        unit = baseline.unit("u")
        assert unit is not None
        data = store.get_object(ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest)
    project = tmp_path / "checkpoint"
    project.mkdir()
    (project / "test_u.py").write_text("assert True\n")
    with Store.init(str(project / ".vouch")) as store, store.write_locked():
        commit_pre_log_checkpoint(
            store, replace(baseline, evidence_heads=()),
            objects=((ArtifactKind.ACCEPTANCE_PACK, data),),
        )
    _must(project, "require-effects")
    _rebind(project, "assert 4 == 4\n")

    row = _effects_row(project)
    assert row.startswith("PASS") and "not declared: u@1" in row
