from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pytest import CaptureFixture

from vouch_mk2.cli import main
from vouch_mk2.core import AnchorMode, Tier, UnitRef, UnitStatus
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.migrate import (
    plan_migration,
    read_ledger,
    render_diff,
    validate_candidate,
)
from vouch_mk2.store import DecodeError, Store


DATA = Path(__file__).parent / "data"
FIXTURE = DATA / "vouch06-fixture-ledger.json"
CORPUS = DATA / "vouch06-corpus"


def _dict(value: object) -> dict[str, object]:
    assert type(value) is dict
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert type(value) is list
    return cast(list[object], value)


def _fixture_object() -> dict[str, object]:
    return _dict(cast(object, json.loads(FIXTURE.read_text())))


def _encoded(document: object) -> bytes:
    return json.dumps(document, sort_keys=True).encode("utf-8")


def _fixture_plan(*, drop_qualified: bool = True):
    return plan_migration(
        read_ledger(FIXTURE.read_bytes()),
        AnchorMode.NONE,
        drop_qualified=drop_qualified,
    )


def _tree(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    )


def test_fixture_maps_all_units_and_requires_explicit_qualified_drop() -> None:
    refused = _fixture_plan(drop_qualified=False)
    assert len(refused.candidate.units) == 9
    assert len(refused.refusals) == 2
    assert all("qualified" in refusal for refusal in refused.refusals)

    dropped = _fixture_plan()
    assert len(dropped.candidate.units) == 9
    assert dropped.candidate.tier is Tier.HIGH
    assert all(
        unit.published_once
        == (unit.status in {UnitStatus.PUBLISHED, UnitStatus.RELEASE_BOUND})
        for unit in dropped.candidate.units
    )
    assert dropped.refusals == ()
    assert {release.release_id for release in dropped.candidate.releases} == {
        "risk-resilient-2026.2"
    }
    assert "risk-core-2025.4" in "\n".join(dropped.dropped)
    assert "risk-core-2026.1" in "\n".join(dropped.dropped)


def test_fixture_deviation_binding_and_release_impact_split() -> None:
    document = _fixture_object()
    ledger = _dict(document["ledger"])
    deviations = _list(ledger["deviations"])
    deviation = _dict(deviations[0])
    impact = _list(deviation["impact_set"])
    impact.append("release:risk-resilient-2026.2")

    plan = plan_migration(
        read_ledger(_encoded(document)),
        AnchorMode.NONE,
        drop_qualified=True,
    )
    migrated = plan.candidate.deviations[0]
    assert migrated.unit == UnitRef("legacy-score-export", "1.0.0")
    assert migrated.impact_set == ("legacy-score-export", "legacy-risk-score")
    assert migrated.release_impact_set == ("risk-resilient-2026.2",)


def test_blocked_on_divergence_is_warned_not_repaired() -> None:
    document = _fixture_object()
    ledger = _dict(document["ledger"])
    units = _list(ledger["units"])
    unit = _dict(units[0])
    unit["blocked_on"] = ["DEV-DRIFT"]
    plan = plan_migration(
        read_ledger(_encoded(document)),
        AnchorMode.NONE,
        drop_qualified=True,
    )
    assert any(
        "observe-market-primary@1.0.0" in warning
        and "DEV-DRIFT" in warning
        and "derived blockers" in warning
        for warning in plan.warnings
    )


def test_unknown_fields_are_warnings_with_value_summaries() -> None:
    document = _fixture_object()
    ledger = _dict(document["ledger"])
    units = _list(ledger["units"])
    _dict(units[0])["future_field"] = {"answer": 42}
    decoded = read_ledger(_encoded(document))
    assert decoded.warnings == (
        'ledger.units[0].future_field: unknown field ignored (value={"answer":42})',
    )


@pytest.mark.parametrize(
    ("name", "reason"),
    (
        ("01-missing-closure-record.json", "release 'release-1'"),
        ("02-false-completion-claim.json", "release 'release-1'"),
        ("03-empty-closure-trigger.json", "release 'release-1'"),
        ("06-missing-closure-trigger-kind.json", "release 'release-1'"),
        ("07-invalid-closure-trigger-kind.json", "release 'release-1'"),
        ("08-bare-unit-deviation-binding.json", "DEV-BARE-BINDING"),
    ),
)
def test_corpus_semantic_cases_are_refused(name: str, reason: str) -> None:
    plan = plan_migration(
        read_ledger((CORPUS / name).read_bytes()),
        AnchorMode.NONE,
        drop_qualified=True,
    )
    assert plan.refusals
    assert reason in "\n".join(plan.refusals)


