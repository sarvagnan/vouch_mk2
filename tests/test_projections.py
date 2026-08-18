from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vouch_mk2.checks.organizational import (
    check_acceptance_ownership,
    check_causal_red_history,
    check_independence,
    check_intent_validation,
    check_protective_closure_truthfulness,
)
from vouch_mk2.core import (
    Accepted,
    AmendDeviation,
    BeginReverify,
    CloseDeviation,
    Command,
    CompleteRelease,
    FreezeAcceptance,
    MarkDone,
    MarkFailedUnrecovered,
    MarkReady,
    OpenDeviation,
    PrepareRelease,
    Publish,
    RejectDeviation,
    Respecify,
    RetireRelease,
    RollBack,
    SetReleaseCriteria,
    SpecifyUnit,
    StartDeploy,
    StartVerification,
    StartWork,
    Supersede,
    apply,
    decide,
)
from vouch_mk2.core.model import (
    AnchorMode,
    ClosureRecord,
    ClosureTriggerKind,
    Deviation,
    DeviationStatus,
    DeviationType,
    EvidenceHead,
    EvidencePackSummary,
    LaunchCriteria,
    Release,
    ReleaseArchetype,
    ReleaseState,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.projections import (
    agent_context,
    next_actions,
    status_facts,
    system_map,
)
from vouch_mk2.store import (
    DecodeError,
    IntegrityError,
    Store,
    StoreError,
    decode_system_map,
    encode_system_map,
)
from vouch_mk2.verify import emit_context, emit_system_map, run_artifact_checks


def _criteria() -> LaunchCriteria:
    return LaunchCriteria(("healthy",), "1h", ("errors",), "ops")


def _closure() -> ClosureRecord:
    return ClosureRecord(
        "operator stop",
        ClosureTriggerKind.OPERATOR,
        (("consumer", "v2", UnitStatus.RELEASE_BOUND.value),),
        True,
        ("latency",),
    )


_REF = UnitRef("u", "v1")
COMMAND_UNIVERSE: tuple[Command, ...] = (
    SpecifyUnit("u", "v1"),
    FreezeAcceptance("u", "p1"),
    MarkReady("u"),
    StartWork("u"),
    MarkDone("u"),
    Publish("u"),
    PrepareRelease("r1", ("u",)),
    SetReleaseCriteria("r1", _criteria()),
    StartDeploy("r1"),
    StartVerification("r1"),
    CompleteRelease("r1"),
    RollBack("r1", "trigger"),
    MarkFailedUnrecovered("r1", "trigger"),
    RetireRelease("r1", "trigger"),
    Supersede("u"),
    Respecify("u"),
    BeginReverify("u"),
    OpenDeviation("d1", DeviationType.PACK_DEFECT, _REF),
    AmendDeviation("d1", "v2"),
    RejectDeviation("d1"),
    CloseDeviation("d1"),
)


def snapshot_key(snapshot: Snapshot) -> tuple[object, ...]:
    return (
        tuple(
            (unit.name, unit.status.value, unit.acceptance_pack_version)
            for unit in snapshot.units
        ),
        tuple(
            (deviation.deviation_id, deviation.status.value)
            for deviation in snapshot.deviations
        ),
        tuple(
            (
                release.release_id,
                release.state.value,
                release.criteria is not None,
                release.closure is not None,
            )
            for release in snapshot.releases
        ),
    )


def _populated_snapshot() -> Snapshot:
    producer = Unit(
        "producer", "v1", UnitStatus.PUBLISHED, acceptance_pack_version="p1"
    )
    consumer = Unit(
        "consumer",
        "v2",
        UnitStatus.RELEASE_BOUND,
        depends_on=("producer",),
        acceptance_pack_version="p2",
    )
    return Snapshot(
        revision=9,
        units=(consumer, producer),
        deviations=(
            Deviation(
                "d1",
                DeviationType.PLAN_GAP,
                consumer.ref(),
                DeviationStatus.CLOSED,
                amendment_version="v3",
                impact_set=("consumer",),
                release_impact_set=("r1",),
            ),
        ),
        releases=(
            Release(
                "r1",
                ReleaseArchetype.OPERATIONAL,
                ReleaseState.RETIRED,
                (consumer.ref(),),
                _criteria(),
                _closure(),
                "b" * 64,
            ),
        ),
        evidence_heads=(
            EvidenceHead("consumer", "c" * 64, 2, "c" * 64, "3"),
        ),
        anchor_mode=AnchorMode.CI,
        formal_certificate_digest="f" * 64,
    )


def _commands(snapshot: Snapshot, kind: str, subject_id: str) -> set[str]:
    return {
        action.command
        for action in next_actions(snapshot)
        if action.subject_kind == kind and action.subject_id == subject_id
    }


def test_system_map_is_deterministic_and_encoding_is_byte_identical() -> None:
    snapshot = _populated_snapshot()
    first = system_map(snapshot)
    second = system_map(snapshot)
    assert first == second
    assert encode_system_map(first, "a" * 64) == encode_system_map(
        second, "a" * 64
    )


def test_system_map_complete_round_trip() -> None:
    snapshot = _populated_snapshot()
    projected = system_map(snapshot)
    data = encode_system_map(projected, "a" * 64)
    assert decode_system_map(data) == (projected, "a" * 64)
    _regression_system_map_v2_refuses_v1_and_contradictory_tiers()
    _regression_system_map_projects_publication_provenance()


def _regression_system_map_v2_refuses_v1_and_contradictory_tiers() -> None:
    data = encode_system_map(system_map(_populated_snapshot()), "a" * 64)
    document = json.loads(data)
    document["map_format"] = 2
    legacy = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    with pytest.raises(DecodeError, match="format 2 is no longer supported"):
        decode_system_map(legacy)

    document["map_format"] = 4
    document["map"]["status_facts"]["tier"] = "high"
    contradictory = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    with pytest.raises(DecodeError, match=r"status_facts\.tier: must equal map\.tier"):
        decode_system_map(contradictory)


def _regression_system_map_projects_publication_provenance() -> None:
    snapshot = Snapshot(
        units=(
            Unit(
                "u",
                "v1",
                UnitStatus.SUPERSEDED,
                acceptance_pack_version="p1",
                published_once=True,
            ),
        )
    )
    assert system_map(snapshot).units[0].published_once


def test_system_map_producer_edges_reverse_three_unit_chain() -> None:
    snapshot = Snapshot(
        units=(
            Unit("c", "1", UnitStatus.SPECIFIED, ("b",)),
            Unit("a", "1", UnitStatus.SPECIFIED),
            Unit("b", "1", UnitStatus.SPECIFIED, ("a",)),
        )
    )
    assert system_map(snapshot).producer_edges == (("a", "b"), ("b", "c"))


def test_system_map_refuses_noncanonical_reordered_json() -> None:
    data = encode_system_map(system_map(_populated_snapshot()), "a" * 64)
    reordered = json.dumps(json.loads(data), indent=2).encode("utf-8")
    with pytest.raises(DecodeError, match="non-canonical"):
        decode_system_map(reordered)


def test_system_map_digest_shape_is_validated_on_encode() -> None:
    with pytest.raises(StoreError, match="source_state_digest"):
        encode_system_map(system_map(Snapshot()), "BAD")


@pytest.mark.parametrize(
    ("status", "pack", "expected"),
    (
        (UnitStatus.SPECIFIED, "", {mk2.CMD_FREEZE}),
        (UnitStatus.ACCEPTANCE_FROZEN, "p", {mk2.CMD_MARK_READY}),
        (UnitStatus.READY, "p", {mk2.CMD_START}),
        (UnitStatus.IN_PROGRESS, "p", {mk2.CMD_MARK_DONE}),
        (UnitStatus.DONE_PENDING_ACCEPTANCE, "p", {mk2.CMD_PUBLISH}),
        (UnitStatus.RE_VERIFYING, "p", {mk2.CMD_PUBLISH}),
        (UnitStatus.BLOCKED, "", {mk2.CMD_RESPECIFY}),
        (UnitStatus.INVALIDATED, "", {mk2.CMD_RESPECIFY}),
        (UnitStatus.PUBLISHED, "p", set()),
        (UnitStatus.RELEASE_BOUND, "p", set()),
        (UnitStatus.SUPERSEDED, "p", set()),
    ),
)
def test_unit_next_action_matrix(
    status: UnitStatus, pack: str, expected: set[str]
) -> None:
    snapshot = Snapshot(
        units=(Unit("u", "v1", status, acceptance_pack_version=pack),)
    )
    assert _commands(snapshot, "unit", "u") == expected


def test_done_with_unsatisfied_dependency_has_no_publish() -> None:
    snapshot = Snapshot(
        units=(
            Unit("dependency", "v1", UnitStatus.IN_PROGRESS),
            Unit(
                "u",
                "v1",
                UnitStatus.DONE_PENDING_ACCEPTANCE,
                ("dependency",),
                "p1",
            ),
        )
    )
    assert mk2.CMD_PUBLISH not in _commands(snapshot, "unit", "u")
    _regression_publish_next_action_obeys_tier_evidence_gate()


def _regression_publish_next_action_obeys_tier_evidence_gate() -> None:
    from vouch_mk2.core import Tier

    unit = Unit(
        "u",
        "v1",
        UnitStatus.DONE_PENDING_ACCEPTANCE,
        acceptance_pack_version="p1",
        acceptance_pack_digest="d" * 64,
    )
    standard = Snapshot(tier=Tier.STANDARD, units=(unit,))
    assert mk2.CMD_PUBLISH not in _commands(standard, "unit", "u")
    standard_ready = Snapshot(
        tier=Tier.STANDARD,
        units=(unit,),
        evidence_heads=(
            EvidenceHead(
                "u",
                "a" * 64,
                1,
                "GENESIS",
                "v1",
                (EvidencePackSummary("d" * 64, True, True),),
            ),
        ),
    )
    assert mk2.CMD_PUBLISH in _commands(standard_ready, "unit", "u")
    high_unready = Snapshot(
        tier=Tier.HIGH,
        units=(unit,),
        evidence_heads=standard_ready.evidence_heads,
    )
    assert mk2.CMD_PUBLISH not in _commands(high_unready, "unit", "u")
    high_ready = Snapshot(
        tier=Tier.HIGH,
        units=(unit,),
        evidence_heads=(
            EvidenceHead(
                "u",
                "a" * 64,
                1,
                "a" * 64,
                "v1",
                (EvidencePackSummary("d" * 64, True, True),),
            ),
        ),
        formal_certificate_digest="f" * 64,
    )
    assert mk2.CMD_PUBLISH in _commands(high_ready, "unit", "u")


def test_blocked_with_open_deviation_has_adjudications_but_no_recovery() -> None:
    unit = Unit("u", "v1", UnitStatus.BLOCKED, acceptance_pack_version="p1")
    snapshot = Snapshot(
        units=(unit,),
        deviations=(
            Deviation(
                "d1", DeviationType.PACK_DEFECT, unit.ref(), DeviationStatus.OPEN
            ),
        ),
    )
    assert _commands(snapshot, "unit", "u") == set()
    assert _commands(snapshot, "deviation", "d1") == {
        mk2.CMD_ADJUDICATE_AMEND,
        mk2.CMD_ADJUDICATE_REJECT,
    }


@pytest.mark.parametrize(
    ("pack", "expected"),
    (
        ("", {mk2.CMD_RESPECIFY}),
        ("p1", {mk2.CMD_RESPECIFY, mk2.CMD_BEGIN_REVERIFY}),
    ),
)
def test_blocked_all_closed_recovery_depends_on_pack(
    pack: str, expected: set[str]
) -> None:
    unit = Unit("u", "v1", UnitStatus.BLOCKED, acceptance_pack_version=pack)
    snapshot = Snapshot(
        units=(unit,),
        deviations=(
            Deviation(
                "d1", DeviationType.PACK_DEFECT, unit.ref(), DeviationStatus.CLOSED
            ),
        ),
    )
    assert _commands(snapshot, "unit", "u") == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (
            DeviationStatus.OPEN,
            {mk2.CMD_ADJUDICATE_AMEND, mk2.CMD_ADJUDICATE_REJECT},
        ),
        (DeviationStatus.AMENDED, {mk2.CMD_CLOSE_DEVIATION}),
        (DeviationStatus.REJECTED, {mk2.CMD_CLOSE_DEVIATION}),
        (DeviationStatus.CLOSED, set()),
    ),
)
def test_deviation_next_action_matrix(
    status: DeviationStatus, expected: set[str]
) -> None:
    deviation = Deviation(
        "d1", DeviationType.PLAN_GAP, UnitRef("u", "v1"), status
    )
    assert _commands(Snapshot(deviations=(deviation,)), "deviation", "d1") == expected


