"""Frozen acceptance pack for rebind-fresh-green@1 (D-51, vouch_mk2-inv.5).

A rebind after publication sets D-36's fresh-GREEN flag, and only a passing GREEN for the current
test set clears it, so a REFACTOR alone no longer satisfies the current-pack evidence predicate
after a rebind. Formal M12 no longer exempts the rebind. Every test here fails before the change,
except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import cast

from vouch_mk2.checks.tier import check_pack_bound
from vouch_mk2.cli import main
from vouch_mk2.core import Snapshot, Unit, UnitRef, UnitStatus
from vouch_mk2.formal import UNIVERSES, Relation, explore, state_fingerprint
from vouch_mk2.formal.invariants import INVARIANTS, Invariant
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import Store
from vouch_mk2.store.codec import encode_snapshot

ROOT = Path(__file__).resolve().parents[1]
UNIT = UnitRef(name="u", version="1")
FAIL_ARGV = ("python3", "-c", "raise SystemExit(1)")
PASS_ARGV = ("python3", "-c", "raise SystemExit(0)")

Json = dict[str, object]


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _must(*argv: str) -> str:
    code, out, err = _run(*argv)
    assert code == 0, (argv, out, err)
    return out


def _pack_args(project: Path) -> tuple[str, ...]:
    return (
        "--project", str(project),
        "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py",
        "--pack-effects-file", "packs/u.effects.json",
    )


def _published(
    tmp_path: Path, *, publish: bool = True, tier: str = "standard",
) -> tuple[Path, str]:
    """A unit published after RED and GREEN on a bound pack (or only marked done)."""
    project = tmp_path / "proj"
    project.mkdir()
    _must("init", "--project", str(project))
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    (project / "packs" / "u.spec.md").write_text("# u\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}\n')
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    root = str(project / ".vouch")
    if tier != "lightweight":
        _must("set-tier", "--root", root, "--tier", tier)
    _must("specify", "--root", root, "--name", "u", "--version", "1")
    _must(
        "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
        *_pack_args(project),
    )
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")
    _evidence(root, "red", FAIL_ARGV)
    _evidence(root, "green", PASS_ARGV)
    _must("mark-done", "--root", root, "--name", "u")
    if publish:
        _must("publish", "--root", root, "--name", "u")
    return project, root


def _evidence(root: str, kind: str, argv: tuple[str, ...]) -> None:
    _must("record-evidence", "--root", root, "--unit", "u@1", "--kind", kind, "--", *argv)


def _rebind(
    project: Path, root: str, *, change_tests: bool = False, spec: str = "# u, adapted\n",
) -> None:
    if change_tests:
        (project / "tests" / "t.py").write_text("def test_x():\n    assert 1\n")
    else:
        (project / "packs" / "u.spec.md").write_text(spec)
    _must(
        "rebind-pack", "--root", root, "--name", "u",
        "--reason", "adapt to a changed shared interface", *_pack_args(project),
    )


def _snapshot(root: str) -> Snapshot:
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot()


def _unit(root: str) -> Unit:
    unit = _snapshot(root).unit_ref(UNIT)
    assert unit is not None
    return unit


def _flag(root: str) -> bool:
    return _unit(root).refreeze_green_required


def _current_pack_green(root: str) -> bool:
    document: object = json.loads(_must("show-evidence", "--root", root, "--json"))
    assert isinstance(document, dict)
    units = cast(Json, document)["units"]
    assert isinstance(units, list)
    (entry,) = [
        cast(Json, item) for item in cast(list[object], units)
        if isinstance(item, dict) and cast(Json, item)["unit"] == "u@1"
    ]
    value = entry["current_pack_green"]
    assert isinstance(value, bool)
    return value


def _event_log_verdict(root: str) -> str:
    _, out, _ = _run("check", "--root", root)
    rows = [line.split() for line in out.splitlines() if mk2.CHECK_EVENT_LOG in line.split()]
    assert len(rows) == 1, out
    return rows[0][0]


# R1


def test_a_spec_only_rebind_needs_an_explicit_green(tmp_path: Path) -> None:
    project, root = _published(tmp_path)
    assert not _flag(root) and _current_pack_green(root)

    _rebind(project, root)
    assert _unit(root).pack_binding == "rebound"
    assert _flag(root) is True
    assert _current_pack_green(root) is False

    _evidence(root, "refactor", PASS_ARGV)
    assert _flag(root) is True
    assert _current_pack_green(root) is False

    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False
    assert _current_pack_green(root) is True


# R2


def test_a_rebind_with_a_changed_test_file_sets_the_flag(tmp_path: Path) -> None:
    project, root = _published(tmp_path)
    before = _unit(root).acceptance_test_set_digest
    _rebind(project, root, change_tests=True)
    assert _unit(root).acceptance_test_set_digest != before
    assert _flag(root) is True

    _evidence(root, "refactor", PASS_ARGV)
    assert _flag(root) is True
    assert _current_pack_green(root) is False

    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False
    assert _current_pack_green(root) is True


# R3


def test_a_red_after_a_rebind_leaves_the_flag_set(tmp_path: Path) -> None:
    project, root = _published(tmp_path)
    _rebind(project, root)
    _evidence(root, "red", FAIL_ARGV)
    assert _flag(root) is True
    assert _current_pack_green(root) is False

    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False
    assert _current_pack_green(root) is True


# R4


def test_the_pure_pack_bound_check_wants_the_green_at_high(tmp_path: Path) -> None:
    project, root = _published(tmp_path)
    _rebind(project, root)
    _evidence(root, "refactor", PASS_ARGV)
    _must("set-tier", "--root", root, "--tier", "high")

    refactor_only = check_pack_bound(_snapshot(root))
    assert refactor_only.verdict is Verdict.FAIL, refactor_only.detail
    assert "u@1" in refactor_only.detail

    _evidence(root, "green", PASS_ARGV)
    with_green = check_pack_bound(_snapshot(root))
    assert with_green.verdict is Verdict.PASS, with_green.detail


# R5


def test_a_store_rebound_before_the_change_fails_the_event_log_until_its_green(
    tmp_path: Path,
) -> None:
    project, root = _published(tmp_path)
    _rebind(project, root)
    snapshot = _snapshot(root)
    as_before = replace(
        snapshot,
        units=tuple(
            replace(unit, refreeze_green_required=False) if unit.ref() == UNIT else unit
            for unit in snapshot.units
        ),
    )
    (Path(root) / "state.json").write_bytes(encode_snapshot(as_before))
    assert _flag(root) is False

    assert _event_log_verdict(root) == "FAIL"

    _evidence(root, "green", PASS_ARGV)
    assert _event_log_verdict(root) == "PASS"
    assert _flag(root) is False


# R8


def test_a_second_rebind_renews_the_requirement(tmp_path: Path) -> None:
    project, root = _published(tmp_path)
    _rebind(project, root, spec="# u, rebind A\n")
    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False

    _rebind(project, root, spec="# u, rebind B\n")
    assert _flag(root) is True
    _evidence(root, "refactor", PASS_ARGV)
    assert _flag(root) is True
    assert _current_pack_green(root) is False
    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False
    assert _current_pack_green(root) is True

    _rebind(project, root, spec="# u, rebind C\n")
    _rebind(project, root, spec="# u, rebind D\n")
    assert len(_unit(root).rebound_history) == 4
    assert _flag(root) is True
    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False


# R9


def test_superseding_a_rebound_unit_clears_the_requirement(tmp_path: Path) -> None:  # guard:
    project, root = _published(tmp_path)
    _rebind(project, root)
    _must("supersede", "--root", root, "--name", "u")
    unit = _unit(root)
    assert unit.status.value == "superseded"
    assert unit.refreeze_green_required is False
    assert _event_log_verdict(root) == "PASS"


# R10


def test_a_refrozen_unit_keeps_its_requirement_when_superseded(tmp_path: Path) -> None:  # guard:
    project, root = _published(tmp_path, publish=False, tier="lightweight")
    _must(
        "open-deviation", "--root", root, "--deviation-id", "DEV-1",
        "--deviation-type", "pack-defect", "--unit", "u@1",
    )
    (project / "packs" / "u.spec.md").write_text("# u, amended\n")
    _must(
        "adjudicate-amend", "--root", root, "--deviation-id", "DEV-1",
        "--amendment-version", "u-pack/2",
    )
    _must("close-deviation", "--root", root, "--deviation-id", "DEV-1")
    _must("respecify", "--root", root, "--name", "u")
    _must(
        "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2",
        *_pack_args(project),
    )
    assert _unit(root).pack_binding != "rebound"
    assert _flag(root) is True
    # At lightweight, publication does not need evidence, so the unit can retire with the flag on.
    for command in ("mark-ready", "start", "mark-done", "publish"):
        _must(command, "--root", root, "--name", "u")
    assert _flag(root) is True

    _must("supersede", "--root", root, "--name", "u")
    unit = _unit(root)
    assert unit.status.value == "superseded"
    assert unit.refreeze_green_required is True
    assert _event_log_verdict(root) == "PASS"


# R6


def _tier_relation() -> Relation:
    universe = next((item for item in UNIVERSES if item.name == "tier-v1"), None)
    assert universe is not None, [item.name for item in UNIVERSES]
    return explore(universe)


def _m12() -> Invariant:
    found = [item for item in INVARIANTS if item.invariant_id.startswith("M12.")]
    assert len(found) == 1, [item.invariant_id for item in INVARIANTS]
    return found[0]


def _inventory_changing_rebinds(
    relation: Relation,
) -> list[tuple[tuple[str, str, str], Unit]]:
    snapshots = dict(relation.representatives)
    found: list[tuple[tuple[str, str, str], Unit]] = []
    for edge in relation.transitions:
        source, command, target = edge
        if command != mk2.CMD_REBIND_PACK:
            continue
        for unit in snapshots[target].units:
            prior = snapshots[source].unit_ref(unit.ref())
            if prior is not None and prior.acceptance_pack_digest != unit.acceptance_pack_digest:
                found.append((edge, unit))
    return found


def test_m12_requires_a_rebind_to_turn_the_flag_on() -> None:
    relation = _tier_relation()
    rebinds = _inventory_changing_rebinds(relation)
    assert rebinds, "tier-v1 has no inventory-changing rebind transition"
    for _edge, unit in rebinds:
        assert unit.refreeze_green_required, unit.ref().label()

    m12 = _m12()
    assert m12.check(relation) == ()

    (source, command, target), unit = rebinds[0]
    snapshots = dict(relation.representatives)
    after = snapshots[target]
    forged = replace(
        after,
        units=tuple(
            replace(item, refreeze_green_required=False) if item.ref() == unit.ref() else item
            for item in after.units
        ),
    )
    fingerprint = state_fingerprint(forged)
    representatives = {**snapshots, fingerprint: forged}
    transitions = set(relation.transitions)
    transitions.discard((source, command, target))
    transitions.add((source, command, fingerprint))
    mutated = replace(
        relation,
        states=tuple(sorted(set(relation.states) | {fingerprint})),
        transitions=tuple(sorted(transitions)),
        representatives=tuple(sorted(representatives.items())),
    )
    assert m12.check(mutated)

    superseded = replace(
        after,
        units=tuple(
            replace(item, status=UnitStatus.SUPERSEDED, refreeze_green_required=False)
            if item.ref() == unit.ref() else item
            for item in after.units
        ),
    )
    retired = state_fingerprint(superseded)
    with_supersede = replace(
        relation,
        states=tuple(sorted(set(relation.states) | {retired})),
        transitions=tuple(
            sorted(set(relation.transitions) | {(target, mk2.CMD_SUPERSEDE, retired)})
        ),
        representatives=tuple(sorted({**snapshots, retired: superseded}.items())),
    )
    assert m12.check(with_supersede) == ()


# R7


def test_claim_boundary_states_the_rebind_rule() -> None:
    text = re.sub(r"\s+", " ", (ROOT / "docs" / "claim-boundary.md").read_text())
    assert (
        "A rebind after publication turns on the fresh-GREEN requirement; while the unit stays "
        "active, only a passing GREEN for the current test set turns it off, and a REFACTOR "
        "does not."
    ) in text
    assert "Superseding a rebound unit ends its requirement." in text


# Guard


def test_guard_a_rebind_followed_by_a_green_replays_cleanly(
    tmp_path: Path,
) -> None:  # guard:
    project, root = _published(tmp_path)
    _rebind(project, root)
    _evidence(root, "green", PASS_ARGV)
    assert _flag(root) is False
    assert _current_pack_green(root) is True
    assert _event_log_verdict(root) == "PASS"
