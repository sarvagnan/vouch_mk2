"""Acceptance tests for formal-fresh-green@1 (D-46, settles vouch_mk2-7kc).

The formal layer reaches D-36's fresh-GREEN requirement in a focused universe, ``refreeze-v1``, at
the ``standard`` tier, and a new invariant M12 checks that a changed bound inventory turns the
requirement on, that no unit publishes while it is on, and that only a GREEN for the current test
set turns it off. Seams (D-44): the ``formal`` package's public registries and relations, the
``vouch-mk2 formal`` CLI, the ``check.formal-certificate`` row, and the docs.
"""

from __future__ import annotations

import io
import re
from collections import deque
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from functools import cache
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core import (
    Accepted,
    RecordEvidence,
    Snapshot,
    Tier,
    Unit,
    UnitStatus,
    apply,
    decide,
)
from vouch_mk2.formal import UNIVERSES, Relation, Universe, explore, state_fingerprint
from vouch_mk2.formal.certificate import CHECKER_VERSION, FormalCertificate
from vouch_mk2.formal.explore import emit_tables
from vouch_mk2.formal.invariants import INVARIANTS, Invariant
from vouch_mk2.formal.run import passed, run_formal
from vouch_mk2.formal.witnesses import WITNESSES
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import Store
from vouch_mk2.store.codec import encode_formal_certificate, encode_formal_tables
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.verify import run_artifact_checks

ROOT = Path(__file__).resolve().parents[1]
_GATED = {Tier.STANDARD, Tier.HIGH}
_STATE_BOUND = 20_000


def _refreeze_universe() -> Universe:
    universe = next((item for item in UNIVERSES if item.name == "refreeze-v1"), None)
    assert universe is not None, [item.name for item in UNIVERSES]
    return universe


def _m12() -> Invariant:
    found = [item for item in INVARIANTS if item.invariant_id.startswith("M12.")]
    assert len(found) == 1, [item.invariant_id for item in INVARIANTS]
    return found[0]


@cache
def _relation() -> Relation:
    return explore(_refreeze_universe())


def _snapshots(relation: Relation) -> dict[str, Snapshot]:
    return dict(relation.representatives)


def _on(snapshot: Snapshot) -> tuple[Unit, ...]:
    return tuple(unit for unit in snapshot.units if unit.refreeze_green_required)


def _entering_published(before: Snapshot, after: Snapshot) -> tuple[Unit, ...]:
    entered: list[Unit] = []
    for unit in after.units:
        prior = before.unit_ref(unit.ref())
        if (
            unit.status is UnitStatus.PUBLISHED
            and prior is not None
            and prior.status is not UnitStatus.PUBLISHED
        ):
            entered.append(prior)
    return tuple(entered)


def _with_unit(snapshot: Snapshot, unit: Unit, **changes: object) -> Snapshot:
    return replace(
        snapshot,
        units=tuple(
            _replace_unit(item, changes) if item.ref() == unit.ref() else item
            for item in snapshot.units
        ),
    )


def _replace_unit(unit: Unit, changes: dict[str, object]) -> Unit:
    if "status" in changes:
        status = changes["status"]
        assert isinstance(status, UnitStatus)
        unit = replace(unit, status=status)
    if "published_once" in changes:
        published_once = changes["published_once"]
        assert isinstance(published_once, bool)
        unit = replace(unit, published_once=published_once)
    if "refreeze_green_required" in changes:
        required = changes["refreeze_green_required"]
        assert isinstance(required, bool)
        unit = replace(unit, refreeze_green_required=required)
    return unit


def _redirect(
    relation: Relation, edge: tuple[str, str, str], forged: Snapshot
) -> Relation:
    """Replace one transition's target with a forged state; nothing else changes."""
    fingerprint = state_fingerprint(forged)
    representatives = dict(relation.representatives)
    representatives[fingerprint] = forged
    source, command, _target = edge
    transitions = set(relation.transitions)
    transitions.discard(edge)
    transitions.add((source, command, fingerprint))
    return replace(
        relation,
        states=tuple(sorted(set(relation.states) | {fingerprint})),
        transitions=tuple(sorted(transitions)),
        representatives=tuple(sorted(representatives.items())),
    )


