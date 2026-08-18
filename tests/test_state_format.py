"""Acceptance pack for self-hosted unit state-format@1 (D-13; bead bd4.2).

Every test here FAILED against the pre-change tree (RED recorded before implementation)
and must PASS at completion — except tests marked `# guard:`. The implementer may not edit
this file; a finding that this pack is wrong is a DEVIATION adjudicated by the owner.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core.model import Snapshot, UnitStatus
from vouch_mk2.store import Store, decode_snapshot, encode_snapshot
from vouch_mk2.store.errors import DecodeError, StoreError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "state-format"
LEGACY = sorted(FIXTURES.glob("format0-*.json"))


def _legacy_store(tmp_path: Path, fixture: Path) -> str:
    """A store whose state.json is a real pre-versioned document of mk2's own history.

    Objects come from the committed dogfood store (CAS content is append-only, so every
    object either era referenced is still present); projections are removed (stale by
    construction); no .lock, like a checkout.
    """
    root = tmp_path / fixture.stem
    shutil.copytree(ROOT / ".vouch", root, ignore=shutil.ignore_patterns(".lock", "map.json", "vouch-context.md"))
    (root / "state.json").write_bytes(fixture.read_bytes())
    return str(root)


# ---- the field, the constant, strict decode ----


def test_current_format_is_one_and_the_field_round_trips() -> None:
    from vouch_mk2.store import CURRENT_STATE_FORMAT

    assert CURRENT_STATE_FORMAT == 4
    document = json.loads(encode_snapshot(Snapshot()))
    assert document["state_format"] == 4
    assert decode_snapshot(encode_snapshot(Snapshot(revision=3))) == Snapshot(revision=3)


def test_missing_state_format_is_refused_naming_upgrade() -> None:
    document = json.loads(encode_snapshot(Snapshot()))
    del document["state_format"]
    with pytest.raises(DecodeError, match="state_format") as raised:
        decode_snapshot(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    assert "upgrade" in str(raised.value)


def test_newer_state_format_is_refused_as_newer() -> None:
    document = json.loads(encode_snapshot(Snapshot()))
    document["state_format"] = 99
    with pytest.raises(DecodeError, match="newer"):
        decode_snapshot(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def test_older_state_format_is_refused_naming_upgrade() -> None:
    document = json.loads(encode_snapshot(Snapshot()))
    document["state_format"] = 0
    with pytest.raises(DecodeError, match="upgrade"):
        decode_snapshot(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")


# ---- the drift guard: layout changes must bump the format ----


def _golden_snapshot() -> Snapshot:
    """A snapshot instantiating EVERY record type and enum family the codec lays out.

    pack/2 (DEV-sf-01): the format-1 golden must pin the nested layouts too — an empty
    snapshot pinned only the top level, so a coordinated unit/deviation/release/evidence
    layout change could ship unversioned.
    """
    from vouch_mk2.core.model import (
        AnchorMode, ClosureRecord, ClosureTriggerKind, Deviation, DeviationStatus, DeviationType,
        EvidenceHead, LaunchCriteria, Release, ReleaseArchetype, ReleaseState, Tier, Unit, UnitRef,
    )

    return Snapshot(
        revision=7, tier=Tier.STANDARD, anchor_mode=AnchorMode.CI, formal_certificate_digest="c" * 64,
        units=(
            Unit("core", "v1", UnitStatus.PUBLISHED, (), "core-pack/2@0123456789abcdef", True, "c" * 64),
            Unit("core", "v0", UnitStatus.SUPERSEDED, (), "core-pack/1@fedcba9876543210", True, "d" * 64),
            Unit("ui", "v1", UnitStatus.BLOCKED, ("core",), "ui-pack/1@00ff00ff00ff00ff", False),
            Unit("docs", "v1", UnitStatus.SPECIFIED, (), "", False),
        ),
        deviations=(
            Deviation("DEV-1", DeviationType.PACK_DEFECT, UnitRef("ui", "v1"), DeviationStatus.OPEN, "", ("ui",), ()),
            Deviation("DEV-0", DeviationType.CONTRACT_CONFLICT, UnitRef("core", "v1"), DeviationStatus.CLOSED, "core-pack/2", ("core", "ui"), ("r1",)),
        ),
        releases=(
            Release(
                "r1", ReleaseArchetype.OPERATIONAL, ReleaseState.INVALIDATED, (UnitRef("core", "v1"),),
                LaunchCriteria(("healthy", "p99<200ms"), "24h", ("error-rate>1%",), "release-owner"),
                ClosureRecord("rollout complete", ClosureTriggerKind.AMENDMENT, (("core", "v1", "published"),), False, ("obs-1",)),
                "b" * 64,
            ),
        ),
        evidence_heads=(
            EvidenceHead("core", "a" * 64, 4, "a" * 64, "v1"),
            EvidenceHead("ui", "d" * 64, 1, "GENESIS", "v1"),
        ),
    )


def test_canonical_layout_of_format_1_matches_the_golden() -> None:
    # If this fails you changed the canonical layout: bump CURRENT_STATE_FORMAT, add an
    # upgrader in vouch_mk2.store.upgrade, add a fixture, and ask the owner to re-pin this golden.
    golden = (FIXTURES / "format4.golden.json").read_bytes()
    assert encode_snapshot(_golden_snapshot()) == golden
    assert decode_snapshot(golden) == _golden_snapshot()


# ---- upgraders: pure, contiguous, no invented state ----


def test_upgraders_are_contiguous_and_pure_dict_functions() -> None:
    from vouch_mk2.store import CURRENT_STATE_FORMAT
    from vouch_mk2.store.upgrade import UPGRADERS

    assert sorted(UPGRADERS) == list(range(CURRENT_STATE_FORMAT))
    document = json.loads(LEGACY[0].read_bytes())
    before = json.dumps(document, sort_keys=True)
    UPGRADERS[0](document)
    assert json.dumps(document, sort_keys=True) == before, "upgraders must not mutate their input"


@pytest.mark.parametrize("fixture", LEGACY, ids=[f.stem for f in LEGACY])
def test_format0_shapes_upgrade_to_the_current_format_without_invented_state(fixture: Path) -> None:
    # pack/3 (DEV-sf-02): published_once is a LATCH. Only published/release-bound are
    # unambiguous (-> True). Both fixtures contain only such units, so no assertions are
    # needed here; the ambiguous case is the next test.
    from vouch_mk2.core.model import Tier
    from vouch_mk2.store.upgrade import upgrade_document

    original = json.loads(fixture.read_bytes())
    upgraded, steps = upgrade_document(original)
    assert steps == [(0, 1), (1, 2), (2, 3), (3, 4)]
    snapshot = decode_snapshot(json.dumps(upgraded, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")
    assert snapshot.revision == original["revision"]
    if "tier" not in original:
        assert snapshot.tier is Tier.LIGHTWEIGHT
    else:
        assert snapshot.tier.value == original["tier"]
    for unit in snapshot.units:
        raw = next(u for u in original["units"] if u["name"] == unit.name and u["version"] == unit.version)
        assert raw["status"] in ("published", "release-bound"), "fixture unexpectedly carries an ambiguous unit"
        assert unit.published_once is raw.get("published_once", True)


def test_format0_ambiguous_published_once_is_refused_unless_asserted() -> None:
    # pack/3 (DEV-sf-02): a legacy unit whose status is not published/release-bound may or
    # may not have been published (blocked/invalidated/re-verifying/specified are all reachable
    # after publication; superseded is reachable without it). The upgrader must not guess:
    # without an operator assertion it refuses, naming the units; with one it records it.
    from vouch_mk2.store.upgrade import UpgradeRefused, upgrade_document

    original = json.loads(LEGACY[1].read_bytes())
    original["units"].append({"name": "maybe", "version": "1", "status": "blocked", "depends_on": [], "acceptance_pack_version": "p"})
    original["units"].append({"name": "gone", "version": "1", "status": "superseded", "depends_on": [], "acceptance_pack_version": "p"})
    with pytest.raises(UpgradeRefused) as raised:
        upgrade_document(original)
    assert "maybe@1" in str(raised.value) and "gone@1" in str(raised.value)
    upgraded, _steps = upgrade_document(original, published_once={"maybe@1": True, "gone@1": False})
    units = upgraded["units"]
    assert isinstance(units, list)
    by_ref = {(u["name"], u["version"]): u for u in units}
    assert by_ref[("maybe", "1")]["published_once"] is True
    assert by_ref[("gone", "1")]["published_once"] is False
    with pytest.raises(UpgradeRefused, match="unknown"):
        upgrade_document(original, published_once={"maybe@1": True, "gone@1": False, "nobody@9": True})


def test_upgrade_verb_requires_assertions_for_ambiguous_units(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _legacy_store(tmp_path, LEGACY[1])
    document = json.loads(Path(root, "state.json").read_bytes())
    document["units"].append({"name": "maybe", "version": "1", "status": "blocked", "depends_on": [], "acceptance_pack_version": "p"})
    Path(root, "state.json").write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    before = Path(root, "state.json").read_bytes()
    assert main(["upgrade", "--root", root, "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "maybe@1" in err and "--was-published" in err and "--never-published" in err
    assert main(["upgrade", "--root", root]) == 2
    assert Path(root, "state.json").read_bytes() == before
    capsys.readouterr()
    assert main(["upgrade", "--root", root, "--never-published", "maybe@1"]) == 0
    out = capsys.readouterr().out
    assert "maybe@1" in out and "never-published" in out
    with Store.open(root, read_only=True) as store:
        unit = store.read_snapshot().unit("maybe")
        assert unit is not None and unit.published_once is False


# ---- the verb ----


@pytest.mark.parametrize("fixture", LEGACY, ids=[f.stem for f in LEGACY])
def test_upgrade_verb_rewrites_a_legacy_store_in_place_and_it_checks_clean(tmp_path: Path, fixture: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _legacy_store(tmp_path, fixture)
    revision_before = json.loads(Path(root, "state.json").read_bytes())["revision"]
    assert main(["upgrade", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "0 -> 1" in out or "0 → 1" in out
    document = json.loads(Path(root, "state.json").read_bytes())
    assert document["state_format"] == 4 and document["revision"] == revision_before
    # The upgraded store OPENS and CHECKS (a certificate recorded under an older checker may
    # legitimately FAIL its row — that is not a format matter — so exit 0 or 1, never 2).
    assert main(["check", "--ci", "--root", root]) in (0, 1)
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.projections import agent_context
    from vouch_mk2.verify import run_artifact_checks
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        rows = {r.check_id: r.verdict for r in run_artifact_checks(store, snapshot)}
    assert rows[mk2.CHECK_MAP_FRESHNESS] is Verdict.PASS
    assert rows[mk2.CHECK_CONTEXT_FRESHNESS] is Verdict.PASS
    assert (Path(root) / "vouch-context.md").read_text() == agent_context(snapshot)


def test_upgrade_is_idempotent_and_reports_current(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(tmp_path / "s")
    Store.init(root).close()
    before = Path(root, "state.json").read_bytes()
    assert main(["upgrade", "--root", root]) == 0
    assert "already" in capsys.readouterr().out
    assert Path(root, "state.json").read_bytes() == before


def test_upgrade_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _legacy_store(tmp_path, LEGACY[0])
    before = Path(root, "state.json").read_bytes()
    assert main(["upgrade", "--root", root, "--dry-run"]) == 0
    assert "0 -> 1" in capsys.readouterr().out
    assert Path(root, "state.json").read_bytes() == before


def test_upgrade_fails_closed_on_an_undecodable_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _legacy_store(tmp_path, LEGACY[0])
    document = json.loads(Path(root, "state.json").read_bytes())
    document["units"].append({"name": "", "version": "", "status": "specified"})  # invalid whatever the upgrader does
    Path(root, "state.json").write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    before = Path(root, "state.json").read_bytes()
    assert main(["upgrade", "--root", root]) == 2
    assert Path(root, "state.json").read_bytes() == before
    assert "upgrade" in capsys.readouterr().err
    assert not list(Path(root).glob("*.tmp"))


def test_upgrade_refuses_a_symlinked_state_file(tmp_path: Path) -> None:
    root = _legacy_store(tmp_path, LEGACY[0])
    real = tmp_path / "elsewhere.json"
    real.write_bytes(Path(root, "state.json").read_bytes())
    Path(root, "state.json").unlink()
    Path(root, "state.json").symlink_to(real)
    assert main(["upgrade", "--root", root]) == 2
    assert Path(root, "state.json").is_symlink()


# ---- read paths on a stale store: clear message, exit 2, no traceback ----


@pytest.mark.parametrize("verb", ["check", "status", "next"])
def test_read_verbs_on_a_legacy_store_exit_2_naming_upgrade(tmp_path: Path, verb: str, capsys: pytest.CaptureFixture[str]) -> None:
    root = _legacy_store(tmp_path, LEGACY[0])
    assert main([verb, "--root", root]) == 2
    err = capsys.readouterr().err
    assert "upgrade" in err and "Traceback" not in err


def test_read_only_open_of_a_legacy_store_names_upgrade(tmp_path: Path) -> None:
    root = _legacy_store(tmp_path, LEGACY[0])
    with pytest.raises(StoreError, match="upgrade"):
        with Store.open(root, read_only=True) as store:
            store.read_snapshot()


# ---- writers of state write the current format ----


def test_init_and_migrate_write_the_current_format(tmp_path: Path) -> None:
    from vouch_mk2.store import CURRENT_STATE_FORMAT

    root = str(tmp_path / "s")
    Store.init(root).close()
    assert json.loads(Path(root, "state.json").read_bytes())["state_format"] == CURRENT_STATE_FORMAT


def test_lock_exposes_upgrade(tmp_path: Path) -> None:
    from vouch_mk2.lock import build_lock

    lock = tmp_path / "vouch.lock"
    lock.write_bytes(build_lock(str(ROOT / "src")))
    result = subprocess.run([sys.executable, str(lock), "upgrade", "--help"], capture_output=True, text=True, env={"PATH": "", "PYTHONPATH": ""}, check=False)
    assert result.returncode == 0 and "--dry-run" in result.stdout


def test_codec_docstring_no_longer_calls_the_format_disposable() -> None:
    # guard-of-honesty: the D-13 line — the disposable era ended at format 1.
    import vouch_mk2.store.codec as codec

    assert "DISPOSABLE" not in (codec.__doc__ or "")
    assert "state_format" in (codec.__doc__ or "")