@pytest.mark.parametrize(
    ("name", "field"),
    (
        ("04-wrong-type-outcome-deviation.json", "outcome_deviations[0]"),
        ("05-wrong-type-formal-certificate-digest.json", "formal_certificate_digest"),
    ),
)
def test_corpus_wrong_types_are_decode_errors(name: str, field: str) -> None:
    with pytest.raises(DecodeError, match=field.replace("[", r"\[")):
        read_ledger((CORPUS / name).read_bytes())


@pytest.mark.parametrize("path", sorted(CORPUS.glob("*.json")), ids=lambda p: p.stem)
def test_every_corpus_case_has_nonzero_cli_exit(
    path: Path, capsys: CaptureFixture[str]
) -> None:
    assert main(["migrate", "--ledger", str(path), "--drop-qualified"]) != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    if path.name in {
        "01-missing-closure-record.json",
        "02-false-completion-claim.json",
        "03-empty-closure-trigger.json",
        "06-missing-closure-trigger-kind.json",
        "07-invalid-closure-trigger-kind.json",
    }:
        assert "release-1" in combined


def test_fixture_warning_and_validation_inventory_is_honest() -> None:
    plan = _fixture_plan()
    assert plan.refusals == ()
    assert plan.warnings == (
        "release 'risk-resilient-2026.2': bundle_inventory_digest "
        "'PENDING-BUNDLE-INVENTORY-DIGEST-r2' is not 64 lowercase hex and was dropped",
    )
    failures = tuple(
        result
        for result in validate_candidate(plan.candidate)
        if result.verdict is Verdict.FAIL
    )
    assert {result.check_id for result in failures} == {
        mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE,
        mk2.CHECK_TIER_EVIDENCE_REQUIRED,
        mk2.CHECK_TIER_HIGH_ATTESTATION,
        mk2.CHECK_PACK_BOUND,
    }
    amended = next(
        result
        for result in failures
        if result.check_id == mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE
    )
    assert "legacy-risk-score" in amended.detail
    report = render_diff(plan, validate_candidate(plan.candidate))
    from vouch_mk2.projections import escape_markdown

    # Report rows are markdown-escaped (an untrusted ledger's identifiers must not
    # become live links on the approval surface); assert the escaped form.
    assert escape_markdown(plan.warnings[0]) in report
    assert failures[0].check_id in report


