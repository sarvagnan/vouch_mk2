"""Thin command-line transport for structured Vouch checks."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from enum import Enum
from typing import Any, Callable, cast, get_args, get_origin, get_type_hints

from vouch_mk2.checks import CheckReport, CheckResult, run_checks
from vouch_mk2.bundle import (
    build_inventory,
    build_pack_inventory,
    pack_test_set_digest,
)
from vouch_mk2.core import (
    Accepted,
    AnchorMode,
    Command,
    EvidenceEvent,
    EvidenceRecorded,
    EvidenceKind,
    EvidenceOutcome,
    FormalCertificateRecorded,
    LaunchCriteria,
    RecordEvidence,
    RecordFormalCertificate,
    Refused,
    Snapshot,
    Tier,
    UnitRef,
    decide,
)
from vouch_mk2.core import commands as command_module
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.hook import HookError, edit_target_from_json, hook_message
from vouch_mk2.init import InitError, init_project
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.io import (
    read_regular_nofollow,
    write_atomic,
    write_atomic_with_parents,
)
from vouch_mk2.lock import build_lock, lock_digest, running_lock_bytes
from vouch_mk2.migrate import (
    MigrationRefused,
    _read_config_anchor,
    _read_config_tier,
    apply_migration,
    plan_migration,
    read_ledger,
    render_diff,
    validate_candidate,
)
from vouch_mk2.mcp import run_stdio
from vouch_mk2.projections import NextAction, next_actions, status_facts
from vouch_mk2.store import (
    CURRENT_STATE_FORMAT,
    DecodeError,
    Store,
    StoreError,
    decode_system_map,
)
from vouch_mk2.store.upgrade import UpgradeRefused
from vouch_mk2.store.event_log import ci_run_id, commit_events, default_actor
from vouch_mk2.store.merge import merge_store_roots
from vouch_mk2.verify import (
    ProjectionRegenerationError,
    RefusedError,
    UpgradeResult,
    emit_context,
    emit_system_map,
    record_evidence,
    record_formal_certificate,
    run_artifact_checks,
    upgrade_store,
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
    pack_file: list[str]
    pack_test_file: list[str]
    reason: str
    lock_path: str | None
    edit_target: str | None
    ledger: str
    anchor_mode: str | None
    config: str | None
    drop_qualified: bool
    approve: bool
    dry_run: bool
    was_published: list[str]
    never_published: list[str]
    command_argv: list[str]
    tree: str
    exclude: list[str]
    tier: str
    actor: str
    other: str


def _parser(program_name: str = "vouch-mk2") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=program_name)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="run structured snapshot checks")
    check.add_argument(
        "--ci",
        action="store_true",
        help=(
            "enforce and name the store's recorded CI policy"
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
    check.add_argument(
        "--project",
        default="",
        help=(
            "project root for acceptance-pack file verification (default: parent "
            "of --root when its basename is .vouch)"
        ),
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
        "--actor",
        default=default_actor(),
        help="recorded actor claim (default: OS user)",
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
        if name in {"next", "status"}:
            command.add_argument(
                "--json",
                dest="json_output",
                action="store_true",
                help="emit structured invocations instead of text",
            )
    bundle_inventory = commands.add_parser(
        "bundle-inventory",
        help="build and place a content-addressed bundle inventory",
    )
    bundle_inventory.add_argument("--root", required=True, help="store root directory")
    bundle_inventory.add_argument(
        "--tree", required=True, help="directory tree to inventory"
    )
    bundle_inventory.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="exclude a path pattern (repeatable)",
    )
    bundle_inventory.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit one JSON result instead of text",
    )
    upgrade = commands.add_parser(
        "upgrade", help="upgrade state.json to the current state format"
    )
    upgrade.add_argument(
        "--root",
        default=".",
        help="store root directory (default: current directory)",
    )
    upgrade.add_argument(
        "--actor",
        default=default_actor(),
        help="recorded actor claim for the pre-log checkpoint (default: OS user)",
    )
    upgrade.add_argument(
        "--dry-run",
        action="store_true",
        help="show required format steps without changing the store",
    )
    upgrade.add_argument(
        "--was-published",
        action="append",
        default=[],
        metavar="NAME@V",
        help="assert that an ambiguous legacy unit was previously published",
    )
    upgrade.add_argument(
        "--never-published",
        action="append",
        default=[],
        metavar="NAME@V",
        help="assert that an ambiguous legacy unit was never published",
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
        "--tier",
        choices=tuple(tier.value for tier in Tier),
        default=Tier.LIGHTWEIGHT.value,
        help="initial assurance tier (default: lightweight)",
    )
    init.add_argument(
        "--lock",
        dest="lock_path",
        default=None,
        help="existing lock to vendor instead of building/copying the running code",
    )
    init.add_argument(
        "--actor",
        default=default_actor(),
        help="recorded actor claim (default: OS user)",
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
    migrate = commands.add_parser(
        "migrate", help="plan or approve a strict Vouch 0.6 ledger migration"
    )
    migrate.add_argument("--ledger", required=True, help="0.6 ledger JSON path")
    migrate.add_argument(
        "--anchor-mode",
        choices=("none", "ci"),
        default=None,
        help="mk2 anchor mode (overrides --config)",
    )
    migrate.add_argument(
        "--actor",
        default=default_actor(),
        help="recorded actor claim (default: OS user)",
    )
    migrate.add_argument(
        "--config",
        default=None,
        help="optional 0.6 config.json carrying anchor.mode and its legacy tier",
    )
    migrate.add_argument(
        "--drop-qualified",
        action="store_true",
        help="report and omit unrepresentable qualified releases",
    )
    migrate.add_argument(
        "--approve",
        action="store_true",
        help="write the validated candidate to a fresh target/.vouch",
    )
    migrate.add_argument(
        "--target",
        default=None,
        help="existing target project directory (required with --approve)",
    )
    merge = commands.add_parser(
        "merge", help="merge another event-log branch into this store by replay"
    )
    merge.add_argument("--root", required=True, help="our store root directory")
    merge.add_argument("--other", required=True, help="other store root directory")
    merge.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit one JSON result instead of text",
    )
    merge.add_argument(
        "--actor",
        default=default_actor(),
        help="recorded actor claim (default: OS user)",
    )
    _add_lifecycle_parsers(commands)
    return parser


def _format_result(result: CheckResult) -> str:
    details = result.detail.splitlines() or [""]
    return "\n".join(
        f"{result.verdict.value.upper():<14} {result.check_id}"
        + (f"  {detail}" if detail else "")
        for detail in details
    )


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
    for universe in run.universes:
        print(f"universe: {universe.universe_name}")
        print(f"states: {universe.state_count}")
        print(f"transitions: {universe.transition_count}")
        print("invariants")
        for verdict in universe.invariants:
            print(f"{'PASS' if verdict.held else 'FAIL':<5} {verdict.invariant_id}")
            if verdict.detail:
                print(f"  {verdict.detail}")
            for violation in verdict.violations:
                print(f"  {violation}")
    print("witnesses")
    for verdict in run.witnesses:
        status = "DISCRIMINATED" if verdict.discriminated else "NOT-DISCRIMINATED"
        print(
            f"{status:<17} {verdict.witness_id} -> {verdict.invariant_id}"
        )


type _LifecycleHandler = Callable[[argparse.Namespace], int]
type _CommandClass = type[Any]


def _kebab_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower().replace("_", "-")


def _unit_ref(value: str) -> UnitRef:
    if value.count("@") != 1:
        raise argparse.ArgumentTypeError("expected NAME@VERSION")
    name, version = value.split("@", 1)
    if not name or not version:
        raise argparse.ArgumentTypeError("expected NAME@VERSION")
    return UnitRef(name, version)


def _command_classes() -> tuple[_CommandClass, ...]:
    reflected = {
        value
        for value in vars(command_module).values()
        if isinstance(value, type)
        and dataclasses.is_dataclass(value)
        and hasattr(value, "ir_command")
    }
    return tuple(sorted(reflected, key=lambda value: cast(str, value.ir_command)))


_LIFECYCLE_COMMANDS: dict[str, _CommandClass] = {
    cast(str, command_type.ir_command): command_type
    for command_type in _command_classes()
}


def _add_lifecycle_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", required=True, help="store root directory")
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit one JSON result instead of text",
    )
    parser.add_argument(
        "--actor",
        default=default_actor(),
        help="recorded actor claim (default: OS user)",
    )


def _is_string_tuple(annotation: object) -> bool:
    return get_origin(annotation) is tuple and get_args(annotation) == (str, Ellipsis)


def _add_reflected_field(
    parser: argparse.ArgumentParser,
    field: dataclasses.Field[Any],
    annotation: object,
) -> None:
    flag = "--" + field.name.replace("_", "-")
    required = (
        field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    )
    if annotation is str:
        parser.add_argument(flag, required=required)
        return
    if _is_string_tuple(annotation):
        parser.add_argument(flag, action="append", required=required, default=[])
        return
    if annotation is UnitRef:
        parser.add_argument(flag, type=_unit_ref, required=required, metavar="NAME@VERSION")
        return
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        parser.add_argument(
            flag,
            choices=tuple(member.value for member in annotation),
            required=required,
        )
        return
    if annotation is LaunchCriteria:
        parser.add_argument("--launch-criteria", action="append", required=True)
        parser.add_argument("--verification-window", required=True)
        parser.add_argument("--rollback-trigger", action="append", required=True)
        parser.add_argument("--go-no-go-authority", required=True)
        return
    raise TypeError(
        f"unsupported lifecycle field {field.name!r} on "
        f"{field.type!r} ({annotation!r})"
    )


def _add_lifecycle_parsers(commands: Any) -> None:
    for verb, command_type in _LIFECYCLE_COMMANDS.items():
        documentation = (command_type.__doc__ or "").splitlines()[0]
        if not documentation or documentation.startswith(command_type.__name__ + "("):
            documentation = f"Record the {verb.replace('-', ' ')} command"
        parser = commands.add_parser(
            verb,
            help=documentation,
        )
        _add_lifecycle_common(parser)
        if command_type in {
            command_module.FreezeAcceptance,
            command_module.BindPack,
            command_module.RebindPack,
        }:
            parser.add_argument("--name", required=True)
            if command_type is command_module.FreezeAcceptance:
                parser.add_argument("--pack-version", required=True)
            if command_type is command_module.RebindPack:
                parser.add_argument("--reason", required=True)
            parser.add_argument(
                "--project",
                default=".",
                help="project root for --pack-file paths (default: current directory)",
            )
            parser.add_argument(
                "--pack-file",
                action="append",
                default=[],
                help="project-relative acceptance spec file (repeatable)",
            )
            parser.add_argument(
                "--pack-test-file",
                action="append",
                default=[],
                help="project-relative executable acceptance test file (repeatable)",
            )
            continue
        if command_type is command_module.RecordEvidence:
            parser.usage = (
                "%(prog)s [-h] --root ROOT [--json] [--actor ACTOR] --unit NAME@VERSION "
                "--kind {red,green,refactor} -- <argv...>"
            )
            parser.epilog = (
                "the recorded outcome is the exit status of a process this verb spawned."
            )
            parser.add_argument("--unit", type=_unit_ref, required=True, metavar="NAME@VERSION")
            parser.add_argument(
                "--kind",
                choices=tuple(kind.value for kind in EvidenceKind),
                required=True,
            )
            continue
        if command_type is command_module.RecordFormalCertificate:
            continue
        annotations = get_type_hints(command_type)
        for field in dataclasses.fields(command_type):
            if (
                command_type is command_module.AdvanceAnchor
                and field.name == "run_id"
            ):
                continue
            _add_reflected_field(parser, field, annotations[field.name])


def _deduplicated(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _command_kwargs(
    command_type: _CommandClass, namespace: argparse.Namespace
) -> dict[str, object]:
    annotations = get_type_hints(command_type)
    values: dict[str, object] = {}
    for field in dataclasses.fields(command_type):
        if (
            command_type is command_module.AdvanceAnchor
            and field.name == "run_id"
        ):
            continue
        annotation = annotations[field.name]
        if annotation is LaunchCriteria:
            values[field.name] = LaunchCriteria(
                launch_criteria=_deduplicated(namespace.launch_criteria),
                verification_window=namespace.verification_window,
                rollback_triggers=_deduplicated(namespace.rollback_trigger),
                go_no_go_authority=namespace.go_no_go_authority,
            )
            continue
        value = getattr(namespace, field.name)
        if _is_string_tuple(annotation):
            value = _deduplicated(value)
        elif isinstance(annotation, type) and issubclass(annotation, Enum):
            value = annotation(value)
        values[field.name] = value
    return values


def _wire_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _wire_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, tuple | list):
        return [_wire_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _wire_value(item) for key, item in value.items()}
    return value


def _event_object(event: object) -> dict[str, object]:
    fields = cast(dict[str, object], _wire_value(event))
    if "event" in fields:
        fields["evidence"] = fields.pop("event")
    return {"event": _kebab_case(type(event).__name__), **fields}


def _present_refusal(
    json_output: bool, refusal: Refused | RefusedError
) -> int:
    if json_output:
        print(
            json.dumps(
                {
                    "accepted": False,
                    "violations": [
                        {"code": violation.code, "detail": violation.detail}
                        for violation in refusal.violations
                    ],
                },
                sort_keys=True,
            )
        )
    else:
        for violation in refusal.violations:
            print(f"{violation.code}: {violation.detail}", file=sys.stderr)
    return 2


def _present_accepted_json(revision: int, events: Sequence[object]) -> None:
    print(
        json.dumps(
            {
                "accepted": True,
                "revision": revision,
                "events": [_event_object(event) for event in events],
            },
            sort_keys=True,
        )
    )


def _accepted_text(command: Command, snapshot: Snapshot) -> str:
    name = getattr(command, "name", None)
    if isinstance(name, str):
        unit = snapshot.unit(name)
        if unit is not None:
            return f"{unit.ref().label()}: {unit.status.value}"
    deviation_id = getattr(command, "deviation_id", None)
    if isinstance(deviation_id, str):
        deviation = snapshot.deviation(deviation_id)
        if deviation is not None:
            return f"deviation {deviation_id}: {deviation.status.value}"
    release_id = getattr(command, "release_id", None)
    if isinstance(release_id, str):
        release = snapshot.release(release_id)
        if release is not None:
            return f"release {release_id}: {release.state.value}"
    unit_name = getattr(command, "unit_name", None)
    if isinstance(unit_name, str):
        head = snapshot.evidence_head(unit_name)
        if head is not None:
            return f"{unit_name}: anchor {head.anchored[:16]}"
    tier = getattr(command, "tier", None)
    if isinstance(tier, Enum):
        return f"tier: {tier.value}"
    policy = getattr(command, "policy", None)
    if isinstance(policy, Enum):
        return f"ci policy: {policy.value}"
    mode = getattr(command, "mode", None)
    if isinstance(mode, Enum):
        return f"anchor mode: {mode.value}"
    return f"{cast(str, type(command).ir_command)}: recorded"


def _generic_lifecycle_handler(
    command_type: _CommandClass, namespace: argparse.Namespace
) -> int:
    constructor = cast(Callable[..., Command], command_type)
    run_id = ci_run_id()
    command_kwargs = _command_kwargs(command_type, namespace)
    if command_type is command_module.AdvanceAnchor:
        command_kwargs["run_id"] = run_id
    command = constructor(**command_kwargs)
    try:
        with Store.open(namespace.root) as store:
            with store.write_locked():
                snapshot = store.read_snapshot()
                decision = decide(snapshot, command)
                if isinstance(decision, Refused):
                    return _present_refusal(namespace.json_output, decision)
                next_snapshot = commit_events(
                    store,
                    snapshot,
                    decision.events,
                    actor=namespace.actor,
                    run_id=run_id,
                )
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    if namespace.json_output:
        _present_accepted_json(next_snapshot.revision, decision.events)
    else:
        print(_accepted_text(command, next_snapshot))
    return 0


def _make_lifecycle_handler(command_type: _CommandClass) -> _LifecycleHandler:
    def handler(namespace: argparse.Namespace) -> int:
        return _generic_lifecycle_handler(command_type, namespace)

    return handler


def _pack_binding_handler(
    command_type: _CommandClass, namespace: argparse.Namespace
) -> int:
    from vouch_mk2.store import (
        ArtifactKind,
        decode_pack_inventory,
        digest_of,
        encode_pack_inventory,
    )

    data: bytes | None = None
    digest = ""
    inventory = None
    command: Command | None
    try:
        pack_files = tuple(cast(list[str], namespace.pack_file))
        pack_test_files = tuple(cast(list[str], namespace.pack_test_file))
        if pack_files or pack_test_files:
            inventory = build_pack_inventory(
                namespace.project, pack_files, pack_test_files
            )
            data = encode_pack_inventory(inventory)
            digest = digest_of(ArtifactKind.ACCEPTANCE_PACK, data)
            test_set_digest = pack_test_set_digest(inventory)
        else:
            test_set_digest = ""
            if command_type in {
                command_module.BindPack,
                command_module.RebindPack,
            }:
                raise StoreError(
                    f"{command_type.ir_command} refuses an empty acceptance pack inventory"
                )
        if command_type is command_module.FreezeAcceptance:
            command = command_module.FreezeAcceptance(
                name=namespace.name,
                pack_version=namespace.pack_version,
                pack_digest=digest,
                test_set_digest=test_set_digest,
            )
        elif command_type is command_module.BindPack:
            command = command_module.BindPack(
                name=namespace.name,
                pack_digest=digest,
                test_set_digest=test_set_digest,
            )
        elif command_type is command_module.RebindPack:
            command = None
        else:
            raise AssertionError("pack binding handler received another command")
        with Store.open(namespace.root) as store:
            with store.write_locked():
                snapshot = store.read_snapshot()
                if command_type is command_module.RebindPack:
                    unit = snapshot.unit(namespace.name)
                    old_inventory = (
                        None
                        if unit is None or not unit.acceptance_pack_digest
                        else decode_pack_inventory(
                            store.get_object(
                                ArtifactKind.ACCEPTANCE_PACK,
                                unit.acceptance_pack_digest,
                            )
                        )
                    )
                    command = command_module.RebindPack(
                        name=namespace.name,
                        pack_digest=digest,
                        test_set_digest=test_set_digest,
                        reason=namespace.reason,
                        old_inventory=old_inventory,
                        new_inventory=inventory,
                    )
                assert command is not None
                decision = decide(snapshot, command)
                if isinstance(decision, Refused):
                    return _present_refusal(namespace.json_output, decision)
                objects = (
                    ()
                    if data is None
                    else ((ArtifactKind.ACCEPTANCE_PACK, data),)
                )
                next_snapshot = commit_events(
                    store,
                    snapshot,
                    decision.events,
                    actor=namespace.actor,
                    run_id=ci_run_id(),
                    objects=objects,
                )
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    if namespace.json_output:
        _present_accepted_json(next_snapshot.revision, decision.events)
    else:
        print(_accepted_text(command, next_snapshot))
    return 0


def _make_pack_binding_handler(command_type: _CommandClass) -> _LifecycleHandler:
    def handler(namespace: argparse.Namespace) -> int:
        return _pack_binding_handler(command_type, namespace)

    return handler


_SECRET_OPTIONS = frozenset(
    {"--token", "--password", "--secret", "--api-key", "--api_key"}
)
_OPAQUE_SECRET = re.compile(r"(?=.{24,}$)(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9_-]+")


def _secret_looking_command(command_argv: Sequence[str]) -> bool:
    for index, argument in enumerate(command_argv):
        lowered = argument.lower()
        option = lowered.partition("=")[0]
        if option in _SECRET_OPTIONS:
            return True
        if lowered.startswith("aws_secret"):
            return True
        if (
            index > 0
            and _OPAQUE_SECRET.fullmatch(argument) is not None
            and re.fullmatch(r"[0-9a-fA-F]+", argument) is None
        ):
            return True
    return False


def _record_evidence_handler(namespace: argparse.Namespace) -> int:
    command_argv = cast(list[str], namespace.command_argv)
    if not command_argv:
        print("vouch-mk2: record-evidence requires -- <argv...>", file=sys.stderr)
        return 2
    if _secret_looking_command(command_argv):
        print(
            "vouch-mk2: warning: command line looks secret-bearing; it will be "
            "stored verbatim in an immutable evidence object",
            file=sys.stderr,
        )
    try:
        with Store.open(namespace.root, read_only=True) as store:
            store.read_snapshot()
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    try:
        completed = subprocess.run(
            command_argv,
            check=False,
            stdout=subprocess.PIPE if namespace.json_output else None,
        )
    except OSError as error:
        print(
            f"vouch-mk2: cannot execute {command_argv[0]!r}: {error}",
            file=sys.stderr,
        )
        return 2
    if namespace.json_output and completed.stdout:
        child_stdout = cast(bytes, completed.stdout)
        stream = getattr(sys.stderr, "buffer", None)
        if stream is None:
            sys.stderr.write(child_stdout.decode("utf-8", errors="replace"))
            sys.stderr.flush()
        else:
            stream.write(child_stdout)
            stream.flush()
    if completed.returncode < 0:
        print(
            "vouch-mk2: record-evidence refused: child produced no verdict "
            f"(terminated by signal {-completed.returncode})",
            file=sys.stderr,
        )
        return 2
    outcome = (
        EvidenceOutcome.TESTS_PASSED
        if completed.returncode == 0
        else EvidenceOutcome.TESTS_FAILED
    )
    kind = EvidenceKind(namespace.kind)
    unit = cast(UnitRef, namespace.unit)
    try:
        with Store.open(namespace.root) as store:
            with store.write_locked():
                snapshot = store.read_snapshot()
                head = snapshot.evidence_head_ref(unit)
                sequence = 1 if head is None else head.head_sequence + 1
                parent = "GENESIS" if head is None else head.head
                resolved = snapshot.unit_ref(unit)
                event = EvidenceEvent(
                    unit=unit,
                    sequence=sequence,
                    parent=parent,
                    kind=kind,
                    command_line=shlex.join(command_argv),
                    outcome=outcome,
                    recorded_at=datetime.datetime.now(datetime.UTC).isoformat(
                        timespec="seconds"
                    ),
                    pack_digest=(
                        ""
                        if resolved is None
                        else resolved.acceptance_test_set_digest
                    ),
                )
                digest = record_evidence(
                    store,
                    event,
                    actor=namespace.actor,
                    run_id=ci_run_id(),
                )
                revision = store.read_snapshot().revision
    except RefusedError as error:
        return _present_refusal(namespace.json_output, error)
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    recorded = EvidenceRecorded(event=event, event_digest=digest)
    if namespace.json_output:
        _present_accepted_json(revision, (recorded,))
    else:
        print(
            f"[evidence] recorded {kind.value} #{sequence} for "
            f"{unit.label()}: {digest[:16]}"
        )
    return 0


def _run_formal_command(
    root: str,
    *,
    record: bool,
    json_output: bool = False,
    actor: str | None = None,
) -> int:
    run = run_formal()
    if not json_output:
        _present_formal(run)
    if not passed(run):
        return 1
    if not record:
        return 0
    try:
        with Store.open(root) as store:
            with store.write_locked():
                digest = record_formal_certificate(
                    store,
                    run,
                    actor=default_actor() if actor is None else actor,
                    run_id=ci_run_id(),
                )
                revision = store.read_snapshot().revision
    except RefusedError as error:
        return _present_refusal(json_output, error)
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    if json_output:
        event = FormalCertificateRecorded(certificate_digest=digest)
        _present_accepted_json(revision, (event,))
    else:
        print(f"formal certificate: {digest}")
    return 0


def _record_formal_certificate_handler(namespace: argparse.Namespace) -> int:
    return _run_formal_command(
        namespace.root,
        record=True,
        json_output=namespace.json_output,
        actor=namespace.actor,
    )


def _merge_handler(namespace: argparse.Namespace) -> int:
    try:
        result = merge_store_roots(
            namespace.root,
            namespace.other,
            actor=namespace.actor,
            run_id=ci_run_id(),
        )
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    if isinstance(result, Refused):
        return _present_refusal(namespace.json_output, result)
    if namespace.json_output:
        print(
            json.dumps(
                {
                    "accepted": True,
                    "mode": result.mode,
                    "replayed_records": result.replayed_records,
                    "revision": result.snapshot.revision,
                },
                sort_keys=True,
            )
        )
    else:
        print(
            f"merge: {result.mode}; revision {result.snapshot.revision}; "
            f"replayed {result.replayed_records} source record(s)"
        )
    return 0


LIFECYCLE_VERBS: dict[str, _LifecycleHandler] = {
    verb: (
        _record_evidence_handler
        if command_type is command_module.RecordEvidence
        else _record_formal_certificate_handler
        if command_type is command_module.RecordFormalCertificate
        else _make_pack_binding_handler(command_type)
        if command_type in {
            command_module.FreezeAcceptance,
            command_module.BindPack,
            command_module.RebindPack,
        }
        else _make_lifecycle_handler(command_type)
    )
    for verb, command_type in _LIFECYCLE_COMMANDS.items()
}


_SUBJECT_FIELDS = {
    "unit": "name",
    "deviation": "deviation_id",
    "release": "release_id",
    "evidence": "unit_name",
}


@dataclasses.dataclass(frozen=True, slots=True)
class _Invocation:
    argv: tuple[str, ...]
    placeholders: tuple[str, ...]
    placeholder_indexes: frozenset[int]


# Fields whose specialized parser replaces the reflected flag with a different
# required CLI input. Kept next to the renderer so a new special case cannot
# drift silently: tests/test_lifecycle_cli_regressions.py parses every rendered
# invocation with the real parser.
_SPECIALIZED_REQUIRED: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("bind-pack", "pack_digest"): (("--pack-file", "<PACK_FILE>"),),
    ("rebind-pack", "pack_digest"): (("--pack-file", "<PACK_FILE>"),),
}


def _required_field_arguments(
    field: dataclasses.Field[Any], annotation: object, command: str = ""
) -> tuple[tuple[str, str], ...]:
    specialized = _SPECIALIZED_REQUIRED.get((command, field.name))
    if specialized is not None:
        return specialized
    if annotation is LaunchCriteria:
        return (
            ("--launch-criteria", "<LAUNCH_CRITERIA>"),
            ("--verification-window", "<VERIFICATION_WINDOW>"),
            ("--rollback-trigger", "<ROLLBACK_TRIGGER>"),
            ("--go-no-go-authority", "<GO_NO_GO_AUTHORITY>"),
        )
    return (
        (
            "--" + field.name.replace("_", "-"),
            f"<{field.name.upper()}>",
        ),
    )


def _structured_invocation(
    action: NextAction, root: str, program_name: str = "vouch-mk2"
) -> _Invocation:
    command_type = _LIFECYCLE_COMMANDS.get(action.command)
    if command_type is None:
        raise ValueError(f"next action names unknown command {action.command!r}")
    subject_field = _SUBJECT_FIELDS.get(action.subject_kind)
    if subject_field is None:
        raise ValueError(
            f"next action has unknown subject kind {action.subject_kind!r}"
        )
    fields = dataclasses.fields(command_type)
    if subject_field not in {field.name for field in fields}:
        raise ValueError(
            f"next action subject {action.subject_kind!r} does not bind "
            f"{action.command!r}"
        )

    subject_flag = "--" + subject_field.replace("_", "-")
    argv = [
        program_name,
        action.command,
        "--root",
        root,
        subject_flag,
        action.subject_id,
    ]
    placeholders: list[str] = []
    placeholder_indexes: set[int] = set()
    annotations = get_type_hints(command_type)
    for field in fields:
        required = (
            field.default is dataclasses.MISSING
            and field.default_factory is dataclasses.MISSING
        )
        if field.name == subject_field or not required:
            continue
        for flag, placeholder in _required_field_arguments(
            field, annotations[field.name], action.command
        ):
            argv.extend((flag, placeholder))
            placeholders.append(flag)
            placeholder_indexes.add(len(argv) - 1)
    return _Invocation(
        argv=tuple(argv),
        placeholders=tuple(placeholders),
        placeholder_indexes=frozenset(placeholder_indexes),
    )


def invocation_for(
    action: NextAction, root: str, program_name: str = "vouch-mk2"
) -> str:
    """Render one complete, shell-safe next-action invocation."""
    invocation = _structured_invocation(action, root, program_name)
    command = " ".join(
        token if index in invocation.placeholder_indexes else shlex.quote(token)
        for index, token in enumerate(invocation.argv)
    )
    return f"{command}  # {action.reason}"


def _next_action_object(
    action: NextAction, root: str, program_name: str = "vouch-mk2"
) -> dict[str, object]:
    invocation = _structured_invocation(action, root, program_name)
    return {
        "command": action.command,
        "subject_kind": action.subject_kind,
        "subject_id": action.subject_id,
        "reason": action.reason,
        "argv": list(invocation.argv),
        "placeholders": list(invocation.placeholders),
    }


def _bundle_inventory_handler(namespace: argparse.Namespace) -> int:
    from vouch_mk2.store import ArtifactKind, encode_bundle_inventory

    try:
        inventory = build_inventory(
            namespace.tree, tuple(cast(list[str], namespace.exclude))
        )
        data = encode_bundle_inventory(inventory)
        with Store.open(namespace.root) as store:
            with store.write_locked():
                digest = store.put_object(ArtifactKind.BUNDLE_INVENTORY, data)
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2
    if namespace.json_output:
        print(
            json.dumps(
                {"accepted": True, "inventory_digest": digest},
                sort_keys=True,
            )
        )
    else:
        print(f"bundle inventory {digest}")
    return 0


def _format_counts(rows: tuple[tuple[str, int], ...]) -> str:
    return ",".join(f"{status}={count}" for status, count in rows)


def _status_object(facts: object) -> dict[str, object]:
    from vouch_mk2.projections import StatusFacts

    if not isinstance(facts, StatusFacts):
        raise TypeError("status projection returned an unexpected value")
    return {
        "unit_counts": dict(facts.unit_counts),
        "deviation_counts": dict(facts.deviation_counts),
        "release_counts": dict(facts.release_counts),
        "evidence_chains": facts.evidence_chains,
        "anchored_current": facts.anchored_current,
        "anchor_run_claims": [list(row) for row in facts.anchor_run_claims],
        "anchor_mode": facts.anchor_mode,
        "tier": facts.tier,
        "ci_policy": facts.ci_policy,
        "ci_exceptions": [list(row) for row in facts.ci_exceptions],
        "formal_certificate_recorded": facts.formal_certificate_recorded,
        "next_action_count": facts.next_action_count,
        "unbound_pack_units": list(facts.unbound_pack_units),
        "late_bound_pack_units": list(facts.late_bound_pack_units),
        "rebound_pack_units": list(facts.rebound_pack_units),
        "pack_rebounds": list(facts.pack_rebounds),
    }


def _present_upgrade(result: UpgradeResult, *, dry_run: bool) -> None:
    prefix = "dry-run: " if dry_run else ""
    if not result.steps:
        suffix = "" if dry_run else "; projections regenerated"
        print(f"{prefix}already at format {CURRENT_STATE_FORMAT}{suffix}")
    else:
        for from_format, to_format in result.steps:
            print(f"{prefix}{from_format} -> {to_format}")
        for ref, was_published in result.assertions:
            assertion = "was-published" if was_published else "never-published"
            print(f"{prefix}assert {assertion} {ref}")
        print(f"{prefix}state format: {CURRENT_STATE_FORMAT}")
    action = "would rebuild" if dry_run else "rebuilt"
    print(
        f"{prefix}{action} {result.evidence_head_summaries_rebuilt} "
        "evidence-head summaries"
    )
    if dry_run:
        print("dry-run: no changes written")


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
    process_invocation = argv is None
    arguments = list(sys.argv[1:] if process_invocation else argv)
    program_name = (
        os.path.basename(sys.argv[0]) or "vouch-mk2"
        if process_invocation
        else "vouch-mk2"
    )
    parser = _parser(program_name)
    evidence_argv: list[str] | None = None
    if (
        arguments
        and arguments[0] == command_module.RecordEvidence.ir_command
    ):
        try:
            separator = arguments.index("--")
        except ValueError:
            if "--help" not in arguments:
                parser.error("record-evidence requires -- <argv...>")
        else:
            lifecycle_arguments = arguments[:separator]
            child_arguments = arguments[separator + 1 :]
            arguments = lifecycle_arguments
            if "--help" not in lifecycle_arguments:
                evidence_argv = child_arguments
                if not evidence_argv:
                    parser.error("record-evidence requires -- <argv...>")
    namespace = _Arguments(
        command="",
        root=".",
        json_output=False,
        ci=False,
        record=False,
        target=None,
        output="",
        project="",
        pack_file=[],
        pack_test_file=[],
        reason="",
        lock_path=None,
        edit_target=None,
        ledger="",
        anchor_mode=None,
        config=None,
        drop_qualified=False,
        approve=False,
        dry_run=False,
        was_published=[],
        never_published=[],
        command_argv=[],
        tree="",
        exclude=[],
        tier=Tier.LIGHTWEIGHT.value,
        actor=default_actor(),
        other="",
    )
    parsed = parser.parse_args(arguments, namespace=namespace)
    if not isinstance(parsed, _Arguments):
        raise TypeError("argparse returned an unexpected namespace type")
    if evidence_argv is not None:
        parsed.command_argv = evidence_argv

    if parsed.command == "merge":
        return _merge_handler(parsed)

    lifecycle_handler = LIFECYCLE_VERBS.get(parsed.command)
    if lifecycle_handler is not None:
        return lifecycle_handler(parsed)

    if parsed.command == "bundle-inventory":
        return _bundle_inventory_handler(parsed)

    if parsed.command == "build-lock":
        try:
            source_root = os.path.dirname(os.path.dirname(__file__))
            payload = build_lock(source_root)
            write_atomic_with_parents(
                parsed.output, payload, 0o755, "vouch.lock"
            )
        except (OSError, StoreError) as error:
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
            report = init_project(
                parsed.project,
                payload,
                tier=Tier(parsed.tier),
                actor=parsed.actor,
            )
        except (InitError, StoreError) as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        for result in report:
            print(f"{result.status.value} {result.path}")
        print("Vouch requires Python 3.14+")
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

    if parsed.command == "migrate":
        try:
            ledger_data = read_regular_nofollow(parsed.ledger, "0.6 ledger")
            if ledger_data is None:
                raise StoreError(f"{parsed.ledger}: 0.6 ledger does not exist")
            document = read_ledger(ledger_data)
            config_anchor: AnchorMode | None = None
            config_tier = None
            if parsed.config is not None:
                config_data = read_regular_nofollow(parsed.config, "0.6 config")
                if config_data is None:
                    raise StoreError(f"{parsed.config}: 0.6 config does not exist")
                config_anchor = _read_config_anchor(config_data)
                config_tier = _read_config_tier(config_data)
            if parsed.anchor_mode is not None:
                anchor_mode = AnchorMode(parsed.anchor_mode)
            elif config_anchor is not None:
                anchor_mode = config_anchor
            else:
                anchor_mode = AnchorMode.NONE
            plan = plan_migration(
                document,
                anchor_mode,
                config_tier=config_tier,
                drop_qualified=parsed.drop_qualified,
            )
            validation = validate_candidate(plan.candidate)
        except DecodeError as error:
            print(f"vouch-mk2: migration decode error: {error}", file=sys.stderr)
            return 1
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2

        print(render_diff(plan, validation), end="")
        failed = any(row.verdict is Verdict.FAIL for row in validation)
        if plan.refusals:
            return 1
        if not parsed.approve:
            return 1 if failed else 0
        if parsed.target is None:
            print("vouch-mk2: migrate --approve requires --target", file=sys.stderr)
            return 2
        try:
            report_path = apply_migration(
                plan,
                parsed.target,
                None,
                actor=parsed.actor,
            )
        except MigrationRefused as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 1
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        print(report_path)
        return 1 if failed else 0

    if parsed.command == "formal":
        if parsed.record and not parsed.root:
            print("vouch-mk2: formal --record requires --root", file=sys.stderr)
            return 2
        return _run_formal_command(
            parsed.root,
            record=parsed.record,
            actor=parsed.actor,
        )

    if parsed.command == "upgrade":
        was_published = set(parsed.was_published)
        never_published = set(parsed.never_published)
        conflicts = sorted(was_published & never_published)
        if conflicts:
            parser.error(
                "conflicting --was-published and --never-published assertion(s): "
                + ", ".join(conflicts)
            )
        assertions = {
            **{ref: True for ref in was_published},
            **{ref: False for ref in never_published},
        }
        try:
            with Store.open(parsed.root, read_only=parsed.dry_run) as store:
                result = upgrade_store(
                    store,
                    dry_run=parsed.dry_run,
                    published_once=assertions,
                    actor=parsed.actor,
                    run_id=ci_run_id(),
                )
        except UpgradeRefused as error:
            print(
                f"vouch-mk2: upgrade refused: {error}; {error.operator_hint}",
                file=sys.stderr,
            )
            return 2
        except ProjectionRegenerationError as error:
            state_outcome = (
                f"state upgraded to format {CURRENT_STATE_FORMAT}"
                if error.state_upgraded
                else f"state already at format {CURRENT_STATE_FORMAT}"
            )
            print(
                f"vouch-mk2: {state_outcome}; projection regeneration failed: "
                f"{error.projection_error} — run 'vouch-mk2 generate'",
                file=sys.stderr,
            )
            return 2
        except Exception as error:
            print(f"vouch-mk2: upgrade failed: {error}", file=sys.stderr)
            return 2
        _present_upgrade(result, dry_run=parsed.dry_run)
        return 0

    if parsed.command == "generate":
        try:
            with Store.open(parsed.root) as store:
                data = emit_system_map(store)
                emit_context(store, None)
            _map, source_digest = decode_system_map(data)
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        print(
            f"map.json and vouch-context.md written "
            f"(source state {source_digest})"
        )
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
            with Store.open(parsed.root, read_only=True) as store:
                snapshot = store.read_snapshot()
        except StoreError as error:
            print(f"vouch-mk2: {error}", file=sys.stderr)
            return 2
        actions = next_actions(snapshot)
        if parsed.command == "next":
            if parsed.json_output:
                print(
                    json.dumps(
                        {
                            "actions": [
                                _next_action_object(
                                    action, parsed.root, program_name
                                )
                                for action in actions
                            ]
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if not actions:
                print("no next actions")
            for action in actions:
                print(invocation_for(action, parsed.root, program_name))
            return 0
        facts = status_facts(snapshot, actions)
        if parsed.json_output:
            print(json.dumps(_status_object(facts), sort_keys=True))
            return 0
        print(f"unit_counts: {_format_counts(facts.unit_counts)}")
        print(f"deviation_counts: {_format_counts(facts.deviation_counts)}")
        print(f"release_counts: {_format_counts(facts.release_counts)}")
        print(f"evidence_chains: {facts.evidence_chains}")
        print(f"anchored_current: {facts.anchored_current}")
        for unit, run_id in facts.anchor_run_claims:
            safe_run_id = run_id.replace("\r", "\\r").replace("\n", "\\n")
            print(f"anchor: {unit} advanced by workflow run {safe_run_id}")
        print(f"anchor_mode: {facts.anchor_mode}")
        print(f"tier: {facts.tier}")
        print(f"ci_policy: {facts.ci_policy}")
        for name, reason in facts.ci_exceptions:
            print(f"ci_exception: {name} reason={reason}")
        recorded = "true" if facts.formal_certificate_recorded else "false"
        print(f"formal_certificate_recorded: {recorded}")
        print(f"next_action_count: {facts.next_action_count}")
        print(
            "unbound_pack_units: " + ",".join(facts.unbound_pack_units)
        )
        print(
            "late_bound_pack_units: " + ",".join(facts.late_bound_pack_units)
        )
        print(
            "rebound_pack_units: " + ",".join(facts.rebound_pack_units)
        )
        for rebound in facts.pack_rebounds:
            print("pack_rebound: " + rebound)
        return 0

    try:
        with Store.open(parsed.root, read_only=True) as store:
            snapshot = store.read_snapshot()
            project = parsed.project or None
            normalized_root = os.path.normpath(parsed.root)
            if project is None and os.path.basename(normalized_root) == ".vouch":
                project = os.path.abspath(os.path.dirname(normalized_root))
            artifact_results = run_artifact_checks(
                store,
                snapshot,
                project=project,
            )
    except StoreError as error:
        print(f"vouch-mk2: {error}", file=sys.stderr)
        return 2

    report = run_checks(
        snapshot,
        artifact_results=artifact_results,
        ci=parsed.ci,
    )
    if parsed.json_output:
        _present_json(report)
    else:
        _present_text(report)
    return 0 if report.composed() is Verdict.PASS else 1
