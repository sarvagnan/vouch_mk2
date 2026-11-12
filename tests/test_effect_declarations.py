"""Acceptance pack for self-hosted unit effect-declarations@1 (D-31; bead vouch_mk2-bd4.8).

Every frozen pack can carry an effects declaration, embedded in the pack inventory (format 3);
a store that requires effects refuses to bind a pack without one. Every test here fails against
the pre-change tree except those marked `# guard:`. The implementer may not edit this file; a
finding that it is wrong is a deviation adjudicated by the owner.
"""

from __future__ import annotations

import dataclasses
import io
import json
import sys
import re
import shutil
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

import pytest

import vouch_mk2.bundle as bundle
from vouch_mk2.bundle import InventoryError, bind_pack, build_pack_inventory, pack_test_set_digest
from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    BindPack,
    FreezeAcceptance,
    RebindPack,
    Refused,
    Snapshot,
    decide,
)
import vouch_mk2.core.model as model
from vouch_mk2.core.model import PackEntry, PackInventory, Unit, UnitStatus
from vouch_mk2.formal import UNIVERSES, passed, run_formal
from vouch_mk2.formal.explore import state_fingerprint
from vouch_mk2.init import init_project
from vouch_mk2.ir import mk2
from vouch_mk2.ir.mk2 import MK2
from vouch_mk2.ir.schema import Coverage, ObligationScope
from vouch_mk2.lock import build_lock
from vouch_mk2.store import (
    CURRENT_STATE_FORMAT,
    ArtifactKind,
    Store,
    StoreError,
    decode_pack_inventory,
    digest_of,
    encode_pack_inventory,
    encode_snapshot,
)
from vouch_mk2.store.event_log import commit_events

ROOT = Path(__file__).resolve().parents[1]

OBSERVE = {
    "name": "health-connect-read",
    "kind": "observation",
    "source": "Health Connect on the device",
    "capture": "one change page as a stored projection",
}
ACT = {
    "name": "portal-delivery",
    "kind": "action",
    "system": "Portal ingest API",
    "outcome": "confirmed",
    "detail": "one HTTP request per attempt",
}


def _effects(*effects: dict[str, str]) -> bytes:
    return json.dumps({"effects_format": 1, "effects": list(effects)}).encode("utf-8")


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


def _project(tmp_path: Path, name: str = "proj") -> tuple[Path, str]:
    project = tmp_path / name
    (project / "tests").mkdir(parents=True)
    (project / "packs").mkdir()
    (project / "tests" / "test_a.py").write_text("def test_a():\n    assert False\n")
    (project / "packs" / "a.md").write_text("# spec\n")
    (project / "packs" / "none.effects.json").write_bytes(_effects())
    (project / "packs" / "act.effects.json").write_bytes(_effects(ACT))
    root = str(project / ".vouch")
    Store.init(root).close()
    _must("set-ci-policy", "--root", root, "--policy", "ledger")
    return project, root


def _freeze(root: str, project: Path, name: str, *extra: str) -> tuple[int, str]:
    _must("specify", "--root", root, "--name", name, "--version", "1")
    return _run(
        "freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/1",
        "--project", str(project), *extra,
    )


def _inventory(root: str, name: str) -> PackInventory:
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit(name)
        assert unit is not None and unit.acceptance_pack_digest
        return decode_pack_inventory(
            store.get_object(ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest)
        )


def _tree(root: str) -> dict[str, bytes]:
    """Every file in the store except its runtime lock: refusals must write nothing."""
    base = Path(root)
    return {
        path.relative_to(base).as_posix(): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.name != ".lock"
    }


def _snapshot(root: str) -> Snapshot:
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot()


# ---- R1, R2: the effects file ----


