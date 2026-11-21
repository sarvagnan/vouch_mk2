"""Acceptance tests for adoption-docs@1 (D-47, settles vouch_mk2-b6g.15 and vouch_mk2-1l0).

The README becomes a plain introduction with the contributor part at its end, and docs/ gains a
router, concepts, a generated lifecycle reference, a refusal-recovery guide, a JSON reference, and
an agent skill with role cards. These tests pin the caveats and recovery rules the drafts kept
getting wrong, and check every documented command, option, refusal code, decision, check id,
lifecycle edge and JSON key against the code. Seams (D-44): the docs' text, the CLI parser and the
CLI's JSON output on a scratch store, the policy IR, the generator script, and ci.yml.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import shlex
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

from vouch_mk2.cli import _parser, main
from vouch_mk2.ir.mk2 import MK2
from vouch_mk2.store import Store
from vouch_mk2.store.codec import decode_effects_file

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS = ROOT / "docs"
GENERATOR = ROOT / "scripts" / "gen_lifecycle_doc.py"
REQUIRED_PAGES = (
    "index.md",
    "concepts.md",
    "lifecycle.md",
    "errors.md",
    "schemas.md",
    "contributing.md",
    "agents/SKILL.md",
    "agents/roles/owner.md",
    "agents/roles/implementer.md",
    "agents/roles/reviewer.md",
)
CONTRIBUTOR_DOCS = (
    "method.md",
    "defect-closure.md",
    "self-hosting-tradeoff.md",
    "performance.md",
    "usage-log.md",
    "contributing.md",
)
BANNER = (
    "> For people changing mk2: this page describes how mk2 itself is built and run. "
    "You do not need it to use Vouch."
)
OVERCLAIMS = (
    "tamper-proof",
    "tamperproof",
    "tamper-evident",
    "catches it immediately",
    "cannot be faked",
    "can't be faked",
    "impossible to fake",
)
# Refusal codes the starter catalog must cover; each was checked by hand against its emitter.
STARTER_CODES = (
    "gate.dod.current-pack-green",
    "evidence.kind-outcome-mismatch",
    "evidence.pack-moved",
    "evidence.redaction-invalid",
    "merge.conflict",
    "merge.ancestry-unproven",
    "transition.illegal",
    "identifier.leading-dash",
    "pack.effects-required",
    "ci-policy.exception-missing",
    "rebind.stale-pack",
)
ANCHOR_MODE_TOKENS = {"none", "ci", "{none,ci}", "none|ci", "MODE"}


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_docs() -> tuple[Path, ...]:
    return tuple(sorted(DOCS.rglob("*.md")))


def _prose_docs() -> tuple[Path, ...]:
    """README and every docs page except the generated command reference and the history log."""
    skip = {DOCS / "commands.md", DOCS / "usage-log.md"}
    return (README,) + tuple(path for path in _all_docs() if path not in skip)


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    action = next(
        item for item in _parser()._actions if isinstance(item, argparse._SubParsersAction)
    )
    choices: dict[str, argparse.ArgumentParser] = dict(action.choices)
    return choices


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _section(text: str, heading: str) -> str:
    parts = text.split(f"\n{heading}\n", 1)
    assert len(parts) == 2, f"missing heading {heading!r}"
    return parts[1].split("\n## ", 1)[0]


def _json_blocks(section: str) -> list[object]:
    blocks: list[object] = []
    for raw in re.findall(r"```json\n(.*?)```", section, re.S):
        parsed: object = json.loads(raw)
        blocks.append(parsed)
    return blocks


def _keys(value: object) -> set[str]:
    assert isinstance(value, dict), value
    return {str(key) for key in value}


def _items(value: object, field: str) -> list[object]:
    assert isinstance(value, dict)
    items = value.get(field)
    assert isinstance(items, list), (field, items)
    result: list[object] = list(items)
    return result


def _literal_tokens() -> set[str]:
    tokens: set[str] = set()
    for path in (ROOT / "src" / "vouch_mk2").rglob("*.py"):
        for node in ast.walk(ast.parse(_read(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                tokens.update(re.findall(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+", node.value))
    return tokens


def _tables(text: str) -> list[list[list[str]]]:
    """Every Markdown table as rows of cells, header first, separator dropped."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("|"):
            if not re.fullmatch(r"\|[\s:|-]+\|", stripped):
                current.append([cell.strip() for cell in stripped.strip("|").split("|")])
        elif current:
            tables.append(current)
            current = []
    return tables