@pytest.mark.parametrize(
    ("state", "criteria", "expected"),
    (
        (
            ReleaseState.PREPARED,
            False,
            {mk2.CMD_BIND_BUNDLE, mk2.CMD_SET_CRITERIA},
        ),
        (
            ReleaseState.PREPARED,
            True,
            {mk2.CMD_BIND_BUNDLE, mk2.CMD_START_DEPLOY},
        ),
        (ReleaseState.DEPLOYING, True, {mk2.CMD_START_VERIFICATION}),
        (ReleaseState.ROLLED_BACK, True, set()),
        (ReleaseState.SUCCEEDED, True, set()),
        (ReleaseState.FAILED_UNRECOVERED, True, set()),
        (ReleaseState.RETIRED, True, set()),
        (ReleaseState.INVALIDATED, True, set()),
    ),
)
def test_release_next_action_matrix(
    state: ReleaseState, criteria: bool, expected: set[str]
) -> None:
    release = Release(
        "r1",
        ReleaseArchetype.OPERATIONAL,
        state,
        (),
        _criteria() if criteria else None,
        None,
    )
    assert _commands(Snapshot(releases=(release,)), "release", "r1") == expected


def test_verifying_completes_only_when_all_gates_pass_otherwise_rolls_back() -> None:
    unit = Unit(
        "u", "v1", UnitStatus.RELEASE_BOUND, acceptance_pack_version="p1"
    )
    release = Release(
        "r1",
        ReleaseArchetype.OPERATIONAL,
        ReleaseState.VERIFYING,
        (unit.ref(),),
        _criteria(),
        None,
    )
    passing = Snapshot(units=(unit,), releases=(release,))
    assert _commands(passing, "release", "r1") == {mk2.CMD_COMPLETE_RELEASE}
    failing = Snapshot(
        units=(Unit("u", "v2", UnitStatus.RELEASE_BOUND, (), "p1"),),
        releases=(release,),
    )
    assert _commands(failing, "release", "r1") == {mk2.CMD_ROLL_BACK}


