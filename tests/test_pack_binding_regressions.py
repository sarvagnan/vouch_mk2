from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot, UnitStatus
from vouch_mk2.projections import system_map
from vouch_mk2.store import (
    DecodeError,
    Store,
    StoreError,
    decode_system_map,
    encode_system_map,
)
from vouch_mk2.store.event_log import commit_pre_log_checkpoint as commit_snapshot_fixture


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def test_pack_inventory_refuses_path_traversal_at_encode_and_decode() -> None:
    from vouch_mk2.core.model import PackEntry, PackInventory
    from vouch_mk2.store import decode_pack_inventory, encode_pack_inventory

    inventory = PackInventory((PackEntry("../outside.py", "a" * 64, 1),))
    with pytest.raises(StoreError):
        encode_pack_inventory(inventory)
    with pytest.raises(DecodeError):
        decode_pack_inventory(
            _canonical(
                {
                    "pack_format": 1,
                    "entries": [
                        {"path": "../outside.py", "sha256": "a" * 64, "size": 1}
                    ],
                }
            )
        )


def test_freeze_refuses_symlinked_pack_file_without_committing(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "real.py"
    target.write_text("assert False\n")
    (project / "linked.py").symlink_to(target)
    root = str(project / ".vouch")
    Store.init(root).close()
    assert main(["specify", "--root", root, "--name", "u", "--version", "1"]) == 0

    assert main(
        [
            "freeze",
            "--root",
            root,
            "--name",
            "u",
            "--pack-version",
            "p1",
            "--project",
            str(project),
            "--pack-file",
            "linked.py",
        ]
    ) == 2
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("u")
        assert unit is not None and unit.status is UnitStatus.SPECIFIED


def test_pack_inventory_refuses_duplicate_paths_at_encode_and_decode() -> None:
    from vouch_mk2.core.model import PackEntry, PackInventory
    from vouch_mk2.store import decode_pack_inventory, encode_pack_inventory

    inventory = PackInventory(
        (
            PackEntry("tests/test_a.py", "a" * 64, 1),
            PackEntry("tests/test_a.py", "b" * 64, 2),
        )
    )
    with pytest.raises(StoreError, match="strictly sorted|duplicate"):
        encode_pack_inventory(inventory)
    with pytest.raises(DecodeError, match="duplicate"):
        decode_pack_inventory(
            _canonical(
                {
                    "pack_format": 1,
                    "entries": [
                        {"path": "tests/test_a.py", "sha256": "a" * 64, "size": 1},
                        {"path": "tests/test_a.py", "sha256": "b" * 64, "size": 2},
                    ],
                }
            )
        )


def test_system_map_format_three_refuses_format_two() -> None:
    encoded = encode_system_map(system_map(Snapshot()), "a" * 64)
    document = json.loads(encoded)
    assert document["map_format"] == 4
    document["map_format"] = 2
    with pytest.raises(DecodeError, match="format 2"):
        decode_system_map(_canonical(document))


def test_pack_history_and_binding_provenance_round_trip_strictly() -> None:
    from vouch_mk2.core.model import Tier, Unit
    from vouch_mk2.projections import next_actions, status_facts
    from vouch_mk2.store import decode_snapshot, encode_snapshot

    late = Unit(
        "u",
        "1",
        UnitStatus.PUBLISHED,
        acceptance_pack_version="p1",
        acceptance_pack_digest="a" * 64,
        published_once=True,
        pack_history=("p0",),
        pack_binding="late",
    )
    snapshot = Snapshot(revision=1, tier=Tier.STANDARD, units=(late,))
    encoded = encode_snapshot(snapshot)
    assert decode_snapshot(encoded) == snapshot
    unit_document = cast(dict[str, object], cast(list[object], json.loads(encoded)["units"])[0])
    assert unit_document["pack_history"] == ["p0"]
    assert unit_document["pack_binding"] == "late"

    mapped = system_map(snapshot)
    assert mapped.units[0].pack_history == ("p0",)
    assert mapped.units[0].pack_binding == "late"
    assert status_facts(snapshot, next_actions(snapshot)).late_bound_pack_units == ("u@1",)
    map_bytes = encode_system_map(mapped, "f" * 64)
    decoded_map, _source = decode_system_map(map_bytes)
    assert decoded_map == mapped

    document = json.loads(encoded)
    raw_unit = cast(dict[str, object], cast(list[object], document["units"])[0])
    raw_unit["pack_binding"] = ""
    with pytest.raises(DecodeError, match="empty iff"):
        decode_snapshot(_canonical(document))
    raw_unit["pack_binding"] = "eventually"
    with pytest.raises(DecodeError, match="at-freeze.*late"):
        decode_snapshot(_canonical(document))


def test_late_binding_is_allowed_only_for_active_records_and_tier_visible() -> None:
    from vouch_mk2.checks.tier import check_pack_bound
    from vouch_mk2.core import Accepted, BindPack, Refused, Unit, apply, decide
    from vouch_mk2.core.model import Tier
    from vouch_mk2.ir.schema import Verdict

    active_statuses = tuple(
        status for status in UnitStatus if status is not UnitStatus.SUPERSEDED
    )
    for status in active_statuses:
        before = Snapshot(
            units=(
                Unit(
                    "u",
                    "1",
                    status,
                    acceptance_pack_version="p1",
                    published_once=status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND},
                ),
            )
        )
        decision = decide(before, BindPack("u", "a" * 64))
        assert isinstance(decision, Accepted), status
        assert apply(before, decision.events).units[0].pack_binding == "late"

    history = Snapshot(
        units=(
            Unit(
                "u",
                "0",
                UnitStatus.SUPERSEDED,
                acceptance_pack_version="p0",
            ),
        )
    )
    refusal = decide(history, BindPack("u", "a" * 64))
    assert isinstance(refusal, Refused)
    assert refusal.violations[0].code == "pack.superseded"

    late = Unit(
        "u",
        "1",
        UnitStatus.PUBLISHED,
        acceptance_pack_version="p1",
        acceptance_pack_digest="a" * 64,
        published_once=True,
        pack_binding="late",
    )
    standard = check_pack_bound(Snapshot(tier=Tier.STANDARD, units=(late,)))
    assert standard.verdict is Verdict.PASS
    assert standard.detail == "bound late: u@1"
    high = check_pack_bound(Snapshot(tier=Tier.HIGH, units=(late,)))
    assert high.verdict is Verdict.FAIL
    assert "u@1" in high.detail and "bound late" in high.detail


