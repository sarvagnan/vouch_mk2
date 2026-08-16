"""Pure invariants shared by every authoritative snapshot ingress."""

from __future__ import annotations

from typing import assert_never

from vouch_mk2.core.model import (
    EvidenceKind,
    EvidenceOutcome,
    Snapshot,
    UnitStatus,
)
from vouch_mk2.ir.schema import Violation


def valid_identifier(text: str) -> bool:
    """Return whether ``text`` is a bounded, printable ledger identifier."""
    return (
        bool(text)
        and len(text) <= 200
        and text == text.strip()
        and text.isprintable()
        and all(
            ord(character) >= 0x20 and ord(character) != 0x7F
            for character in text
        )
    )


def valid_unit_identifier(text: str) -> bool:
    """Return whether ``text`` is an unambiguous unit-name/version component."""
    return valid_identifier(text) and "@" not in text


def evidence_kind_outcome_coherent(
    kind: EvidenceKind, outcome: EvidenceOutcome
) -> bool:
    """The one legal mapping from evidence kind to recorded test outcome."""
    match kind:
        case EvidenceKind.RED:
            match outcome:
                case EvidenceOutcome.TESTS_FAILED:
                    return True
                case EvidenceOutcome.TESTS_PASSED:
                    return False
                case _:
                    assert_never(outcome)
        case EvidenceKind.GREEN | EvidenceKind.REFACTOR:
            match outcome:
                case EvidenceOutcome.TESTS_FAILED:
                    return False
                case EvidenceOutcome.TESTS_PASSED:
                    return True
                case _:
                    assert_never(outcome)
        case _:
            assert_never(kind)


def _dependency_cycle(snapshot: Snapshot) -> tuple[str, ...] | None:
    graph = {
        unit.name: unit.depends_on
        for unit in snapshot.units
        if unit.status is not UnitStatus.SUPERSEDED
    }
    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> tuple[str, ...] | None:
        if name in active:
            start = path.index(name)
            return path[start:] + (name,)
        if name in visited or name not in graph:
            return None
        active.add(name)
        next_path = path + (name,)
        for dependency in graph[name]:
            cycle = visit(dependency, next_path)
            if cycle is not None:
                return cycle
        active.remove(name)
        visited.add(name)
        return None

    for name in graph:
        cycle = visit(name, ())
        if cycle is not None:
            return cycle
    return None


