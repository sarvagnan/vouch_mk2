"""Frozen acceptance pack for self-hosted unit refreeze-green@1 (D-36).

Found by the activity_tracker_portal pilot, finding 3 (``vouch_mk2-bd4.4.5``): a pack was amended
by changing only files bound with ``--pack-file``, the data its bound tests load. The test-set
digest did not change, so after the re-freeze ``publish`` accepted the GREEN recorded against the
old pack, with no run against the amended files. Every test here failed against the pre-change
tree, except tests marked ``# guard:``. The implementer may not edit this file.
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core import Snapshot, UnitStatus
from vouch_mk2.projections.next_actions import next_actions
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
FAIL = [sys.executable, "-c", "raise SystemExit(1)"]
OK = [sys.executable, "-c", "raise SystemExit(0)"]
CODE = "gate.dod.current-pack-green"
DETAIL = "after the latest freeze"


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _must(*argv: str) -> str:
    code, output = _run(*argv)
    assert code == 0, (argv, output)
    return output


def _snapshot(root: str) -> Snapshot:
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot()


def _status(root: str) -> UnitStatus:
    unit = _snapshot(root).unit("u")
    assert unit is not None
    return unit.status


def _write_pack(project: Path, spec: str, test_body: str) -> None:
    (project / "packs").mkdir(exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    (project / "packs" / "u.spec.md").write_text(spec)
    (project / "packs" / "u.effects.json").write_text('{"effects_format": 1, "effects": []}')
    (project / "tests" / "t.py").write_text(f"def test_x():\n    {test_body}\n")


def _freeze(project: Path, root: str, pack_version: str, *, late: bool = False) -> None:
    """Freeze with the pack bound, or (``late``) label-only and then bound with ``bind-pack``."""
    files = (
        "--project", str(project), "--pack-file", "packs/u.spec.md",
        "--pack-test-file", "tests/t.py", "--pack-effects-file", "packs/u.effects.json",
    )
    if late:
        _must("freeze", "--root", root, "--name", "u", "--pack-version", pack_version)
        _must("bind-pack", "--root", root, "--name", "u", *files)
    else:
        _must("freeze", "--root", root, "--name", "u", "--pack-version", pack_version, *files)
    _must("mark-ready", "--root", root, "--name", "u")
    _must("start", "--root", root, "--name", "u")


def _evidence(root: str, kind: str, argv: list[str]) -> None:
    _must("record-evidence", "--root", root, "--unit", "u@v1", "--kind", kind, "--", *argv)


def _refrozen(
    tmp_path: Path, spec: str, test_body: str, *, late: bool = False
) -> tuple[Path, str]:
    """u@v1 frozen as u-pack/1, RED then GREEN, done; then amended and re-frozen as u-pack/2.

    The re-freeze binds ``spec`` and ``test_body`` (``late``: label-only, then ``bind-pack``); no
    evidence is recorded after it, and the unit is marked done again, so the next step is
    ``publish``.
    """
    project = tmp_path / "proj"
    project.mkdir()
    _must("init", "--project", str(project))
    root = str(project / ".vouch")
    _must("set-tier", "--root", root, "--tier", "standard")
    _write_pack(project, "# u acceptance pack\n", "assert True")
    _must("specify", "--root", root, "--name", "u", "--version", "v1")
    _freeze(project, root, "u-pack/1")
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _must("mark-done", "--root", root, "--name", "u")
    _must(
        "open-deviation", "--root", root, "--deviation-id", "DEV-1",
        "--deviation-type", "pack-defect", "--unit", "u@v1",
    )
    _write_pack(project, spec, test_body)
    _must(
        "adjudicate-amend", "--root", root, "--deviation-id", "DEV-1",
        "--amendment-version", "u-pack/2",
    )
    _must("close-deviation", "--root", root, "--deviation-id", "DEV-1")
    _must("respecify", "--root", root, "--name", "u")
    _freeze(project, root, "u-pack/2", late=late)
    _must("mark-done", "--root", root, "--name", "u")
    return project, root


def _publish_offered(root: str) -> bool:
    return any(
        action.subject_kind == "unit" and action.command == "publish"
        for action in next_actions(_snapshot(root))
    )


def _spec_only(tmp_path: Path) -> tuple[Path, str]:
    return _refrozen(tmp_path, "# u acceptance pack\n\nAmended: the vectors changed.\n", "assert True")


def test_publish_after_a_spec_only_refreeze_requires_a_fresh_green(tmp_path: Path) -> None:
    """R1."""
    _project, root = _spec_only(tmp_path)
    before = _snapshot(root)
    assert _status(root) is UnitStatus.DONE_PENDING_ACCEPTANCE
    code, output = _run("publish", "--root", root, "--name", "u")
    assert code == 2, output
    codes = re.findall(r"\bgate\.[a-z.-]+", output)
    assert codes == [CODE], output
    assert DETAIL in " ".join(output.split()), output
    assert _snapshot(root) == before


def test_next_offers_publish_only_after_the_fresh_green(tmp_path: Path) -> None:
    """R2."""
    _project, root = _spec_only(tmp_path)
    assert not _publish_offered(root)
    _evidence(root, "green", OK)
    assert _publish_offered(root)
    _must("publish", "--root", root, "--name", "u")
    assert _status(root) is UnitStatus.PUBLISHED


def test_a_refreeze_of_identical_bytes_keeps_the_earlier_green(tmp_path: Path) -> None:
    """R3. # guard:"""
    _project, root = _refrozen(tmp_path, "# u acceptance pack\n", "assert True")
    assert _publish_offered(root)
    _must("publish", "--root", root, "--name", "u")
    assert _status(root) is UnitStatus.PUBLISHED


