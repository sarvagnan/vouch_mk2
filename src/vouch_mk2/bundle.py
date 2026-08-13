"""Filesystem-side construction and binding of canonical release inventories."""

from __future__ import annotations

import os
import stat

from vouch_mk2.core import (
    Accepted,
    BindBundle,
    BundleInventory,
    InventoryEntry,
    apply,
    decide,
)
from vouch_mk2.store import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    ArtifactKind,
    RelPath,
    Store,
    StoreError,
    digest_of,
    encode_bundle_inventory,
)


class BundleError(StoreError):
    """Base class for bundle construction failures."""


class InvalidExclusionPattern(BundleError):
    """An exclusion pattern uses an unsupported wildcard form."""


class InventoryError(BundleError):
    """A tree entry cannot safely participate in an inventory."""


def _pattern_segments(pattern: str) -> tuple[str, ...]:
    segments = tuple(pattern.split("/"))
    for segment in segments:
        if "**" in segment and segment != "**":
            raise InvalidExclusionPattern(
                f"pattern {pattern!r}: '**' must occupy a whole segment"
            )
    return segments


def _matches(
    path: tuple[str, ...], pattern: tuple[str, ...], *, descendants_only: bool
) -> bool:
    memo: dict[tuple[int, int], bool] = {}

    def visit(path_index: int, pattern_index: int) -> bool:
        key = (path_index, pattern_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern):
            found = path_index == len(path)
        else:
            segment = pattern[pattern_index]
            if segment == "**":
                minimum = (
                    1
                    if descendants_only and pattern_index == len(pattern) - 1
                    else 0
                )
                found = any(
                    visit(next_index, pattern_index + 1)
                    for next_index in range(path_index + minimum, len(path) + 1)
                )
            elif path_index == len(path):
                found = False
            elif segment == "*" or segment == path[path_index]:
                found = visit(path_index + 1, pattern_index + 1)
            else:
                found = False
        memo[key] = found
        return found

    return visit(0, 0)


def matches_exclusion(path: RelPath, pattern: str) -> bool:
    """Match whole path segments using literals, ``*``, and ``**``.

    The historical D-pruning examples are normative: ``a/*/**`` excludes
    descendants of an immediate child (``a/x/y``) but not the child (``a/x``);
    ``**/golden/**`` excludes descendants below any ``golden`` directory; and
    ``a/b`` matches only ``a/b``, never ``a/b/c``.
    """
    segments = _pattern_segments(pattern)
    return _matches(
        path.segments,
        segments,
        descendants_only=bool(segments and segments[-1] == "**"),
    )


def _prunes(path: RelPath, patterns: tuple[tuple[str, ...], ...]) -> bool:
    for pattern in patterns:
        if pattern == ("**",):
            # The universal pattern excludes every file at every depth, so
            # every directory is prunable — without this case its empty
            # prefix would match only the root and prune coverage would be
            # narrower than exclusion coverage.
            return True
        if (
            len(pattern) > 0
            and pattern[-1] == "**"
            and _matches(path.segments, pattern[:-1], descendants_only=False)
        ):
            return True
    return False


def _read_regular_file(path: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise InventoryError(f"{path}: cannot open: {error}") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise InventoryError(f"{path}: expected regular file")
        if info.st_size > DEFAULT_MAX_ARTIFACT_BYTES:
            raise InventoryError(
                f"{path}: exceeds the {DEFAULT_MAX_ARTIFACT_BYTES}-byte artifact bound"
            )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def build_inventory(root: str, exclusions: tuple[str, ...]) -> BundleInventory:
    patterns = tuple(_pattern_segments(pattern) for pattern in exclusions)
    absolute_root = os.path.abspath(root)
    try:
        root_info = os.lstat(absolute_root)
    except OSError as error:
        raise InventoryError(
            f"{absolute_root}: cannot inspect root: {error}"
        ) from error
    if stat.S_ISLNK(root_info.st_mode):
        raise InventoryError(f"{absolute_root}: symlink root refused")
    if not stat.S_ISDIR(root_info.st_mode):
        raise InventoryError(f"{absolute_root}: expected directory root")
    entries: list[InventoryEntry] = []

    # Iterative walk: tree depth comes from the (level-2) filesystem, so it
    # must not be able to blow the interpreter recursion limit into an
    # untyped RecursionError.
    pending: list[tuple[str, tuple[str, ...]]] = [(absolute_root, ())]
    while pending:
        directory, prefix = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise InventoryError(f"{directory}: cannot enumerate: {error}") from error
        for child in children:
            relative_text = "/".join(prefix + (child.name,))
            try:
                relative = RelPath.parse(relative_text)
            except StoreError as error:
                raise InventoryError(f"{relative_text!r}: {error}") from error
            try:
                child_info = child.stat(follow_symlinks=False)
            except OSError as error:
                raise InventoryError(
                    f"{relative_text}: cannot inspect: {error}"
                ) from error
            if stat.S_ISDIR(child_info.st_mode):
                if not _prunes(relative, patterns):
                    pending.append((child.path, relative.segments))
                continue
            # Exclusion is decided BEFORE the entry-type refusals: the
            # exclusion list is the designed escape hatch for content that
            # cannot be inventoried (a committed symlink, a FIFO), so an
            # excluded entry is skipped whatever it is. Refusals apply only
            # to entries the inventory would actually have to read.
            if any(matches_exclusion(relative, pattern) for pattern in exclusions):
                continue
            if stat.S_ISLNK(child_info.st_mode):
                raise InventoryError(f"{relative_text}: symlink refused")
            if not stat.S_ISREG(child_info.st_mode):
                raise InventoryError(f"{relative_text}: expected regular file")
            data = _read_regular_file(child.path)
            entries.append(
                InventoryEntry(
                    path=relative_text,
                    digest=digest_of(ArtifactKind.FILE, data),
                )
            )
    return BundleInventory(
        entries=tuple(sorted(entries, key=lambda entry: entry.path)),
        exclusions=exclusions,
    )


def bind_bundle(store: Store, release_id: str, inventory: BundleInventory) -> str:
    snapshot = store.read_snapshot()
    data = encode_bundle_inventory(inventory)
    digest = digest_of(ArtifactKind.BUNDLE_INVENTORY, data)
    decision = decide(snapshot, BindBundle(release_id, digest))
    if not isinstance(decision, Accepted):
        codes = tuple(violation.code for violation in decision.violations)
        raise StoreError(f"bind-bundle refused: {codes!r}")
    store.commit(
        apply(snapshot, decision.events),
        objects=((ArtifactKind.BUNDLE_INVENTORY, data),),
    )
    return digest
