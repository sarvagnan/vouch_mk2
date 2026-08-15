"""Typed failures exposed by the snapshot store."""


class StoreError(Exception):
    """Base class for all store-domain failures."""


class PathError(StoreError):
    """A path failed the store's relative-path validation."""


class DecodeError(StoreError):
    """Serialized state could not be decoded strictly into domain values."""


class IntegrityError(StoreError):
    """Stored filesystem content violated an integrity invariant."""


class CasConflict(StoreError):
    """A commit's expected predecessor revision was not current."""


class StoreBusy(StoreError):
    """The store's commit lock is held by another process (non-blocking probe)."""
