"""Acceptance pack for self-hosted unit pack-binding@1 (D-14; bead bd4.6).

Every test here FAILED against the pre-change tree (RED recorded before implementation)
and must PASS at completion — except tests marked `# guard:`. The implementer may not edit
this file; a finding that this pack is wrong is a DEVIATION adjudicated by the owner.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot, UnitStatus
from vouch_mk2.store import Store, decode_snapshot, encode_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_a.py").write_text("def test_a():\n    assert False\n")
    (project / "tests" / "test_b.py").write_text("def test_b():\n    assert False\n")
    root = str(project / ".vouch")
    Store.init(root).close()
    assert main(["specify", "--root", root, "--name", "core", "--version", "v1"]) == 0
    return project, root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- the field, the format, the object ----


def test_state_format_is_two_and_the_unit_carries_a_pack_digest() -> None:
    from vouch_mk2.core.model import Unit
    from vouch_mk2.store import CURRENT_STATE_FORMAT

    assert CURRENT_STATE_FORMAT == 2
    unit = Unit("u", "1", UnitStatus.ACCEPTANCE_FROZEN, (), "u-pack/1", False, acceptance_pack_digest="a" * 64, pack_history=("u-pack/0",))
    snapshot = Snapshot(revision=1, units=(unit,))
    assert decode_snapshot(encode_snapshot(snapshot)) == snapshot
    document = json.loads(encode_snapshot(snapshot))["units"][0]
    assert document["acceptance_pack_digest"] == "a" * 64 and document["pack_history"] == ["u-pack/0"]


def test_format1_upgrades_to_format2_with_unbound_packs() -> None:
    from vouch_mk2.store.upgrade import upgrade_document

    document = json.loads((ROOT / "tests" / "fixtures" / "state-format" / "format1.golden.json").read_bytes())
    upgraded, steps = upgrade_document(document)
    assert steps == [(1, 2)]
    units = upgraded["units"]
    assert isinstance(units, list)
    for unit in units:
        assert unit["acceptance_pack_digest"] == "" and unit["pack_history"] == []
    decode_snapshot(json.dumps(upgraded, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")


def test_format2_golden_is_pinned() -> None:
    # If this fails you changed the canonical layout: bump the format, add an upgrader, and
    # ask the owner to re-pin. The golden is the format-1 golden snapshot at format 2 with
    # digests bound on core@v1/core@v0 and ui@v1 left label-only (a legal state).
    golden = (ROOT / "tests" / "fixtures" / "state-format" / "format2.golden.json").read_bytes()
    snapshot = decode_snapshot(golden)
    assert snapshot.revision == 7
    assert {u.acceptance_pack_digest for u in snapshot.units} == {"", "c" * 64, "d" * 64}
    assert encode_snapshot(snapshot) == golden


def test_pack_inventory_is_a_canonical_object_kind() -> None:
    from vouch_mk2.core.model import PackEntry, PackInventory
    from vouch_mk2.store import ArtifactKind, decode_pack_inventory, digest_of, encode_pack_inventory

    inventory = PackInventory(entries=(PackEntry("tests/test_a.py", "a" * 64, 12), PackEntry("tests/test_b.py", "b" * 64, 3)))
    data = encode_pack_inventory(inventory)
    assert decode_pack_inventory(data) == inventory
    assert json.loads(data)["pack_format"] == 1
    assert len(digest_of(ArtifactKind.ACCEPTANCE_PACK, data)) == 64
    with pytest.raises(Exception):
        encode_pack_inventory(PackInventory(entries=(PackEntry("tests/test_b.py", "b" * 64, 3), PackEntry("tests/test_a.py", "a" * 64, 12))))  # unsorted


# ---- freeze binds; bind-pack binds after the fact; write-once ----


def test_freeze_with_pack_files_binds_an_inventory_object(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "core-pack/1", "--project", str(project),
                 "--pack-file", "tests/test_a.py", "--pack-file", "tests/test_b.py"]) == 0
    from vouch_mk2.store import ArtifactKind, decode_pack_inventory

    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("core")
        assert unit is not None and len(unit.acceptance_pack_digest) == 64
        inventory = decode_pack_inventory(store.get_object(ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest))
    assert [e.path for e in inventory.entries] == ["tests/test_a.py", "tests/test_b.py"]
    assert inventory.entries[0].sha256 == _sha(project / "tests" / "test_a.py")


def test_freeze_without_files_is_label_only_and_bind_pack_binds_later(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "core-pack/1"]) == 0
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().unit("core").acceptance_pack_digest == ""  # type: ignore[union-attr]
    assert main(["bind-pack", "--root", root, "--name", "core", "--project", str(project), "--pack-file", "tests/test_a.py"]) == 0
    with Store.open(root, read_only=True) as store:
        assert len(store.read_snapshot().unit("core").acceptance_pack_digest) == 64  # type: ignore[union-attr]


def test_binding_is_write_once_per_pack_version_and_respecify_clears_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, root = _project(tmp_path)
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "core-pack/1", "--project", str(project), "--pack-file", "tests/test_a.py"]) == 0
    assert main(["bind-pack", "--root", root, "--name", "core", "--project", str(project), "--pack-file", "tests/test_b.py"]) == 2
    assert "pack.already-bound" in capsys.readouterr().err
    for argv in (["mark-ready", "--name", "core"], ["start", "--name", "core"],
                 ["open-deviation", "--deviation-id", "D1", "--deviation-type", "pack-defect", "--unit", "core@v1"],
                 ["adjudicate-amend", "--deviation-id", "D1", "--amendment-version", "core-pack/2"],
                 ["close-deviation", "--deviation-id", "D1"], ["respecify", "--name", "core"]):
        assert main([argv[0], "--root", root, *argv[1:]]) == 0, argv
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("core")
        assert unit is not None and unit.acceptance_pack_digest == "" and unit.acceptance_pack_version == ""
        # pack/2 (DEV-pb-01): the retired label is REMEMBERED so it can never be rebound.
        assert unit.pack_history == ("core-pack/1",)
    capsys.readouterr()
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "core-pack/1", "--project", str(project), "--pack-file", "tests/test_b.py"]) == 2
    assert "pack.version-reused" in capsys.readouterr().err
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "core-pack/2", "--project", str(project), "--pack-file", "tests/test_b.py"]) == 0


def test_bind_pack_is_refused_on_superseded_history() -> None:
    # pack/2 (DEV-pb-01): a superseded record is inert history (D-9, M10); binding it is refused.
    from vouch_mk2.core import Accepted, BindPack, Refused, decide
    from vouch_mk2.core.model import Unit

    s = Snapshot(revision=1, units=(Unit("u", "0", UnitStatus.SUPERSEDED, (), "u-pack/1", True), Unit("u", "1", UnitStatus.PUBLISHED, (), "u-pack/2", True)))
    d = decide(s, BindPack("u", "a" * 64))
    # the active record (u@1) is the one addressed by name; it binds fine
    assert isinstance(d, Accepted)
    only_history = Snapshot(revision=1, units=(Unit("u", "0", UnitStatus.SUPERSEDED, (), "u-pack/1", True),))
    d2 = decide(only_history, BindPack("u", "a" * 64))
    assert isinstance(d2, Refused) and d2.violations[0].code in ("unit.unknown", "pack.superseded")


def test_freeze_refuses_a_pack_file_outside_the_project_or_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, root = _project(tmp_path)
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "p", "--project", str(project), "--pack-file", "../outside.py"]) == 2
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "p", "--project", str(project), "--pack-file", "tests/nope.py"]) == 2
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().unit("core").status is UnitStatus.SPECIFIED  # type: ignore[union-attr]


# ---- the checks: tier-scoped binding, working-tree re-verification ----


def test_pack_bound_check_is_tier_scoped(tmp_path: Path) -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core.model import Tier, Unit
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict

    unbound = Unit("u", "1", UnitStatus.ACCEPTANCE_FROZEN, (), "u-pack/1", False)
    light = Snapshot(revision=1, tier=Tier.LIGHTWEIGHT, units=(unbound,))
    standard = Snapshot(revision=1, tier=Tier.STANDARD, units=(unbound,))
    row = lambda s: next(r for r in run_checks(s).mechanical if r.check_id == mk2.CHECK_PACK_BOUND)  # noqa: E731
    assert row(light).verdict is Verdict.NOT_APPLICABLE and "label-only" in row(light).detail
    assert row(standard).verdict is Verdict.FAIL and "u@1" in row(standard).detail
    bound = Unit("u", "1", UnitStatus.ACCEPTANCE_FROZEN, (), "u-pack/1", False, acceptance_pack_digest="a" * 64)
    assert row(Snapshot(revision=1, tier=Tier.STANDARD, units=(bound,))).verdict is Verdict.PASS


def test_pack_files_match_check_verifies_the_working_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, root = _project(tmp_path)
    assert main(["set-tier", "--root", root, "--tier", "standard"]) == 0  # pack/3 (DEV-pb-02): both checks are tier-scoped
    assert main(["set-anchor-mode", "--root", root, "--mode", "ci"]) == 0  # standard also requires anchoring mode
    assert main(["freeze", "--root", root, "--name", "core", "--pack-version", "core-pack/1", "--project", str(project), "--pack-file", "tests/test_a.py"]) == 0
    assert main(["check", "--ci", "--root", root, "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "check.pack-files-match" in out and "check.pack-bound" in out
    (project / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")  # the goalposts move
    assert main(["check", "--ci", "--root", root, "--project", str(project)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "tests/test_a.py" in out
    # pack/3: the store is <project>/.vouch, so `--project` DEFAULTS to the parent — the changed
    # file is still caught without the flag; a store not named .vouch has no default and at
    # standard+ reports NOT_CHECKED "no project root" (never a clean N/A).
    assert main(["check", "--ci", "--root", root]) == 1
    assert "changed pack file" in capsys.readouterr().out
    other = str(tmp_path / "elsewhere-store")
    Store.init(other).close()
    assert main(["set-tier", "--root", other, "--tier", "standard"]) == 0
    assert main(["specify", "--root", other, "--name", "x", "--version", "1"]) == 0
    assert main(["freeze", "--root", other, "--name", "x", "--pack-version", "p", "--project", str(project), "--pack-file", "tests/test_b.py"]) == 0
    capsys.readouterr()
    assert main(["check", "--ci", "--root", other]) == 1
    assert "no project root" in capsys.readouterr().out


def test_bound_object_missing_from_store_is_a_fail(tmp_path: Path) -> None:
    from vouch_mk2.core.model import Tier, Unit
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    root = str(tmp_path / "s")
    Store.init(root).close()
    with Store.open(root) as store:
        store.commit(Snapshot(revision=1, tier=Tier.STANDARD, units=(Unit("u", "1", UnitStatus.ACCEPTANCE_FROZEN, (), "p", False, acceptance_pack_digest="f" * 64),)))
        rows = {r.check_id: r for r in run_artifact_checks(store, store.read_snapshot())}
    assert rows[mk2.CHECK_PACK_BOUND].verdict is Verdict.FAIL and "missing" in rows[mk2.CHECK_PACK_BOUND].detail


# ---- projections, transports, docs ----


def test_pack_digest_appears_in_map_and_status(tmp_path: Path) -> None:
    from vouch_mk2.core.model import Unit
    from vouch_mk2.projections import next_actions, status_facts, system_map

    s = Snapshot(revision=1, units=(Unit("u", "1", UnitStatus.ACCEPTANCE_FROZEN, (), "p", False, acceptance_pack_digest="a" * 64),))
    assert system_map(s).units[0].acceptance_pack_digest == "a" * 64
    assert status_facts(s, next_actions(s)).unbound_pack_units == ()


def test_ir_cell_claims_only_what_is_now_true() -> None:
    from vouch_mk2.ir.mk2 import MK2

    cell = next(r for r in MK2.tier_requirements if r.control_id == "acceptance-frozen-before-implementation").standard
    assert "check.pack-bound" in cell.mechanical_checks
    assert "human" in cell.residual


def test_quickstart_and_walkthrough_freeze_with_pack_files() -> None:
    text = (ROOT / "docs" / "quickstart.md").read_text()
    assert "--pack-file" in text
    assert "does not read or hash the pack" not in text
    assert "--pack-file" in (ROOT / "scripts" / "walkthrough.py").read_text()
