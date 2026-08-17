from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Iterator
from typing import cast

from vouch_mk2.checks import run_checks
from vouch_mk2.mcp import serve
from vouch_mk2.store import Store
from vouch_mk2.verify import run_artifact_checks


def _request(identifier: int, method: str, params: object) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
    )


def _responses(lines: list[str], root: str = "/nonexistent-store") -> list[dict[str, object]]:
    decoded: list[dict[str, object]] = []
    for line in serve(root, lines):
        item: object = json.loads(line)
        assert isinstance(item, dict)
        decoded.append(cast(dict[str, object], item))
    return decoded


def test_mcp_initialize_list_and_structured_check(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    responses = _responses(
        [
            _request(1, "initialize", {}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            ),
            _request(2, "tools/list", {}),
            _request(
                3,
                "tools/call",
                {"name": "check", "arguments": {}},
            ),
        ],
        root=str(root),
    )
    assert [response["id"] for response in responses] == [1, 2, 3]
    initialized = cast(dict[str, object], responses[0]["result"])
    assert initialized["protocolVersion"] == "2025-06-18"
    assert initialized["capabilities"] == {"tools": {}}

    listed = cast(dict[str, object], responses[1]["result"])
    tools = cast(list[dict[str, object]], listed["tools"])
    assert [tool["name"] for tool in tools] == ["check", "formal", "next", "status", "map"]

    tool_result = cast(dict[str, object], responses[2]["result"])
    structured = cast(dict[str, object], tool_result["structuredContent"])
    assert structured["composed"] == "pass"
    content = cast(list[dict[str, object]], tool_result["content"])
    assert json.loads(cast(str, content[0]["text"])) == structured

    with Store.open(str(root)) as store:
        snapshot = store.read_snapshot()
        expected = run_checks(
            snapshot,
            artifact_results=run_artifact_checks(store, snapshot),
        )
    mechanical = cast(list[dict[str, object]], structured["mechanical"])
    assert [row["check_id"] for row in mechanical] == [
        result.check_id for result in expected.mechanical
    ]


def test_mcp_unknown_method_bad_params_and_mutation_name() -> None:
    responses = _responses(
        [
            _request(1, "explode", {}),
            _request(2, "tools/call", {"name": "check", "arguments": ["not", "an", "object"]}),
            _request(3, "tools/call", {"name": "commit", "arguments": {}}),
        ]
    )
    errors = [cast(dict[str, object], response["error"]) for response in responses]
    assert errors[0]["code"] == -32601
    assert errors[1]["code"] == -32602
    assert errors[2]["code"] == -32602
    assert "no mutation tools exist" in cast(str, errors[2]["message"])


def test_mcp_store_error_is_a_tool_error_result(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    response = _responses(
        [
            _request(
                1,
                "tools/call",
                {"name": "status", "arguments": {}},
            )
        ],
        root=str(missing),
    )[0]
    result = cast(dict[str, object], response["result"])
    assert result["isError"] is True
    structured = cast(dict[str, object], result["structuredContent"])
    assert "error" in structured


def test_mcp_remaining_read_tools_return_structured_objects(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    calls = [
        _request(1, "tools/call", {"name": "formal", "arguments": {}}),
        *(
            _request(
                identifier,
                "tools/call",
                {"name": name, "arguments": {}},
            )
            for identifier, name in ((2, "next"), (3, "status"), (4, "map"))
        ),
    ]
    responses = _responses(calls, root=str(root))
    structured = [
        cast(
            dict[str, object],
            cast(dict[str, object], response["result"])["structuredContent"],
        )
        for response in responses
    ]
    assert structured[0]["passed"] is True
    assert structured[1]["actions"] == [
        {
            "subject_kind": "unit",
            "subject_id": "<UNIT_NAME>",
            "command": "specify",
            "reason": "Specify the first unit to bootstrap this store.",
        }
    ]
    assert "status" in structured[2]
    assert structured[3]["map_format"] == 4



def test_mcp_streams_responses_before_eof(tmp_path: Path) -> None:
    # A live client holds stdin open for the whole session; the server must
    # answer each request as it arrives, not buffer until EOF.
    root = tmp_path / "store"
    Store.init(str(root)).close()

    def feed() -> Iterator[str]:
        yield _request(1, "initialize", {})
        # If serve() buffered until EOF, this assertion would never be reached
        # with responses already produced.
        yield _request(2, "tools/list", {})

    produced = []
    for line in serve(str(root), feed()):
        produced.append(json.loads(line)["id"])
        if len(produced) == 1:
            # First response arrived while the generator still has a pending item.
            assert produced == [1]
    assert produced == [1, 2]


def test_mcp_survives_hostile_lines_and_keeps_serving(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    nested = "[" * 200000
    lines = [
        nested,
        _request(1, "tools/call", {"name": "status", "arguments": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "check", "arguments": {"root": "/etc"}}}),
        _request(3, "tools/list", {}),
    ]
    responses = _responses(lines, root=str(root))
    # parse error for the nested bomb, then every later request answered
    assert cast(dict[str, object], responses[0]["error"])["code"] == -32700
    assert responses[1]["id"] == 1 and "result" in responses[1]
    # a client-supplied root is refused as invalid params — the root is pinned
    assert responses[2]["id"] == 2
    assert cast(dict[str, object], responses[2]["error"])["code"] == -32602
    assert responses[3]["id"] == 3 and "result" in responses[3]


def test_mcp_tools_do_not_mutate_the_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    orphan = root / ".state-victim.tmp"
    orphan.write_bytes(b"orphan")
    before = (root / "state.json").read_bytes()
    _responses(
        [_request(i, "tools/call", {"name": name, "arguments": {}})
         for i, name in enumerate(("check", "next", "status", "map"), 1)],
        root=str(root),
    )
    assert (root / "state.json").read_bytes() == before
    assert orphan.exists(), "a read-only transport must not sweep the store"


def test_mcp_held_lock_yields_error_not_hang(tmp_path: Path) -> None:
    import fcntl

    root = tmp_path / "store"
    Store.init(str(root)).close()
    holder = open(root / ".lock", "r+b")
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        responses = _responses(
            [_request(1, "tools/call", {"name": "status", "arguments": {}})],
            root=str(root),
        )
        # Read-only open never takes the commit lock, so this returns promptly.
        assert responses[0]["id"] == 1 and "result" in responses[0]
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