def test_pack_files_check_is_tier_scoped_before_io_and_defaults_dot_vouch_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import vouch_mk2.verify as verify_module
    from vouch_mk2.core.model import Tier, Unit
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    empty_root = tmp_path / "empty-store"
    Store.init(str(empty_root)).close()
    lightweight = Snapshot(
        tier=Tier.LIGHTWEIGHT,
        units=(
            Unit(
                "u",
                "1",
                UnitStatus.ACCEPTANCE_FROZEN,
                acceptance_pack_version="p1",
                acceptance_pack_digest="a" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        verify_module,
        "build_pack_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("filesystem touched")),
    )
    with Store.open(str(empty_root), read_only=True) as store:
        rows = run_artifact_checks(store, lightweight, project=str(tmp_path))
    files_row = next(row for row in rows if row.check_id == mk2.CHECK_PACK_FILES_MATCH)
    assert files_row.verdict is Verdict.NOT_APPLICABLE
    assert "IR tier matrix lightweight cell" in files_row.detail
    monkeypatch.undo()

    project = tmp_path / "project"
    project.mkdir()
    pack = project / "pack.py"
    pack.write_text("assert False\n")
    root = project / ".vouch"
    Store.init(str(root)).close()
    assert main(["set-ci-policy", "--root", str(root), "--policy", "ledger"]) == 0
    assert main(["set-tier", "--root", str(root), "--tier", "standard"]) == 0
    assert main(["set-anchor-mode", "--root", str(root), "--mode", "ci"]) == 0
    assert main(["specify", "--root", str(root), "--name", "u", "--version", "1"]) == 0
    assert main([
        "freeze", "--root", str(root), "--name", "u", "--pack-version", "p1",
        "--project", str(project), "--pack-file", "pack.py",
    ]) == 0
    capsys.readouterr()
    assert main(["check", "--ci", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert f"project root used: {project}" in output
    pack.write_text("assert True\n")
    assert main(["check", "--ci", "--root", str(root)]) == 1
    assert "changed pack file 'pack.py'" in capsys.readouterr().out


def test_standard_pack_files_without_a_project_root_is_not_checked(tmp_path: Path) -> None:
    from vouch_mk2.core.model import Tier
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    root = tmp_path / "store"
    Store.init(str(root)).close()
    with Store.open(str(root), read_only=True) as store:
        row = next(
            result
            for result in run_artifact_checks(
                store, Snapshot(tier=Tier.STANDARD), project=None
            )
            if result.check_id == mk2.CHECK_PACK_FILES_MATCH
        )
    assert row.verdict is Verdict.NOT_CHECKED
    assert row.detail == "no project root; pass --project"


def test_empty_pack_inventory_is_refused_at_every_ingress(tmp_path: Path) -> None:
    from vouch_mk2.bundle import InventoryError, bind_pack, build_pack_inventory
    from vouch_mk2.core.model import PackInventory
    from vouch_mk2.store import decode_pack_inventory, encode_pack_inventory

    empty = PackInventory(())
    with pytest.raises(StoreError, match="at least one"):
        encode_pack_inventory(empty)
    with pytest.raises(DecodeError, match="must not be empty"):
        decode_pack_inventory(_canonical({"pack_format": 1, "entries": []}))
    with pytest.raises(InventoryError, match="at least one"):
        build_pack_inventory(str(tmp_path / "does-not-need-to-exist"), ())
    root = tmp_path / "store"
    Store.init(str(root)).close()
    with Store.open(str(root)) as store, pytest.raises(StoreError, match="empty"):
        bind_pack(store, "u", empty)


def test_pack_paths_require_exact_case_for_every_segment(tmp_path: Path) -> None:
    from vouch_mk2.bundle import InventoryError, build_pack_inventory

    project = tmp_path / "project"
    directory = project / "Tests"
    directory.mkdir(parents=True)
    (directory / "Pack.py").write_text("assert False\n")
    assert build_pack_inventory(str(project), ("Tests/Pack.py",)).entries[0].path == "Tests/Pack.py"
    for wrong in ("tests/Pack.py", "Tests/pack.py"):
        with pytest.raises(InventoryError, match="exact case"):
            build_pack_inventory(str(project), (wrong,))


def test_pack_path_surrogates_are_typed_store_refusals() -> None:
    from vouch_mk2.core.model import PackEntry, PackInventory
    from vouch_mk2.store import decode_pack_inventory, encode_pack_inventory

    path = "bad-\udcff.py"
    inventory = PackInventory((PackEntry(path, "a" * 64, 1),))
    with pytest.raises(StoreError, match="UTF-8"):
        encode_pack_inventory(inventory)
    data = json.dumps(
        {
            "entries": [{"path": path, "sha256": "a" * 64, "size": 1}],
            "pack_format": 1,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"
    with pytest.raises(DecodeError, match="UTF-8"):
        decode_pack_inventory(data)


def test_registry_completeness_requires_the_pack_bound_artifact_evaluator(
    tmp_path: Path,
) -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    root = tmp_path / "store"
    Store.init(str(root)).close()
    with Store.open(str(root), read_only=True) as store:
        snapshot = store.read_snapshot()
        complete = run_artifact_checks(store, snapshot)
    missing = tuple(row for row in complete if row.check_id != mk2.CHECK_PACK_BOUND)
    meta = next(
        row
        for row in run_checks(snapshot, artifact_results=missing).mechanical
        if row.check_id == mk2.CHECK_REGISTRY_COMPLETE
    )
    assert meta.verdict is Verdict.FAIL
    assert "artifact missing" in meta.detail and mk2.CHECK_PACK_BOUND in meta.detail


def test_mcp_status_exposes_unbound_and_late_bound_pack_units(tmp_path: Path) -> None:
    from vouch_mk2.core.model import Tier, Unit
    from vouch_mk2.mcp import serve

    root = tmp_path / "store"
    Store.init(str(root)).close()
    snapshot = Snapshot(
        revision=1,
        tier=Tier.STANDARD,
        units=(
            Unit("late", "1", UnitStatus.PUBLISHED, acceptance_pack_version="p1", acceptance_pack_digest="a" * 64, published_once=True, pack_binding="late"),
            Unit("unbound", "1", UnitStatus.ACCEPTANCE_FROZEN, acceptance_pack_version="p2"),
        ),
    )
    with Store.open(str(root)) as store:
        snapshot = commit_snapshot_fixture(store, snapshot)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {}},
        }
    )
    response = cast(dict[str, object], json.loads(next(iter(serve(str(root), [request])))))
    result = cast(dict[str, object], response["result"])
    structured = cast(dict[str, object], result["structuredContent"])
    status = cast(dict[str, object], structured["status"])
    assert status["unbound_pack_units"] == ["unbound@1"]
    assert status["late_bound_pack_units"] == ["late@1"]


def test_acceptance_control_and_claim_boundary_disclose_execution_gap() -> None:
    from vouch_mk2.ir.mk2 import MK2

    sentence = (
        "the pack's EXECUTION is not verified — runner configuration "
        "(conftest.py, pytest.ini, pyproject testpaths) can deselect a bound test; "
        "bind those files too"
    )
    requirement = next(
        row
        for row in MK2.tier_requirements
        if row.control_id == "acceptance-frozen-before-implementation"
    )
    assert all(sentence in cell.residual for cell in (
        requirement.lightweight, requirement.standard, requirement.high
    ))
    claim_boundary = Path("docs/claim-boundary.md").read_text()
    assert sentence in " ".join(claim_boundary.split())


def test_formal_bind_pack_transitions_preserve_m10_and_skip_superseded() -> None:
    from vouch_mk2.formal import HISTORY_V1, run_formal
    from vouch_mk2.ir import mk2

    history = run_formal().universe(HISTORY_V1.name)
    representatives = dict(history.relation.representatives)
    transitions = [
        (representatives[source], representatives[target])
        for source, command, target in history.relation.transitions
        if command == mk2.CMD_BIND_PACK
    ]
    assert transitions
    for before, after in transitions:
        changed = tuple(
            prior
            for prior in before.units
            if after.unit_ref(prior.ref()) != prior
        )
        assert len(changed) == 1
        assert changed[0].status is not UnitStatus.SUPERSEDED
        assert all(
            after.unit_ref(prior.ref()) == prior
            for prior in before.units
            if prior.status is UnitStatus.SUPERSEDED
        )
    m10 = next(
        verdict
        for verdict in history.invariants
        if verdict.invariant_id == "M10.one-active-record-per-name"
    )
    assert m10.held and not m10.violations