def _first_edge(
    relation: Relation,
    predicate: Callable[[Snapshot, str, Snapshot], bool],
) -> tuple[str, str, str]:
    snapshots = _snapshots(relation)
    for edge in relation.transitions:
        source, command, target = edge
        if predicate(snapshots[source], command, snapshots[target]):
            return edge
    raise AssertionError("refreeze-v1 lacks the transition this mutation needs")


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _normalized(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


# R1


def test_refreeze_v1_is_a_small_gated_universe_that_runs_m12() -> None:
    universe = _refreeze_universe()
    assert universe.initial.tier in _GATED, universe.initial.tier
    relation = _relation()
    assert len(relation.states) < _STATE_BOUND, len(relation.states)
    expected = {
        item.invariant_id
        for item in INVARIANTS
        if item.invariant_id != "M6.no-nonterminal-deadlock"
    }
    evaluated = {
        verdict.invariant_id
        for verdict in run_formal().universe("refreeze-v1").invariants
    }
    assert expected <= evaluated, sorted(expected - evaluated)


# R2


def test_refreeze_v1_reaches_the_requirement_through_a_changed_inventory() -> None:
    relation = _relation()
    snapshots = _snapshots(relation)
    turned_on = [
        (command, after)
        for source, command, target in relation.transitions
        if not _on(before := snapshots[source])
        and _on(after := snapshots[target])
        and before.tier in _GATED
    ]
    assert turned_on, "no transition turns the fresh-GREEN requirement on"
    assert {command for command, _after in turned_on} <= {
        mk2.CMD_FREEZE,
        mk2.CMD_BIND_PACK,
    }, {command for command, _after in turned_on}
    waiting = [
        state
        for state, snapshot in snapshots.items()
        if any(unit.status is UnitStatus.DONE_PENDING_ACCEPTANCE for unit in _on(snapshot))
    ]
    assert waiting, "no done-pending-acceptance state has the requirement on"


# R3


def test_refreeze_v1_never_publishes_while_the_requirement_is_on() -> None:
    relation = _relation()
    snapshots = _snapshots(relation)
    for source, command, target in relation.transitions:
        for prior in _entering_published(snapshots[source], snapshots[target]):
            assert not prior.refreeze_green_required, (command, prior)


# R4


def test_refreeze_v1_publishes_after_a_green_turns_the_requirement_off() -> None:
    relation = _relation()
    snapshots = _snapshots(relation)
    cleared = {
        target
        for source, command, target in relation.transitions
        if command == mk2.CMD_RECORD_EVIDENCE
        and _on(snapshots[source])
        and not _on(snapshots[target])
    }
    assert cleared, "no recorded GREEN turns the requirement off"
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for source, command, target in relation.transitions:
        outgoing.setdefault(source, []).append((command, target))
    seen = set(cleared)
    queue = deque(cleared)
    while queue:
        state = queue.popleft()
        for command, target in outgoing.get(state, ()):
            if command == mk2.CMD_PUBLISH and _entering_published(
                snapshots[state], snapshots[target]
            ):
                return
            if target not in seen:
                seen.add(target)
                queue.append(target)
    raise AssertionError("no publish is reachable after the requirement turns off")


# R5


def test_m12_holds_on_the_real_relation_and_catches_a_change_that_leaves_it_off() -> None:
    m12 = _m12()
    relation = _relation()
    assert m12.check(relation) == ()
    edge = _first_edge(
        relation,
        lambda before, _command, after: not _on(before) and bool(_on(after)),
    )
    after = _snapshots(relation)[edge[2]]
    forged = _with_unit(after, _on(after)[0], refreeze_green_required=False)
    assert m12.check(_redirect(relation, edge, forged))


# R6


def test_m12_catches_a_publish_while_the_requirement_is_on() -> None:
    m12 = _m12()
    relation = _relation()
    snapshots = _snapshots(relation)
    source = next(
        state
        for state, snapshot in snapshots.items()
        if snapshot.tier in _GATED
        and any(unit.status is UnitStatus.DONE_PENDING_ACCEPTANCE for unit in _on(snapshot))
    )
    unit = next(
        unit
        for unit in _on(snapshots[source])
        if unit.status is UnitStatus.DONE_PENDING_ACCEPTANCE
    )
    forged = _with_unit(
        snapshots[source], unit, status=UnitStatus.PUBLISHED, published_once=True
    )
    fingerprint = state_fingerprint(forged)
    mutated = replace(
        relation,
        states=tuple(sorted(set(relation.states) | {fingerprint})),
        transitions=tuple(
            sorted(set(relation.transitions) | {(source, mk2.CMD_PUBLISH, fingerprint)})
        ),
        representatives=tuple(sorted({**snapshots, fingerprint: forged}.items())),
    )
    assert m12.check(mutated)


# R7


def test_m12_catches_the_requirement_turning_off_without_a_green() -> None:
    m12 = _m12()
    relation = _relation()
    edge = _first_edge(
        relation,
        lambda before, command, after: command != mk2.CMD_RECORD_EVIDENCE
        and bool(_on(before))
        and bool(_on(after)),
    )
    after = _snapshots(relation)[edge[2]]
    forged = _with_unit(after, _on(after)[0], refreeze_green_required=False)
    assert m12.check(_redirect(relation, edge, forged))


# R8


def test_formal_runs_m12_everywhere_and_its_witness_discriminates() -> None:
    m12 = _m12()
    owned = [item for item in WITNESSES if item.invariant_id == m12.invariant_id]
    assert len(owned) == 1, [item.witness_id for item in WITNESSES]
    run = run_formal()
    assert passed(run)
    for name in ("core-v1", "history-v1", "effects-v1", "refreeze-v1"):
        verdicts = {
            verdict.invariant_id: verdict.held
            for verdict in run.universe(name).invariants
        }
        assert verdicts.get(m12.invariant_id) is True, (name, verdicts)
    verdict = next(item for item in run.witnesses if item.invariant_id == m12.invariant_id)
    assert verdict.discriminated, verdict


# R9


def test_the_formal_command_reports_refreeze_v1_and_m12() -> None:
    m12 = _m12()
    code, output = _run("formal")
    assert code == 0, output
    assert "universe: refreeze-v1" in output
    assert re.search(rf"^PASS\s+{re.escape(m12.invariant_id)}$", output, re.MULTILINE)
    assert re.search(rf"^DISCRIMINATED\s+W12\.\S+ -> {re.escape(m12.invariant_id)}$", output, re.MULTILINE)


# R10


def test_the_certificate_check_names_m1_to_m12_as_its_residual(tmp_path: Path) -> None:
    tables = encode_formal_tables(emit_tables())
    certificate = FormalCertificate(
        checker_version=CHECKER_VERSION,
        tables_digest=digest_of(ArtifactKind.FORMAL_TABLES, tables),
        universes=tuple((universe.name, "a" * 64, 1, 1) for universe in UNIVERSES),
        invariants=tuple(
            (universe.name, invariant_id, True)
            for universe in UNIVERSES
            for invariant_id in (
                universe.invariant_ids
                or tuple(invariant.invariant_id for invariant in INVARIANTS)
            )
        ),
        witnesses=tuple(
            (witness.witness_id, witness.invariant_id, True) for witness in WITNESSES
        ),
    )
    data = encode_formal_certificate(certificate)
    with Store.init(
        str(tmp_path / "store"),
        initial_snapshot=Snapshot(
            tier=Tier.STANDARD,
            formal_certificate_digest=digest_of(ArtifactKind.FORMAL_CERTIFICATE, data),
        ),
    ) as store:
        with store.write_locked():
            store.put_object(ArtifactKind.FORMAL_TABLES, tables)
            store.put_object(ArtifactKind.FORMAL_CERTIFICATE, data)
        row = next(
            item
            for item in run_artifact_checks(store, store.read_snapshot())
            if item.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )
    assert row.verdict is Verdict.PASS, row.detail
    assert "executing M1-M12" in row.detail, row.detail
    assert "M1-M11" not in row.detail, row.detail


# R11


def test_the_docs_state_twelve_invariants_and_the_refreeze_universe() -> None:
    claims = _normalized(ROOT / "docs" / "claim-boundary.md")
    for phrase in (
        "evaluates M1–M12",
        "ran M1–M12",
        "`vouch-mk2 formal` performs that full exploration over every formal universe",
        "The `refreeze-v1` formal universe reaches D-36's fresh-GREEN requirement at the "
        "`standard` tier, and M12 checks that a changed pack turns it on, that no unit "
        "publishes while it is on, and that only a GREEN for the current pack turns it off.",
    ):
        assert phrase in claims, phrase
    assert "M1–M11" not in claims
    readme = _normalized(ROOT / "README.md")
    assert "checking twelve hand-written invariants" in readme
    assert "eleven hand-written" not in readme


# R12 (pack/2, DEV-ffg-01; pack/3, DEV-ffg-02)


def _expected_evidence_rows(relation: Relation) -> set[tuple[str, str, str, str]]:
    """Recompute every record-evidence edge's row through decide() and apply() directly."""
    rows: set[tuple[str, str, str, str]] = set()
    commands = [
        command
        for command in _refreeze_universe().commands
        if isinstance(command, RecordEvidence)
    ]
    for state, snapshot in relation.representatives:
        for command in commands:
            decision = decide(snapshot, command)
            if isinstance(decision, Accepted):
                target = state_fingerprint(apply(snapshot, decision.events))
                rows.add(
                    (state, target, command.event.kind.value, command.event.pack_digest)
                )
    return rows


def _with_rows(
    relation: Relation, edge: tuple[str, str, str], forged: Snapshot, kinds: tuple[tuple[str, str], ...]
) -> Relation:
    source, _command, target = edge
    mutated = _redirect(relation, edge, forged)
    fingerprint = state_fingerprint(forged)
    return replace(
        mutated,
        evidence_kinds=tuple(
            sorted(
                {row for row in relation.evidence_kinds if (row[0], row[1]) != (source, target)}
                | {(source, fingerprint, kind, digest) for kind, digest in kinds}
            )
        ),
    )


def test_m12_requires_the_clearing_record_to_be_a_green_for_the_current_test_set() -> None:
    m12 = _m12()
    relation = _relation()
    snapshots = _snapshots(relation)
    evidence_edges = {
        (source, target)
        for source, command, target in relation.transitions
        if command == mk2.CMD_RECORD_EVIDENCE
    }
    assert set(relation.evidence_kinds) == _expected_evidence_rows(relation)
    rows = {(row[0], row[1]): (row[2], row[3]) for row in relation.evidence_kinds}
    assert len(relation.evidence_kinds) == len(evidence_edges)
    assert set(rows) == evidence_edges
    for source, target in evidence_edges:
        if _on(snapshots[source]) and not _on(snapshots[target]):
            unit = _on(snapshots[source])[0]
            assert rows[(source, target)] == ("green", unit.acceptance_test_set_digest)
    edge = next(
        (
            (source, command, target)
            for source, command, target in relation.transitions
            if command == mk2.CMD_RECORD_EVIDENCE
            and rows[(source, target)][0] == "refactor"
            and _on(snapshots[source])
        ),
        None,
    )
    assert edge is not None, "no REFACTOR is recorded while the requirement is on"
    source, _command, target = edge
    after = snapshots[target]
    assert _on(after), "a REFACTOR turned the requirement off"
    unit = _on(after)[0]
    current = unit.acceptance_test_set_digest
    before_head = snapshots[source].evidence_head_ref(unit.ref())
    after_head = after.evidence_head_ref(unit.ref())
    assert after_head is not None and before_head is not None
    assert after_head.head_sequence > before_head.head_sequence
    assert any(
        summary.pack_digest == current and summary.green
        for summary in after_head.pack_summaries
    ), "the REFACTOR edge must keep an earlier GREEN for the current test set"
    forged = _with_unit(after, unit, refreeze_green_required=False)
    other = "0" * 64
    assert other != current
    for kinds in (
        (("refactor", current),),
        (("refactor", current), ("green", other)),
        (("green", other),),
    ):
        assert m12.check(_with_rows(relation, edge, forged, kinds)), kinds
