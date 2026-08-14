"""Pure formal-certificate value type; digest computation belongs to the store."""

from __future__ import annotations

from dataclasses import dataclass

CHECKER_VERSION = "formal-checker-v1"


@dataclass(frozen=True, slots=True)
class FormalCertificate:
    universe_name: str
    checker_version: str
    tables_digest: str
    relation_digest: str
    state_count: int
    transition_count: int
    invariants: tuple[tuple[str, bool], ...]
    witnesses: tuple[tuple[str, str, bool], ...]
