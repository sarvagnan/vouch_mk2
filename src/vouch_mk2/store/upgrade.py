"""Pure, contiguous state-document upgrades to the current format."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from vouch_mk2.core.model import UnitStatus
from vouch_mk2.store.codec import CURRENT_STATE_FORMAT
from vouch_mk2.store.errors import DecodeError


type StateDocument = dict[str, object]
type Upgrader = Callable[[StateDocument], StateDocument]

_PUBLISHED_STATUSES = frozenset(
    {
        UnitStatus.PUBLISHED.value,
        UnitStatus.RELEASE_BOUND.value,
        UnitStatus.SUPERSEDED.value,
    }
)
_KNOWN_STATUSES = frozenset(status.value for status in UnitStatus)


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
                and status in _KNOWN_STATUSES
            ):
                copied["published_once"] = status in _PUBLISHED_STATUSES
            units.append(copied)
        upgraded["units"] = units

    upgraded["state_format"] = 1
    return upgraded


UPGRADERS: dict[int, Upgrader] = {0: _upgrade_0_to_1}


def upgrade_document(
    document: StateDocument,
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


__all__ = ["UPGRADERS", "upgrade_document"]
