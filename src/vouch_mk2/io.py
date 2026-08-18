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


def write_atomic_with_parents(
    path: str, data: bytes, mode: int, purpose: str
) -> None:
    """Create safe parent directories and atomically write the requested file.

    Existing directory symlinks are ordinary path components and may be followed.
    After following one, however, this command refuses to create any missing
    directory: that creation would occur outside the caller's apparent tree.
    The walk and final replace remain descriptor-relative so opened directories
    cannot be redirected by a later path swap.
    """
    absolute = os.path.abspath(path)
    parent, filename = os.path.split(absolute)
    if not filename:
        raise StoreError(f"{path}: {purpose} target has no filename")
    components = tuple(component for component in parent.split(os.sep) if component)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    nofollow_flags = directory_flags | os.O_NOFOLLOW
    try:
        directory_fd = os.open(os.sep, nofollow_flags)
    except OSError as error:
        raise StoreError(f"{path}: cannot open filesystem root: {error}") from error
    apparent = os.sep
    symlinked_ancestor: str | None = None
    try:
        for component in components:
            apparent = os.path.join(apparent, component)
            try:
                child_fd = os.open(
                    component, nofollow_flags, dir_fd=directory_fd
                )
            except FileNotFoundError:
                if symlinked_ancestor is not None:
                    raise StoreError(
                        f"{apparent}: refusing to create {purpose} parent "
                        f"through symlinked ancestor {symlinked_ancestor}; "
                        "directory creation would escape the requested tree"
                    )
                try:
                    os.mkdir(component, 0o755, dir_fd=directory_fd)
                    child_fd = os.open(
                        component, nofollow_flags, dir_fd=directory_fd
                    )
                except OSError as error:
                    raise StoreError(
                        f"{apparent}: cannot create {purpose} parent: {error}"
                    ) from error
            except OSError as error:
                try:
                    info = os.stat(
                        component, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError:
                    info = None
                if info is not None and stat.S_ISLNK(info.st_mode):
                    try:
                        child_fd = os.open(
                            component, directory_flags, dir_fd=directory_fd
                        )
                    except OSError as follow_error:
                        raise StoreError(
                            f"{apparent}: {purpose} parent symlink does not "
                            f"resolve to an existing directory: {follow_error}"
                        ) from follow_error
                    if symlinked_ancestor is None:
                        symlinked_ancestor = apparent
                else:
                    raise StoreError(
                        f"{apparent}: {purpose} parent is not a real directory: {error}"
                    ) from error
            os.close(directory_fd)
            directory_fd = child_fd

        temp = f".{filename}.{secrets.token_hex(8)}.tmp"
        temp_exists = False
        try:
            try:
                fd = os.open(
                    temp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    mode,
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise StoreError(
                    f"{path}: cannot write {purpose}: {error}"
                ) from error
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
                os.replace(
                    temp,
                    filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            except OSError as error:
                raise StoreError(
                    f"{path}: cannot write {purpose}: {error}"
                ) from error
            temp_exists = False
            os.fsync(directory_fd)
        finally:
            if temp_exists:
                try:
                    os.unlink(temp, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
    finally:
        os.close(directory_fd)
