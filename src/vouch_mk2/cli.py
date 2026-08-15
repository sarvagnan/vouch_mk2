"""Thin command-line transport for structured Vouch checks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from vouch_mk2.checks import CheckReport, CheckResult, run_checks
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.hook import HookError, edit_target_from_json, hook_message
from vouch_mk2.init import InitError, init_project
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.io import read_regular_nofollow, write_atomic
from vouch_mk2.lock import build_lock, lock_digest, running_lock_bytes
from vouch_mk2.mcp import run_stdio
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
    output: str
    project: str
    lock_path: str | None
    edit_target: str | None


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
    build = commands.add_parser(
        "build-lock", help="build the reproducible vouch.lock zipapp"
    )
    build.add_argument("--output", required=True, help="lock output path")
    self_check = commands.add_parser(
        "self-check",
        help="print the digest of the running vouch.lock; --expect pins it",
    )
    self_check.add_argument(
        "--expect",
        default=None,
        help=(
            "a 64-hex digest, or @PATH to a config.json carrying lock_digest; "
            "exit 1 unless the running lock's digest matches"
        ),
    )
    init = commands.add_parser(
        "init", help="initialize the closed D-7 project file set"
    )
    init.add_argument("--project", required=True, help="target project directory")
    init.add_argument(
        "--lock",
        dest="lock_path",
        default=None,
        help="existing lock to vendor instead of building/copying the running code",
    )
    mcp = commands.add_parser(
        "mcp", help="serve read-only MCP over line-delimited stdio for ONE store"
    )
    mcp.add_argument(
        "--root",
        required=True,
        help="the store root this server is pinned to (tools take no root)",
    )
    hook = commands.add_parser("hook", help="run a warn-only check for an edit target")
    hook.add_argument(
        "--edit-target",
        default=None,
        help="edited file or directory; absent reads tool_input.file_path from stdin JSON",
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


def _expected_lock_digest(spec: str) -> str | None:
    """Resolve ``--expect``: a literal 64-hex digest, or ``@PATH`` naming a
    config.json whose ``lock_digest`` is read (strictly: object, string, 64-hex)."""
    if spec.startswith("@"):
        payload = read_regular_nofollow(spec[1:], "lock config")
        if payload is None:
            return None
        try:
            decoded: object = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(decoded, dict):
            return None
        value = decoded.get("lock_digest")
        spec = value if isinstance(value, str) else ""
    if len(spec) == 64 and all(c in "0123456789abcdef" for c in spec):
        return spec
    return None


def main(argv: Sequence[str] | None = None) -> int:
    namespace = _Arguments(
        command="",
        root=".",
        json_output=False,
        ci=False,
        record=False,
        target=None,
        output="",
        project="",
        lock_path=None,
        edit_target=None,
    )
    parsed = _parser().parse_args(argv, namespace=namespace)
    if not isinstance(parsed, _Arguments):
        raise TypeError("argparse returned an unexpected namespace type")

    if parsed.command == "build-lock":
        try:
            source_root = os.path.dirname(os.path.dirname(__file__))
            payload = build_lock(source_root)
            write_atomic(parsed.output, payload, 0o755, "vouch.lock")
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        print(f"vouch.lock {lock_digest(payload)}")
        return 0

    if parsed.command == "self-check":
        try:
            payload = running_lock_bytes()
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        if payload is None:
            print("not running from a lock")
            return 1
        actual = lock_digest(payload)
        print(actual)
        if parsed.expect is None:
            return 0
        expected = _expected_lock_digest(parsed.expect)
        if expected is None:
            print("vouch-mk2: --expect must be a 64-hex digest or @config.json", file=sys.stderr)
            return 2
        if actual != expected:
            print(f"vouch-mk2: lock digest mismatch: expected {expected}", file=sys.stderr)
            return 1
        return 0

    if parsed.command == "init":
        try:
            if parsed.lock_path is not None:
                payload = read_regular_nofollow(parsed.lock_path, "lock input")
                if payload is None:
                    raise InitError(f"{parsed.lock_path}: lock input does not exist")
            else:
                payload = running_lock_bytes()
                if payload is None:
                    source_root = os.path.dirname(os.path.dirname(__file__))
                    payload = build_lock(source_root)
            report = init_project(parsed.project, payload)
        except (InitError, StoreError) as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        for result in report:
            print(f"{result.status.value} {result.path}")
        return 0

    if parsed.command == "mcp":
        run_stdio(parsed.root)
        return 0

    if parsed.command == "hook":
        try:
            edit_target = parsed.edit_target
            if edit_target is None:
                edit_target = edit_target_from_json(sys.stdin.read())
            if not edit_target:
                print("vouch: no edit target; skipping")
                return 0
            print(hook_message(edit_target))
        except (HookError, StoreError, OSError) as error:
            print(f"vouch: hook error: {error}")
        return 0

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