def _code(cell: str) -> str:
    match = re.fullmatch(r"`([^`]*)`", cell)
    assert match is not None, f"cell is not one code span: {cell!r}"
    return match.group(1)


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", text)


def _invocations(path: Path) -> list[tuple[int, list[str]]]:
    """Commands shown in code spans and code blocks, continuations joined, split on && ; |."""
    text = _read(path)
    snippets: list[str] = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        snippets.extend(block.replace("\\\n", " ").splitlines())
    prose = re.sub(r"```[a-z]*\n.*?```", "", text, flags=re.S)
    snippets.extend(re.findall(r"`([^`\n]+)`", prose))
    found: list[tuple[int, list[str]]] = []
    for snippet in snippets:
        for part in re.split(r"&&|;|\|\||\|", snippet):
            try:
                words = shlex.split(part, comments=True)
            except ValueError:
                words = part.split()
            for start in range(len(words)):
                tail = words[start:]
                if tail[:2] == ["python3", "vouch.lock"] and len(tail) > 2:
                    found.append((start, tail[2:]))
                    break
                if tail[:1] == ["vouch-mk2"] and len(tail) > 1:
                    found.append((start, tail[1:]))
                    break
    return found


# R1


def test_the_readme_opens_as_a_plain_introduction() -> None:
    text = _read(README)
    lines = text.splitlines()
    assert lines[0] == "# Vouch: keeping a checkable record of agent-delivered work"
    head = "\n".join(lines[:12])
    for link in ("docs/index.md", "docs/quickstart.md", "docs/commands.md"):
        assert f"]({link})" in head, link
    flat = _flat(text)
    for sentence in (
        "It cannot prove that the tests were frozen before the code was written.",
        "At the default `lightweight` tier, `publish` has no evidence gate; at `standard` and "
        "`high` it needs a GREEN for the current pack, after a RED for the same tests or an "
        "owner rebind, and after any re-freeze that changed the pack.",
        "Vouch records who claimed each step but does not verify their identity",
    ):
        assert sentence in flat, sentence


# R2


def test_the_readme_keeps_the_contributor_part_at_its_end() -> None:
    text = _read(README)
    headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
    assert "Layout" not in headings, headings
    assert headings[-2:] == ["Where mk2 came from", "Gates"], headings
    assert "](docs/contributing.md)" in text
    assert "checking twelve hand-written invariants" in _flat(text)
    counts = re.findall(r"\b\d+ tests\b", text)
    assert len(counts) == 1, counts
    assert re.search(r"\b\d+ tests\b", _section(text, "## Gates"))


# R3


def test_contributing_holds_the_layout() -> None:
    text = _read(DOCS / "contributing.md")
    assert re.search(r"^## Layout$", text, re.MULTILINE)
    assert "The Python package lives under `src/vouch_mk2/`." in _flat(text)
    for package in ("`ir/`", "`core/`", "`store/`", "`formal/`", "`projections/`"):
        assert package in text, package


# R4


def test_contributor_pages_carry_the_banner() -> None:
    for name in CONTRIBUTOR_DOCS:
        head = "\n".join(_read(DOCS / name).splitlines()[:5])
        assert BANNER in head, name


# R5


def test_the_router_links_every_page_and_every_link_resolves() -> None:
    for page in REQUIRED_PAGES:
        assert (DOCS / page).is_file(), page
    router = DOCS / "index.md"
    text = _read(router)
    assert not re.search(r"^\s*\[[^\]]+\]:\s", text, re.MULTILINE), "use inline links only"
    resolved: set[Path] = set()
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"[a-z]+:", target):
            continue
        path_part, _, fragment = target.partition("#")
        path = (router.parent / path_part).resolve() if path_part else router.resolve()
        assert path.is_file(), target
        if fragment:
            headings = re.findall(r"^#+ (.+)$", _read(path), re.MULTILINE)
            assert fragment in {_slug(item) for item in headings}, target
        resolved.add(path)
    for page in _all_docs():
        if page != router:
            assert page.resolve() in resolved, page.relative_to(ROOT)


# R6


