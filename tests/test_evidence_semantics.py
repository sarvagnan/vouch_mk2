"""Acceptance pack for self-hosted unit evidence-semantics@1 (D-17; adoption reviews' finding #1).

Every test here FAILED against the pre-change tree (RED recorded before implementation) and
must PASS at completion — except tests marked `# guard:`. The implementer may not edit this
file; a finding that this pack is wrong is a DEVIATION adjudicated by the owner.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.model import UnitStatus
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]
OK = [sys.executable, "-c", "raise SystemExit(0)"]


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _project(tmp_path: Path, tier: str = "standard") -> tuple[Path, str]:
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "t.py").write_text("def test_x():\n    assert False\n")
    root = str(project / ".vouch")
    Store.init(root).close()
    if tier != "lightweight":
        assert _run("set-tier", "--root", root, "--tier", tier)[0] == 0
        assert _run("set-anchor-mode", "--root", root, "--mode", "ci")[0] == 0
    return project, root


def _frozen_unit(project: Path, root: str, name: str = "u") -> None:
    assert _run("specify", "--root", root, "--name", name, "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", name, "--pack-version", f"{name}-pack/1", "--project", str(project), "--pack-file", "tests/t.py")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", name)[0] == 0
    assert _run("start", "--root", root, "--name", name)[0] == 0


def _evidence(root: str, unit: str, kind: str, argv: list[str]) -> tuple[int, str]:
    return _run("record-evidence", "--root", root, "--unit", unit, "--kind", kind, "--", *argv)


# ---- publication requires current-pack GREEN (the critical finding) ----


def test_red_only_evidence_cannot_publish_at_standard(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    code, out = _run("publish", "--root", root, "--name", "u")
    assert code == 2 and "gate.dod.current-pack-green" in out


def test_red_then_green_publishes_and_green_then_red_does_not(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0  # regressed after green
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    code, out = _run("publish", "--root", root, "--name", "u")
    assert code == 2 and "current-pack-green" in out
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0


def test_evidence_binds_the_pack_digest_it_ran_against(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    from vouch_mk2.store import ArtifactKind, decode_evidence_event

    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        unit = snapshot.unit("u")
        assert unit is not None
        head = snapshot.evidence_head("u")
        assert head is not None
        event = decode_evidence_event(store.get_object(ArtifactKind.EVIDENCE, head.head))
    assert event.pack_digest == unit.acceptance_pack_digest and len(event.pack_digest) == 64


def test_evidence_for_an_old_pack_does_not_count_after_amendment(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    for argv in (["open-deviation", "--deviation-id", "D1", "--deviation-type", "pack-defect", "--unit", "u@v1"],
                 ["adjudicate-amend", "--deviation-id", "D1", "--amendment-version", "u-pack/2"],
                 ["close-deviation", "--deviation-id", "D1"], ["respecify", "--name", "u"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0, argv
    (project / "tests" / "t.py").write_text("def test_x():\n    assert 1 == 2\n")
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2", "--project", str(project), "--pack-file", "tests/t.py")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    code, out = _run("publish", "--root", root, "--name", "u")
    assert code == 2 and "current-pack-green" in out  # the old pack's green must not count
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0


def test_lightweight_keeps_publish_free_of_the_green_gate(tmp_path: Path) -> None:
    # guard: passes today and must keep passing — the green gate is tier-scoped to standard+.
    project, root = _project(tmp_path, tier="lightweight")
    _frozen_unit(project, root)
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0


def test_check_reports_a_published_unit_whose_latest_current_pack_evidence_is_not_green(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    for kind, argv in (("red", FAIL), ("green", OK)):
        assert _evidence(root, "u@v1", kind, argv)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0  # regression recorded after publication
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--ci", "--root", root, "--project", str(project))
    assert code == 1
    assert any("check.evidence-chains" in line and "FAIL" in line for line in out.splitlines())


# ---- record-evidence is gated on status; late evidence is allowed and visible ----


def test_record_evidence_is_refused_before_the_pack_is_frozen(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    assert _run("specify", "--root", root, "--name", "w", "--version", "v1")[0] == 0
    code, out = _evidence(root, "w@v1", "green", OK)
    assert code == 2 and "evidence.no-pack" in out
    with Store.open(root, read_only=True) as store:
        assert store.read_snapshot().evidence_head("w") is None  # nothing poisoned


def test_late_evidence_on_a_published_unit_is_allowed(tmp_path: Path) -> None:
    # guard: passes today and must keep passing — the status gate is only BEFORE acceptance-frozen.
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    for kind, argv in (("red", FAIL), ("green", OK)):
        assert _evidence(root, "u@v1", kind, argv)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0
    assert _evidence(root, "u@v1", "refactor", OK)[0] == 0


# ---- re-versioning is reachable (and is the escape from a poisoned chain) ----


def test_specify_accepts_a_name_whose_only_records_are_superseded(tmp_path: Path) -> None:
    project, root = _project(tmp_path, tier="lightweight")
    _frozen_unit(project, root, "api")
    assert _run("mark-done", "--root", root, "--name", "api")[0] == 0
    assert _run("publish", "--root", root, "--name", "api")[0] == 0
    assert _run("supersede", "--root", root, "--name", "api")[0] == 0
    code, out = _run("specify", "--root", root, "--name", "api", "--version", "v2")
    assert code == 0, out
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        active = snapshot.unit("api")
        assert active is not None and active.version == "v2" and active.status is UnitStatus.SPECIFIED
        assert sum(1 for u in snapshot.units if u.name == "api") == 2
        assert snapshot.evidence_head("api") is None or snapshot.evidence_head("api").head_sequence == 0  # type: ignore[union-attr]
    code, out = _run("specify", "--root", root, "--name", "api", "--version", "v1")
    assert code == 2 and "unit.exists" in out  # the same version cannot be re-created


def test_reversioned_unit_starts_with_an_empty_evidence_chain_keyed_by_ref(tmp_path: Path) -> None:
    project, root = _project(tmp_path, tier="lightweight")
    _frozen_unit(project, root, "api")
    assert _evidence(root, "api@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "api@v1", "green", OK)[0] == 0
    assert _run("mark-done", "--root", root, "--name", "api")[0] == 0
    assert _run("publish", "--root", root, "--name", "api")[0] == 0
    assert _run("supersede", "--root", root, "--name", "api")[0] == 0
    assert _run("specify", "--root", root, "--name", "api", "--version", "v2")[0] == 0
    assert _run("freeze", "--root", root, "--name", "api", "--pack-version", "api-pack/v2-1", "--project", str(project), "--pack-file", "tests/t.py")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "api")[0] == 0
    assert _run("start", "--root", root, "--name", "api")[0] == 0
    code, out = _evidence(root, "api@v2", "green", OK)
    # a green with no earlier red on the NEW version's chain must be its own problem, not v1's
    assert code == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--root", root)
    assert any("evidence-chains" in line and "api" in line and "v2" in line for line in out.splitlines()), out


# ---- the IR cell and docs say what is now true ----


def test_ir_cell_lists_the_green_gate() -> None:
    from vouch_mk2.ir.mk2 import MK2

    cell = next(r for r in MK2.tier_requirements if r.control_id == "harness-recorded-red-green").standard
    assert "gate.dod.current-pack-green" in cell.mechanical_checks


def test_quickstart_and_claim_boundary_state_the_rule() -> None:
    text = (ROOT / "docs" / "quickstart.md").read_text() + (ROOT / "docs" / "claim-boundary.md").read_text()
    assert "gate.dod.current-pack-green" in text
    assert "latest" in text.lower() and "current pack" in text.lower()


# ---- pack/3: evidence binds the TEST-SET digest (R7); rebind-pack (R8) ----


def _spec_and_test(project: Path, spec: str = "prose v1", test_body: str = "assert False") -> None:
    (project / "packs").mkdir(parents=True, exist_ok=True)
    (project / "packs" / "p.spec.md").write_text(spec + "\n")
    (project / "tests" / "t.py").write_text(f"def test_x():\n    {test_body}\n")


def _freeze_roles(project: Path, root: str, name: str, version: str) -> tuple[int, str]:
    return _run("freeze", "--root", root, "--name", name, "--pack-version", version,
                "--project", str(project), "--pack-file", "packs/p.spec.md",
                "--pack-test-file", "tests/t.py")


def test_freeze_with_test_files_records_roles_and_a_test_set_digest(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _spec_and_test(project)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    code, out = _freeze_roles(project, root, "u", "u-pack/1")
    assert code == 0, out
    from vouch_mk2.store import ArtifactKind, decode_pack_inventory
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        unit = snapshot.unit("u")
        assert unit is not None
        inventory = decode_pack_inventory(
            store.get_object(ArtifactKind.ACCEPTANCE_PACK, unit.acceptance_pack_digest)
        )
    roles = {entry.path: entry.role for entry in inventory.entries}
    assert roles == {"packs/p.spec.md": "spec", "tests/t.py": "test"}


def test_prose_only_amendment_preserves_current_pack_evidence(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _spec_and_test(project)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _freeze_roles(project, root, "u", "u-pack/1")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    for argv in (["open-deviation", "--deviation-id", "D9", "--deviation-type", "pack-defect", "--unit", "u@v1"],
                 ["adjudicate-amend", "--deviation-id", "D9", "--amendment-version", "u-pack/2"],
                 ["close-deviation", "--deviation-id", "D9"], ["respecify", "--name", "u"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0, argv
    _spec_and_test(project, spec="prose v2 REWRITTEN")  # ONLY the spec changes
    assert _freeze_roles(project, root, "u", "u-pack/2")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    code, out = _run("publish", "--root", root, "--name", "u")
    assert code == 0, f"a prose-only amendment must keep RED->GREEN: {out}"


def test_changing_a_test_file_still_voids_prior_evidence(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _spec_and_test(project)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _freeze_roles(project, root, "u", "u-pack/1")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    for argv in (["open-deviation", "--deviation-id", "D9", "--deviation-type", "pack-defect", "--unit", "u@v1"],
                 ["adjudicate-amend", "--deviation-id", "D9", "--amendment-version", "u-pack/2"],
                 ["close-deviation", "--deviation-id", "D9"], ["respecify", "--name", "u"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0, argv
    _spec_and_test(project, test_body="assert 1 == 2")  # a TEST file changes
    assert _freeze_roles(project, root, "u", "u-pack/2")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    code, out = _run("publish", "--root", root, "--name", "u")
    assert code == 2 and "current-pack-green" in out


def test_legacy_pack_format_one_binds_the_whole_inventory(tmp_path: Path) -> None:
    # guard: a pack frozen WITHOUT --pack-test-file keeps pre-amendment behaviour exactly.
    project, root = _project(tmp_path)
    _frozen_unit(project, root)
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    for argv in (["open-deviation", "--deviation-id", "D8", "--deviation-type", "pack-defect", "--unit", "u@v1"],
                 ["adjudicate-amend", "--deviation-id", "D8", "--amendment-version", "u-pack/2"],
                 ["close-deviation", "--deviation-id", "D8"], ["respecify", "--name", "u"]):
        assert _run(argv[0], "--root", root, *argv[1:])[0] == 0, argv
    (project / "tests" / "t.py").write_text("def test_x():\n    assert 1 == 2\n")
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/2",
                "--project", str(project), "--pack-file", "tests/t.py")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 2


def _published_with_roles(tmp_path: Path, tier: str = "standard") -> tuple[Path, str]:
    project, root = _project(tmp_path, tier=tier)
    _spec_and_test(project)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _freeze_roles(project, root, "u", "u-pack/1")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    assert _evidence(root, "u@v1", "red", FAIL)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("mark-done", "--root", root, "--name", "u")[0] == 0
    assert _run("publish", "--root", root, "--name", "u")[0] == 0
    return project, root


def _rebind(project: Path, root: str, reason: str = "adapt to EvidenceHead field") -> tuple[int, str]:
    return _run("rebind-pack", "--root", root, "--name", "u", "--project", str(project),
                "--pack-file", "packs/p.spec.md", "--pack-test-file", "tests/t.py",
                "--reason", reason)


def test_rebind_pack_replaces_the_inventory_and_requires_a_fresh_green(tmp_path: Path) -> None:
    project, root = _published_with_roles(tmp_path)
    with Store.open(root, read_only=True) as store:
        before = store.read_snapshot().unit("u")
        assert before is not None
    _spec_and_test(project, test_body="assert True  # adapted to a changed shared type")
    code, out = _rebind(project, root)
    assert code == 0, out
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        unit = snapshot.unit("u")
        assert unit is not None
        assert unit.acceptance_pack_digest != before.acceptance_pack_digest
        assert unit.pack_binding == "rebound"
        assert unit.status is UnitStatus.PUBLISHED  # chain carried forward, no respecify
        head = snapshot.evidence_head("u")
        assert head is not None and head.head_sequence == 2  # chain untouched
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--root", root, "--project", str(project))
    assert any("evidence-chains" in line and "FAIL" in line for line in out.splitlines()), out
    assert _evidence(root, "u@v1", "green", OK)[0] == 0  # a GREEN, no RED demanded
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--root", root, "--project", str(project))
    assert not any("evidence-chains" in line and "FAIL" in line for line in out.splitlines()), out


def test_rebind_pack_is_refused_before_publication(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    _spec_and_test(project)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _freeze_roles(project, root, "u", "u-pack/1")[0] == 0
    assert _run("mark-ready", "--root", root, "--name", "u")[0] == 0
    assert _run("start", "--root", root, "--name", "u")[0] == 0
    code, out = _rebind(project, root)
    assert code == 2 and "rebind.not-published" in out


def test_rebind_pack_requires_a_reason(tmp_path: Path) -> None:
    project, root = _published_with_roles(tmp_path)
    _spec_and_test(project, test_body="assert True")
    code, out = _rebind(project, root, reason="")
    assert code == 2 and "rebind.reason-required" in out


def test_rebind_pack_is_listed_by_name_in_pack_bound(tmp_path: Path) -> None:
    project, root = _published_with_roles(tmp_path)
    _spec_and_test(project, test_body="assert True")
    assert _rebind(project, root)[0] == 0
    assert _evidence(root, "u@v1", "green", OK)[0] == 0
    assert _run("advance-anchor", "--root", root, "--unit-name", "u")[0] == 0
    assert _run("generate", "--root", root)[0] == 0
    code, out = _run("check", "--root", root, "--project", str(project))
    assert any("pack-bound" in line and "rebound after publication" in line and "u@v1" in line
               for line in out.splitlines()), out
