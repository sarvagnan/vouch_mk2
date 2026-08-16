"""Acceptance pack for self-hosted unit assurance-tier@1 (D-11 pilot #2; D-12; 0.6 D045).

Every test here FAILED against the pre-change tree (RED recorded before implementation)
and must PASS at completion — except tests marked `# guard:`, which pass today and must
KEEP passing (they pin a property the unit must not regress). The implementer may not edit this file — a review finding
that says this pack is wrong is a DEVIATION adjudicated by the acceptance owner.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

import pytest

from vouch_mk2.core import Accepted, Refused, apply, decide
from vouch_mk2.core.model import Snapshot, Unit, UnitStatus
from vouch_mk2.store import Store, decode_snapshot, encode_snapshot

@contextlib.contextmanager
def _tmp_store():  # type: ignore[no-untyped-def]
    with tempfile.TemporaryDirectory() as d:
        store = Store.init(d + "/s")
        try:
            yield store
        finally:
            store.close()



def _unit(name: str, status: UnitStatus) -> Unit:
    return Unit(name, "v1", status, acceptance_pack_version="p" if status is not UnitStatus.SPECIFIED else "")


# ---- the tier is snapshot state, one authority, closed enum ----


def test_tier_is_snapshot_state_defaulting_to_lightweight_and_round_trips() -> None:
    from vouch_mk2.core.model import Tier

    assert Snapshot().tier is Tier.LIGHTWEIGHT
    for tier in Tier:
        s = Snapshot(revision=1, tier=tier)
        assert decode_snapshot(encode_snapshot(s)) == s


def test_set_tier_is_a_recorded_command_and_raising_is_free() -> None:
    from vouch_mk2.core import SetTier
    from vouch_mk2.core.model import Tier

    s = Snapshot()
    d = decide(s, SetTier(Tier.HIGH))
    assert isinstance(d, Accepted)
    assert apply(s, d.events).tier is Tier.HIGH


def test_lowering_tier_is_refused_once_work_is_committed() -> None:
    # machine.md §1: budget NEVER lowers tier below risk. Once any unit is beyond
    # `specified`, the tier may only rise.
    from vouch_mk2.core import SetTier
    from vouch_mk2.core.model import Tier

    fresh = Snapshot(revision=1, tier=Tier.HIGH, units=(_unit("u", UnitStatus.SPECIFIED),))
    assert isinstance(decide(fresh, SetTier(Tier.LIGHTWEIGHT)), Accepted)
    committed = Snapshot(revision=1, tier=Tier.HIGH, units=(_unit("u", UnitStatus.ACCEPTANCE_FROZEN),))
    d = decide(committed, SetTier(Tier.STANDARD))
    assert isinstance(d, Refused)
    assert d.violations[0].code == "tier.lowering-refused"


def test_no_tier_field_exists_in_project_config(tmp_path: Path) -> None:
    # guard: One authority: the tier is NOT in .vouch/config.json (0.6's D045 shape).
    import json

    from vouch_mk2.init import init_project
    from vouch_mk2.lock import build_lock

    project = tmp_path / "p"
    project.mkdir()
    init_project(str(project), build_lock(str(Path(__file__).resolve().parents[1] / "src")))
    config = json.loads((project / ".vouch" / "config.json").read_text())
    assert "tier" not in config and "assurance_tier" not in config


# ---- the tier matrix is mk2 IR data, and the checks read it ----


def test_tier_matrix_is_first_class_mk2_ir_and_matches_06() -> None:
    from vouch_mk2.ir.mk2 import MK2
    from vouch_mk2.ir.vouch_06 import VOUCH_06

    assert len(MK2.tier_requirements) == 13
    assert {t.control_id for t in MK2.tier_requirements} == {t.control_id for t in VOUCH_06.tier_requirements}


# ---- the ONE mechanical consumer: tier-scoped evidence requirements ----


def test_standard_tier_makes_missing_anchoring_a_fail_not_not_applicable() -> None:
    from vouch_mk2.core.model import AnchorMode, Tier
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    with _tmp_store() as store:
        store.commit(Snapshot(revision=1, tier=Tier.STANDARD, anchor_mode=AnchorMode.NONE,
                              units=(_unit("u", UnitStatus.PUBLISHED),)))
        snapshot = store.read_snapshot()
        anchors = next(r for r in run_artifact_checks(store, snapshot) if r.check_id == mk2.CHECK_EVIDENCE_ANCHORS)
        assert anchors.verdict is Verdict.FAIL
        assert "standard" in anchors.detail


def test_lightweight_tier_keeps_anchoring_not_applicable_under_mode_none() -> None:
    from vouch_mk2.core.model import AnchorMode, Tier
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict
    from vouch_mk2.verify import run_artifact_checks

    with _tmp_store() as store:
        store.commit(Snapshot(revision=1, tier=Tier.LIGHTWEIGHT, anchor_mode=AnchorMode.NONE,
                              units=(_unit("u", UnitStatus.PUBLISHED),)))
        snapshot = store.read_snapshot()
        anchors = next(r for r in run_artifact_checks(store, snapshot) if r.check_id == mk2.CHECK_EVIDENCE_ANCHORS)
        assert anchors.verdict is Verdict.NOT_APPLICABLE


def test_standard_tier_requires_a_chain_for_every_published_unit() -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core.model import Tier
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict

    s = Snapshot(revision=1, tier=Tier.STANDARD, units=(_unit("u", UnitStatus.PUBLISHED),))
    row = next(r for r in run_checks(s).mechanical if r.check_id == mk2.CHECK_TIER_EVIDENCE_REQUIRED)
    assert row.verdict is Verdict.FAIL and "u" in row.detail
    light = Snapshot(revision=1, tier=Tier.LIGHTWEIGHT, units=(_unit("u", UnitStatus.PUBLISHED),))
    assert next(r for r in run_checks(light).mechanical if r.check_id == mk2.CHECK_TIER_EVIDENCE_REQUIRED).verdict is Verdict.NOT_APPLICABLE


def test_high_tier_requires_anchored_chains_and_a_formal_certificate() -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core.model import EvidenceHead, Tier
    from vouch_mk2.ir import mk2
    from vouch_mk2.ir.schema import Verdict

    unanchored = Snapshot(revision=1, tier=Tier.HIGH, units=(_unit("u", UnitStatus.PUBLISHED),),
                          evidence_heads=(EvidenceHead("u", "a" * 64, 1, "GENESIS"),))
    row = next(r for r in run_checks(unanchored).mechanical if r.check_id == mk2.CHECK_TIER_HIGH_ATTESTATION)
    assert row.verdict is Verdict.FAIL
    assert "anchor" in row.detail or "certificate" in row.detail


# ---- what the tier does NOT check is said, per tier, as organizational rows ----


def test_unchecked_matrix_controls_appear_as_tier_scoped_organizational_rows() -> None:
    from vouch_mk2.checks import run_checks
    from vouch_mk2.core.model import Tier
    from vouch_mk2.ir.schema import Verdict

    report = run_checks(Snapshot(revision=1, tier=Tier.STANDARD))
    org = {r.check_id: r for r in report.organizational}
    assert "org.tier-controls" in org
    row = org["org.tier-controls"]
    assert row.verdict is Verdict.NOT_CHECKED
    assert "standard" in row.detail and "mutation" in row.detail.lower()


# ---- the tier is visible in every projection and transport ----


def test_tier_appears_in_status_facts_map_and_context() -> None:
    from vouch_mk2.core.model import Tier
    from vouch_mk2.projections import agent_context, next_actions, status_facts, system_map

    s = Snapshot(revision=1, tier=Tier.STANDARD)
    assert system_map(s).tier == "standard"
    assert status_facts(s, next_actions(s)).tier == "standard"
    assert "standard" in agent_context(s)