def test_the_lifecycle_reference_matches_the_policy_ir() -> None:
    text = _read(DOCS / "lifecycle.md")
    for machine in MK2.machines:
        section = _section(text, f"## `{machine.name}`")
        tables = _tables(section)
        commands = [table for table in tables if table[0] == ["From", "Command", "To", "Gates"]]
        assert len(commands) == 1, (machine.name, [table[0] for table in tables])
        rows = commands[0][1:]
        assert all(len(row) == 4 for row in rows), machine.name
        documented = sorted(
            (
                _code(row[0]),
                _code(row[1]),
                _code(row[2]),
                tuple(re.findall(r"`([^`]+)`", row[3])),
            )
            for row in rows
        )
        expected = sorted(
            (item.source, item.command, item.target, item.gates)
            for item in machine.command_transitions
        )
        assert documented == expected, machine.name
        entries = [table for table in tables if table[0] == ["Trigger", "To", "From", "Except"]]
        if machine.adjudication_entries:
            assert len(entries) == 1, machine.name
            got = sorted(
                (
                    _code(row[0]),
                    _code(row[1]),
                    tuple(re.findall(r"`([^`]+)`", row[2])),
                    tuple(re.findall(r"`([^`]+)`", row[3])),
                )
                for row in entries[0][1:]
            )
            want = sorted(
                (entry.trigger, entry.target, entry.sources, entry.excluded_sources)
                for entry in machine.adjudication_entries
            )
            assert got == want, machine.name
        else:
            assert not entries, machine.name
    # One edge worked by hand from ir/mk2.py, independent of the comparison above.
    unit = _section(text, "## `unit`")
    assert (
        "| `blocked` | `respecify` | `specified` | `gate.unblock.no-blocking-deviations` |"
        in unit
    )


# R7


def test_the_lifecycle_generator_writes_checks_and_ci_runs_it(tmp_path: Path) -> None:
    committed = (DOCS / "lifecycle.md").read_bytes()
    fresh = tmp_path / "fresh" / "lifecycle.md"
    written = subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(fresh)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert written.returncode == 0, written.stdout + written.stderr
    assert fresh.read_bytes() == committed
    for path in (None, fresh):
        argv = [sys.executable, str(GENERATOR), "--check"]
        if path is not None:
            argv += ["--output", str(path)]
        current = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
        assert current.returncode == 0, current.stdout + current.stderr
    drifted = committed + b"\nedited by hand\n"
    fresh.write_bytes(drifted)
    drift = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(fresh)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert drift.returncode == 1, drift.stdout + drift.stderr
    assert fresh.read_bytes() == drifted
    workflow = yaml.safe_load(_read(ROOT / ".github" / "workflows" / "ci.yml"))
    assert isinstance(workflow, dict)
    steps = workflow["jobs"]["gates"]["steps"]
    assert isinstance(steps, list)
    runs = [
        line.strip()
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
        for line in str(step["run"]).splitlines()
    ]
    assert "uv run python scripts/gen_lifecycle_doc.py --check" in runs


# R8


def test_the_errors_guide_names_real_codes_and_honest_recoveries() -> None:
    text = _read(DOCS / "errors.md")
    catalogs = [table for table in _tables(text) if table[0] and table[0][0] == "Code"]
    assert catalogs, "no table headed Code"
    codes = [_code(row[0]) for table in catalogs for row in table[1:]]
    for code in codes:
        assert re.fullmatch(r"[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)+", code), code
        assert not code.startswith(("check.", "org.")), code
    known = _literal_tokens()
    assert set(codes) <= known, sorted(set(codes) - known)
    assert set(STARTER_CODES) <= set(codes), sorted(set(STARTER_CODES) - set(codes))
    flat = _flat(text)
    for sentence in (
        "Never record a RED after the implementation exists to satisfy this gate; if the "
        "current pack has no RED, ask the acceptance owner.",
        "Do not re-run a passing RED as GREEN: unless the owner has rebound the pack, a first "
        "GREEN with no RED before it makes `check.evidence-chains` fail, and the chain is "
        "append-only.",
        "`--redact-arg` positions run from 1 to the last argument, inclusive.",
    ):
        assert sentence in flat, sentence


# R9


