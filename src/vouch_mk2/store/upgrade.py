"""Pure, contiguous state-document upgrades to the current format."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from vouch_mk2.core.model import UnitStatus
from vouch_mk2.store.codec import CURRENT_STATE_FORMAT
from vouch_mk2.store.errors import DecodeError, StoreError


type StateDocument = dict[str, object]
type Upgrader = Callable[[StateDocument], StateDocument]

_UNAMBIGUOUS_PUBLISHED_STATUSES = frozenset(
    {
        UnitStatus.PUBLISHED.value,
        UnitStatus.RELEASE_BOUND.value,
    }
)
_KNOWN_STATUSES = frozenset(status.value for status in UnitStatus)


class UpgradeRefused(StoreError):
    """A legacy document needs operator facts that it does not contain."""

    def __init__(
        self,
        message: str,
        *,
        operator_hint: str = (
            "provide --was-published NAME@V or --never-published NAME@V"
        ),
    ) -> None:
        self.operator_hint = operator_hint
        super().__init__(message)


class _AmbiguousEvidenceBinding(Exception):
    def __init__(self, unit_name: str, candidates: tuple[str, ...]) -> None:
        self.unit_name = unit_name
        self.candidates = candidates
        super().__init__(unit_name)


def _upgrade_0_to_1(document: StateDocument) -> StateDocument:
    upgraded = dict(document)
    if "tier" not in upgraded:
        upgraded["tier"] = "lightweight"

    raw_units = document.get("units")
    if type(raw_units) is list:
        units: list[object] = []
        for raw_unit in cast(list[object], raw_units):
            if type(raw_unit) is not dict:
                units.append(raw_unit)
                continue
            unit = cast(dict[str, object], raw_unit)
            copied = dict(unit)
            status = unit.get("status")
            if (
                "published_once" not in unit
                and type(status) is str
                and status in _UNAMBIGUOUS_PUBLISHED_STATUSES
            ):
                copied["published_once"] = True
            units.append(copied)
        upgraded["units"] = units

    upgraded["state_format"] = 1
    return upgraded


def _upgrade_1_to_2(document: StateDocument) -> StateDocument:
    """Record the truthful pre-D-14 fact: no acceptance pack was byte-bound."""
    upgraded = dict(document)
    raw_units = document.get("units")
    if type(raw_units) is list:
        units: list[object] = []
        for raw_unit in cast(list[object], raw_units):
            if type(raw_unit) is not dict:
                units.append(raw_unit)
                continue
            copied = dict(cast(dict[str, object], raw_unit))
            copied["acceptance_pack_digest"] = ""
            copied["pack_history"] = list[object]()
            units.append(copied)
        upgraded["units"] = units
    upgraded["state_format"] = 2
    return upgraded


def _upgrade_2_to_3(document: StateDocument) -> StateDocument:
    """Make name-keyed evidence heads exact without inventing evidence results.

    A sole active record, or a sole historical candidate when there is no
    active record, is derivable. Multiple superseded candidates are not.
    Per-pack RED/GREEN summary facts did not exist, so they truthfully upgrade
    to an empty tuple; immutable legacy evidence remains independently readable.
    """
    upgraded = dict(document)
    raw_units = document.get("units")
    candidates: dict[str, list[tuple[str, str]]] = {}
    if type(raw_units) is list:
        units: list[object] = []
        for raw_unit in cast(list[object], raw_units):
            if type(raw_unit) is not dict:
                units.append(raw_unit)
                continue
            unit = cast(dict[str, object], raw_unit)
            copied_unit = dict(unit)
            copied_unit["acceptance_test_set_digest"] = unit.get(
                "acceptance_pack_digest", ""
            )
            copied_unit["rebound_history"] = list[object]()
            units.append(copied_unit)
            name = unit.get("name")
            version = unit.get("version")
            status = unit.get("status")
            if type(name) is str and type(version) is str and type(status) is str:
                candidates.setdefault(name, []).append((version, status))
        upgraded["units"] = units

    raw_heads = document.get("evidence_heads")
    if type(raw_heads) is list:
        heads: list[object] = []
        for raw_head in cast(list[object], raw_heads):
            if type(raw_head) is not dict:
                heads.append(raw_head)
                continue
            copied = dict(cast(dict[str, object], raw_head))
            name = copied.get("unit_name")
            records = (
                candidates.get(name, list[tuple[str, str]]())
                if type(name) is str
                else list[tuple[str, str]]()
            )
            active = [
                version
                for version, status in records
                if status != UnitStatus.SUPERSEDED.value
            ]
            if len(active) == 1:
                version = active[0]
            elif not active and len(records) == 1:
                version = records[0][0]
            elif not active and len(records) > 1 and type(name) is str:
                raise _AmbiguousEvidenceBinding(
                    name,
                    tuple(sorted(f"{name}@{version}" for version, _ in records)),
                )
            else:
                version = ""
            copied["unit_version"] = version
            copied["pack_summaries"] = list[object]()
            heads.append(copied)
        upgraded["evidence_heads"] = heads

    upgraded["state_format"] = 3
    return upgraded


def _upgrade_3_to_4(document: StateDocument) -> StateDocument:
    """Add the truthful pre-log sentinel; the store layer records its checkpoint."""
    upgraded = dict(document)
    upgraded["event_head"] = ""
    upgraded["state_format"] = 4
    return upgraded


def _upgrade_4_to_5(document: StateDocument) -> StateDocument:
    """Older anchors carry no CI run claim; do not invent one during upgrade."""
    upgraded = dict(document)
    raw_heads = document.get("evidence_heads")
    if type(raw_heads) is list:
        heads: list[object] = []
        for raw_head in cast(list[object], raw_heads):
            if type(raw_head) is not dict:
                heads.append(raw_head)
                continue
            copied = dict(cast(dict[str, object], raw_head))
            copied["anchor_run_id"] = ""
            heads.append(copied)
        upgraded["evidence_heads"] = heads
    upgraded["state_format"] = 5
    return upgraded


UPGRADERS: dict[int, Upgrader] = {
    0: _upgrade_0_to_1,
    1: _upgrade_1_to_2,
    2: _upgrade_2_to_3,
    3: _upgrade_3_to_4,
    4: _upgrade_4_to_5,
}


def _repair_pre_extension_format_three(document: StateDocument) -> StateDocument:
    """Repair the format-3 layout committed before pack/3 finished.

    This branch exists only because format 3 was committed mid-unit inside this
    repository and may be dropped before release.
    """
    raw_units = document.get("units")
    if type(raw_units) is not list:
        return dict(document)
    changed = False
    units: list[object] = []
    for raw_unit in cast(list[object], raw_units):
        if type(raw_unit) is not dict:
            units.append(raw_unit)
            continue
        unit = cast(dict[str, object], raw_unit)
        copied = dict(unit)
        if "acceptance_test_set_digest" not in copied:
            # Every pack written before this mid-unit extension was format 1,
            # whose test-set digest is exactly its inventory digest (D-13).
            copied["acceptance_test_set_digest"] = copied.get(
                "acceptance_pack_digest", ""
            )
            changed = True
        if "rebound_history" not in copied:
            copied["rebound_history"] = list[object]()
            changed = True
        units.append(copied)
    if not changed:
        return dict(document)
    repaired = dict(document)
    repaired["units"] = units
    return repaired


def _prepare_format_zero_assertions(
    document: StateDocument,
    published_once: Mapping[str, bool],
) -> StateDocument:
    """Copy format 0 and apply only operator-provided latch assertions."""
    prepared = dict(document)
    raw_units = document.get("units")
    units: list[object] = []
    ambiguous: set[str] = set()
    missing: list[str] = []
    unknown_statuses: list[str] = []

    if type(raw_units) is list:
        for index, raw_unit in enumerate(cast(list[object], raw_units)):
            if type(raw_unit) is not dict:
                units.append(raw_unit)
                continue
            unit = cast(dict[str, object], raw_unit)
            copied = dict(unit)
            name = unit.get("name")
            version = unit.get("version")
            ref = (
                f"{name}@{version}"
                if type(name) is str and type(version) is str
                else f"units[{index}]"
            )
            status = unit.get("status")
            if type(status) is not str or status not in _KNOWN_STATUSES:
                unknown_statuses.append(f"{ref} has unknown status {status!r}")
            elif "published_once" not in unit:
                if status in _UNAMBIGUOUS_PUBLISHED_STATUSES:
                    copied["published_once"] = True
                else:
                    ambiguous.add(ref)
                    assertion = published_once.get(ref)
                    if assertion is None and ref not in published_once:
                        missing.append(ref)
                    elif type(assertion) is not bool:
                        unknown_statuses.append(
                            f"{ref} published_once assertion must be boolean"
                        )
                    else:
                        copied["published_once"] = assertion
            units.append(copied)
        prepared["units"] = units

    unknown_refs = sorted(set(published_once) - ambiguous)
    problems = list(unknown_statuses)
    if missing:
        problems.append(
            "published_once is ambiguous for " + ", ".join(sorted(missing))
        )
    if unknown_refs:
        problems.append(
            "unknown published_once assertion(s): " + ", ".join(unknown_refs)
        )
    if problems:
        raise UpgradeRefused("; ".join(problems))
    return prepared


def upgrade_document(
    document: StateDocument,
    published_once: Mapping[str, bool] | None = None,
    *,
    store_name: str = "state document",
) -> tuple[StateDocument, list[tuple[int, int]]]:
    """Return an upgraded copy and the ordered ``(from, to)`` steps applied."""
    raw_format = document.get("state_format", 0)
    if type(raw_format) is not int:
        raise DecodeError("state_format: expected integer")
    if raw_format > CURRENT_STATE_FORMAT:
        raise DecodeError(
            f"state_format: format {raw_format} was written by a newer vouch"
        )
    if raw_format < 0:
        raise DecodeError(f"state_format: no upgrader exists for format {raw_format}")

    assertions: Mapping[str, bool] = {} if published_once is None else published_once
    if raw_format == 0:
        upgraded = _prepare_format_zero_assertions(document, assertions)
    else:
        if assertions:
            raise UpgradeRefused(
                "unknown published_once assertion(s): "
                + ", ".join(sorted(assertions))
            )
        upgraded = dict(document)
    if raw_format == 3:
        upgraded = _repair_pre_extension_format_three(upgraded)
    steps: list[tuple[int, int]] = []
    current = raw_format
    while current < CURRENT_STATE_FORMAT:
        upgrader = UPGRADERS.get(current)
        if upgrader is None:
            raise DecodeError(
                f"state_format: no upgrader exists for format {current}"
            )
        try:
            upgraded = upgrader(upgraded)
        except _AmbiguousEvidenceBinding as error:
            candidates = ", ".join(error.candidates)
            raise UpgradeRefused(
                f"store {store_name!r}: evidence head for unit "
                f"{error.unit_name!r} has no active record and ambiguous candidates "
                f"{candidates}",
                operator_hint=(
                    "restore the correct candidate as the one active record in the "
                    "format-2 store, then retry the upgrade"
                ),
            ) from error
        if type(upgraded) is not dict:
            raise DecodeError(
                f"state_format: upgrader {current} -> {current + 1} "
                "did not return an object"
            )
        steps.append((current, current + 1))
        current += 1
    return upgraded, steps


__all__ = ["UPGRADERS", "UpgradeRefused", "upgrade_document"]