def test_a_refreeze_that_changes_the_tests_still_needs_red_then_green(tmp_path: Path) -> None:
    """R4. # guard:"""
    _project, root = _refrozen(tmp_path, "# u acceptance pack\n", "assert 1 + 1 == 2")
    code, output = _run("publish", "--root", root, "--name", "u")
    assert code == 2, output
    assert re.findall(r"\bgate\.[a-z.-]+", output) == [CODE], output
    _evidence(root, "green", OK)
    code, output = _run("publish", "--root", root, "--name", "u")
    assert code == 2, output
    _evidence(root, "red", FAIL)
    _evidence(root, "green", OK)
    _must("publish", "--root", root, "--name", "u")
    assert _status(root) is UnitStatus.PUBLISHED


def test_a_label_only_refreeze_bound_late_to_changed_bytes_needs_a_fresh_green(
    tmp_path: Path,
) -> None:
    """R6 (pack/3, DEV-rg-02)."""
    _project, root = _refrozen(
        tmp_path, "# u acceptance pack\n\nAmended: the vectors changed.\n", "assert True", late=True
    )
    before = _snapshot(root)
    assert not _publish_offered(root)
    code, output = _run("publish", "--root", root, "--name", "u")
    assert code == 2, output
    assert re.findall(r"\bgate\.[a-z.-]+", output) == [CODE], output
    assert DETAIL in " ".join(output.split()), output
    assert _snapshot(root) == before
    _evidence(root, "green", OK)
    assert _publish_offered(root)
    _must("publish", "--root", root, "--name", "u")
    assert _status(root) is UnitStatus.PUBLISHED


def test_a_label_only_refreeze_bound_late_to_identical_bytes_keeps_the_earlier_green(
    tmp_path: Path,
) -> None:
    """R7 (pack/3, DEV-rg-02). # guard:"""
    _project, root = _refrozen(tmp_path, "# u acceptance pack\n", "assert True", late=True)
    assert _publish_offered(root)
    _must("publish", "--root", root, "--name", "u")
    assert _status(root) is UnitStatus.PUBLISHED


def test_quickstart_states_that_a_refreeze_needs_a_fresh_green() -> None:
    """R5."""
    text = " ".join((ROOT / "docs" / "quickstart.md").read_text().split())
    assert (
        "A re-freeze that changes any bound pack file needs a GREEN recorded after it" in text
    )
