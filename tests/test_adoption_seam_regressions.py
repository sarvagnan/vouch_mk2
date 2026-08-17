from __future__ import annotations

import argparse
import io
import json
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.checks import run_checks
from vouch_mk2.checks.tier import check_tier_controls
from vouch_mk2.cli import _parser, main
from vouch_mk2.core import CiException, CiPolicy, Snapshot, Tier, Unit, UnitStatus
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    decode_evidence_event,
    decode_snapshot,
    encode_snapshot,
)
from vouch_mk2.verify import run_artifact_checks


def _run(*arguments: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(arguments))
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
    project, root = _init(tmp_path, "--tier", "standard")
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
    assert snapshot.tier is Tier.STANDARD
    assert snapshot.ci_policy is CiPolicy.STRICT
    workflow = (project / ".github/workflows/vouch.yml").read_text()
    assert "python-version: '3.14'" in workflow
    assert "check --ci --root .vouch --project ." in workflow
    assert b"Vouch requires Python 3.14+" in (project / "vouch.lock").read_bytes()


def test_format_three_legacy_policy_defaults_to_ledger_and_new_facts_round_trip() -> None:
    legacy = encode_snapshot(Snapshot())
    assert b"ci_policy" not in legacy and b"ci_exceptions" not in legacy
    assert decode_snapshot(legacy).ci_policy is CiPolicy.LEDGER
    strict = Snapshot(
        units=(Unit("u", "v1", UnitStatus.SPECIFIED),),
        ci_policy=CiPolicy.STRICT,
        ci_exceptions=(CiException("u", "planned work"),),
    )
    encoded = encode_snapshot(strict)
    assert json.loads(encoded)["state_format"] == 3
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
    assert "u (owner approved through Friday)" in output
    assert _run("clear-open-in-ci", "--root", root, "--name", "u")[0] == 0
    assert _run("check", "--root", root, "--ci")[0] == 1


def test_next_bootstrap_and_json_use_the_actual_program_name(tmp_path: Path) -> None:
    _project, root = _init(tmp_path)
    code, output, _error = _run("next", "--root", root, "--json")
    assert code == 0
    action = json.loads(output)["actions"][0]
    assert action["command"] == "specify"
    assert action["argv"][:2] == [Path(sys.argv[0]).name, "specify"]
    assert "--version" in action["placeholders"]


def test_status_json_carries_text_policy_counts_and_exception_reasons(
    tmp_path: Path,
) -> None:
    _project, root = _init(tmp_path)
    assert _run("specify", "--root", root, "--name", "u", "--version", "v1")[0] == 0
    assert _run(
        "mark-open-in-ci", "--root", root, "--name", "u", "--reason", "in flight"
    )[0] == 0
    code, output, _error = _run("status", "--root", root, "--json")
    assert code == 0
    document = json.loads(output)
    assert document["unit_counts"] == {"specified": 1}
    assert document["ci_policy"] == "strict"
    assert document["ci_exceptions"] == [["u", "in flight"]]


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
    assert len(lines) == len(mk2.MK2.tier_requirements)
    assert max(map(len, lines)) < 370
    for requirement in mk2.MK2.tier_requirements:
        assert sum(f"[{requirement.control_id}]" in line for line in lines) == 1


def test_build_lock_makes_nested_output_directories(tmp_path: Path) -> None:
    output = tmp_path / "one" / "two" / "vouch.lock"
    assert _run("build-lock", "--output", str(output))[0] == 0
    assert output.is_file()


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
    assert "vouch-mk2 generate" not in context
    assert "`specify`" in context
