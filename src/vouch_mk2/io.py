"""Small I/O-side helpers shared by thin transports.

This module deliberately sits outside the pure core and projection packages.
"""

from __future__ import annotations

import os
import secrets
import stat

from vouch_mk2.store.errors import StoreError


def read_regular_nofollow(path: str, purpose: str) -> bytes | None:
    """Read an optional regular file without following its final symlink."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StoreError(f"{path}: cannot inspect {purpose}: {error}") from error
    if stat.S_ISLNK(info.st_mode):
        raise StoreError(f"{path}: symlinked {purpose} refused")
    if not stat.S_ISREG(info.st_mode):
        raise StoreError(f"{path}: {purpose} is not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise StoreError(f"{path}: cannot read {purpose}: {error}") from error
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            try:
                return stream.read()
            except OSError as error:
                raise StoreError(f"{path}: cannot read {purpose}: {error}") from error
    finally:
        os.close(fd)


def write_atomic(path: str, data: bytes, mode: int, purpose: str) -> None:
    """Atomically replace ``path`` from a same-directory, fsynced temp file."""
    directory = os.path.dirname(path) or "."
    temp = os.path.join(
        directory,
        f".{os.path.basename(path)}.{secrets.token_hex(8)}.tmp",
    )
    temp_exists = False
    try:
        try:
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
            )
        except OSError as error:
            raise StoreError(f"{path}: cannot write {purpose}: {error}") from error
        temp_exists = True
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp, path)
        except OSError as error:
            raise StoreError(f"{path}: cannot write {purpose}: {error}") from error
        temp_exists = False
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as error:
            raise StoreError(f"{path}: cannot open parent directory: {error}") from error
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        raise StoreError(f"{path}: cannot write {purpose}: {error}") from error
    finally:
        if temp_exists:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