def test_an_effects_file_parses_to_typed_effects_sorted_by_name() -> None:
    parsed = bundle.parse_effects_file(_effects(ACT, OBSERVE))
    assert parsed == (
        model.ObservationEffect(
            name="health-connect-read",
            source="Health Connect on the device",
            capture="one change page as a stored projection",
        ),
        model.ActionEffect(
            name="portal-delivery",
            system="Portal ingest API",
            outcome=model.ActionOutcome.CONFIRMED,
            detail="one HTTP request per attempt",
        ),
    )
    assert bundle.parse_effects_file(_effects()) == ()
    assert {outcome.value for outcome in model.ActionOutcome} == {"confirmed", "compensated", "irreversible"}


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe",
        b"[]",
        json.dumps({"effects": []}).encode(),
        json.dumps({"effects_format": 2, "effects": []}).encode(),
        json.dumps({"effects_format": True, "effects": []}).encode(),
        json.dumps({"effects_format": 1, "effects": [], "extra": 1}).encode(),
        b'{"effects_format": 1, "effects": [], "effects": []}',
        _effects({**OBSERVE, "system": "x"}),
        _effects({k: v for k, v in OBSERVE.items() if k != "capture"}),
        _effects({**OBSERVE, "source": "  "}),
        _effects({**ACT, "outcome": "probably"}),
        _effects({k: v for k, v in ACT.items() if k != "outcome"}),
        _effects({**ACT, "kind": "transform"}),
        _effects({**ACT, "name": "Portal_Delivery"}),
        _effects(ACT, {**OBSERVE, "name": "portal-delivery"}),
        b'{"effects_format": 1, "effects": [{"name": "a", "kind": "action", "system": "\\ud800", "outcome": "confirmed"}]}',
        b'{"effects_format": 1, "effects": [{"name": "a", "kind": "observation", "source": "s", "capture": "c", "detail": "\\udfff"}]}',
    ],
    ids=[
        "not-utf8", "not-object", "no-format", "format-2", "format-bool", "unknown-key",
        "duplicate-key", "observation-with-system", "observation-without-capture",
        "blank-source", "bad-outcome", "action-without-outcome", "unknown-kind",
        "bad-name", "duplicate-name", "lone-surrogate", "lone-surrogate-detail",
    ],
)
def test_an_invalid_effects_file_is_refused(data: bytes) -> None:
    with pytest.raises(InventoryError):
        bundle.parse_effects_file(data)


# ---- R3: pack format 3 ----


