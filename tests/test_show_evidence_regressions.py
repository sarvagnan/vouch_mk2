"""Owner regressions for show-evidence@1 (D-49), beyond the frozen pack."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

from vouch_mk2.cli import main
from vouch_mk2.mcp import serve

Json = dict[str, object]


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _must(*argv: str) -> str:
    code, out, err = _run(*argv)
    assert code == 0, (argv, out, err)
    return out


def _object(text: str) -> Json:
    document: object = json.loads(text)
    assert isinstance(document, dict)
    return cast(Json, document)


def test_a_corrupt_earlier_record_discards_the_partial_chain(tmp_path: Path) -> None:
    """Astra's review note: R12 deletes the head, so a kept partial prefix would go unseen."""
    project = tmp_path / "proj"
    project.mkdir()
    _must("init", "--project", str(project))
    root = str(project / ".vouch")
    (project / "packs").mkdir()
    (project / "tests").mkdir()
    (project / "packs" / "u.spec.md").write_text("# u\n")
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}')
    (project / "tests" / "t.py").write_text("def test_x():\n    assert True\n")
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    _must(
        "freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1",
        "--project", str(project), "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py", "--pack-effects-file", "packs/u.effects.json",
    )
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")
    receipt = _object(_must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "red", "--json",
        "--", "python3", "-c", "raise SystemExit(1)",
    ))
    _must(
        "record-evidence", "--root", root, "--unit", "u@v1", "--kind", "green",
        "--", "python3", "-c", "raise SystemExit(0)",
    )
    red_digest = cast(list[Json], receipt["events"])[0]["event_digest"]
    assert isinstance(red_digest, str)
    (Path(root) / "objects" / "sha256" / red_digest).write_bytes(b"bad")

    code, out, err = _run("show-evidence", "--root", root, "--json")

    assert code == 1, (out, err)
    document = _object(out)
    (unit,) = cast(list[Json], document["units"])
    assert isinstance(unit["chain_error"], str) and unit["chain_error"]
    assert unit["records"] == [] and unit["notes"] == []
    assert unit["current_pack_green"] is True

    lines = [json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "evidence", "arguments": {}},
    })]
    (response,) = [_object(line) for line in serve(root, lines)]
    result = cast(Json, response["result"])
    assert result["structuredContent"] == document
    assert result.get("isError") is True
