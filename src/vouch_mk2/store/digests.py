"""The sole registry and implementation for store artifact digests."""

from __future__ import annotations

import hashlib
from enum import Enum

from vouch_mk2.store.errors import StoreError


class ArtifactKind(Enum):
    """Closed digest domains; identical payloads in different domains never alias."""

    STATE = "state"
    OBJECT = "object"
    EVIDENCE = "evidence"
    BUNDLE_INVENTORY = "bundle-inventory"
    FORMAL_CERTIFICATE = "formal-certificate"
    FORMAL_TABLES = "formal-tables"
    FORMAL_RELATION = "formal-relation"
    FILE = "file"
    LOCK = "lock"
    ACCEPTANCE_PACK = "acceptance-pack"


def digest_of(kind: ArtifactKind, payload: bytes) -> str:
    """Return a domain-separated SHA-256 digest as lowercase hexadecimal.

    Framing is ``b"vouch-mk2/" + kind + NUL + payload``; injectivity requires
    every kind label to be NUL-free ASCII, which is validated here so a future
    enum member cannot silently break domain separation."""
    label = kind.value.encode("ascii")
    if b"\x00" in label or not label:
        raise StoreError(f"artifact kind {kind.value!r} must be non-empty, NUL-free")
    framed = b"vouch-mk2/" + label + b"\x00" + payload
    return hashlib.sha256(framed).hexdigest()