def test_a_format_3_inventory_round_trips_and_legacy_formats_do_not_change() -> None:
    entries = (
        PackEntry("packs/a.effects.json", "e" * 64, 9, "effects"),
        PackEntry("packs/a.md", "a" * 64, 7, "spec"),
        PackEntry("tests/test_a.py", "b" * 64, 5, "test"),
    )
    effects = (model.ActionEffect(name="portal-delivery", system="Portal", outcome=model.ActionOutcome.IRREVERSIBLE),)
    inventory = PackInventory(entries=entries, pack_format=3, effects=effects)
    data = encode_pack_inventory(inventory)
    assert decode_pack_inventory(data) == inventory
    document = json.loads(data)
    assert document["pack_format"] == 3
    assert document["effects"] == [
        {"kind": "action", "name": "portal-delivery", "outcome": "irreversible", "system": "Portal"}
    ]
    legacy = PackInventory(entries=entries[1:], pack_format=2)
    assert legacy.effects is None
    legacy_data = encode_pack_inventory(legacy)
    assert b"effects" not in legacy_data and decode_pack_inventory(legacy_data) == legacy
    for bad in (
        PackInventory(entries=entries[1:], pack_format=3, effects=()),  # no effects entry
        PackInventory(entries=(entries[0], entries[1], PackEntry("packs/b.effects.json", "f" * 64, 9, "effects"), entries[2]),
                      pack_format=3, effects=()),  # two effects entries, sorted
        PackInventory(entries=entries, pack_format=3, effects=None),  # format 3 without effects
        PackInventory(entries=entries[1:], pack_format=2, effects=()),  # effects below format 3
        PackInventory(entries=(entries[0],), pack_format=3, effects=()),  # effects entry only
    ):
        with pytest.raises(StoreError):
            encode_pack_inventory(bad)
    def effects_of(d: dict[str, object]) -> list[dict[str, str]]:
        return cast(list[dict[str, str]], d["effects"])

    def entries_of(d: dict[str, object]) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], d["entries"])

    mutations: tuple[Callable[[dict[str, object]], object], ...] = (
        lambda d: d.pop("effects"),  # format 3 without effects
        lambda d: d.update(pack_format=2),  # effects key below format 3
        lambda d: d.update(entries=[e for e in entries_of(d) if e["role"] != "effects"]),
        lambda d: effects_of(d).append({"kind": "action", "name": "portal-delivery", "outcome": "irreversible", "system": "x"}),
        lambda d: effects_of(d)[0].update(kind="transform"),
    )
    for mutate in mutations:
        document = cast(dict[str, object], json.loads(data))
        mutate(document)
        with pytest.raises(Exception):
            decode_pack_inventory(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    legacy_with_role = json.loads(legacy_data)
    legacy_with_role["entries"][0]["role"] = "effects"
    with pytest.raises(Exception):
        decode_pack_inventory(json.dumps(legacy_with_role, sort_keys=True, separators=(",", ":")).encode() + b"\n")


# ---- R4, R5: building, and the test-set digest ----


def test_building_hashes_and_parses_the_same_effects_bytes(tmp_path: Path) -> None:
    import hashlib

    project, _root = _project(tmp_path)
    path = project / "packs" / "act.effects.json"
    one = build_pack_inventory(str(project), ("packs/a.md",), ("tests/test_a.py",), "packs/act.effects.json")
    raw = path.read_bytes()
    assert one.pack_format == 3 and one.effects == bundle.parse_effects_file(raw)
    entry = next(e for e in one.entries if e.role == "effects")
    assert (entry.path, entry.sha256, entry.size) == ("packs/act.effects.json", hashlib.sha256(raw).hexdigest(), len(raw))
    path.write_bytes(json.dumps({"effects_format": 1, "effects": [ACT]}, indent=4).encode())  # same path, same effects
    two = build_pack_inventory(str(project), ("packs/a.md",), ("tests/test_a.py",), "packs/act.effects.json")
    respaced = next(e for e in two.entries if e.role == "effects")
    assert one.effects == two.effects
    assert respaced.sha256 == hashlib.sha256(path.read_bytes()).hexdigest() != entry.sha256  # raw bytes stay bound
    assert encode_pack_inventory(one) != encode_pack_inventory(two)
    (project / "tests" / "test_valid.json").write_bytes(_effects())
    (project / "packs" / "valid.md").write_bytes(_effects())
    for spec, tests, effects in (
        (("packs/a.md",), ("tests/test_a.py",), "packs/missing.effects.json"),
        (("packs/a.md",), ("tests/test_valid.json",), "tests/test_valid.json"),  # valid JSON, collides with a test path
        (("packs/valid.md",), ("tests/test_a.py",), "packs/valid.md"),  # valid JSON, collides with the spec
    ):
        with pytest.raises(InventoryError):
            build_pack_inventory(str(project), spec, tests, effects)


def test_effects_never_change_the_test_set_digest(tmp_path: Path) -> None:
    project, _root = _project(tmp_path)
    plain = build_pack_inventory(str(project), ("packs/a.md",), ("tests/test_a.py",))
    none = build_pack_inventory(str(project), ("packs/a.md",), ("tests/test_a.py",), "packs/none.effects.json")
    act = build_pack_inventory(str(project), ("packs/a.md",), ("tests/test_a.py",), "packs/act.effects.json")
    assert plain.pack_format == 2 and plain.effects is None
    assert pack_test_set_digest(plain) == pack_test_set_digest(none) == pack_test_set_digest(act)
    assert encode_pack_inventory(none) != encode_pack_inventory(act)
    spec_only = build_pack_inventory(str(project), ("packs/a.md",))
    spec_with_effects = build_pack_inventory(str(project), ("packs/a.md",), (), "packs/act.effects.json")
    assert spec_only.pack_format == 1 and spec_with_effects.pack_format == 3
    assert pack_test_set_digest(spec_only) == pack_test_set_digest(spec_with_effects)


# ---- R6: the CLI binds an effects file ----


def test_freeze_and_bind_pack_accept_an_effects_file(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    code, output = _freeze(root, project, "a", "--pack-file", "packs/a.md", "--pack-test-file",
                           "tests/test_a.py", "--pack-effects-file", "packs/act.effects.json")
    assert code == 0, output
    assert _inventory(root, "a").effects == bundle.parse_effects_file(_effects(ACT))
    code, output = _freeze(root, project, "b")  # label-only
    assert code == 0, output
    _must("bind-pack", "--root", root, "--name", "b", "--project", str(project),
          "--pack-test-file", "tests/test_a.py", "--pack-effects-file", "packs/none.effects.json")
    assert _inventory(root, "b").effects == ()
    (project / "packs" / "bad.effects.json").write_bytes(b'{"effects_format": 1}')
    _must("specify", "--root", root, "--name", "c", "--version", "1")
    before = _tree(root)
    for extra in (
        ("--pack-test-file", "tests/test_a.py", "--pack-effects-file", "packs/bad.effects.json"),
        ("--pack-effects-file", "packs/act.effects.json"),  # an effects file needs pack files
    ):
        code, output = _run("freeze", "--root", root, "--name", "c", "--pack-version", "c-pack/1",
                            "--project", str(project), *extra)
        assert code == 2, (extra, output)
        assert _tree(root) == before
    code, output = _run("bind-pack", "--root", root, "--name", "c", "--project", str(project),
                        "--pack-test-file", "tests/test_a.py", "--pack-effects-file", "packs/bad.effects.json")
    assert code == 2 and _tree(root) == before, output
    unit = _snapshot(root).unit("c")
    assert unit is not None and unit.status is UnitStatus.SPECIFIED
    assert "--pack-effects-file" in _must("freeze", "--help")


# ---- R7, R8, R9: the store requirement ----


def test_require_effects_is_recorded_and_one_way(tmp_path: Path) -> None:
    _project_dir, root = _project(tmp_path)
    assert _snapshot(root).effects_required is False
    assert b"effects_required" not in encode_snapshot(_snapshot(root))
    _must("require-effects", "--root", root)
    assert _snapshot(root).effects_required is True
    assert b'"effects_required":true' in encode_snapshot(_snapshot(root))
    _must("require-effects", "--root", root)  # idempotent, still required
    assert _snapshot(root).effects_required is True
    assert CURRENT_STATE_FORMAT == 5
    assert mk2.CMD_REQUIRE_EFFECTS == "require-effects"
    assert not [name for name in dir(mk2) if name.startswith("CMD_") and "EFFECT" in name and name != "CMD_REQUIRE_EFFECTS"]


def test_a_store_that_requires_effects_refuses_an_undeclared_pack(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    code, output = _freeze(root, project, "legacy", "--pack-test-file", "tests/test_a.py")
    assert code == 0, output  # before the requirement: unchanged behaviour
    _must("require-effects", "--root", root)
    _must("specify", "--root", root, "--name", "u", "--version", "1")
    before = _tree(root)
    code, output = _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
                        "--project", str(project), "--pack-test-file", "tests/test_a.py")
    assert code == 2 and "pack.effects-required" in output, output
    assert _tree(root) == before
    _must("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")  # label-only
    code, output = _run("bind-pack", "--root", root, "--name", "u", "--project", str(project),
                        "--pack-test-file", "tests/test_a.py")
    assert code == 2 and "pack.effects-required" in output, output
    _must("bind-pack", "--root", root, "--name", "u", "--project", str(project),
          "--pack-test-file", "tests/test_a.py", "--pack-effects-file", "packs/act.effects.json")
    assert _inventory(root, "u").effects == bundle.parse_effects_file(_effects(ACT))


def test_the_decision_and_the_library_enforce_the_requirement(tmp_path: Path) -> None:
    specified = Unit("u", "1", UnitStatus.SPECIFIED)
    frozen = Unit("u", "1", UnitStatus.ACCEPTANCE_FROZEN, (), "u-pack/1", False)
    required = Snapshot(revision=1, effects_required=True, units=(specified,))
    refused = decide(required, FreezeAcceptance(name="u", pack_version="p", pack_digest="a" * 64))
    assert isinstance(refused, Refused)
    assert [v.code for v in refused.violations] == ["pack.effects-required"]
    assert isinstance(decide(required, FreezeAcceptance(name="u", pack_version="p", pack_digest="a" * 64, effects_declared=True)), Accepted)
    assert isinstance(decide(required, FreezeAcceptance(name="u", pack_version="p")), Accepted)  # label-only
    bound = Snapshot(revision=1, effects_required=True, units=(frozen,))
    refused = decide(bound, BindPack(name="u", pack_digest="a" * 64))
    assert isinstance(refused, Refused) and [v.code for v in refused.violations] == ["pack.effects-required"]
    assert isinstance(decide(bound, BindPack(name="u", pack_digest="a" * 64, effects_declared=True)), Accepted)
    assert isinstance(decide(Snapshot(revision=1, units=(frozen,)), BindPack(name="u", pack_digest="a" * 64)), Accepted)

    project, root = _project(tmp_path)
    _must("require-effects", "--root", root)
    _must("specify", "--root", root, "--name", "lib", "--version", "1")
    _must("freeze", "--root", root, "--name", "lib", "--pack-version", "lib-pack/1")
    undeclared = build_pack_inventory(str(project), (), ("tests/test_a.py",))
    with Store.open(root) as store, store.write_locked():
        with pytest.raises(StoreError, match="pack.effects-required"):
            bind_pack(store, "lib", undeclared)
        declared = build_pack_inventory(str(project), (), ("tests/test_a.py",), "packs/none.effects.json")
        bind_pack(store, "lib", declared)
    assert _inventory(root, "lib").effects == ()


# ---- R10: rebind keeps the declaration exactly ----


def _rebind(old: PackInventory, new: PackInventory) -> Accepted | Refused:
    old_digest = "1" * 64
    unit = Unit("u", "1", UnitStatus.PUBLISHED, (), "u-pack/1", True,
                acceptance_pack_digest=old_digest, acceptance_test_set_digest=old_digest)
    return decide(
        Snapshot(revision=1, units=(unit,)),
        RebindPack(name="u", pack_digest="2" * 64, test_set_digest="3" * 64,
                   reason="adapt to a shared interface", old_inventory=old, new_inventory=new),
    )


def test_rebind_keeps_the_declaration_exactly() -> None:
    test_entry = PackEntry("tests/test_a.py", "a" * 64, 5, "test")
    changed_test = PackEntry("tests/test_a.py", "b" * 64, 6, "test")
    effects_entry = PackEntry("packs/u.effects.json", "e" * 64, 9, "effects")
    one = (model.ActionEffect(name="send", system="Portal", outcome=model.ActionOutcome.CONFIRMED),)
    two = (model.ActionEffect(name="send", system="Portal", outcome=model.ActionOutcome.COMPENSATED),)

    def declared(test: PackEntry, effects: tuple[model.ActionEffect, ...]) -> PackInventory:
        return PackInventory(entries=(effects_entry, test), pack_format=3, effects=effects)

    def legacy(test: PackEntry) -> PackInventory:
        return PackInventory(entries=(test,), pack_format=2)

    assert isinstance(_rebind(declared(test_entry, one), declared(changed_test, one)), Accepted)
    assert isinstance(_rebind(legacy(test_entry), legacy(changed_test)), Accepted)
    for old, new in (
        (declared(test_entry, one), declared(changed_test, two)),
        (legacy(test_entry), declared(changed_test, one)),
        (declared(test_entry, one), PackInventory(entries=(changed_test,), pack_format=2)),
    ):
        decision = _rebind(old, new)
        assert isinstance(decision, Refused), (old, new)
        assert "rebind.effects-changed" in [v.code for v in decision.violations]


# ---- R11: new projects require effects ----


def test_init_requires_effects_for_new_projects(tmp_path: Path) -> None:
    project = tmp_path / "fresh"
    project.mkdir()
    init_project(str(project), build_lock(str(ROOT / "src")))
    assert _snapshot(str(project / ".vouch")).effects_required is True


# ---- R12: semantic merge ----


def test_a_merge_replays_declared_packs_onto_a_store_that_requires_effects(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _must("specify", "--root", root, "--name", "shared", "--version", "1")
    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    their_root = str(theirs / ".vouch")
    code, output = _freeze(their_root, theirs, "declared", "--pack-test-file", "tests/test_a.py",
                           "--pack-effects-file", "packs/act.effects.json")
    assert code == 0, output
    _must("require-effects", "--root", root)
    _must("merge", "--root", root, "--other", their_root)
    snapshot = _snapshot(root)
    unit = snapshot.unit("declared")
    assert snapshot.effects_required is True
    assert unit is not None and unit.status is UnitStatus.ACCEPTANCE_FROZEN
    assert _inventory(root, "declared").effects == bundle.parse_effects_file(_effects(ACT))


def test_a_merge_refuses_an_undeclared_pack_onto_a_store_that_requires_effects(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _must("specify", "--root", root, "--name", "shared", "--version", "1")
    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    their_root = str(theirs / ".vouch")
    code, output = _freeze(their_root, theirs, "undeclared", "--pack-test-file", "tests/test_a.py")
    assert code == 0, output
    _must("require-effects", "--root", root)
    before = _tree(root)
    code, output = _run("merge", "--root", root, "--other", their_root)
    assert code != 0 and "pack.effects-required" in output, output
    assert _tree(root) == before


# ---- R13, R14, R15: what Vouch reports ----


def _mixed_store(tmp_path: Path) -> tuple[Path, str]:
    project, root = _project(tmp_path)
    for name, extra in (
        ("a", ("--pack-test-file", "tests/test_a.py", "--pack-effects-file", "packs/none.effects.json")),
        ("b", ("--pack-test-file", "tests/test_a.py", "--pack-effects-file", "packs/act.effects.json")),
        ("c", ("--pack-test-file", "tests/test_a.py")),
        ("d", ()),
    ):
        code, output = _freeze(root, project, name, *extra)
        assert code == 0, output
    return project, root


def test_check_reports_declared_and_undeclared_units_without_failing(tmp_path: Path) -> None:
    project, root = _mixed_store(tmp_path)
    code, output = _run("check", "--ci", "--root", root, "--project", str(project))
    assert code == 0, output
    row = next(line for line in output.splitlines() if re.match(r"PASS\s+check\.effects-declared\s", line))
    assert "2 declared (1 with no effects), 2 not declared" in row
    assert "c@1" in row and "d@1" in row
    org = next(line for line in output.splitlines() if re.match(r"NOT-CHECKED\s+org\.effects-truth\s", line))
    assert "records what was declared" in org
    scopes = {o.obligation_id: o.scope for o in MK2.obligations}
    assert scopes[mk2.CHECK_EFFECTS_DECLARED] is ObligationScope.ARTIFACT_CHECK
    assert scopes[mk2.ORG_EFFECTS_TRUTH] is ObligationScope.ORGANIZATIONAL


def test_an_unreadable_declared_inventory_fails_the_effects_row(tmp_path: Path) -> None:
    project, root = _mixed_store(tmp_path)
    digest = _snapshot(root).unit("b")
    assert digest is not None
    objects = list((Path(root) / "objects").rglob(digest.acceptance_pack_digest))
    assert len(objects) == 1
    objects[0].unlink()
    _code, output = _run("check", "--root", root, "--project", str(project))
    assert re.search(r"(?m)^FAIL\s+check\.effects-declared\s.*b@1", output), output


def test_show_effects_lists_each_active_unit(tmp_path: Path) -> None:
    _project_dir, root = _mixed_store(tmp_path)
    text = _must("show-effects", "--root", root)
    assert "a@1: declares no effects" in text
    assert "b@1: action portal-delivery (system: Portal ingest API; declared outcome: confirmed)" in text
    assert "c@1: effects not declared" in text
    assert "d@1: no pack bound, effects not declared" in text
    document = json.loads(_must("show-effects", "--root", root, "--json"))
    assert document["effects_required"] is False
    assert [(u["unit"], u["declaration"]) for u in document["units"]] == [
        ("a@1", "declared"), ("b@1", "declared"), ("c@1", "not-declared"), ("d@1", "no-pack"),
    ]
    assert document["units"][1]["effects"] == [
        {"detail": "one HTTP request per attempt", "kind": "action", "name": "portal-delivery",
         "outcome": "confirmed", "system": "Portal ingest API"}
    ]
    assert document["units"][0]["effects"] == [] and document["units"][2]["effects"] is None


def test_status_and_agent_context_state_the_requirement(tmp_path: Path) -> None:
    _project_dir, root = _project(tmp_path)
    assert json.loads(_must("status", "--root", root, "--json"))["effects_required"] is False
    _must("generate", "--root", root)
    assert "Effect declarations are required" not in (Path(root) / "vouch-context.md").read_text()
    _must("require-effects", "--root", root)
    assert json.loads(_must("status", "--root", root, "--json"))["effects_required"] is True
    _must("generate", "--root", root)
    assert "Effect declarations are required" in (Path(root) / "vouch-context.md").read_text()


# ---- R16: the tier row claims only what is checked ----


def test_the_tier_row_claims_declared_effects_only() -> None:
    requirement = next(r for r in MK2.tier_requirements if r.control_id == "typed-contracts")
    assert requirement.description == "Declared effects (transform, observation, action)"
    for cell in (requirement.lightweight, requirement.standard, requirement.high):
        assert cell.mechanical_checks == (mk2.CHECK_EFFECTS_DECLARED,)
        assert cell.mechanical_evidence == ()
        assert cell.coverage is Coverage.PARTIAL
        assert "human reviewed" in cell.residual and "not yet bindable" in cell.residual


# ---- R17: the formal model ----


def test_the_formal_model_covers_the_requirement_without_moving_other_universes() -> None:
    assert [u.name for u in UNIVERSES] == ["core-v1", "history-v1", "tier-v1", "effects-v1"]
    plain = Snapshot(revision=1)
    assert state_fingerprint(plain) != state_fingerprint(Snapshot(revision=1, effects_required=True))
    run = run_formal()
    assert passed(run)
    core = run.universe("core-v1")
    assert (core.state_count, core.transition_count) == (264_462, 1_381_350)
    history = run.universe("history-v1")
    assert (history.state_count, history.transition_count) == (62_832, 219_976)
    tier = run.universe("tier-v1")
    assert (tier.state_count, tier.transition_count) == (246, 1_640)
    effects_universe = next(u for u in UNIVERSES if u.name == "effects-v1")
    assert "M6.no-nonterminal-deadlock" not in effects_universe.invariant_ids
    assert "M10.one-active-record-per-name" in effects_universe.invariant_ids
    effects = run.universe("effects-v1")
    assert all(verdict.held for verdict in effects.invariants)
    by_fingerprint = dict(effects.relation.representatives)

    def frozen(s: Snapshot, bound: bool) -> bool:
        return any(u.status is UnitStatus.ACCEPTANCE_FROZEN and bool(u.acceptance_pack_digest) is bound for u in s.units)

    required_unbound = {f for f, s in by_fingerprint.items() if s.effects_required and frozen(s, False)}
    required_bound = {f for f, s in by_fingerprint.items() if s.effects_required and frozen(s, True)}
    assert required_unbound and required_bound
    assert any(
        source in required_unbound and label == mk2.CMD_BIND_PACK and target in required_bound
        for source, label, target in effects.relation.transitions
    )
    assert not any(
        isinstance(decide(by_fingerprint[f], BindPack(name=u.name, pack_digest="e" * 64)), Accepted)
        for f in required_unbound
        for u in by_fingerprint[f].units if u.status is UnitStatus.ACCEPTANCE_FROZEN and not u.acceptance_pack_digest
    )


# ---- R18, R19: docs and the decision ----


def test_the_docs_show_effect_declarations() -> None:
    quickstart = (ROOT / "docs" / "quickstart.md").read_text()
    assert "--pack-effects-file" in quickstart and ".effects.json" in quickstart
    boundary = (ROOT / "docs" / "claim-boundary.md").read_text()
    assert "records what was declared, not that the code has only those effects" in boundary
    assert "require-effects" in boundary
    assert "reaches the effects requirement but does not verify its gate" in boundary  # pack/3, DEV-ed-07
    commands = (ROOT / "docs" / "commands.md").read_text()
    assert "## require-effects" in commands and "## show-effects" in commands
    assert "--pack-effects-file" in (ROOT / "scripts" / "walkthrough.py").read_text()


def test_d31_records_the_decision() -> None:  # guard:
    log = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    assert re.search(r"(?m)^## D-31 — Every frozen pack declares its unit's effects", log)


# ---- pack/3: Astra's review (DEV-ed-04..07) ----

FAIL_CMD = [sys.executable, "-c", "raise SystemExit(1)"]
OK_CMD = [sys.executable, "-c", "raise SystemExit(0)"]


def _published_unit(project: Path, root: str, name: str) -> None:
    code, output = _freeze(root, project, name, "--pack-test-file", "tests/test_a.py")
    assert code == 0, output
    for verb in ("mark-ready", "start"):
        _must(verb, "--root", root, "--name", name)
    _must("record-evidence", "--root", root, "--unit", f"{name}@1", "--kind", "red", "--", *FAIL_CMD)
    _must("record-evidence", "--root", root, "--unit", f"{name}@1", "--kind", "green", "--", *OK_CMD)
    for verb in ("mark-done", "publish"):
        _must(verb, "--root", root, "--name", name)


def test_a_rebind_must_name_the_pack_it_replaces(tmp_path: Path) -> None:
    """R10b (DEV-ed-04): a replayed rebind recorded against another pack is refused."""
    published = Unit("u", "1", UnitStatus.PUBLISHED, (), "u-pack/1", True,
                     acceptance_pack_digest="1" * 64, acceptance_test_set_digest="1" * 64)
    legacy_old = PackInventory(entries=(PackEntry("tests/test_a.py", "a" * 64, 5, "test"),), pack_format=2)
    legacy_new = PackInventory(entries=(PackEntry("tests/test_a.py", "b" * 64, 6, "test"),), pack_format=2)

    def rebind(old_pack_digest: str) -> Accepted | Refused:
        return decide(
            Snapshot(revision=1, units=(published,)),
            RebindPack(name="u", pack_digest="2" * 64, test_set_digest="2" * 64, reason="adapt",
                       old_inventory=legacy_old, new_inventory=legacy_new, old_pack_digest=old_pack_digest),
        )

    stale = rebind("9" * 64)
    assert isinstance(stale, Refused) and [v.code for v in stale.violations] == ["rebind.stale-pack"]
    assert isinstance(rebind("1" * 64), Accepted)
    assert isinstance(rebind(""), Accepted)  # not asserted by the caller

    project, root = _project(tmp_path)
    _published_unit(project, root, "u")
    theirs = tmp_path / "theirs"
    shutil.copytree(project, theirs)
    their_root = str(theirs / ".vouch")
    (theirs / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    _must("rebind-pack", "--root", their_root, "--name", "u", "--project", str(theirs),
          "--pack-test-file", "tests/test_a.py", "--reason", "theirs adapts")
    (project / "tests" / "test_a.py").write_text("def test_a():\n    assert 2\n")
    _must("rebind-pack", "--root", root, "--name", "u", "--project", str(project),
          "--pack-test-file", "tests/test_a.py", "--reason", "ours adapts")
    before = _tree(root)
    code, output = _run("merge", "--root", root, "--other", their_root)
    assert code != 0 and "rebind.stale-pack" in output, output
    assert _tree(root) == before


def test_check_fails_an_undeclared_pack_bound_after_effects_were_required(tmp_path: Path) -> None:
    """R13c (DEV-ed-05): the event log, not the adapter's flag, decides; legacy binds stay grandfathered."""
    project, root = _project(tmp_path)
    code, output = _freeze(root, project, "legacy", "--pack-test-file", "tests/test_a.py")
    assert code == 0, output
    _must("require-effects", "--root", root)
    code, output = _run("check", "--root", root, "--project", str(project))
    assert re.search(r"(?m)^PASS\s+check\.effects-declared\s", output), output
    _must("specify", "--root", root, "--name", "forged", "--version", "1")
    undeclared = build_pack_inventory(str(project), (), ("tests/test_a.py",))
    data = encode_pack_inventory(undeclared)
    with Store.open(root) as store, store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, FreezeAcceptance(
            name="forged", pack_version="forged-pack/1",
            pack_digest=digest_of(ArtifactKind.ACCEPTANCE_PACK, data),
            test_set_digest=pack_test_set_digest(undeclared),
            effects_declared=True,  # an adapter that lies
        ))
        assert isinstance(decision, Accepted)
        commit_events(store, snapshot, decision.events, actor="forger", run_id="",
                      objects=((ArtifactKind.ACCEPTANCE_PACK, data),))
    code, output = _run("check", "--root", root, "--project", str(project))
    row = next(line for line in output.splitlines() if re.match(r"FAIL\s+check\.effects-declared\s", line))
    assert "forged@1" in row and "bound after effects were required" in row
    assert "legacy@1: bound after" not in row


def test_check_fails_a_declaration_detached_from_its_effects_file(tmp_path: Path) -> None:
    """R13d (DEV-ed-06): where pack files are checked, the embedded declaration must equal the file's parse."""
    project, root = _project(tmp_path)
    _must("set-tier", "--root", root, "--tier", "standard")
    _must("set-anchor-mode", "--root", root, "--mode", "ci")
    _must("specify", "--root", root, "--name", "u", "--version", "1")
    _must("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")
    honest = build_pack_inventory(str(project), (), ("tests/test_a.py",), "packs/act.effects.json")
    with Store.open(root) as store, store.write_locked():
        bind_pack(store, "u", dataclasses.replace(honest, effects=()))  # declares nothing; the file declares an action
    _code, output = _run("check", "--root", root, "--project", str(project))
    assert re.search(r"(?m)^FAIL\s+check\.effects-declared\s.*u@1.*does not match", output), output


def test_a_legacy_rebind_after_the_requirement_stays_grandfathered(tmp_path: Path) -> None:
    """R13e (pack/4, DEV-ed-08): a declaration-preserving rebind inherits its original binding's status."""
    project, root = _project(tmp_path)
    _published_unit(project, root, "u")
    _must("require-effects", "--root", root)
    (project / "tests" / "test_a.py").write_text("def test_a():\n    assert 3\n")
    _must("rebind-pack", "--root", root, "--name", "u", "--project", str(project),
          "--pack-test-file", "tests/test_a.py", "--reason", "adapt to a changed shared interface")
    code, output = _run("check", "--root", root, "--project", str(project))
    assert re.search(r"(?m)^PASS\s+check\.effects-declared\s.*not declared: u@1", output), output
    _must("specify", "--root", root, "--name", "late", "--version", "1")
    _must("freeze", "--root", root, "--name", "late", "--pack-version", "late-pack/1")
    code, output = _run("bind-pack", "--root", root, "--name", "late", "--project", str(project),
                        "--pack-test-file", "tests/test_a.py")
    assert code == 2 and "pack.effects-required" in output, output  # a fresh undeclared bind is still refused
