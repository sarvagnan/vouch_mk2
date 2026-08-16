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
            copied["pack_history"] = []
            units.append(copied)
        upgraded["units"] = units
    upgraded["state_format"] = 2
    return upgraded


UPGRADERS: dict[int, Upgrader] = {0: _upgrade_0_to_1, 1: _upgrade_1_to_2}


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
    steps: list[tuple[int, int]] = []
    current = raw_format
    while current < CURRENT_STATE_FORMAT:
        upgrader = UPGRADERS.get(current)
        if upgrader is None:
            raise DecodeError(
                f"state_format: no upgrader exists for format {current}"
            )
        upgraded = upgrader(upgraded)
        if type(upgraded) is not dict:
            raise DecodeError(
                f"state_format: upgrader {current} -> {current + 1} "
                "did not return an object"
            )
        steps.append((current, current + 1))
        current += 1
    return upgraded, steps


__all__ = ["UPGRADERS", "UpgradeRefused", "upgrade_document"]