def test_stagnant_ci_anchor_emits_advance_and_none_mode_does_not() -> None:
    head = EvidenceHead("u", "a" * 64, 1, "GENESIS", "v1")
    units = (Unit("u", "v1", UnitStatus.SPECIFIED),)
    ci = Snapshot(units=units, evidence_heads=(head,), anchor_mode=AnchorMode.CI)
    assert _commands(ci, "evidence", "u") == {mk2.CMD_ADVANCE_ANCHOR}
    none = Snapshot(units=units, evidence_heads=(head,), anchor_mode=AnchorMode.NONE)
    assert _commands(none, "evidence", "u") == set()


def test_publish_next_action_is_suppressed_until_current_pack_is_green() -> None:
    from vouch_mk2.core import Tier

    unit = Unit(
        "u",
        "v1",
        UnitStatus.DONE_PENDING_ACCEPTANCE,
        acceptance_pack_version="pack/1",
        acceptance_pack_digest="a" * 64,
    )
    red_head = EvidenceHead(
        "u",
        "b" * 64,
        1,
        "GENESIS",
        "v1",
        (EvidencePackSummary("a" * 64, True, False),),
    )
    snapshot = Snapshot(tier=Tier.STANDARD, units=(unit,), evidence_heads=(red_head,))
    assert mk2.CMD_PUBLISH not in _commands(snapshot, "unit", "u")


