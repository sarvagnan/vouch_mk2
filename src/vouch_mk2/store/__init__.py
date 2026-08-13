"""Public surface of the capability-rooted snapshot store."""

from vouch_mk2.store.codec import (
    decode_bundle_inventory,
    decode_evidence_event,
    decode_snapshot,
    encode_bundle_inventory,
    encode_evidence_event,
    encode_snapshot,
)
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.errors import (
    CasConflict,
    DecodeError,
    IntegrityError,
    PathError,
    StoreError,
)
from vouch_mk2.store.paths import RelPath
from vouch_mk2.store.store import (
    COMMIT_STEPS,
    DEFAULT_MAX_ARTIFACT_BYTES,
    Store,
)

__all__ = [
    "COMMIT_STEPS",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "ArtifactKind",
    "CasConflict",
    "DecodeError",
    "IntegrityError",
    "PathError",
    "RelPath",
    "Store",
    "StoreError",
    "decode_snapshot",
    "decode_bundle_inventory",
    "decode_evidence_event",
    "digest_of",
    "encode_snapshot",
    "encode_bundle_inventory",
    "encode_evidence_event",
]
