"""Pure invariants shared by every authoritative snapshot ingress."""

from __future__ import annotations

from typing import assert_never

from vouch_mk2.core.model import (
    EvidenceKind,
    EvidenceOutcome,
    Snapshot,
    Unit,
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


def valid_command_line(text: str) -> bool:
    """Return whether a rendered argv is bounded printable ledger text."""
    return (
        bool(text)
        and len(text) <= 4096
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


def current_pack_evidence_is_green(snapshot: Snapshot, unit: Unit) -> bool:
    """Whether the exact unit's current pack has a RED→GREEN result still green.

    ``REFACTOR`` records preserve the preceding result; the fold maintains this
    summary so the pure publication gate never needs artifact I/O.
    """
    head = snapshot.evidence_head_ref(unit.ref())
    if head is None:
        return False
    summary = next(
        (
            item
            for item in head.pack_summaries
            if item.pack_digest == unit.acceptance_test_set_digest
        ),
        None,
    )
    return (
        summary is not None
        and summary.green
        and (summary.red_seen or unit.pack_binding == "rebound")
        and (
            not unit.rebound_history
            or head.head_sequence
            > unit.rebound_history[-1].evidence_head_sequence
        )
    )


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

    if snapshot.event_head and (
        len(snapshot.event_head) != 64
        or any(character not in "0123456789abcdef" for character in snapshot.event_head)
    ):
        add(
            "event.head-invalid",
            "event_head must be empty or 64 lowercase hexadecimal characters",
        )

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
        digest = unit.acceptance_pack_digest
        test_set_digest = unit.acceptance_test_set_digest
        if digest and (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            add(
                "pack.digest-invalid",
                f"units[{index}].acceptance_pack_digest must be empty or 64 "
                "lowercase hexadecimal characters",
            )
        if digest and not unit.acceptance_pack_version:
            add(
                "pack.digest-without-version",
                f"units[{index}] binds a pack digest without a pack version",
            )
        if test_set_digest and (
            len(test_set_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in test_set_digest
            )
        ):
            add(
                "pack.test-set-digest-invalid",
                f"units[{index}].acceptance_test_set_digest must be empty or 64 "
                "lowercase hexadecimal characters",
            )
        if bool(test_set_digest) != bool(digest):
            add(
                "pack.test-set-digest-incoherent",
                f"units[{index}].acceptance_test_set_digest must be empty iff "
                "its pack digest is empty",
            )
        if unit.pack_binding not in {"", "at-freeze", "late", "rebound"}:
            add(
                "pack.binding-invalid",
                f"units[{index}].pack_binding must be '', 'at-freeze', 'late', or 'rebound'",
            )
        if bool(unit.pack_binding) != bool(digest):
            add(
                "pack.binding-incoherent",
                f"units[{index}].pack_binding must be empty iff its pack digest is empty",
            )
        if unit.pack_binding == "rebound" and not unit.rebound_history:
            add(
                "rebind.history-missing",
                f"units[{index}] has rebound binding without rebound history",
            )
        previous_rebound_digest = ""
        previous_rebound_sequence = -1
        for rebound_index, rebound in enumerate(unit.rebound_history):
            for field_name, rebound_digest in (
                ("old_inventory_digest", rebound.old_inventory_digest),
                ("new_inventory_digest", rebound.new_inventory_digest),
                ("old_test_set_digest", rebound.old_test_set_digest),
                ("new_test_set_digest", rebound.new_test_set_digest),
            ):
                if len(rebound_digest) != 64 or any(
                    character not in "0123456789abcdef"
                    for character in rebound_digest
                ):
                    add(
                        "rebind.history-digest-invalid",
                        f"units[{index}].rebound_history[{rebound_index}].{field_name} "
                        "must be 64 lowercase hexadecimal characters",
                    )
            if not rebound.reason.strip():
                add(
                    "rebind.history-reason-empty",
                    f"units[{index}].rebound_history[{rebound_index}].reason "
                    "must be non-empty",
                )
            if rebound.evidence_head_sequence < previous_rebound_sequence:
                add(
                    "rebind.history-sequence-regressed",
                    f"units[{index}].rebound_history[{rebound_index}] evidence "
                    "sequence precedes the prior rebound",
                )
            if previous_rebound_digest and (
                rebound.old_inventory_digest != previous_rebound_digest
            ):
                add(
                    "rebind.history-disconnected",
                    f"units[{index}].rebound_history[{rebound_index}] old inventory "
                    "does not equal the prior new inventory",
                )
            previous_rebound_digest = rebound.new_inventory_digest
            previous_rebound_sequence = rebound.evidence_head_sequence
        if unit.rebound_history:
            latest = unit.rebound_history[-1]
            if (
                latest.new_inventory_digest != unit.acceptance_pack_digest
                and unit.pack_binding == "rebound"
            ):
                add(
                    "rebind.history-current-mismatch",
                    f"units[{index}] rebound history does not name its current inventory",
                )
            if (
                latest.new_test_set_digest != unit.acceptance_test_set_digest
                and unit.pack_binding == "rebound"
            ):
                add(
                    "rebind.history-current-test-set-mismatch",
                    f"units[{index}] rebound history does not name its current test set",
                )
        seen_pack_versions: set[str] = set()
        for history_index, pack_version in enumerate(unit.pack_history):
            if not valid_identifier(pack_version):
                add(
                    "identifier.invalid",
                    f"units[{index}].pack_history[{history_index}] "
                    f"{pack_version!r} is not a valid identifier",
                )
            if pack_version in seen_pack_versions:
                add(
                    "pack.history-duplicate",
                    f"units[{index}].pack_history[{history_index}] duplicates "
                    f"retired version {pack_version!r}",
                )
            seen_pack_versions.add(pack_version)
        if unit.acceptance_pack_version in seen_pack_versions:
            add(
                "pack.version-reused",
                f"units[{index}] reuses retired acceptance pack version "
                f"{unit.acceptance_pack_version!r}",
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

    seen_head_refs: set[tuple[str, str]] = set()
    for index, head in enumerate(snapshot.evidence_heads):
        if not valid_unit_identifier(head.unit_name):
            add(
                "identifier.invalid",
                f"evidence_heads[{index}].unit_name {head.unit_name!r} is not "
                "a valid identifier",
            )
        if not valid_unit_identifier(head.unit_version):
            add(
                "identifier.invalid",
                f"evidence_heads[{index}].unit_version {head.unit_version!r} is not "
                "a valid identifier",
            )
        key = (head.unit_name, head.unit_version)
        if key in seen_head_refs:
            add(
                "evidence.head-duplicate",
                f"evidence_heads[{index}] duplicates unit ref {head.ref().label()!r}",
            )
        seen_head_refs.add(key)
        if snapshot.unit_ref(head.ref()) is None:
            add(
                "evidence.unit-unresolved",
                f"evidence head for {head.ref().label()!r} does not resolve to a unit",
            )
        summary_digests = tuple(
            summary.pack_digest for summary in head.pack_summaries
        )
        if summary_digests != tuple(sorted(summary_digests)):
            add(
                "evidence.summary-order-invalid",
                f"evidence_heads[{index}].pack_summaries must be sorted by pack digest",
            )
        if len(summary_digests) != len(set(summary_digests)):
            add(
                "evidence.summary-duplicate",
                f"evidence_heads[{index}].pack_summaries contains duplicate pack digests",
            )
        for summary_index, summary in enumerate(head.pack_summaries):
            digest = summary.pack_digest
            if digest and (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                add(
                    "evidence.pack-digest-invalid",
                    f"evidence_heads[{index}].pack_summaries[{summary_index}].pack_digest "
                    "must be empty or 64 lowercase hexadecimal characters",
                )
    seen_ci_exceptions: set[str] = set()
    known_ci_refs = {unit.ref().label() for unit in snapshot.units}
    for index, exception in enumerate(snapshot.ci_exceptions):
        binding_parts = exception.unit_name.rsplit("@", 1)
        valid_binding = (
            len(binding_parts) == 2
            and valid_unit_identifier(binding_parts[0])
            and valid_unit_identifier(binding_parts[1])
        ) or valid_unit_identifier(exception.unit_name)
        if not valid_binding:
            add(
                "identifier.invalid",
                f"ci_exceptions[{index}].unit_name {exception.unit_name!r} "
                "is not a valid unit ref",
            )
        if exception.unit_name in seen_ci_exceptions:
            add(
                "ci-policy.exception-duplicate",
                f"ci_exceptions[{index}] duplicates unit {exception.unit_name!r}",
            )
        seen_ci_exceptions.add(exception.unit_name)
        if (
            exception.unit_name not in known_ci_refs
            and snapshot.unit(exception.unit_name) is None
        ):
            add(
                "ci-policy.exception-unit-unknown",
                f"ci_exceptions[{index}] names missing unit ref "
                f"{exception.unit_name!r}",
            )
        if not exception.reason.strip():
            add(
                "ci-policy.reason-required",
                f"ci_exceptions[{index}].reason must be non-empty",
            )
    cycle = _dependency_cycle(snapshot)
    if cycle is not None:
        add("unit.dependency-cycle", f"dependency cycle: {cycle!r}")

    return tuple(found)


__all__ = [
    "current_pack_evidence_is_green",
    "evidence_kind_outcome_coherent",
    "valid_command_line",
    "valid_identifier",
    "valid_unit_identifier",
    "validate_snapshot",
]