def test_status_facts_reuses_already_derived_actions() -> None:
    snapshot = Snapshot(units=(Unit("u", "v1", UnitStatus.SPECIFIED),))
    actions = next_actions(snapshot)
    facts = status_facts(snapshot, actions)
    assert facts.unit_counts == (("specified", 1),)
    assert facts.deviation_counts == ()
    assert facts.next_action_count == len(actions) == 1


def test_system_map_flattens_per_digest_evidence_summaries() -> None:
    head = EvidenceHead(
        "u",
        "f" * 64,
        2,
        "GENESIS",
        "v1",
        (
            EvidencePackSummary("a" * 64, True, True),
            EvidencePackSummary("b" * 64, True, False),
        ),
    )
    projected = system_map(
        Snapshot(
            units=(Unit("u", "v1", UnitStatus.ACCEPTANCE_FROZEN),),
            evidence_heads=(head,),
        )
    )
    document = json.loads(encode_system_map(projected, "c" * 64))
    summaries = document["map"]["evidence_heads"][0]["pack_summaries"]
    assert summaries == [
        {"green": True, "pack_digest": "a" * 64, "red_seen": True},
        {"green": False, "pack_digest": "b" * 64, "red_seen": True},
    ]
    assert decode_system_map(encode_system_map(projected, "c" * 64))[0] == projected


