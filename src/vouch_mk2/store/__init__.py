"""Public surface of the capability-rooted snapshot store."""

from vouch_mk2.store.codec import decode_snapshot, encode_snapshot
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.errors import (
    CasConflict,
    DecodeError,
    IntegrityError,
    PathError,
    StoreError,
)
from vouch_mk2.store.paths import RelPath
from vouch_mk2.store.store import COMMIT_STEPS, Store

__all__ = [
    "COMMIT_STEPS",
    "ArtifactKind",
    "CasConflict",
    "DecodeError",
    "IntegrityError",
    "PathError",
    "RelPath",
    "Store",
    "StoreError",
    "decode_snapshot",
    "digest_of",
    "encode_snapshot",
]
