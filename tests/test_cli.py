from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture

from vouch_mk2.cli import main
from vouch_mk2.core.model import (
    Deviation,
    DeviationStatus,
    DeviationType,
    Snapshot,
    Unit,
    UnitRef,
    UnitStatus,
)
from vouch_mk2.ir import mk2
from vouch_mk2.store import Store


def init_store(root: Path) -> None:
    store = Store.init(str(root))
    store.close()


def commit_drifted_snapshot(root: Path) -> None:
    # A blocking deviation bound to an in-progress unit: decodable, but
    # unreachable through decide() — the standing checks must catch it.
    with Store.open(str(root)) as store:
        store.commit(
            Snapshot(
                revision=1,
                units=(
                    Unit(
                        name="u",
                        version="v1",
                        status=UnitStatus.IN_PROGRESS,
                        acceptance_pack_version="p1",
                    ),
                ),
                deviations=(
                    Deviation(
                        deviation_id="d",
                        deviation_type=DeviationType.PACK_DEFECT,
                        unit=UnitRef(name="u", version="v1"),
                        status=DeviationStatus.OPEN,
                    ),
                ),
            )
        )


def test_cli_check_on_fresh_store_passes(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    assert main(["check", "--ci", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "organizational (never mechanically provable)" in output
    assert output.rstrip().endswith("composed: PASS")


def test_cli_check_on_drifted_store_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_drifted_snapshot(root)
    assert main(["check", "--ci", "--root", str(root)]) == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert mk2.CHECK_BLOCKED_STATUS_COHERENT in output
    assert output.rstrip().endswith("composed: FAIL")


def test_cli_json_matches_text_composition_and_lists_organizational_ids(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    init_store(root)
    commit_drifted_snapshot(root)

    assert main(["check", "--ci", "--root", str(root)]) == 1
    text_output = capsys.readouterr().out
    text_composed = text_output.rstrip().splitlines()[-1].split(": ", 1)[1].lower()

    assert main(["check", "--ci", "--json", "--root", str(root)]) == 1
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert isinstance(payload, dict)
    assert payload["composed"] == text_composed
    organizational = payload["organizational"]
    assert isinstance(organizational, list)
    organizational_ids = {
        row["check_id"] for row in organizational if isinstance(row, dict)
    }
    assert organizational_ids == {
        mk2.ORG_ACCEPTANCE_OWNERSHIP,
        mk2.ORG_INTENT_VALIDATION,
        mk2.ORG_INDEPENDENCE,
        mk2.ORG_CAUSAL_RED_HISTORY,
        mk2.ORG_PROTECTIVE_CLOSURE_TRUTHFULNESS,
    }


def test_cli_bad_root_returns_typed_transport_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    bad_root = tmp_path / "nonexistent"
    assert main(["check", "--root", str(bad_root)]) == 2
    captured = capsys.readouterr()
    assert captured.err