def test_every_emitted_reachable_action_names_a_legal_ir_edge() -> None:
    visited = {snapshot_key(Snapshot())}
    frontier = [Snapshot()]
    while frontier:
        snapshot = frontier.pop()
        for action in next_actions(snapshot):
            if action.command == mk2.CMD_SPECIFY:
                assert not snapshot.units
                continue
            if action.subject_kind == "unit":
                subject = snapshot.unit(action.subject_id)
                assert subject is not None
                machine = mk2.MK2.machine(mk2.UNIT_MACHINE)
                assert any(
                    edge.source == subject.status.value
                    and edge.command == action.command
                    for edge in machine.command_transitions
                )
            elif action.subject_kind == "deviation":
                subject = snapshot.deviation(action.subject_id)
                assert subject is not None
                machine = mk2.MK2.machine(mk2.DEVIATION_MACHINE)
                assert any(
                    edge.source == subject.status.value
                    and edge.command == action.command
                    for edge in machine.command_transitions
                )
            elif action.subject_kind == "release":
                subject = snapshot.release(action.subject_id)
                assert subject is not None
                machine = mk2.MK2.machine(mk2.RELEASE_O_MACHINE)
                assert any(
                    edge.source == subject.state.value
                    and edge.command == action.command
                    for edge in machine.command_transitions
                )
            elif action.subject_kind == "evidence":
                head = snapshot.evidence_head(action.subject_id)
                assert action.command == mk2.CMD_ADVANCE_ANCHOR
                assert head is not None and head.anchored != head.head
            else:
                raise AssertionError(f"unknown action kind {action.subject_kind!r}")
        for command in COMMAND_UNIVERSE:
            decision = decide(snapshot, command)
            if not isinstance(decision, Accepted):
                continue
            after = apply(snapshot, decision.events)
            key = snapshot_key(after)
            if key not in visited:
                visited.add(key)
                frontier.append(after)


