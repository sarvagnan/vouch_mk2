"""Git-index conflict recovery around the authoritative semantic store merge."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from vouch_mk2.core.decide import Refused
from vouch_mk2.store.errors import StoreError
from vouch_mk2.store.event_log import ci_run_id
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots
from vouch_mk2.store.store import DEFAULT_MAX_ARTIFACT_BYTES, Store
from vouch_mk2.verify import emit_context, emit_system_map


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_SAFE_GIT_MODE = b"100644"
_MAX_GIT_OUTPUT = DEFAULT_MAX_ARTIFACT_BYTES


@dataclass(frozen=True, slots=True)
class _IndexEntry:
    mode: bytes
    object_id: bytes
    stage: int
    path: bytes


def _git_output(repository: str, *arguments: bytes) -> bytes:
    """Run one fixed Git plumbing command without a shell and bound its output."""
    command = (
        b"git",
        b"--no-replace-objects",
        b"-C",
        os.fsencode(repository),
        *arguments,
    )
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        raise StoreError(f"git.command-failed: cannot start Git: {error}") from error
    if process.stdout is None:
        process.kill()
        process.wait()
        raise StoreError("git.command-failed: Git stdout pipe is unavailable")
    try:
        output = process.stdout.read(_MAX_GIT_OUTPUT + 1)
        if len(output) > _MAX_GIT_OUTPUT:
            process.kill()
            process.wait()
            raise StoreError(
                f"git.output-too-large: Git output exceeds {_MAX_GIT_OUTPUT} bytes"
            )
        return_code = process.wait()
    finally:
        process.stdout.close()
    if return_code != 0:
        detail = output.decode("utf-8", errors="replace").strip()
        raise StoreError(
            f"git.command-failed: Git exited {return_code}"
            + (f": {detail}" if detail else "")
        )
    return output


def _open_absolute_directory(path: str) -> int:
    """Open an absolute directory component-by-component without following links."""
    absolute = os.path.abspath(path)
    components = tuple(component for component in absolute.split(os.sep) if component)
    try:
        directory_fd = os.open(os.sep, _DIRECTORY_FLAGS)
    except OSError as error:
        raise StoreError(f"git.path-unsafe: cannot open filesystem root: {error}") from error
    apparent = os.sep
    try:
        for component in components:
            apparent = os.path.join(apparent, component)
            try:
                child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as error:
                raise StoreError(
                    f"git.path-unsafe: {apparent}: expected a non-symlink directory: {error}"
                ) from error
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _repository_for_root(root: str) -> tuple[str, str]:
    parent = os.path.dirname(os.path.abspath(root))
    output = _git_output(parent, b"rev-parse", b"--show-prefix")
    if not output.endswith(b"\n") or b"\n" in output[:-1] or b"\x00" in output:
        raise StoreError("git.path-unsafe: Git returned an invalid repository prefix")
    raw_prefix = output[:-1]
    prefix_parts = tuple(part for part in raw_prefix.split(b"/") if part)
    if raw_prefix and not raw_prefix.endswith(b"/"):
        raise StoreError("git.path-unsafe: Git returned a non-directory repository prefix")
    if any(part in {b".", b".."} for part in prefix_parts):
        raise StoreError("git.path-unsafe: Git returned an unsafe repository prefix")
    repository = parent
    for _part in prefix_parts:
        repository = os.path.dirname(repository)
    repository_fd = _open_absolute_directory(repository)
    os.close(repository_fd)
    absolute_root = os.path.abspath(root)
    try:
        within_repository = os.path.commonpath((repository, absolute_root)) == repository
    except ValueError:
        within_repository = False
    if not within_repository or absolute_root == repository:
        raise StoreError("git.path-unsafe: store root must be below the repository root")
    relative_parts = tuple(os.fsdecode(part) for part in prefix_parts) + (
        os.path.basename(absolute_root),
    )
    relative = os.path.join(*relative_parts)
    return repository, relative


def _parse_index(data: bytes, root_relative: str) -> tuple[_IndexEntry, ...]:
    prefix = os.fsencode(root_relative.replace(os.sep, "/")) + b"/"
    entries: list[_IndexEntry] = []
    for row in data.split(b"\x00"):
        if not row:
            continue
        try:
            header, path = row.split(b"\t", 1)
            mode, object_id, raw_stage = header.split(b" ", 2)
            stage = int(raw_stage)
        except (ValueError, UnicodeError) as error:
            raise StoreError("git.index-invalid: malformed index stage row") from error
        if stage not in {0, 1, 2, 3}:
            raise StoreError(f"git.index-invalid: unsupported index stage {stage}")
        if not path.startswith(prefix):
            raise StoreError("git.path-unsafe: index path is outside the requested store root")
        relative = path[len(prefix) :]
        components = relative.split(b"/")
        if not relative or any(component in {b"", b".", b".."} for component in components):
            raise StoreError("git.path-unsafe: index path is not a safe store-relative path")
        if b"\x00" in relative:
            raise StoreError("git.path-unsafe: index path contains NUL")
        entries.append(_IndexEntry(mode, object_id, stage, relative))
    return tuple(entries)


def _validated_stage_maps(
    entries: tuple[_IndexEntry, ...],
) -> tuple[dict[bytes, bytes], dict[bytes, bytes]]:
    by_path: dict[bytes, dict[int, _IndexEntry]] = {}
    for entry in entries:
        stages = by_path.setdefault(entry.path, {})
        if entry.stage in stages:
            raise StoreError(
                f"git.index-invalid: duplicate stage {entry.stage} for {os.fsdecode(entry.path)!r}"
            )
        stages[entry.stage] = entry

    has_conflict = any(
        stage != 0 for stages in by_path.values() for stage in stages
    )
    if not has_conflict:
        raise StoreError(
            "git.index-no-conflict: index has no unmerged path under the store root"
        )

    for entry in entries:
        if entry.mode != _SAFE_GIT_MODE:
            path = os.fsdecode(entry.path)
            mode = entry.mode.decode("ascii", errors="replace")
            raise StoreError(
                f"git.index-mode-unsafe: {path!r} has mode {mode}; expected 100644"
            )

    ours: dict[bytes, bytes] = {}
    theirs: dict[bytes, bytes] = {}
    for path, stages in by_path.items():
        stage_numbers = frozenset(stages)
        if stage_numbers == {0}:
            ours[path] = stages[0].object_id
            theirs[path] = stages[0].object_id
            continue
        if 0 in stages or not {2, 3}.issubset(stages) or not stage_numbers.issubset({1, 2, 3}):
            raise StoreError(
                "git.index-stages-incomplete: "
                f"{os.fsdecode(path)!r} has stages {sorted(stage_numbers)}; expected paired 2 and 3"
            )
        ours[path] = stages[2].object_id
        theirs[path] = stages[3].object_id
    return ours, theirs


def _open_relative_parent(root_fd: int, path: bytes, *, create: bool) -> tuple[int, bytes]:
    components = path.split(b"/")
    if not components or any(component in {b"", b".", b".."} for component in components):
        raise StoreError("git.path-unsafe: unsafe store-relative publication path")
    directory_fd = os.dup(root_fd)
    try:
        for component in components[:-1]:
            try:
                child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=directory_fd)
                    child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
                except OSError as error:
                    raise StoreError(
                        f"git.path-unsafe: cannot create publication directory {os.fsdecode(component)!r}: {error}"
                    ) from error
            except OSError as error:
                raise StoreError(
                    f"git.path-unsafe: publication ancestor {os.fsdecode(component)!r} is unsafe: {error}"
                ) from error
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd, components[-1]
    except BaseException:
        os.close(directory_fd)
        raise


def _write_relative(root_fd: int, path: bytes, payload: bytes) -> None:
    parent_fd, filename = _open_relative_parent(root_fd, path, create=True)
    temp_name = b".git-merge-" + secrets.token_hex(16).encode("ascii") + b".tmp"
    temp_exists = False
    try:
        try:
            fd = os.open(temp_name, _FILE_WRITE_FLAGS, 0o644, dir_fd=parent_fd)
            temp_exists = True
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temp_exists = False
            os.fsync(parent_fd)
        except OSError as error:
            raise StoreError(
                f"git.publish-failed: cannot publish {os.fsdecode(path)!r}: {error}"
            ) from error
    finally:
        if temp_exists:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def _materialize_store(
    repository: str,
    destination: str,
    objects: Mapping[bytes, bytes],
) -> None:
    os.mkdir(destination, 0o700)
    root_fd = _open_absolute_directory(destination)
    try:
        for path, object_id in sorted(objects.items()):
            payload = _git_output(repository, b"cat-file", b"blob", object_id)
            _write_relative(root_fd, path, payload)
    finally:
        os.close(root_fd)
    with Store.open(destination, read_only=True) as store:
        store.read_snapshot()


def _candidate_files(candidate: str) -> tuple[tuple[bytes, bytes], ...]:
    files: list[tuple[bytes, bytes]] = []
    for directory, names, filenames in os.walk(candidate, followlinks=False):
        names.sort()
        filenames.sort()
        relative_directory = os.path.relpath(directory, candidate)
        for name in filenames:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise StoreError(f"git.path-unsafe: candidate entry {path!r} is not a regular file")
            relative = name if relative_directory == "." else os.path.join(relative_directory, name)
            if relative == ".lock":
                continue
            try:
                fd = os.open(path, _FILE_READ_FLAGS)
            except OSError as error:
                raise StoreError(f"git.path-unsafe: cannot read candidate {path!r}: {error}") from error
            try:
                if info.st_size > DEFAULT_MAX_ARTIFACT_BYTES:
                    raise StoreError(
                        f"git.output-too-large: candidate {relative!r} exceeds the artifact bound"
                    )
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    payload = stream.read()
            finally:
                os.close(fd)
            files.append((os.fsencode(relative.replace(os.sep, "/")), payload))
    return tuple(files)


@contextmanager
def _publication_lock(root_fd: int) -> Iterator[None]:
    try:
        lock_fd = os.open(b".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
    except OSError as error:
        raise StoreError(f"git.path-unsafe: cannot open store publication lock: {error}") from error
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _publish_candidate(root_fd: int, candidate: str) -> None:
    files = _candidate_files(candidate)
    ordered = tuple(item for item in files if item[0] != b"state.json") + tuple(
        item for item in files if item[0] == b"state.json"
    )
    if not ordered or ordered[-1][0] != b"state.json":
        raise StoreError("git.publish-failed: candidate store has no state.json")
    with _publication_lock(root_fd):
        for path, payload in ordered:
            _write_relative(root_fd, path, payload)


def merge_git_stages(root: str, *, actor: str) -> MergeOutcome | Refused:
    """Recover one conflicted store from Git's paired index stages."""
    root_fd = _open_absolute_directory(root)
    try:
        repository, root_relative = _repository_for_root(root)
        index_data = _git_output(
            repository,
            b"ls-files",
            b"--stage",
            b"-z",
            b"--",
            os.fsencode(root_relative),
        )
        ours, theirs = _validated_stage_maps(_parse_index(index_data, root_relative))
        with tempfile.TemporaryDirectory(
            prefix=".vouch-git-merge-", dir=repository
        ) as staging:
            ours_root = os.path.join(staging, "ours")
            theirs_root = os.path.join(staging, "theirs")
            _materialize_store(repository, ours_root, ours)
            _materialize_store(repository, theirs_root, theirs)
            result = merge_store_roots(
                ours_root,
                theirs_root,
                actor=actor,
                run_id=ci_run_id(),
            )
            if isinstance(result, Refused):
                return result
            with Store.open(ours_root) as candidate_store:
                emit_system_map(candidate_store)
                emit_context(candidate_store, None)
            current_index = _git_output(
                repository,
                b"ls-files",
                b"--stage",
                b"-z",
                b"--",
                os.fsencode(root_relative),
            )
            if current_index != index_data:
                raise StoreError("git.concurrent-update: index changed during recovery")
            _publish_candidate(root_fd, ours_root)
            return result
    finally:
        os.close(root_fd)


__all__ = ["merge_git_stages"]
