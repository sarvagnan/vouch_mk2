"""Thin command-line transport for structured Vouch checks."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from vouch_mk2.checks import CheckReport, CheckResult, run_checks
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import Store, StoreError
from vouch_mk2.verify import run_artifact_checks


class _Arguments(argparse.Namespace):
    command: str
    root: str
    json_output: bool
    ci: bool


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


def main(argv: Sequence[str] | None = None) -> int:
    namespace = _Arguments(command="", root=".", json_output=False, ci=False)
    parsed = _parser().parse_args(argv, namespace=namespace)
    if not isinstance(parsed, _Arguments):
        raise TypeError("argparse returned an unexpected namespace type")

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
