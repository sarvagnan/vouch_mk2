"""Thin command-line transport for structured Vouch checks."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from vouch_mk2.checks import CheckReport, CheckResult, run_checks
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.projections import next_actions, status_facts
from vouch_mk2.store import Store, StoreError, decode_system_map
from vouch_mk2.verify import (
    emit_context,
    emit_system_map,
    record_formal_certificate,
    run_artifact_checks,
)


class _Arguments(argparse.Namespace):
    command: str
    root: str
    json_output: bool
    ci: bool
    record: bool
    target: str | None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vouch-mk2")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="run structured snapshot checks")
    check.add_argument(
        "--ci",
        action="store_true",
        help=(
            "no-op today; makes CI intent explicit (IMPLEMENTATION-PLAN.md Phase 3)"
        ),
    )
    check.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit one JSON report instead of text",
    )
    check.add_argument(
        "--root",
        default=".",
        help="store root directory (default: current directory)",
    )
    formal = commands.add_parser(
        "formal", help="explore and check the bounded runtime relation"
    )
    formal.add_argument(
        "--root",
        default="",
        help="store root directory (required with --record)",
    )
    formal.add_argument(
        "--record",
        action="store_true",
        help="record the passing formal certificate in the store",
    )
    for name, help_text in (
        ("generate", "generate the canonical system map"),
        ("next", "show binding next actions"),
        ("status", "show aggregate snapshot status facts"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--root",
            default=".",
            help="store root directory (default: current directory)",
        )
    context = commands.add_parser(
        "emit-context", help="emit deterministic agent context"
    )
    context.add_argument(
        "--root",
        default=".",
        help="store root directory (default: current directory)",
    )
    context.add_argument(
        "--target",
        default=None,
        help="optional caller-chosen context target path",
    )
    return parser


def _format_result(result: CheckResult) -> str:
    detail = f"  {result.detail}" if result.detail else ""
    return f"{result.verdict.value.upper():<14} {result.check_id}{detail}"


def _present_text(report: CheckReport) -> None:
    print(f"revision: {report.revision}")
    print("mechanical")
    for result in report.mechanical:
        print(_format_result(result))
    print("organizational (never mechanically provable)")
    for result in report.organizational:
        print(_format_result(result))
    print(f"composed: {report.composed().value.upper()}")


def _result_object(result: CheckResult) -> dict[str, str]:
    return {
        "check_id": result.check_id,
        "verdict": result.verdict.value,
        "detail": result.detail,
    }


def _present_json(report: CheckReport) -> None:
    payload = {
        "revision": report.revision,
        "mechanical": [_result_object(result) for result in report.mechanical],
        "organizational": [
            _result_object(result) for result in report.organizational
        ],
        "composed": report.composed().value,
    }
    print(json.dumps(payload, sort_keys=True))


def _present_formal(run: FormalRun) -> None:
    print(f"states: {run.state_count}")
    print(f"transitions: {run.transition_count}")
    print("invariants")
    for verdict in run.invariants:
        print(f"{'PASS' if verdict.held else 'FAIL':<5} {verdict.invariant_id}")
        for violation in verdict.violations:
            print(f"  {violation}")
    print("witnesses")
    for verdict in run.witnesses:
        status = "DISCRIMINATED" if verdict.discriminated else "NOT-DISCRIMINATED"
        print(
            f"{status:<17} {verdict.witness_id} -> {verdict.invariant_id}"
        )


def _format_counts(rows: tuple[tuple[str, int], ...]) -> str:
    return ",".join(f"{status}={count}" for status, count in rows)


def main(argv: Sequence[str] | None = None) -> int:
    namespace = _Arguments(
        command="",
        root=".",
        json_output=False,
        ci=False,
        record=False,
        target=None,
    )
    parsed = _parser().parse_args(argv, namespace=namespace)
    if not isinstance(parsed, _Arguments):
        raise TypeError("argparse returned an unexpected namespace type")

    if parsed.command == "formal":
        if parsed.record and not parsed.root:
            print("vouch-mk2: formal --record requires --root", file=sys.stderr)
            return 2
        run = run_formal()
        _present_formal(run)
        if parsed.record:
            try:
                with Store.open(parsed.root) as store:
                    digest = record_formal_certificate(store, run)
            except StoreError as error:
                print(f"vouch-mk2: {error}", file=sys.stderr)
                return 2
            print(f"formal certificate: {digest}")
        return 0 if passed(run) else 1

    if parsed.command == "generate":
        try:
            with Store.open(parsed.root) as store:
                data = emit_system_map(store)
            _map, source_digest = decode_system_map(data)
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        print(f"map.json written (source state {source_digest})")
        return 0

    if parsed.command == "emit-context":
        try:
            with Store.open(parsed.root) as store:
                written = emit_context(store, parsed.target)
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        print(written)
        return 0

    if parsed.command in ("next", "status"):
        try:
            with Store.open(parsed.root) as store:
                snapshot = store.read_snapshot()
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        actions = next_actions(snapshot)
        if parsed.command == "next":
            if not actions:
                print("no next actions")
            for action in actions:
                print(
                    f"{action.command}  "
                    f"{action.subject_kind}/{action.subject_id}  {action.reason}"
                )
            return 0
        facts = status_facts(snapshot, actions)
        print(f"unit_counts: {_format_counts(facts.unit_counts)}")
        print(f"deviation_counts: {_format_counts(facts.deviation_counts)}")
        print(f"release_counts: {_format_counts(facts.release_counts)}")
        print(f"evidence_chains: {facts.evidence_chains}")
        print(f"anchored_current: {facts.anchored_current}")
        print(f"anchor_mode: {facts.anchor_mode}")
        recorded = "true" if facts.formal_certificate_recorded else "false"
        print(f"formal_certificate_recorded: {recorded}")
        print(f"next_action_count: {facts.next_action_count}")
        return 0

    try:
        with Store.open(parsed.root) as store:
            snapshot = store.read_snapshot()
            artifact_results = run_artifact_checks(store, snapshot)
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2

    report = run_checks(snapshot, artifact_results=artifact_results)
    if parsed.json_output:
        _present_json(report)
    else:
        _present_text(report)
    return 0 if report.composed() is Verdict.PASS else 1