def test_dry_run_writes_nothing_even_when_target_is_supplied(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    before = _tree(tmp_path)
    assert (
        main(
            [
                "migrate",
                "--ledger",
                str(FIXTURE),
                "--drop-qualified",
                "--target",
                str(target),
            ]
        )
        == 1
    )
    assert _tree(tmp_path) == before
    assert mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE in capsys.readouterr().out


def test_approve_commits_revision_one_reports_predicted_fail_and_refuses_rerun(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    command = [
        "migrate",
        "--ledger",
        str(FIXTURE),
        "--drop-qualified",
        "--approve",
        "--target",
        str(target),
    ]
    assert main(command) == 1
    migration_output = capsys.readouterr().out
    report = target / ".vouch/migration-report.md"
    assert report.is_file()
    assert str(report) in migration_output
    report_text = report.read_text()
    assert "risk-core-2025.4" in report_text
    assert "risk-core-2026.1" in report_text
    assert mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE in report_text

    with Store.open(str(target / ".vouch")) as store:
        snapshot = store.read_snapshot()
    assert snapshot.revision == 1
    assert len(snapshot.units) == 9

    assert main(["check", "--root", str(target / ".vouch")]) == 1
    check_output = capsys.readouterr().out
    fail_ids = {
        line.split()[1]
        for line in check_output.splitlines()
        if line.startswith("FAIL")
    }
    assert fail_ids == {
        mk2.CHECK_AMENDED_DEVIATIONS_COMPLETE,
        mk2.CHECK_EVIDENCE_ANCHORS,
        mk2.CHECK_TIER_EVIDENCE_REQUIRED,
        mk2.CHECK_TIER_HIGH_ATTESTATION,
        mk2.CHECK_PACK_BOUND,
    }
    formal_row = next(
        line
        for line in check_output.splitlines()
        if mk2.CHECK_FORMAL_CERTIFICATE in line
    )
    assert formal_row.startswith("NOT-APPLICABLE")
    assert "subjects-present: formal-certificate" in formal_row
    assert check_output.rstrip().endswith("composed: FAIL")

    before = report.read_bytes()
    assert main(command) == 1
    rerun = capsys.readouterr()
    assert "store already exists" in rerun.err
    assert report.read_bytes() == before


def test_cli_anchor_mode_overrides_config(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"anchor":{"mode":"ci"}}')
    target = tmp_path / "target"
    target.mkdir()
    assert (
        main(
            [
                "migrate",
                "--ledger",
                str(FIXTURE),
                "--config",
                str(config),
                "--anchor-mode",
                "none",
                "--drop-qualified",
                "--approve",
                "--target",
                str(target),
            ]
        )
        == 1
    )
    capsys.readouterr()
    with Store.open(str(target / ".vouch")) as store:
        assert store.read_snapshot().anchor_mode is AnchorMode.NONE

    configured_target = tmp_path / "configured-target"
    configured_target.mkdir()
    assert (
        main(
            [
                "migrate",
                "--ledger",
                str(FIXTURE),
                "--config",
                str(config),
                "--drop-qualified",
                "--approve",
                "--target",
                str(configured_target),
            ]
        )
        == 1
    )
    capsys.readouterr()
    with Store.open(str(configured_target / ".vouch")) as store:
        assert store.read_snapshot().anchor_mode is AnchorMode.CI

    disagreeing = tmp_path / "disagreeing.json"
    disagreeing.write_text('{"anchor":{"mode":"ci"},"tier":"lightweight"}')
    assert (
        main(
            [
                "migrate",
                "--ledger",
                str(FIXTURE),
                "--config",
                str(disagreeing),
                "--drop-qualified",
            ]
        )
        == 1
    )
    assert "authorities disagree" in capsys.readouterr().out
    _regression_config_tier_present_null_or_wrong_type_is_refused(
        tmp_path, capsys
    )


def _regression_config_tier_present_null_or_wrong_type_is_refused(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    for field in ("tier", "assurance_tier"):
        for label, value in (("null", None), ("wrong-type", 7)):
            config = tmp_path / f"{field}-{label}.json"
            config.write_text(json.dumps({field: value}), encoding="utf-8")
            assert (
                main(
                    [
                        "migrate",
                        "--ledger",
                        str(FIXTURE),
                        "--config",
                        str(config),
                        "--drop-qualified",
                    ]
                )
                == 1
            )
            captured = capsys.readouterr()
            assert field in captured.err
            assert "expected string" in captured.err


def test_empty_string_criteria_are_refused(tmp_path: Path) -> None:
    ledger = json.loads(FIXTURE.read_text())
    for release in ledger["ledger"]["releases"]:
        if release["archetype"] == "operational" and "launch_criteria" in release:
            release["launch_criteria"] = ""
            release["verification_window"] = ""
            release["rollback_triggers"] = ""
            release["go_no_go_authority"] = ""
    doc = read_ledger(json.dumps(ledger).encode())
    plan = plan_migration(doc, AnchorMode.NONE, drop_qualified=True)
    assert any("criteria fields present but empty" in r for r in plan.refusals)


def test_approve_refuses_a_candidate_that_cannot_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two ACTIVE same-name records cannot be decoded back; approving would
    # persist an undecodable state.json. Force such a candidate and assert
    # nothing is written.
    from dataclasses import replace

    from vouch_mk2.migrate import MigrationRefused, apply_migration

    doc = read_ledger(FIXTURE.read_bytes())
    plan = plan_migration(doc, AnchorMode.NONE, drop_qualified=True)
    dup = plan.candidate.units[0]
    twin = replace(dup, version=dup.version + "-twin", status=UnitStatus.PUBLISHED)
    active_dup = replace(dup, status=UnitStatus.PUBLISHED)
    broken = replace(
        plan,
        candidate=replace(plan.candidate, units=(active_dup, twin) + plan.candidate.units[1:]),
    )
    target = tmp_path / "proj"
    target.mkdir()
    with pytest.raises(MigrationRefused, match="round-trip"):
        apply_migration(broken, str(target), None)
    assert not (target / ".vouch").exists()
