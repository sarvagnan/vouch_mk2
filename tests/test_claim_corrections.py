"""Acceptance pack for claim-corrections@1 (D-48)."""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "state-format"
MARK = "state-format.json"


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _text(relative: str) -> str:
    return " ".join((ROOT / relative).read_text().split())


def _section(relative: str, heading: str) -> str:
    """Return one `## ` section of a page, whitespace normalised."""
    raw = (ROOT / relative).read_text()
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", raw, re.S | re.M)
    assert match is not None, f"{relative}: no '## {heading}' section"
    return " ".join(match.group(1).split())


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != ".lock"
    }


def _store_with_an_event_log(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    assert _run("init", "--project", str(project))[0] == 0
    root = project / ".vouch"
    assert _run("specify", "--root", str(root), "--name", "u", "--version", "v1")[0] == 0
    assert _run("specify", "--root", str(root), "--name", "w", "--version", "v1")[0] == 0
    return root


def _declare(root: Path, declared: int, *, keep_event_head: bool) -> None:
    """Hand-edit a current store into a lower declared format, mark removed."""
    (root / MARK).unlink()
    state = root / "state.json"
    document = json.loads(state.read_text())
    if declared == 0:
        document.pop("state_format")
    else:
        document["state_format"] = declared
    if keep_event_head:
        assert document["event_head"]
        for path in (root / "objects" / "sha256").iterdir():
            path.unlink()
    else:
        document.pop("event_head")
    state.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")


# R1


@pytest.mark.parametrize("declared", [0, 1, 2])
@pytest.mark.parametrize("signal", ["event_head", "event object"])
def test_upgrade_refuses_a_rebaseline_below_format_four(
    tmp_path: Path, declared: int, signal: str
) -> None:
    root = _store_with_an_event_log(tmp_path)
    _declare(root, declared, keep_event_head=signal == "event_head")
    before = _snapshot_tree(root)

    code, output = _run("upgrade", "--root", str(root))

    assert code == 2, output
    assert signal in output, output
    assert "git history" in output, output
    assert _snapshot_tree(root) == before
    assert not (root / MARK).exists()


# R2


@pytest.mark.parametrize("signal", ["event_head", "event object"])
def test_the_dry_run_refuses_the_same_rebaseline(tmp_path: Path, signal: str) -> None:
    root = _store_with_an_event_log(tmp_path)
    _declare(root, 2, keep_event_head=signal == "event_head")
    before = _snapshot_tree(root)

    code, output = _run("upgrade", "--root", str(root), "--dry-run")

    assert code == 2, output
    assert signal in output, output
    assert _snapshot_tree(root) == before


# R3 (guard)


@pytest.mark.parametrize("fixture", ["format1.golden", "format2.golden", "format3.golden"])
def test_a_genuine_pre_log_store_still_upgrades(tmp_path: Path, fixture: str) -> None:
    # guard: a store that never had an event log carries neither signal.
    root = tmp_path / "legacy" / ".vouch"
    (root / "objects" / "sha256").mkdir(parents=True)
    document = json.loads((FIXTURES / f"{fixture}.json").read_text())
    assert "event_head" not in document
    document["evidence_heads"] = list[object]()
    (root / "state.json").write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    )

    code, output = _run("upgrade", "--root", str(root))

    assert code == 0, output
    state = json.loads((root / "state.json").read_text())
    assert state["event_head"], "the upgrade wrote no pre-log checkpoint"
    assert (root / MARK).exists()


# R4


def test_claim_boundary_says_what_cleanup_leaves() -> None:
    text = _text("docs/claim-boundary.md")
    assert "An opportunistic cleanup reclaims those files" not in text
    assert "may differ until cleanup runs" not in text
    assert (
        "Other unreferenced objects, such as evidence and pack objects, stay in the store as "
        "inert files."
    ) in text
    assert "The event-object sweep is best-effort" in text


# R5


def test_claim_boundary_names_the_checkpoint_step_and_every_format_below_four() -> None:
    text = _text("docs/claim-boundary.md")
    assert "Upgrading to format 3 writes" not in text
    assert "to format 3:**" not in text
    assert "refuses format 3 if" not in text
    assert (
        "Upgrading a store from format 3 or older writes a pre-log checkpoint at the step from "
        "format 3 to format 4."
    ) in text
    assert (
        "The upgrader refuses a store that declares a format below 4 if its raw `state.json` "
        "still has an `event_head` key or a canonical event object already exists."
    ) in text


# R6


SAME_TESTS = (
    '"The same tests" means the same frozen test set; Vouch does not compare the commands '
    "that RED and GREEN ran."
)


def test_docs_say_the_gates_compare_the_test_set_not_the_command() -> None:
    evidence = _section("docs/claim-boundary.md", "Evidence")
    assert (
        "The publication gates compare the frozen test set, not the command: a GREEN can run "
        "a different command from the RED before it."
    ) in evidence
    for relative in ("README.md", "docs/concepts.md"):
        assert SAME_TESTS in _text(relative), relative


# R7


def test_claim_boundary_states_the_fork_rule_as_a_design_choice() -> None:
    text = _text("docs/claim-boundary.md")
    assert "give forks write access" not in text
    assert (
        "By design, the generated workflow never anchors from a fork, so a fork's work is "
        "anchored only after it is merged into the base repository."
    ) in text
