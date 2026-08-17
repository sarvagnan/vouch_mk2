"""Permanent read/verify-only MCP transport; mutation tools do not exist.

The store root is PINNED at server start (``vouch-mk2 mcp --root R``) and is not
a tool argument: every other transport takes its root from a human's CLI
invocation, but MCP tool arguments are chosen by a model — i.e. by repository
content under threat-model level 2 — so an unconstrained ``root`` would make
every directory on disk client-reachable (D-7, amended in Phase 8 review).

Framing is line-delimited JSON: every input line and every output line is one
complete JSON-RPC 2.0 object, streamed (a response is written as soon as its
request is handled — a live client holding stdin open gets answers). Lines
are bounded; a hostile line is refused with a parse error, never buffered
without limit. No exception escapes the loop: a malformed request yields an
error response and the server keeps serving. LSP-style ``Content-Length``
framing is not supported by this intentionally minimal stdlib server.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator
from typing import BinaryIO, cast

from vouch_mk2 import __version__
from vouch_mk2.checks import CheckReport, CheckResult, run_checks
from vouch_mk2.formal.run import FormalRun, passed, run_formal
from vouch_mk2.projections import NextAction, StatusFacts, next_actions, status_facts, system_map
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    StoreError,
    encode_snapshot,
    encode_system_map,
    digest_of,
)
from vouch_mk2.verify import run_artifact_checks


_PROTOCOL_VERSION = "2025-06-18"
_READ_TOOLS = frozenset({"check", "formal", "next", "status", "map"})
# One line = one JSON-RPC object; a request larger than this is hostile or a
# framing error, and is refused rather than read into memory.
MAX_LINE_BYTES = 1024 * 1024


class _InvalidParams(Exception):
    pass


def _canonical_json(document: object) -> str:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _InvalidParams(message)
    raw = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise _InvalidParams(message)
    return {cast(str, key): item for key, item in raw.items()}


def _exact_keys(
    document: dict[str, object], expected: frozenset[str], message: str
) -> None:
    if frozenset(document) != expected:
        raise _InvalidParams(message)


def _no_arguments(arguments: object, tool: str) -> None:
    document = _object(arguments, f"{tool} arguments must be an empty object")
    _exact_keys(document, frozenset(), f"{tool} arguments must be an empty object")


def _result_object(result: CheckResult) -> dict[str, object]:
    return {
        "check_id": result.check_id,
        "verdict": result.verdict.value,
        "detail": result.detail,
    }


def _report_object(report: CheckReport) -> dict[str, object]:
    return {
        "revision": report.revision,
        "mechanical": [_result_object(result) for result in report.mechanical],
        "organizational": [
            _result_object(result) for result in report.organizational
        ],
        "composed": report.composed().value,
    }


def _formal_object(run: FormalRun) -> dict[str, object]:
    return {
        "universes": [
            {
                "universe_name": universe.universe_name,
                "state_count": universe.state_count,
                "transition_count": universe.transition_count,
                "invariants": [
                    {
                        "invariant_id": verdict.invariant_id,
                        "held": verdict.held,
                        "violations": list(verdict.violations),
                    }
                    for verdict in universe.invariants
                ],
            }
            for universe in run.universes
        ],
        "witnesses": [
            {
                "witness_id": verdict.witness_id,
                "invariant_id": verdict.invariant_id,
                "discriminated": verdict.discriminated,
            }
            for verdict in run.witnesses
        ],
        "passed": passed(run),
    }


def _action_object(action: NextAction) -> dict[str, object]:
    return {
        "subject_kind": action.subject_kind,
        "subject_id": action.subject_id,
        "command": action.command,
        "reason": action.reason,
    }


def _counts_object(rows: tuple[tuple[str, int], ...]) -> dict[str, object]:
    return {name: count for name, count in rows}


def _status_object(facts: StatusFacts) -> dict[str, object]:
    return {
        "unit_counts": _counts_object(facts.unit_counts),
        "deviation_counts": _counts_object(facts.deviation_counts),
        "release_counts": _counts_object(facts.release_counts),
        "evidence_chains": facts.evidence_chains,
        "anchored_current": facts.anchored_current,
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


def _call_tool(root: str, name: str, arguments: object) -> dict[str, object]:
    if name not in _READ_TOOLS:
        if name == "commit":
            raise _InvalidParams("no mutation tools exist; MCP is permanently read-only")
        raise _InvalidParams(f"unknown tool {name!r}; no mutation tools exist")
    _no_arguments(arguments, name)
    if name == "formal":
        return _formal_object(run_formal())

    # Read-only open: no orphan sweep (that is a write under an exclusive lock —
    # a "read-only" transport must not mutate the directory it reads) and a
    # non-blocking lock so a held store yields an error, never a hang.
    with Store.open(root, read_only=True) as store:
        snapshot = store.read_snapshot()
        if name == "check":
            return _report_object(
                run_checks(snapshot, artifact_results=run_artifact_checks(store, snapshot))
            )
        actions = next_actions(snapshot)
        if name == "next":
            return {
                "revision": snapshot.revision,
                "actions": [_action_object(action) for action in actions],
            }
        if name == "status":
            return {
                "revision": snapshot.revision,
                "status": _status_object(status_facts(snapshot, actions)),
            }
        if name == "map":
            encoded = encode_system_map(
                system_map(snapshot),
                digest_of(ArtifactKind.STATE, encode_snapshot(snapshot)),
            )
            decoded: object = json.loads(encoded)
            return _object(decoded, "internal map encoding was not an object")
        raise AssertionError(f"unreachable read tool {name!r}")


def _tool_result(structured: dict[str, object], *, is_error: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "content": [{"type": "text", "text": _canonical_json(structured)}],
        "structuredContent": structured,
    }
    if is_error:
        result["isError"] = True
    return result


def _tools() -> list[object]:
    root_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "additionalProperties": False,
    }
    empty_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    descriptions = {
        "check": "Run the complete structured check report.",
        "formal": "Run the pure bounded formal checker.",
        "next": "Project binding next actions from a snapshot.",
        "status": "Project aggregate status facts from a snapshot.",
        "map": "Project the canonical complete system map.",
    }
    return [
        {
            "name": name,
            "description": descriptions[name],
            "inputSchema": empty_schema if name == "formal" else root_schema,
        }
        for name in ("check", "formal", "next", "status", "map")
    ]


def _id(request: dict[str, object]) -> str | int | None:
    value = request.get("id")
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise _InvalidParams("JSON-RPC id must be a string, integer, or null")


def _error(identifier: str | int | None, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def _response(identifier: str | int | None, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _dispatch(root: str, raw: object) -> dict[str, object] | None:
    try:
        request = _object(raw, "request must be a JSON object")
        identifier = _id(request)
        is_notification = "id" not in request
        if request.get("jsonrpc") != "2.0":
            raise _InvalidParams("jsonrpc must be '2.0'")
        method = request.get("method")
        if not isinstance(method, str):
            raise _InvalidParams("method must be a string")
        params = request.get("params", {})

        if method == "notifications/initialized":
            _object(params, "initialized params must be an object")
            return None if is_notification else _response(identifier, None)
        if method == "initialize":
            _object(params, "initialize params must be an object")
            result = {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "vouch-mk2", "version": __version__},
            }
            return None if is_notification else _response(identifier, result)
        if method == "tools/list":
            document = _object(params, "tools/list params must be an empty object")
            _exact_keys(document, frozenset(), "tools/list params must be an empty object")
            return None if is_notification else _response(identifier, {"tools": _tools()})
        if method == "tools/call":
            document = _object(params, "tools/call params must be an object")
            _exact_keys(
                document,
                frozenset({"name", "arguments"}),
                "tools/call params must contain only name and arguments",
            )
            name = document["name"]
            if not isinstance(name, str):
                raise _InvalidParams("tool name must be a string")
            try:
                structured = _call_tool(root, name, document["arguments"])
                result = _tool_result(structured)
            except StoreError as error:
                result = _tool_result({"error": str(error)}, is_error=True)
            return None if is_notification else _response(identifier, result)
        return None if is_notification else _error(identifier, -32601, f"method {method!r} not found")
    except _InvalidParams as error:
        identifier: str | int | None = None
        if isinstance(raw, dict):
            try:
                identifier = _id(_object(raw, "request must be a JSON object"))
            except _InvalidParams:
                pass
        return _error(identifier, -32602, str(error))


def serve(root: str, stdin_lines: Iterable[str]) -> Iterator[str]:
    """Yield one response line per handled request, as each is handled.

    Streaming is the contract: a live client holds stdin open for the whole
    session, so responses must not wait for EOF. Nothing escapes this loop —
    an oversize line, invalid JSON, a hostile argument, or an internal fault
    each produce an error line and the server continues.
    """
    for line in stdin_lines:
        if len(line) > MAX_LINE_BYTES:
            yield _canonical_json(
                _error(None, -32700, f"line exceeds {MAX_LINE_BYTES} bytes")
            )
            continue
        if not line.strip():
            continue
        try:
            request: object = json.loads(line)
        except (json.JSONDecodeError, RecursionError, ValueError) as error:
            yield _canonical_json(_error(None, -32700, str(error)[:200]))
            continue
        try:
            response = _dispatch(root, request)
        except Exception as error:  # the loop must survive anything a client sends
            identifier: str | int | None = None
            if isinstance(request, dict):
                try:
                    identifier = _id(_object(request, "request"))
                except _InvalidParams:
                    pass
            response = _error(
                identifier, -32603, f"internal error: {type(error).__name__}"
            )
        if response is not None:
            yield _canonical_json(response)


def _bounded_lines(raw: BinaryIO) -> Iterator[str]:
    """Read stdin line by line with a hard per-line byte bound (never slurp).

    An oversize line is drained to its newline (so framing re-synchronizes)
    and surfaced to ``serve`` as an over-bound marker that yields a parse
    error; the client's next line is handled normally."""
    while True:
        chunk = raw.readline(MAX_LINE_BYTES + 1)
        if not chunk:
            return
        if len(chunk) > MAX_LINE_BYTES:
            while not chunk.endswith(b"\n"):
                chunk = raw.readline(MAX_LINE_BYTES)
                if not chunk:
                    break
            yield "x" * (MAX_LINE_BYTES + 1)
            continue
        yield chunk.decode("utf-8", errors="replace")


def run_stdio(root: str) -> None:
    for response in serve(root, _bounded_lines(sys.stdin.buffer)):
        sys.stdout.write(response + "\n")
        sys.stdout.flush()


__all__ = ["run_stdio", "serve"]