def test_the_json_reference_matches_real_output(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store.init(str(root)).close()
    code, accepted_output = _run(
        "specify", "--root", str(root), "--name", "greeter", "--version", "1", "--json"
    )
    assert code == 0, accepted_output
    code, refused_output = _run(
        "specify", "--root", str(root), "--name", "greeter", "--version", "1", "--json"
    )
    assert code == 2, refused_output
    text = _read(DOCS / "schemas.md")
    for verb, lists in (
        ("check", ("mechanical", "organizational")),
        ("next", ("actions",)),
        ("status", ()),
    ):
        code, output = _run(verb, "--root", str(root), "--json")
        assert code in (0, 1), (verb, output)
        live: object = json.loads(output)
        blocks = _json_blocks(_section(text, f"## `{verb} --json`"))
        assert blocks, verb
        documented = blocks[0]
        assert _keys(documented) == _keys(live), verb
        for field in lists:
            live_items = _items(live, field)
            assert live_items, (verb, field)
            want = _keys(live_items[0])
            shown = _items(documented, field)
            assert shown, (verb, field)
            for item in shown:
                assert _keys(item) == want, (verb, field, item)
    lifecycle = _json_blocks(_section(text, "## Lifecycle `--json`"))
    assert len(lifecycle) >= 2
    assert _keys(lifecycle[0]) == _keys(json.loads(accepted_output))
    assert _keys(lifecycle[1]) == _keys(json.loads(refused_output))
    effects = _json_blocks(_section(text, "## Effects declaration"))
    assert effects
    decode_effects_file(json.dumps(effects[0]).encode())
    for raw in re.findall(r"```json\n(.*?)```", text, re.S):
        json.loads(raw)


# R10


def test_documented_commands_and_options_exist() -> None:  # guard:
    subcommands = _subcommands()
    seen = 0
    for path in _prose_docs():
        for _start, argv in _invocations(path):
            verb = argv[0]
            if not re.fullmatch(r"[a-z][a-z-]*", verb):
                continue  # an option or a placeholder such as <verb>
            assert verb in subcommands, (str(path.relative_to(ROOT)), argv)
            options = _options(subcommands[verb])
            before_child = argv[1 : argv.index("--")] if "--" in argv else argv[1:]
            for word in before_child:
                if word.startswith("--"):
                    option = word.split("=", 1)[0]
                    assert option in options, (str(path.relative_to(ROOT)), verb, option)
            seen += 1
    assert seen >= 30, seen


# R11


def test_documented_ids_and_anchor_modes_are_real() -> None:  # guard:
    decisions = _read(ROOT / "DECISIONS.md")
    headings = set(re.findall(r"^## (D-\d+)\b", decisions, re.MULTILINE))
    declared = {item.obligation_id for item in MK2.obligations}
    for path in _prose_docs():
        text = _read(path)
        name = str(path.relative_to(ROOT))
        assert set(re.findall(r"\bD-\d+\b", text)) <= headings, name
        checks = set(re.findall(r"\bcheck(?:\.[a-z0-9-]+)+", text))
        assert checks <= declared, (name, sorted(checks - declared))
        for token in re.findall(r"--mode[ =](\S+)", text):
            value = token.strip("`.,;:)\"'")
            assert value in ANCHOR_MODE_TOKENS, (name, token)
        assert not re.search(r"anchor[- ]mode[^.\n]{0,40}`local`", text), name


# R12


def test_no_page_uses_the_overclaim_words() -> None:
    for path in _prose_docs():
        lowered = _read(path).lower()
        for phrase in OVERCLAIMS:
            assert phrase not in lowered, (str(path.relative_to(ROOT)), phrase)


# R13


def test_the_agent_skill_and_role_cards_state_the_limits() -> None:
    text = _read(DOCS / "agents" / "SKILL.md")
    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert front is not None
    assert re.search(r"^name: vouch$", front.group(1), re.MULTILINE)
    assert re.search(r"^description: \S", front.group(1), re.MULTILINE)
    assert len(text.splitlines()) <= 150
    assert "](../errors.md)" in text
    flat = _flat(text)
    for sentence in (
        "`check --ci` exits 1 on a branch while an evidence anchor waits for CI on the default "
        "branch; do not lower the anchor mode or the CI policy, and do not set `GITHUB_RUN_ID`, "
        "to make it pass.",
        "Vouch records who claimed each step but does not verify identity or enforce roles.",
    ):
        assert sentence in flat, sentence
    duties = {
        "owner": ("`freeze`", "`adjudicate-amend`"),
        "implementer": ("`open-deviation`", "`record-evidence`"),
        "reviewer": ("`check`", "`next`"),
    }
    for role, names in duties.items():
        card = _read(DOCS / "agents" / "roles" / f"{role}.md")
        for item in names:
            assert item in card, (role, item)


# R14


def test_the_claim_boundary_no_longer_says_every_exclusion_is_a_row() -> None:
    flat = _flat(_read(DOCS / "claim-boundary.md"))
    assert "Reports list the items below as organizational `NOT_CHECKED` rows." not in flat
    assert (
        "Some of the items below appear in reports as organizational `NOT_CHECKED` rows; the "
        "others are documented exclusions with no report row." in flat
    )
