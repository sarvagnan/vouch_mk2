from __future__ import annotations

import argparse
import io
import json
import re
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.checks import CheckResult, run_checks
from vouch_mk2.checks.tier import check_tier_controls
from vouch_mk2.cli import _parser, _secret_looking_command, main
from vouch_mk2.core import CiException, CiPolicy, Snapshot, Tier, Unit, UnitStatus
from vouch_mk2.formal import FormalCertificate, UNIVERSES
from vouch_mk2.formal.certificate import CHECKER_VERSION
from vouch_mk2.formal.explore import emit_tables
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.witnesses import WITNESSES
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    decode_evidence_event,
    decode_snapshot,
    digest_of,
    encode_formal_certificate,
    encode_formal_tables,
    encode_snapshot,
)
from vouch_mk2.verify import run_artifact_checks


def _run(*arguments: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(arguments))
    return code, stdout.getvalue(), stderr.getvalue()


def _run_invoked_as(program_name: str, *arguments: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    original_argv = sys.argv
    sys.argv = [program_name, *arguments]
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main()
    finally:
        sys.argv = original_argv
    return code, stdout.getvalue(), stderr.getvalue()


def _init(tmp_path: Path, *extra: str) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    code, output, error = _run("init", "--project", str(project), *extra)
    assert code == 0, output + error
    return project, str(project / ".vouch")


def _advance_to_published(root: str, name: str = "u") -> None:
    for arguments in (
        ("specify", "--name", name, "--version", "v1"),
        ("freeze", "--name", name, "--pack-version", "pack/1"),
        ("mark-ready", "--name", name),
        ("start", "--name", name),
        ("mark-done", "--name", name),
        ("publish", "--name", name),
    ):
        assert _run(arguments[0], "--root", root, *arguments[1:])[0] == 0


def test_init_records_strict_policy_selected_tier_and_python_314_transport(
    tmp_path: Path,
) -> None:
    project, root = _init(
        tmp_path,
        "--tier",
        "standard",
        "--test-job",
        "unit-tests",
    )
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.tier is Tier.STANDARD
    assert snapshot.ci_policy is CiPolicy.STRICT
    workflow = (project / ".github/workflows/vouch.yml").read_text()
    assert "python-version: '3.14'" in workflow
    assert "check --ci --root .vouch --project ." in workflow
    assert b"Vouch requires Python 3.14+" in (project / "vouch.lock").read_bytes()


def test_generated_workflow_runs_exhaustive_formal_after_ci_check(
    tmp_path: Path,
) -> None:
    project, _root = _init(tmp_path)
    workflow = (project / ".github/workflows/vouch.yml").read_text()
    check = "python vouch.lock check --ci --root .vouch --project ."
    formal = "python vouch.lock formal"
    assert formal in workflow
    assert workflow.index(check) < workflow.index(formal)


def test_format_four_legacy_policy_defaults_to_ledger_and_new_facts_round_trip() -> None:
    legacy = encode_snapshot(Snapshot(ci_policy=CiPolicy.LEDGER))
    assert b"ci_policy" not in legacy and b"ci_exceptions" not in legacy
    assert decode_snapshot(legacy).ci_policy is CiPolicy.LEDGER
    strict = Snapshot(
        units=(Unit("u", "v1", UnitStatus.SPECIFIED),),
        ci_policy=CiPolicy.STRICT,
        ci_exceptions=(CiException("u@v1", "planned work"),),
    )
    encoded = encode_snapshot(strict)
    assert json.loads(encoded)["state_format"] == 5
    assert decode_snapshot(encoded) == strict


def test_ci_policy_is_ci_scoped_and_reasoned_exceptions_are_reversible(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "p1")[0] == 0
    assert _run("check", "--root", root)[0] == 0
    assert _run("check", "--root", root, "--ci")[0] == 1
    code, _out, error = _run(
        "mark-open-in-ci", "--root", root, "--name", "u", "--reason", "  "
    )
    assert code == 2 and "ci-policy.reason-required" in error
    assert _run(
        "mark-open-in-ci",
        "--root",
        root,
        "--name",
        "u",
        "--reason",
        "owner approved through Friday",
    )[0] == 0
    code, output, _error = _run("check", "--root", root, "--ci")
    assert code == 0
    assert "u@v1 (owner approved through Friday)" in output
    assert _run("clear-open-in-ci", "--root", root, "--name", "u")[0] == 0
    assert _run("check", "--root", root, "--ci")[0] == 1


def test_next_bootstrap_and_json_use_the_actual_program_name(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    code, output, _error = _run("next", "--root", root, "--json")
    assert code == 0
    action = json.loads(output)["actions"][0]
    assert action["command"] == "specify"
    assert action["argv"][:2] == ["vouch-mk2", "specify"]
    assert "--version" in action["placeholders"]


def test_explicit_main_argv_uses_vouch_mk2_for_next_and_context(
    tmp_path: Path,
) -> None:
    project, root = _init(tmp_path)
    assert _run("set-ci-policy", "--root", root, "--policy", "ledger")[0] == 0
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    code, output, error = _run("next", "--root", root, "--json")
    assert code == 0, output + error
    assert {
        action["argv"][0] for action in json.loads(output)["actions"]
    } == {"vouch-mk2"}
    assert _run("generate", "--root", root)[0] == 0
    context = (project / ".vouch" / "vouch-context.md").read_text()
    assert "`vouch-mk2 generate`" in context


def test_persisted_context_bytes_are_identical_across_invocation_names(
    tmp_path: Path,
) -> None:
    project, root = _init(tmp_path)
    code, output, error = _run_invoked_as(
        "first-vouch.lock", "generate", "--root", root
    )
    assert code == 0, output + error
    first = (project / ".vouch" / "vouch-context.md").read_bytes()
    code, output, error = _run_invoked_as(
        "second-vouch.lock", "generate", "--root", root
    )
    assert code == 0, output + error
    second = (project / ".vouch" / "vouch-context.md").read_bytes()
    assert first == second
    assert b"`vouch-mk2 generate`" in second.splitlines()[0]


def test_context_freshness_passes_across_generate_and_check_invocation_names(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    code, output, error = _run_invoked_as(
        "generator.lock", "generate", "--root", root
    )
    assert code == 0, output + error
    code, output, error = _run_invoked_as(
        "checker.lock", "check", "--ci", "--root", root
    )
    assert code == 0, output + error
    assert re.search(
        r"^PASS\s+check\.context-freshness(?:\s|$)",
        output,
        flags=re.MULTILINE,
    )


def test_status_json_carries_text_policy_counts_and_exception_reasons(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "p1")[0] == 0
    assert _run(
        "mark-open-in-ci", "--root", root, "--name", "u", "--reason", "in flight"
    )[0] == 0
    code, output, _error = _run("status", "--root", root, "--json")
    assert code == 0
    document = json.loads(output)
    assert document["unit_counts"] == {"acceptance-frozen": 1}
    assert document["ci_policy"] == "strict"
    assert document["ci_exceptions"] == [["u@v1", "in flight"]]


def test_ci_exception_is_bound_to_exact_ref_and_cannot_carry_to_new_version(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "p1")[0] == 0
    assert _run(
        "mark-open-in-ci",
        "--root",
        root,
        "--name",
        "u",
        "--reason",
        "approved for v1 only",
    )[0] == 0
    code, output, error = _run("check", "--root", root, "--ci")
    assert code == 0, output + error
    assert "u@v1 (approved for v1 only)" in output
    for arguments in (
        ("mark-ready",),
        ("start",),
        ("mark-done",),
        ("publish",),
        ("supersede",),
    ):
        assert _run(arguments[0], "--root", root, "--name", "u")[0] == 0
    assert _run("specify", "--root", root, "--name", "u", "--version", "v2")[0] == 0
    assert _run("freeze", "--root", root, "--name", "u", "--pack-version", "p2")[0] == 0
    code, output, error = _run("check", "--root", root, "--ci")
    assert code == 1, output + error
    assert "u@v2" in output
    assert "approved for v1 only" not in output


def test_mark_open_in_ci_refuses_terminal_units(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    _advance_to_published(root)
    code, output, error = _run(
        "mark-open-in-ci",
        "--root",
        root,
        "--name",
        "u",
        "--reason",
        "too late",
    )
    assert code == 2, output + error
    assert "ci-policy.exception-not-open" in error


def test_empty_subject_checks_cite_ir_applicability_instead_of_passing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    with Store.init(str(root)) as store:
        snapshot = store.read_snapshot()
        report = run_checks(
            snapshot,
            artifact_results=run_artifact_checks(store, snapshot),
        )
    by_id = {row.check_id: row for row in report.mechanical}
    for check_id in (
        mk2.CHECK_DEPENDENCIES_EXIST,
        mk2.CHECK_DEVIATION_BINDINGS_RESOLVE,
        mk2.CHECK_EVIDENCE_CHAINS,
        mk2.CHECK_BUNDLE_INVENTORIES,
        mk2.CHECK_CONTEXT_FRESHNESS,
    ):
        assert by_id[check_id].verdict is Verdict.NOT_APPLICABLE
        assert "IR applicability rule" in by_id[check_id].detail


def test_tier_control_detail_has_one_bounded_line_per_control() -> None:
    detail = check_tier_controls(Snapshot()).detail
    lines = detail.splitlines()
    assert len(lines) >= len(mk2.MK2.tier_requirements)
    assert max(map(len, lines)) < 370
    for requirement in mk2.MK2.tier_requirements:
        assert sum(f"[{requirement.control_id}]" in line for line in lines) == 1
    assert all(
        any(requirement.description in line for requirement in mk2.MK2.tier_requirements)
        for line in lines
    )


def test_org_tier_control_rows_preserve_every_requirement_fact() -> None:
    for tier in Tier:
        lines = check_tier_controls(Snapshot(tier=tier)).detail.splitlines()
        for requirement in mk2.MK2.tier_requirements:
            index = next(
                index
                for index, row in enumerate(lines)
                if f"[{requirement.control_id}]" in row
            )
            line = lines[index]
            continuation = (
                lines[index + 1]
                if index + 1 < len(lines) and "[" not in lines[index + 1]
                else ""
            )
            complete_row = line + "\n" + continuation
            cell = requirement.cell(tier)
            assert requirement.description in line
            assert cell.level.value in complete_row
            if cell.level.value == "waived":
                assert "WAIVED / no obligation" in complete_row
            elif cell.coverage.value == "partial":
                assert "PARTIAL" in complete_row
                for fact in (*cell.mechanical_evidence, *cell.mechanical_checks):
                    assert fact in complete_row
                assert cell.residual in complete_row
            elif cell.coverage.value == "none":
                assert "NOT CHECKED" in complete_row
                assert cell.residual in complete_row


def test_build_lock_creates_missing_parents_under_real_path(tmp_path: Path) -> None:
    output = tmp_path / "one" / "two" / "vouch.lock"
    assert _run("build-lock", "--output", str(output))[0] == 0
    assert output.is_file()


def test_build_lock_allows_existing_symlinked_output_parent(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    output = link / "vouch.lock"
    code, stdout, stderr = _run("build-lock", "--output", str(output))
    assert code == 0, stdout + stderr
    assert output.is_file()


def test_build_lock_refuses_creating_missing_parent_through_escaping_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    output = link / "nested" / "vouch.lock"
    code, stdout, stderr = _run("build-lock", "--output", str(output))
    assert code == 2, stdout + stderr
    assert "refusing to create" in stderr
    assert "through symlinked ancestor" in stderr
    assert not (outside / "nested").exists()


def test_reflected_help_uses_human_sentences_not_generated_dataclass_signatures() -> None:
    parser = _parser()
    action = next(
        item
        for item in parser._actions  # noqa: SLF001
        if isinstance(item, argparse._SubParsersAction)
    )
    for choice in action._choices_actions:  # noqa: SLF001
        help_text = choice.help or ""
        assert not (help_text.endswith(")") and ": '" in help_text)


def test_secret_warning_does_not_redact_the_immutable_evidence_command(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    for arguments in (
        ("specify", "--name", "u", "--version", "v1"),
        ("freeze", "--name", "u", "--pack-version", "p1"),
        ("mark-ready", "--name", "u"),
        ("start", "--name", "u"),
    ):
        assert _run(arguments[0], "--root", root, *arguments[1:])[0] == 0
    child = [
        sys.executable,
        "-c",
        "raise SystemExit(1)",
        "--password",
        "opaque-password-value",
    ]
    code, _output, error = _run(
        "record-evidence",
        "--root",
        root,
        "--unit",
        "u@v1",
        "--kind",
        "red",
        "--",
        *child,
    )
    assert code == 0 and "stored verbatim" in error
    with Store.open(root, read_only=True) as store:
        head = store.read_snapshot().evidence_head("u")
        assert head is not None
        event = decode_evidence_event(
            store.get_object(ArtifactKind.EVIDENCE, head.head)
        )
    assert event.command_line == shlex.join(child)


def test_secret_warning_matches_sensitive_option_names_not_substrings() -> None:
    assert not _secret_looking_command(["tool", "--tokenize", "input"])
    assert not _secret_looking_command(["tool", "--secretary", "input"])
    assert _secret_looking_command(["tool", "--token", "value"])
    assert _secret_looking_command(["tool", "--password=hunter2"])
    assert _secret_looking_command(["tool", "AWS_SECRET_ACCESS_KEY=value"])


def test_secret_warning_has_no_false_positive_for_quickstart_commands() -> None:
    quickstart = Path("docs/quickstart.md").read_text()
    shell_blocks = re.findall(r"```(?:bash|sh)\n(.*?)```", quickstart, re.DOTALL)
    command_lines: list[str] = []
    for block in shell_blocks:
        logical = ""
        for raw_line in block.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            logical += stripped.removesuffix("\\").strip() + " "
            if stripped.endswith("\\"):
                continue
            command_lines.append(logical.strip())
            logical = ""
    assert command_lines, "quickstart has no shell command lines"
    offenders = [
        line
        for line in command_lines
        if _secret_looking_command(shlex.split(line, comments=True))
    ]
    assert offenders == []


def test_recorded_formal_certificate_is_checked_at_every_tier(
    tmp_path: Path,
) -> None:
    for tier in Tier:
        root = tmp_path / tier.value
        with Store.init(
            str(root),
            initial_snapshot=Snapshot(
                tier=tier,
                formal_certificate_digest="f" * 64,
            ),
        ) as store:
            result = next(
                row
                for row in run_artifact_checks(store, store.read_snapshot())
                if row.check_id == mk2.CHECK_FORMAL_CERTIFICATE
            )
        assert result.verdict is Verdict.FAIL
        assert "missing/corrupt formal certificate" in result.detail


def test_standard_tier_cheap_formal_certificate_passes_with_explicit_residual_and_failures_stay_blocking(
    tmp_path: Path,
) -> None:
    tables = encode_formal_tables(emit_tables())
    tables_digest = digest_of(ArtifactKind.FORMAL_TABLES, tables)
    certificate = FormalCertificate(
        checker_version=CHECKER_VERSION,
        tables_digest=tables_digest,
        universes=tuple(
            (universe.name, "a" * 64, 1, 1) for universe in UNIVERSES
        ),
        invariants=tuple(
            (universe.name, invariant_id, True)
            for universe in UNIVERSES
            for invariant_id in (
                universe.invariant_ids
                or tuple(invariant.invariant_id for invariant in INVARIANTS)
            )
        ),
        witnesses=tuple(
            (witness.witness_id, witness.invariant_id, True)
            for witness in WITNESSES
        ),
    )

    def check(certificate_value: FormalCertificate, name: str) -> CheckResult:
        data = encode_formal_certificate(certificate_value)
        certificate_digest = digest_of(ArtifactKind.FORMAL_CERTIFICATE, data)
        root = tmp_path / name
        with Store.init(
            str(root),
            initial_snapshot=Snapshot(
                tier=Tier.STANDARD,
                formal_certificate_digest=certificate_digest,
            ),
        ) as store:
            with store.write_locked():
                store.put_object(ArtifactKind.FORMAL_TABLES, tables)
                store.put_object(ArtifactKind.FORMAL_CERTIFICATE, data)
            return next(
                row
                for row in run_artifact_checks(store, store.read_snapshot())
                if row.check_id == mk2.CHECK_FORMAL_CERTIFICATE
            )

    passed = check(certificate, "valid")
    assert passed.verdict is Verdict.PASS
    for verified in (
        "resolves at its recorded digest and decodes strictly",
        "checker identity is current",
        "recorded tables resolve and equal freshly emitted tables",
        "invariant/witness rows cover the current registries and are all true",
    ):
        assert verified in passed.detail
    for residual in (
        "regenerating the reachable relations",
        "executing M1-M11",
        "witness mutations over them",
        "`vouch-mk2 formal`",
    ):
        assert residual in passed.detail

    stale = check(
        FormalCertificate(
            checker_version="stale-checker",
            tables_digest=certificate.tables_digest,
            universes=certificate.universes,
            invariants=certificate.invariants,
            witnesses=certificate.witnesses,
        ),
        "stale",
    )
    assert stale.verdict is Verdict.FAIL
    assert stale.detail == "checker_version mismatch"

    mismatched = check(
        FormalCertificate(
            checker_version=certificate.checker_version,
            tables_digest="b" * 64,
            universes=certificate.universes,
            invariants=certificate.invariants,
            witnesses=certificate.witnesses,
        ),
        "mismatched",
    )
    assert mismatched.verdict is Verdict.FAIL
    assert "tables_digest mismatch" in mismatched.detail

    dangling_root = tmp_path / "dangling"
    with Store.init(
        str(dangling_root),
        initial_snapshot=Snapshot(
            tier=Tier.STANDARD,
            formal_certificate_digest="f" * 64,
        ),
    ) as store:
        dangling = next(
            row
            for row in run_artifact_checks(store, store.read_snapshot())
            if row.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )
    assert dangling.verdict is Verdict.FAIL
    assert "missing/corrupt formal certificate" in dangling.detail


def test_absent_formal_certificate_cites_subjects_present_at_every_tier(
    tmp_path: Path,
) -> None:
    for tier in Tier:
        root = tmp_path / tier.value
        with Store.init(
            str(root), initial_snapshot=Snapshot(tier=tier)
        ) as store:
            result = next(
                row
                for row in run_artifact_checks(store, store.read_snapshot())
                if row.check_id == mk2.CHECK_FORMAL_CERTIFICATE
            )
        assert result.verdict is Verdict.NOT_APPLICABLE
        assert "subjects-present: formal-certificate" in result.detail


def test_next_names_bind_bundle_and_its_required_digest_placeholder(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    _advance_to_published(root)
    assert _run(
        "prepare-release", "--root", root, "--release-id", "r1", "--includes", "u"
    )[0] == 0
    code, output, _error = _run("next", "--root", root, "--json")
    assert code == 0
    action = next(
        row for row in json.loads(output)["actions"] if row["command"] == "bind-bundle"
    )
    assert action["placeholders"] == ["--inventory-digest"]


def test_context_projection_does_not_hardcode_an_installation_command() -> None:
    from vouch_mk2.projections import agent_context

    context = agent_context(Snapshot(ci_policy=CiPolicy.STRICT))
    assert "pip install" not in context
    assert "`vouch-mk2 generate`" in context
    assert "`specify`" in context