def _artifact_result(store: Store, snapshot: Snapshot, check_id: str) -> Verdict:
    for result in run_artifact_checks(store, snapshot):
        if result.check_id == check_id:
            return result.verdict
    raise AssertionError(f"missing artifact result {check_id}")


def test_map_freshness_passes_then_detects_stale_and_undecodable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        emit_system_map(store)
        snapshot = store.read_snapshot()
        assert (
            _artifact_result(store, snapshot, mk2.CHECK_MAP_FRESHNESS)
            is Verdict.PASS
        )
        store.commit(Snapshot(revision=1))
        stale = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_MAP_FRESHNESS
        )
        assert stale.verdict is Verdict.FAIL
        assert "stale" in stale.detail
        store.write_generated("map.json", b"not-json")
        corrupt = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_MAP_FRESHNESS
        )
        assert corrupt.verdict is Verdict.FAIL
        assert "undecodable" in corrupt.detail
    _regression_context_freshness_requires_context_with_map_and_detects_drift(
        tmp_path
    )


def test_absent_map_freshness_is_not_applicable(tmp_path: Path) -> None:
    with Store.init(str(tmp_path / "store")) as store:
        snapshot = store.read_snapshot()
        assert (
            _artifact_result(store, snapshot, mk2.CHECK_MAP_FRESHNESS)
            is Verdict.NOT_APPLICABLE
        )


def _regression_context_freshness_requires_context_with_map_and_detects_drift(
    tmp_path: Path,
) -> None:
    with Store.init(str(tmp_path / "context-store")) as store:
        snapshot = store.read_snapshot()
        assert (
            _artifact_result(store, snapshot, mk2.CHECK_CONTEXT_FRESHNESS)
            is Verdict.NOT_APPLICABLE
        )
        emit_system_map(store)
        absent = next(
            result
            for result in run_artifact_checks(store, snapshot)
            if result.check_id == mk2.CHECK_CONTEXT_FRESHNESS
        )
        assert absent.verdict is Verdict.FAIL
        assert "absent" in absent.detail

        emit_context(store, None)
        assert (
            _artifact_result(store, snapshot, mk2.CHECK_CONTEXT_FRESHNESS)
            is Verdict.PASS
        )
        store.commit(Snapshot(revision=1))
        stale = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_CONTEXT_FRESHNESS
        )
        assert stale.verdict is Verdict.FAIL
        assert "stale" in stale.detail

        store.write_generated("vouch-context.md", b"\xff")
        corrupt = next(
            result
            for result in run_artifact_checks(store, store.read_snapshot())
            if result.check_id == mk2.CHECK_CONTEXT_FRESHNESS
        )
        assert corrupt.verdict is Verdict.FAIL
        assert "undecodable" in corrupt.detail


def _regression_tier_control_coverage_reports_partial_none_full_and_waived() -> None:
    from vouch_mk2.checks.tier import check_tier_controls
    from vouch_mk2.core import Tier
    from vouch_mk2.ir.schema import Coverage, RequirementLevel

    for requirement in mk2.MK2.tier_requirements:
        for tier in Tier:
            cell = requirement.cell(tier)
            if requirement.control_id in {
                "typed-contracts",
            }:
                assert cell.coverage is Coverage.PARTIAL
                assert cell.residual
            elif requirement.control_id == "acceptance-frozen-before-implementation":
                expected = (
                    Coverage.NONE
                    if tier is Tier.LIGHTWEIGHT
                    else Coverage.PARTIAL
                )
                assert cell.coverage is expected
                assert cell.residual
            elif requirement.control_id == mk2.TIER_EVIDENCE_CONTROL:
                assert cell.coverage is Coverage.PARTIAL
                assert (
                    "recorded order of Vouch's own events is not proof of chronology "
                    "relative to implementation"
                ) in cell.residual
                assert (
                    "recording a command's exit status is not proof that the bound "
                    "acceptance tests actually executed"
                ) in cell.residual
            else:
                assert cell.coverage is Coverage.NONE
                if cell.level is not RequirementLevel.WAIVED:
                    assert cell.residual

    light = check_tier_controls(Snapshot(tier=Tier.LIGHTWEIGHT)).detail
    assert "Typed contracts, declared classes, purity checks" in light
    assert "PARTIAL; mech=pyrefly strict, import-linter" in light
    assert "residual=declared classes are human reviewed" in light
    assert "Harness-recorded red→green→refactor" in light
    assert "Mutation testing [mutation-testing]" in light
    assert "waived — WAIVED / no obligation" in light
    standard = check_tier_controls(Snapshot(tier=Tier.STANDARD)).detail
    assert "trigger evaluation NOT CHECKED" in standard
    assert f"[{mk2.TIER_EVIDENCE_CONTROL}]" in standard
    assert "PARTIAL; mech=" in standard
    assert (
        "the recorded order of Vouch's own events is not proof of chronology "
        "relative to implementation"
    ) in standard
    assert (
        "recording a command's exit status is not proof that the bound acceptance "
        "tests actually executed"
    ) in standard


