"""Reproducible construction and self-identification of ``vouch.lock``."""

from __future__ import annotations

import io
import os
import sys
import zipfile

from vouch_mk2.store import ArtifactKind, StoreError, digest_of


SHEBANG = b"#!/usr/bin/env python3\n"
_MAIN = b"from vouch_mk2.cli import main; raise SystemExit(main())\n"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_EXTERNAL_ATTR = 0o100644 << 16
_COMPRESSLEVEL = 9


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = _ZIP_EXTERNAL_ATTR
    return info


def build_lock(source_root: str) -> bytes:
    """Build a deterministic zipapp from ``source_root/vouch_mk2`` only."""
    package_root = os.path.join(os.path.abspath(source_root), "vouch_mk2")
    if not os.path.isdir(package_root):
        raise StoreError(f"{package_root}: vouch_mk2 package directory not found")

    entries: list[tuple[str, bytes]] = [("__main__.py", _MAIN)]
    for directory, names, filenames in os.walk(package_root):
        # No symlinked subpackages: os.walk does not follow them, and a source
        # install would — the lock must not silently differ from the tree.
        for name in sorted(names):
            if os.path.islink(os.path.join(directory, name)):
                raise StoreError(
                    f"{os.path.join(directory, name)}: symlinked package directory refused"
                )
        names[:] = sorted(name for name in names if name != "__pycache__")
        for filename in sorted(filenames):
            # The digest is the artifact's IDENTITY (D-2/D-7): bind EVERY file
            # the package ships (py.typed, data files, ...), not only *.py — two
            # different trees must never share a digest. Only bytecode caches
            # and editor droppings are excluded, by name.
            if filename.startswith(".") or filename.endswith(
                (".pyc", ".pyo", ".orig", ".swp", "~")
            ):
                # Dotfiles (.DS_Store, editor state) and caches are host
                # droppings, not package content; everything else ships and
                # is bound.
                continue
            path = os.path.join(directory, filename)
            if os.path.islink(path):
                raise StoreError(f"{path}: symlinked package file refused")
            relative = os.path.relpath(path, package_root)
            archive_path = "vouch_mk2/" + relative.replace(os.sep, "/")
            try:
                with open(path, "rb") as stream:
                    payload = stream.read()
            except OSError as error:
                raise StoreError(f"{path}: cannot read lock source: {error}") from error
            entries.append((archive_path, payload))

    archive = io.BytesIO()
    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=_COMPRESSLEVEL,
    ) as bundle:
        bundle.comment = b""
        for archive_path, payload in sorted(entries, key=lambda item: item[0]):
            bundle.writestr(
                _entry(archive_path),
                payload,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=_COMPRESSLEVEL,
            )
    return SHEBANG + archive.getvalue()


def running_lock_path() -> str | None:
    """Return the archive the EXECUTING code was imported from, or ``None``.

    The ``__file__``-derived path is authoritative: it names the zip that
    zipimport actually loaded this module from. ``sys.argv[0]`` is only a
    fallback — it is caller-controlled and can name a different file than the
    one running (Phase 8 review: a decoy lock's digest was attested)."""
    candidates: list[str] = []
    marker = f"{os.sep}vouch_mk2{os.sep}"
    module_path = os.path.abspath(__file__)
    if marker in module_path:
        candidates.append(module_path.split(marker, 1)[0])
    candidates.append(os.path.abspath(sys.argv[0]))
    for candidate in candidates:
        if os.path.isfile(candidate) and zipfile.is_zipfile(candidate):
            return candidate
    return None


def running_lock_bytes() -> bytes | None:
    """Read the current zipapp without assuming how Python populated argv[0]."""
    path = running_lock_path()
    if path is None:
        return None
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise StoreError(f"{path}: cannot read running lock: {error}") from error


def lock_digest(payload: bytes) -> str:
    return digest_of(ArtifactKind.LOCK, payload)

