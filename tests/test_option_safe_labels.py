"""Frozen acceptance pack for self-hosted unit option-safe-labels@1 (D-40).

Found by Sol implementing option-safe-identifiers@1 (``vouch_mk2-rkh``): ``decide()`` accepted a
deviation id, release id, pack version or amendment version that begins with ``-``. Only the bound
form (``--deviation-id=-d``) could deliver one, and every later command that passes it as a separate
token exits 2, including commands ``next`` suggests. D-40 extends D-35's refusal
(``identifier.leading-dash``) to all four, in the command that introduces each. Every test here
failed against the pre-change tree, except tests marked ``# guard:``. The implementer may not edit
this file.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vouch_mk2.cli import main
from vouch_mk2.core import Accepted, Refused, Snapshot, decide
from vouch_mk2.core.commands import (
    AmendDeviation,
    Command,
    FreezeAcceptance,
    OpenDeviation,
    PrepareRelease,
)
from vouch_mk2.core.model import (
    Deviation,
    DeviationStatus,
    DeviationType,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.store import Store

ROOT = Path(__file__).resolve().parents[1]
CODE = "identifier.leading-dash"
DETAIL = "may not begin with '-'"
PACK = "a" * 64


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _specified() -> Snapshot:
    return Snapshot(revision=1, units=(Unit("u", "1", UnitStatus.SPECIFIED),))


def _in_progress() -> Snapshot:
    unit = Unit(
        "u", "1", UnitStatus.IN_PROGRESS, acceptance_pack_version="u-pack/1",
        acceptance_pack_digest=PACK, acceptance_test_set_digest=PACK,
        pack_history=(), pack_binding="at-freeze",
    )
    return Snapshot(revision=4, units=(unit,))


def _open_deviation() -> Snapshot:
    deviation = Deviation(
        "D1", DeviationType.PACK_DEFECT, UnitRef("u", "1"), DeviationStatus.OPEN
    )
    return Snapshot(revision=5, units=_in_progress().units, deviations=(deviation,))


def _published() -> Snapshot:
    unit = Unit(
        "u", "1", UnitStatus.PUBLISHED, acceptance_pack_version="u-pack/1",
        published_once=True, acceptance_pack_digest=PACK, acceptance_test_set_digest=PACK,
        pack_binding="at-freeze",
    )
    return Snapshot(revision=9, units=(unit,))


def _freeze(version: str) -> FreezeAcceptance:
    return FreezeAcceptance("u", version, PACK, PACK, effects_declared=True)


def _deviation(deviation_id: str) -> OpenDeviation:
    return OpenDeviation(deviation_id, DeviationType.PACK_DEFECT, UnitRef("u", "1"))


def _amend(version: str) -> AmendDeviation:
    return AmendDeviation("D1", version)


def _release(release_id: str) -> PrepareRelease:
    return PrepareRelease(release_id, ("u",))


def _codes(snapshot: Snapshot, command: Command) -> tuple[list[str], str]:
    decision = decide(snapshot, command)
    assert isinstance(decision, Refused), decision
    return (
        [violation.code for violation in decision.violations],
        decision.violations[0].detail,
    )


def _accepted(snapshot: Snapshot, command: Command) -> None:
    decision = decide(snapshot, command)
    assert isinstance(decision, Accepted), (command, decision)


def test_decide_refuses_a_pack_version_that_begins_with_a_dash() -> None:
    """R1."""
    _accepted(_specified(), _freeze("u-pack/1"))
    codes, detail = _codes(_specified(), _freeze("-p"))
    assert codes == [CODE]
    assert DETAIL in detail


def test_decide_refuses_a_deviation_id_that_begins_with_a_dash() -> None:
    """R2."""
    _accepted(_in_progress(), _deviation("D1"))
    codes, detail = _codes(_in_progress(), _deviation("-d"))
    assert codes == [CODE]
    assert DETAIL in detail


def test_decide_refuses_an_amendment_version_that_begins_with_a_dash() -> None:
    """R3."""
    _accepted(_open_deviation(), _amend("u-pack/2"))
    codes, detail = _codes(_open_deviation(), _amend("-a"))
    assert codes == [CODE]
    assert DETAIL in detail


def test_decide_refuses_a_release_id_that_begins_with_a_dash() -> None:
    """R4."""
    _accepted(_published(), _release("r1"))
    codes, detail = _codes(_published(), _release("-r"))
    assert codes == [CODE]
    assert DETAIL in detail


def _project(tmp_path: Path) -> str:
    project = tmp_path / "proj"
    project.mkdir()
    code, output = _run("init", "--project", str(project))
    assert code == 0, output
    root = str(project / ".vouch")
    code, output = _run("specify", "--root", root, "--name", "u", "--version", "1")
    assert code == 0, output
    return root


def _revision(root: str) -> int:
    with Store.open(root, read_only=True) as store:
        return store.read_snapshot().revision


def test_cli_freeze_refuses_a_dash_led_pack_version_and_records_nothing(tmp_path: Path) -> None:
    """R5: text output."""
    root = _project(tmp_path)
    before = _revision(root)
    code, output = _run("freeze", "--root", root, "--name", "u", "--pack-version=-p")
    assert code == 2, output
    assert CODE in output
    assert DETAIL in output
    assert _revision(root) == before


def test_cli_open_deviation_refuses_a_dash_led_id_as_json(tmp_path: Path) -> None:
    """R5: JSON output."""
    root = _project(tmp_path)
    code, output = _run("freeze", "--root", root, "--name", "u", "--pack-version", "u-pack/1")
    assert code == 0, output
    before = _revision(root)
    code, output = _run(
        "open-deviation", "--root", root, "--unit", "u@1", "--deviation-id=-d",
        "--deviation-type", "pack-defect", "--json",
    )
    assert code == 2, output
    result = json.loads(output)
    assert result["accepted"] is False
    assert [violation["code"] for violation in result["violations"]] == [CODE]
    assert _revision(root) == before


def test_cli_adjudicate_amend_refuses_a_dash_led_amendment_version(tmp_path: Path) -> None:
    """R5: amendment."""
    root = _project(tmp_path)
    for argv in (
        ("freeze", "--name", "u", "--pack-version", "u-pack/1"),
        ("open-deviation", "--unit", "u@1", "--deviation-id", "D1", "--deviation-type",
         "pack-defect"),
    ):
        code, output = _run(argv[0], "--root", root, *argv[1:])
        assert code == 0, output
    before = _revision(root)
    code, output = _run(
        "adjudicate-amend", "--root", root, "--deviation-id", "D1", "--amendment-version=-a"
    )
    assert code == 2, output
    assert CODE in output
    assert _revision(root) == before


def test_inner_and_trailing_dashes_are_still_accepted() -> None:
    """R6. # guard: these were legal before and stay legal."""
    _accepted(_specified(), _freeze("u-pack/1-rc"))
    _accepted(_specified(), _freeze("p-"))
    _accepted(_in_progress(), _deviation("DEV-osl-01"))
    _accepted(_in_progress(), _deviation("d-"))
    _accepted(_open_deviation(), _amend("u-pack/2-"))
    _accepted(_published(), _release("r-1"))
    _accepted(_published(), _release("r-"))