def test_generated_store_allowlist_symlink_refusal_and_temp_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    with Store.init(str(root)) as store:
        with pytest.raises(StoreError):
            store.write_generated("evil.txt", b"evil")
        store.write_generated("map.json", b"map")
        assert store.read_generated("map.json") == b"map"
        assert not any(path.name.startswith(".generated-") for path in root.iterdir())
        os.unlink(root / "map.json")
        os.symlink(outside, root / "map.json")
        with pytest.raises(IntegrityError):
            store.read_generated("map.json")


def test_agent_context_is_deterministic_and_sources_all_org_details() -> None:
    snapshot = _populated_snapshot()
    first = agent_context(snapshot)
    assert first == agent_context(snapshot)
    for check in (
        check_acceptance_ownership,
        check_intent_validation,
        check_independence,
        check_causal_red_history,
        check_protective_closure_truthfulness,
    ):
        result = check(snapshot)
        assert f"- {result.check_id}: {result.detail}" in first
    _regression_tier_control_coverage_reports_partial_none_full_and_waived()


def test_emit_context_absent_equal_and_different_targets_use_sidecar(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    target = tmp_path / "AGENTS.md"
    with Store.init(str(root)) as store:
        written = emit_context(store, str(target))
        assert written == str(target)
        generated = target.read_bytes()
        assert store.read_generated("vouch-context.md") == generated

        written = emit_context(store, str(target))
        assert written == str(target)
        assert target.read_bytes() == generated

        target.write_bytes(b"human-owned\n")
        written = emit_context(store, str(target))
        assert written == str(target) + ".vouch-suggested"
        assert target.read_bytes() == b"human-owned\n"
        assert Path(written).read_bytes() == generated


def test_emit_context_refuses_symlinked_targets(tmp_path: Path) -> None:
    # A caller-supplied --target that is a symlink is never followed: a
    # dangling link would create its referent (a path the caller never named)
    # and a live link would be opened for writing through the link.
    root = tmp_path / "store"
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_bytes(b"human-owned\n")
    live_link = tmp_path / "AGENTS.md"
    os.symlink(elsewhere, live_link)
    dangling = tmp_path / "CLAUDE.md"
    os.symlink(tmp_path / "does-not-exist.md", dangling)
    with Store.init(str(root)) as store:
        with pytest.raises(StoreError, match="symlinked context target refused"):
            emit_context(store, str(live_link))
        assert elsewhere.read_bytes() == b"human-owned\n"
        with pytest.raises(StoreError, match="symlinked context target refused"):
            emit_context(store, str(dangling))
        assert not (tmp_path / "does-not-exist.md").exists()
        assert not (tmp_path / "AGENTS.md.vouch-suggested").exists()


def test_emit_context_writes_atomically_without_leftover_temps(tmp_path: Path) -> None:
    root = tmp_path / "store"
    target = tmp_path / "AGENTS.md"
    with Store.init(str(root)) as store:
        emit_context(store, str(target))
    assert target.exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