def validate_snapshot(snapshot: Snapshot) -> tuple[Violation, ...]:
    """Return every structural violation in one authoritative snapshot."""
    found: list[Violation] = []

    def add(code: str, detail: str) -> None:
        found.append(Violation(code=code, detail=detail))

    seen_unit_refs: set[tuple[str, str]] = set()
    active_names: set[str] = set()
    for index, unit in enumerate(snapshot.units):
        if not valid_unit_identifier(unit.name):
            add(
                "identifier.invalid",
                f"units[{index}].name {unit.name!r} is not a valid identifier",
            )
        if not valid_unit_identifier(unit.version):
            add(
                "identifier.invalid",
                f"units[{index}].version {unit.version!r} is not a valid identifier",
            )
        if unit.acceptance_pack_version and not valid_identifier(
            unit.acceptance_pack_version
        ):
            add(
                "identifier.invalid",
                "units["
                f"{index}].acceptance_pack_version {unit.acceptance_pack_version!r} "
                "is not a valid identifier",
            )
        for dependency_index, dependency in enumerate(unit.depends_on):
            if not valid_unit_identifier(dependency):
                add(
                    "identifier.invalid",
                    f"units[{index}].depends_on[{dependency_index}] "
                    f"{dependency!r} is not a valid unit-name identifier",
                )
            elif snapshot.unit(dependency) is None:
                # Parity with decide(): a bare-name dependency must name a
                # unit that exists (any version — dependencies are name-keyed
                # by design), else freeze/publish would refuse what decode and
                # commit admitted.
                add(
                    "unit.dependency-unknown",
                    f"units[{index}].depends_on[{dependency_index}] "
                    f"{dependency!r} does not name a unit in the snapshot",
                )
        key = (unit.name, unit.version)
        if key in seen_unit_refs:
            add(
                "unit.binding-duplicate",
                f"units[{index}] duplicates exact unit ref {unit.ref().label()!r}",
            )
        seen_unit_refs.add(key)
        if unit.status is not UnitStatus.SUPERSEDED:
            if unit.name in active_names:
                add(
                    "unit.active-name-duplicate",
                    f"units[{index}] duplicates active unit name {unit.name!r}",
                )
            active_names.add(unit.name)

    seen_deviation_ids: set[str] = set()
    for index, deviation in enumerate(snapshot.deviations):
        if not valid_identifier(deviation.deviation_id):
            add(
                "identifier.invalid",
                f"deviations[{index}].deviation_id {deviation.deviation_id!r} "
                "is not a valid identifier",
            )
        if deviation.deviation_id in seen_deviation_ids:
            add(
                "deviation.id-duplicate",
                f"deviations[{index}] duplicates id {deviation.deviation_id!r}",
            )
        seen_deviation_ids.add(deviation.deviation_id)
        if not valid_unit_identifier(
            deviation.unit.name
        ) or not valid_unit_identifier(
            deviation.unit.version
        ):
            add(
                "identifier.invalid",
                f"deviations[{index}].unit {deviation.unit.label()!r} contains "
                "an invalid identifier",
            )
        if snapshot.unit_ref(deviation.unit) is None:
            add(
                "deviation.unit-unresolved",
                f"deviations[{index}] binds missing unit {deviation.unit.label()!r}",
            )
        for impact_index, name in enumerate(deviation.impact_set):
            if not valid_unit_identifier(name):
                add(
                    "identifier.invalid",
                    f"deviations[{index}].impact_set[{impact_index}] {name!r} "
                    "is not a valid unit-name identifier",
                )
        for impact_index, release_id in enumerate(deviation.release_impact_set):
            if not valid_identifier(release_id):
                add(
                    "identifier.invalid",
                    f"deviations[{index}].release_impact_set[{impact_index}] "
                    f"{release_id!r} is not a valid release identifier",
                )

    seen_release_ids: set[str] = set()
    for release_index, release in enumerate(snapshot.releases):
        if not valid_identifier(release.release_id):
            add(
                "identifier.invalid",
                f"releases[{release_index}].release_id {release.release_id!r} "
                "is not a valid identifier",
            )
        if release.release_id in seen_release_ids:
            add(
                "release.id-duplicate",
                f"releases[{release_index}] duplicates id {release.release_id!r}",
            )
        seen_release_ids.add(release.release_id)
        if not release.includes:
            add(
                "release.roster-empty",
                f"release {release.release_id!r} has an empty roster",
            )
        seen_roster_refs: set[tuple[str, str]] = set()
        for ref_index, ref in enumerate(release.includes):
            key = (ref.name, ref.version)
            if key in seen_roster_refs:
                add(
                    "release.roster-duplicate",
                    f"releases[{release_index}].includes[{ref_index}] duplicates "
                    f"exact unit ref {ref.label()!r}",
                )
            seen_roster_refs.add(key)
            if not valid_unit_identifier(ref.name) or not valid_unit_identifier(
                ref.version
            ):
                add(
                    "identifier.invalid",
                    f"releases[{release_index}].includes[{ref_index}] "
                    f"{ref.label()!r} contains an invalid identifier",
                )
            if snapshot.unit_ref(ref) is None:
                add(
                    "release.roster-unresolved",
                    f"release {release.release_id!r} roster ref {ref.label()!r} "
                    "does not resolve",
                )
        if release.closure is not None:
            closure_refs: set[tuple[str, str]] = set()
            valid_statuses = {status.value for status in UnitStatus} | {"missing"}
            for row_index, (name, version, status) in enumerate(
                release.closure.roster_snapshot
            ):
                if not valid_unit_identifier(name):
                    add(
                        "identifier.invalid",
                        f"releases[{release_index}].closure.roster_snapshot["
                        f"{row_index}][0] {name!r} is not a valid unit-name "
                        "identifier",
                    )
                if not valid_unit_identifier(version):
                    add(
                        "identifier.invalid",
                        f"releases[{release_index}].closure.roster_snapshot["
                        f"{row_index}][1] {version!r} is not a valid unit-version "
                        "identifier",
                    )
                key = (name, version)
                if key in closure_refs:
                    add(
                        "release.closure-roster-duplicate",
                        f"releases[{release_index}].closure.roster_snapshot["
                        f"{row_index}] duplicates exact unit ref {name!r}@{version!r}",
                    )
                closure_refs.add(key)
                if status not in valid_statuses:
                    add(
                        "release.closure-roster-status-invalid",
                        f"releases[{release_index}].closure.roster_snapshot["
                        f"{row_index}][2] has unknown roster status {status!r}",
                    )
            if closure_refs != seen_roster_refs:
                add(
                    "release.closure-roster-mismatch",
                    f"release {release.release_id!r} closure roster refs "
                    f"{tuple(sorted(closure_refs))!r} do not equal release roster "
                    f"refs {tuple(sorted(seen_roster_refs))!r}",
                )

    seen_head_names: set[str] = set()
    for index, head in enumerate(snapshot.evidence_heads):
        if not valid_unit_identifier(head.unit_name):
            add(
                "identifier.invalid",
                f"evidence_heads[{index}].unit_name {head.unit_name!r} is not "
                "a valid identifier",
            )
        if head.unit_name in seen_head_names:
            add(
                "evidence.head-duplicate",
                f"evidence_heads[{index}] duplicates unit name {head.unit_name!r}",
            )
        seen_head_names.add(head.unit_name)
        if snapshot.unit(head.unit_name) is None:
            add(
                "evidence.unit-unresolved",
                f"evidence head for {head.unit_name!r} does not resolve to a unit",
            )

    cycle = _dependency_cycle(snapshot)
    if cycle is not None:
        add("unit.dependency-cycle", f"dependency cycle: {cycle!r}")

    return tuple(found)


__all__ = [
    "evidence_kind_outcome_coherent",
    "valid_identifier",
    "valid_unit_identifier",
    "validate_snapshot",
]