def test_other_invalid_labels_keep_the_identifier_invalid_code() -> None:
    """R7. # guard: where a label already had a validity rule, that rule is reported alone."""
    for label in ("", " -d", "-d\n"):
        if label:
            codes, _detail = _codes(_in_progress(), _deviation(label))
            assert codes == ["identifier.invalid"], label
        codes, _detail = _codes(_published(), _release(label))
        assert codes == ["identifier.invalid"], label
    for label in (" -p", "-p\n"):
        codes, _detail = _codes(_specified(), _freeze(label))
        assert codes == ["identifier.invalid"], label


def test_mk2_store_holds_no_dash_led_label() -> None:
    """R8. # guard: D-40 checks that mk2's own store holds no such value."""
    with Store.open(str(ROOT / ".vouch"), read_only=True) as store:
        snapshot = store.read_snapshot()
    labels = [deviation.deviation_id for deviation in snapshot.deviations]
    labels += [deviation.amendment_version for deviation in snapshot.deviations]
    labels += [release.release_id for release in snapshot.releases]
    for unit in snapshot.units:
        labels += [unit.acceptance_pack_version, *unit.pack_history]
    assert [label for label in labels if label.startswith("-")] == []


def test_quickstart_states_the_rule_for_all_four_labels() -> None:
    """R9."""
    text = re.sub(r"\s+", " ", (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8"))
    assert (
        "A deviation id, release id, pack version or amendment version may not begin with `-` "
        "either" in text
    )
