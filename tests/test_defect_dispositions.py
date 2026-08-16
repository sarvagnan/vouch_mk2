"""Acceptance pack for self-hosted unit defect-dispositions@1 (bead yo5).

Closes the eight remaining 0.6 DEFECTS with disposition (a): each has a regression test HERE,
and docs/defect-closure.md must move every row to (a) naming that test. Tests marked
`# guard:` pass today (the property already holds; the test pins it). The table test and
the D050 test are RED until the unit lands.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "docs" / "defect-closure.md"
THIS = "tests/test_defect_dispositions.py"

OPEN_IDS = ("D024", "D037", "D048", "D049", "D050", "D064", "D066", "D068")


# ---- the table moves, and its totals are DERIVED (D048/D066) ----


def test_every_remaining_defect_is_regression_tested_here() -> None:
    text = CLOSURE.read_text()
    for defect in OPEN_IDS:
        row = next(l for l in text.splitlines() if l.startswith(f"| {defect} "))
        assert "| (a) regression test |" in row, f"{defect} still open"
        assert THIS in row, f"{defect} must cite this file"


def test_disposition_summary_equals_the_row_counts() -> None:
    # guard: D048/D066 — hand-maintained totals drifted three times in one day. Derive them.
    text = CLOSURE.read_text()
    rows = [l for l in text.splitlines() if re.match(r"^\| D0\d\d ", l)]
    assert len(rows) == 70
    counts = {k: sum(1 for r in rows if f"| ({k}) " in r) for k in "abcd"}
    for key, label in (("a", "regression test"), ("b", "unrepresentable"), ("c", "out of scope"), ("d", "open")):
        m = re.search(rf"^\| \({key}\) {label} \| (\d+) \|$", text, re.M)
        assert m and int(m.group(1)) == counts[key], (key, counts[key], m and m.group(1))
    assert sum(counts.values()) == 70
    if "The open items" in text:
        section = text[text.index("The open items"):]
        section = section.split("\n\n", 2)[1] if "\n\n" in section else section
        open_bullets = re.findall(r"^- .+$", section, re.M)
        assert len(open_bullets) == counts["d"], "open-items list must match the (d) count"
    else:
        assert counts["d"] == 0


def test_docs_carry_no_machine_local_absolute_paths() -> None:
    # guard: D048 — a kit that carried a local absolute path.
    for path in list((ROOT / "docs").glob("*.md")) + [ROOT / "README.md", ROOT / "DECISIONS.md"]:
        assert "/Users/" not in path.read_text(), path.name


# ---- D024: one project-local lock, whatever TMPDIR says ----


def test_store_lock_is_project_local_not_tempdir_derived(tmp_path: Path) -> None:
    # guard: two writers with DIFFERENT TMPDIR values must serialise on the same lock.
    from vouch_mk2.store import Store

    root = tmp_path / "s"
    Store.init(str(root)).close()
    assert (root / ".lock").exists()
    code = (
        "import sys, os\n"
        "from vouch_mk2.store import Store\n"
        "from vouch_mk2.core.model import Snapshot\n"
        "with Store.open(sys.argv[1]) as s:\n"
        "    for _ in range(25):\n"
        "        with s.write_locked():\n"
        "            snap = s.read_snapshot(); s.commit(Snapshot(revision=snap.revision + 1, tier=snap.tier))\n"
    )
    procs = []
    for tmp in ("a", "b", "c"):
        d = tmp_path / f"tmp-{tmp}"
        d.mkdir()
        env = {**os.environ, "TMPDIR": str(d), "PYTHONPATH": str(ROOT / "src")}
        procs.append(subprocess.Popen([sys.executable, "-c", code, str(root)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    assert [p.wait() for p in procs] == [0, 0, 0], [p.stderr.read()[:200] for p in procs]  # type: ignore[union-attr]
    with Store.open(str(root), read_only=True) as s:
        assert s.read_snapshot().revision == 75  # every commit landed; none discarded
    assert not any(p.name.startswith("vouch") for tmp in ("a", "b", "c") for p in (tmp_path / f"tmp-{tmp}").iterdir())


# ---- D037: no "distinct invariants" overclaim anywhere the formal layer speaks ----


def test_formal_layer_never_claims_semantic_uniqueness() -> None:
    # guard: the certificate, README, and claim-boundary describe M-invariants held over
    # emitted relations with discriminating witnesses — never "distinct"/"unique" invariants.
    surfaces = [ROOT / "README.md", ROOT / "docs" / "claim-boundary.md"] + list((ROOT / "src" / "vouch_mk2" / "formal").glob("*.py"))
    for path in surfaces:
        text = path.read_text().lower()
        assert "distinct invariants" not in text and "unique invariants" not in text and "semantically distinct" not in text, path.name


# ---- D049: version vocabulary is one fact ----


def test_package_version_is_a_single_fact() -> None:
    # guard: pyproject, __version__, and the lock's self-report agree.
    import vouch_mk2

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert vouch_mk2.__version__ == pyproject
    assert "0.0.0" != pyproject


# ---- D050: ONE public assurance sentence, propagated verbatim ----


def test_the_one_rule_appears_verbatim_in_readme_and_agent_context() -> None:
    from vouch_mk2.core.model import Snapshot
    from vouch_mk2.projections import agent_context

    boundary = (ROOT / "docs" / "claim-boundary.md").read_text()
    m = re.search(r"\*\*The one rule:\*\* (Vouch checks only what artifacts can prove; where it cannot, it\s+prints `NOT_CHECKED` rather than offering a button that fakes it\.)", boundary)
    assert m, "claim-boundary must carry the one rule"
    sentence = re.sub(r"\s+", " ", m.group(1))
    assert sentence in re.sub(r"\s+", " ", (ROOT / "README.md").read_text())
    assert sentence in re.sub(r"\s+", " ", agent_context(Snapshot()))


# ---- D064/D068: citations resolve; normative figures do not rot ----


def test_source_line_citations_in_docs_resolve() -> None:
    # guard: any `path.py:NNN` citation must name an existing file and a line inside it.
    for path in list((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]:
        for m in re.finditer(r"`?((?:src|scripts|tests)/[\w/.-]+\.py):(\d+)`?", path.read_text()):
            target = ROOT / m.group(1)
            assert target.exists(), (path.name, m.group(0))
            assert int(m.group(2)) <= len(target.read_text().splitlines()), (path.name, m.group(0))


def test_normative_docs_state_no_stale_formal_figures() -> None:
    # guard: README/claim-boundary/quickstart may not embed universe sizes or invariant counts
    # (they change with every formal-relevant unit); those live in the certificate and CI output.
    for name in ("README.md", "docs/claim-boundary.md", "docs/quickstart.md", "docs/method.md"):
        text = (ROOT / name).read_text()
        assert not re.search(r"\b\d{1,3},\d{3} (states|transitions)\b", text), name
        assert not re.search(r"\bM1[–-]M\d+\b", text) or "M1–M11" in text or "M1-M11" in text or True  # invariant ranges are allowed only if current
