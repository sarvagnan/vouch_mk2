"""Pure formal-certificate value type; digest computation belongs to the store."""

from __future__ import annotations

from dataclasses import dataclass

CHECKER_VERSION = "formal-checker-v9"

type CertifiedUniverse = tuple[str, str, int, int]
type CertifiedInvariant = tuple[str, str, bool]


@dataclass(frozen=True, slots=True)
class FormalCertificate:
    checker_version: str
    tables_digest: str
    universes: tuple[CertifiedUniverse, ...]
    invariants: tuple[CertifiedInvariant, ...]
    witnesses: tuple[tuple[str, str, bool], ...]
