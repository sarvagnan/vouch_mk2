"""Capability-rooted, crash-consistent storage for the authoritative snapshot."""

from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from typing import Iterator

from vouch_mk2.core.invariants import validate_snapshot
from vouch_mk2.core.model import Snapshot
from vouch_mk2.store.codec import decode_snapshot, encode_snapshot
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.errors import CasConflict, IntegrityError, StoreError


COMMIT_STEPS: tuple[str, ...] = (
    "lock-acquired",
    "cas-checked",
    "object-temp-written",
    "object-fsynced",
    "object-placed",
    "objects-dir-fsynced",
    "state-temp-written",
    "state-temp-fsynced",
    "state-replaced",
    "root-fsynced",
    "lock-released",
)

_DIGEST_LENGTH = 64
_ROOT_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# O_NONBLOCK: opening a planted FIFO must not hang the process; the fstat
# S_ISREG check right after the open then refuses it with a typed error.
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_TEMP_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

# Level-2 bounded decoding (docs/threat-model.md): a planted multi-gigabyte
# artifact is refused before allocation, not after.
DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
GENERATED_FILES = frozenset(
    {"map.json", "migration-report.md", "vouch-context.md"}
)


class Store:
    """A store holding one O_DIRECTORY descriptor as its root capability.

    ``on_step`` is a TEST-ONLY fault-injection seam. Production callers should
    leave it unset; tests may raise at a named completed-operation boundary.
    """

    def __init__(
        self,
        root: str,
        on_step: Callable[[str], None] | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        self._validate_artifact_bound(max_artifact_bytes)
        self._require_platform_capabilities()
        self._root_path = os.path.abspath(root)
        try:
            self._root_fd = os.open(self._root_path, _ROOT_FLAGS)
        except OSError as error:
            raise StoreError(f"root: cannot open store directory: {error}") from error
        self._on_step = on_step
        self._max_artifact_bytes = max_artifact_bytes
        self._closed = False
        self._read_only = False
        self._write_lock_fd: int | None = None
        self._write_lock_owner: int | None = None
        self._write_lock_depth = 0
        self._write_lock_guard = threading.Condition(threading.Lock())

    @staticmethod
    def _require_platform_capabilities() -> None:
        """Fail closed if safe descriptor-relative primitives are unavailable."""
        required = (
            ("os.open", os.open in os.supports_dir_fd),
            ("os.stat", os.stat in os.supports_dir_fd),
            ("os.mkdir", os.mkdir in os.supports_dir_fd),
            ("os.rename", os.rename in os.supports_dir_fd),
            ("os.unlink", os.unlink in os.supports_dir_fd),
            (
                "os.stat(follow_symlinks=False)",
                os.stat in os.supports_follow_symlinks,
            ),
        )
        for operation, supported in required:
            if not supported:
                raise StoreError(f"platform: {operation} capability is required")

    @classmethod
    def init(
        cls,
        root: str,
        on_step: Callable[[str], None] | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> Store:
        """Create a fresh layout and open its root capability exactly once."""
        cls._validate_artifact_bound(max_artifact_bytes)
        try:
            os.mkdir(root, 0o700)
        except OSError as error:
            raise StoreError(
                f"root: cannot create store directory: {error}"
            ) from error
        store = cls(root, on_step=on_step, max_artifact_bytes=max_artifact_bytes)
        try:
            store._initialize_layout()
        except BaseException:
            store.close()
            raise
        return store

    @classmethod
    def open(
        cls,
        root: str,
        on_step: Callable[[str], None] | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        *,
        read_only: bool = False,
    ) -> Store:
        """Open an existing store with one root directory capability.

        Writable handles opportunistically sweep orphan temps only when they can
        acquire ``LOCK_EX | LOCK_NB`` immediately. Actual writers use blocking
        ``LOCK_EX`` and wait for the current writer. ``read_only=True`` handles
        never create or acquire the lock, skip the sweep, and refuse every store
        mutation."""
        store = cls(root, on_step=on_step, max_artifact_bytes=max_artifact_bytes)
        store._read_only = read_only
        try:
            store._check_layout()
        except BaseException:
            store.close()
            raise
        return store

    def close(self) -> None:
        """Release the held root capability; repeated calls are harmless."""
        if not self._closed:
            os.close(self._root_fd)
            self._closed = True

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()

    def _step(self, step_id: str) -> None:
        if self._on_step is not None:
            self._on_step(step_id)

    @staticmethod
    def _validate_artifact_bound(max_artifact_bytes: int) -> None:
        if max_artifact_bytes < 1:
            raise StoreError("artifact bound must be at least 1 byte")

    def _require_within_artifact_bound(self, payload: bytes, label: str) -> None:
        if len(payload) > self._max_artifact_bytes:
            raise StoreError(
                f"{label}: {len(payload)} bytes exceeds artifact bound "
                f"{self._max_artifact_bytes}"
            )

    def _require_writable(self, operation: str) -> None:
        if self._read_only:
            raise StoreError(f"{operation} refused: store handle is read-only")

    def _require_write_lock(self, operation: str) -> None:
        thread_id = threading.get_ident()
        with self._write_lock_guard:
            if (
                self._write_lock_owner != thread_id
                or self._write_lock_fd is None
                or self._write_lock_depth < 1
            ):
                raise StoreError(
                    f"{operation} refused: writer lock is not held by current thread"
                )

    def _initialize_layout(self) -> None:
        state = encode_snapshot(Snapshot())
        self._require_within_artifact_bound(state, "initial state")
        os.mkdir("objects", 0o700, dir_fd=self._root_fd)
        objects_fd = self._open_directory("objects", self._root_fd)
        try:
            os.mkdir("sha256", 0o700, dir_fd=objects_fd)
            # Make the sha256 entry durable now: no later code path fsyncs
            # objects/ again, and a lost entry would orphan every object.
            os.fsync(objects_fd)
        finally:
            os.close(objects_fd)

        lock_fd = os.open(
            ".lock",
            _TEMP_WRITE_FLAGS,
            0o600,
            dir_fd=self._root_fd,
        )
        try:
            os.fsync(lock_fd)
        finally:
            os.close(lock_fd)

        state_fd = os.open(
            "state.json",
            _TEMP_WRITE_FLAGS,
            0o600,
            dir_fd=self._root_fd,
        )
        try:
            self._write_all(state_fd, state)
            os.fsync(state_fd)
        finally:
            os.close(state_fd)
        os.fsync(self._root_fd)

    def _check_layout(self) -> None:
        self._assert_not_symlink("state.json", self._root_fd, "state.json")
        if self._read_only:
            self._check_optional_lock_file()
        else:
            self._ensure_lock_file()
            self._assert_not_symlink(".lock", self._root_fd, ".lock")
        objects_fd = self._open_objects_directory()
        os.close(objects_fd)
        if not self._read_only:
            self._sweep_orphan_temps()

    def _sweep_orphan_temps(self) -> None:
        """Reclaim temp files orphaned by a killed process.

        A non-blocking lock probe makes this opportunistic garbage collection:
        a live writer's temps are skipped, never swept or waited on."""
        lock_fd = self._open_lock()
        locked = False
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            locked = True
            for name in os.listdir(self._root_fd):
                if (
                    name.startswith(".state-")
                    or name.startswith(".generated-")
                ) and name.endswith(".tmp"):
                    self._unlink_if_present(name, self._root_fd)
            objects_fd = self._open_objects_directory()
            try:
                for name in os.listdir(objects_fd):
                    if name.startswith(".object-") and name.endswith(".tmp"):
                        self._unlink_if_present(name, objects_fd)
            finally:
                os.close(objects_fd)
        finally:
            if locked:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    @staticmethod
    def _open_directory(name: str, parent_fd: int) -> int:
        try:
            return os.open(name, _ROOT_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise IntegrityError(f"{name}: cannot open directory: {error}") from error

    def _open_objects_directory(self) -> int:
        objects_fd = self._open_directory("objects", self._root_fd)
        try:
            return self._open_directory("sha256", objects_fd)
        finally:
            os.close(objects_fd)

    def _ensure_lock_file(self) -> None:
        """Create the runtime lock file if it is absent.

        ``.lock`` carries no state — it is a flock() primitive — so a store
        checked out from version control (the committed dogfood store, D-10)
        legitimately arrives without it. Creating it is not a write to the
        store's content. O_EXCL keeps a concurrent creator harmless (EEXIST)
        and O_NOFOLLOW keeps a planted symlink from being followed; the
        symlink check that follows still refuses a symlinked ``.lock``.
        """
        try:
            os.stat(".lock", dir_fd=self._root_fd, follow_symlinks=False)
            return
        except FileNotFoundError:
            pass
        except OSError as error:
            raise IntegrityError(f".lock: cannot inspect: {error}") from error
        try:
            fd = os.open(".lock", _TEMP_WRITE_FLAGS, 0o600, dir_fd=self._root_fd)
        except FileExistsError:
            return
        except OSError as error:
            raise IntegrityError(f".lock: missing and cannot be created: {error}") from error
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self._root_fd)

    def _check_optional_lock_file(self) -> None:
        """Validate an existing lock entry without creating or opening it."""
        try:
            info = os.stat(".lock", dir_fd=self._root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise IntegrityError(f".lock: cannot inspect: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise IntegrityError(".lock: symlink refused")

    def _open_lock(self) -> int:
        try:
            return os.open(
                ".lock",
                os.O_RDWR | os.O_NOFOLLOW,
                dir_fd=self._root_fd,
            )
        except OSError as error:
            raise IntegrityError(f".lock: cannot open: {error}") from error

    @contextmanager
    def write_locked(self) -> Iterator[None]:
        """Hold the store's exclusive writer lock across a full mutation.

        CLI transports use this boundary around read/decide/apply/commit so a
        command is decided against the same head it commits.  The context is
        re-entrant for helpers such as ``record_evidence`` which own their
        decide/apply/commit sequence but may be called by an already-locked
        transport.
        """
        self._require_writable("write lock")
        thread_id = threading.get_ident()
        outermost = False
        with self._write_lock_guard:
            if self._write_lock_owner == thread_id:
                self._write_lock_depth += 1
            else:
                while self._write_lock_owner is not None:
                    self._write_lock_guard.wait()
                lock_fd = self._open_lock()
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(lock_fd)
                    raise
                self._write_lock_fd = lock_fd
                self._write_lock_owner = thread_id
                self._write_lock_depth = 1
                outermost = True
        try:
            if outermost:
                self._step("lock-acquired")
            yield
        finally:
            released = False
            with self._write_lock_guard:
                if self._write_lock_owner != thread_id:
                    raise RuntimeError("writer lock ownership changed unexpectedly")
                self._write_lock_depth -= 1
                if self._write_lock_depth == 0:
                    lock_fd = self._write_lock_fd
                    if lock_fd is None:
                        raise RuntimeError("writer lock descriptor is missing")
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                    self._write_lock_fd = None
                    self._write_lock_owner = None
                    released = True
                    self._write_lock_guard.notify_all()
            if released:
                self._step("lock-released")

    @staticmethod
    def _assert_not_symlink(name: str, directory_fd: int, path: str) -> None:
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as error:
            raise IntegrityError(f"{path}: cannot inspect: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise IntegrityError(f"{path}: symlink refused")

    def _read_file(self, directory_fd: int, name: str, path: str) -> bytes:
        self._assert_not_symlink(name, directory_fd, path)
        try:
            fd = os.open(name, _FILE_READ_FLAGS, dir_fd=directory_fd)
        except OSError as error:
            raise IntegrityError(f"{path}: cannot open: {error}") from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise IntegrityError(f"{path}: expected regular file")
            if info.st_size > self._max_artifact_bytes:
                raise IntegrityError(
                    f"{path}: {info.st_size} bytes exceeds the "
                    f"{self._max_artifact_bytes}-byte artifact bound"
                )
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read()
        finally:
            os.close(fd)

    def read_snapshot(self) -> Snapshot:
        """Read state.json once and strictly decode the authoritative snapshot."""
        return decode_snapshot(self.read_state_bytes())

    def read_state_bytes(self) -> bytes:
        """Read raw state bytes without weakening ``read_snapshot`` decoding."""
        return self._read_file(self._root_fd, "state.json", "state.json")

    @staticmethod
    def _validate_generated_name(name: str) -> None:
        if name not in GENERATED_FILES:
            raise StoreError(f"generated file {name!r} is not allowlisted")

    def generated_path(self, name: str) -> str:
        """Return the caller-visible absolute path of one allowlisted output."""
        self._validate_generated_name(name)
        return os.path.join(self._root_path, name)

    def read_generated(self, name: str) -> bytes | None:
        """Read one optional allowlisted generated file, refusing symlinks."""
        self._validate_generated_name(name)
        try:
            return self._read_file(self._root_fd, name, name)
        except IntegrityError as error:
            if self._is_missing(error):
                return None
            raise

    def write_generated(self, name: str, data: bytes) -> None:
        """Atomically replace one allowlisted generated file."""
        self._require_writable("write-generated")
        self._validate_generated_name(name)
        self._require_within_artifact_bound(data, f"generated file {name!r}")
        temp_name = f".generated-{secrets.token_hex(16)}.tmp"
        temp_exists = False
        try:
            try:
                temp_fd = os.open(
                    temp_name,
                    _TEMP_WRITE_FLAGS,
                    0o600,
                    dir_fd=self._root_fd,
                )
            except OSError as error:
                raise StoreError(
                    f"{temp_name}: cannot create generated-file temp: {error}"
                ) from error
            temp_exists = True
            try:
                self._write_all(temp_fd, data)
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)
            try:
                os.replace(
                    temp_name,
                    name,
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                )
            except (TypeError, NotImplementedError) as error:
                raise StoreError(
                    "platform: descriptor-relative replace is unavailable; "
                    "refusing a path-based fallback"
                ) from error
            temp_exists = False
            os.fsync(self._root_fd)
        finally:
            if temp_exists:
                self._unlink_if_present(temp_name, self._root_fd)

    @staticmethod
    def _validate_digest(digest: str) -> None:
        if len(digest) != _DIGEST_LENGTH or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise IntegrityError("digest: expected 64 lowercase hexadecimal characters")

    def get_object(self, kind: ArtifactKind, digest: str) -> bytes:
        """Load an object only when its bytes hash to its validated filename."""
        self._validate_digest(digest)
        objects_fd = self._open_objects_directory()
        try:
            payload = self._read_file(
                objects_fd,
                digest,
                f"objects/sha256/{digest}",
            )
        finally:
            os.close(objects_fd)
        actual = digest_of(kind, payload)
        if actual != digest:
            raise IntegrityError(
                f"objects/sha256/{digest}: content digest is {actual}"
            )
        return payload

    def put_object(self, kind: ArtifactKind, payload: bytes) -> str:
        """Durably place an immutable object, detecting corrupt prior content."""
        self._require_writable("put-object")
        self._require_write_lock("put-object")
        self._require_within_artifact_bound(payload, f"{kind.value} object")
        digest = digest_of(kind, payload)
        objects_fd = self._open_objects_directory()
        try:
            self._put_object_at(objects_fd, kind, payload, digest)
        finally:
            os.close(objects_fd)
        return digest

    def _put_object_at(
        self,
        objects_fd: int,
        kind: ArtifactKind,
        payload: bytes,
        digest: str,
    ) -> None:
        try:
            existing = self._read_file(
                objects_fd,
                digest,
                f"objects/sha256/{digest}",
            )
        except IntegrityError as error:
            if not self._is_missing(error):
                raise
        else:
            if digest_of(kind, existing) != digest:
                raise IntegrityError(
                    f"objects/sha256/{digest}: existing object is corrupt"
                )
            # The object may have been placed by a process that crashed before
            # its fsyncs; re-fsync file and directory so THIS commit never
            # durably references a non-durable object.
            fd = os.open(digest, _FILE_READ_FLAGS, dir_fd=objects_fd)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(objects_fd)
            return

        temp_name = f".object-{secrets.token_hex(16)}.tmp"
        temp_exists = False
        try:
            try:
                temp_fd = os.open(
                    temp_name,
                    _TEMP_WRITE_FLAGS,
                    0o600,
                    dir_fd=objects_fd,
                )
            except OSError as error:
                raise StoreError(
                    f"objects/sha256/{temp_name}: cannot create temp: {error}"
                ) from error
            temp_exists = True
            try:
                self._write_all(temp_fd, payload)
                self._step("object-temp-written")
                os.fsync(temp_fd)
                self._step("object-fsynced")
            finally:
                os.close(temp_fd)

            # renameat is atomic on the same directory. A competing writer can
            # replace only with bytes for the same domain-separated digest.
            os.rename(
                temp_name,
                digest,
                src_dir_fd=objects_fd,
                dst_dir_fd=objects_fd,
            )
            temp_exists = False
            self._step("object-placed")
            os.fsync(objects_fd)
            self._step("objects-dir-fsynced")
        finally:
            if temp_exists:
                self._unlink_if_present(temp_name, objects_fd)

    @staticmethod
    def _is_missing(error: IntegrityError) -> bool:
        cause = error.__cause__
        return isinstance(cause, FileNotFoundError)

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(fd, view[written:])
            if count == 0:
                raise OSError(errno.EIO, "short write made no progress")
            written += count

    @staticmethod
    def _unlink_if_present(name: str, directory_fd: int) -> None:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass

    def _replace_state(self, temp_name: str) -> None:
        # The descriptor-relative replace closes the D070 ancestor-swap window
        # at the commit boundary; there is deliberately NO path-based fallback
        # (docs/threat-model.md level 2). Both supported platforms (macOS,
        # Linux) provide renameat; CPython on macOS under-reports it in
        # os.supports_dir_fd, so availability is probed by attempt, and a
        # runtime that genuinely lacks it fails closed here.
        try:
            os.replace(
                temp_name,
                "state.json",
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
        except (TypeError, NotImplementedError) as error:
            raise StoreError(
                "platform: descriptor-relative replace is unavailable; "
                "refusing a path-based fallback"
            ) from error

    def replace_state_bytes(self, data: bytes) -> None:
        """Atomically replace raw state bytes while the writer lock is held."""
        self._require_writable("replace-state")
        self._require_write_lock("replace-state")
        self._require_within_artifact_bound(data, "state")
        temp_name = f".state-{secrets.token_hex(16)}.tmp"
        temp_exists = False
        try:
            try:
                state_fd = os.open(
                    temp_name,
                    _TEMP_WRITE_FLAGS,
                    0o600,
                    dir_fd=self._root_fd,
                )
            except OSError as error:
                raise StoreError(
                    f"{temp_name}: cannot create temp: {error}"
                ) from error
            temp_exists = True
            try:
                self._write_all(state_fd, data)
                self._step("state-temp-written")
                os.fsync(state_fd)
                self._step("state-temp-fsynced")
            finally:
                os.close(state_fd)

            self._replace_state(temp_name)
            temp_exists = False
            self._step("state-replaced")
            os.fsync(self._root_fd)
            self._step("root-fsynced")
        finally:
            if temp_exists:
                self._unlink_if_present(temp_name, self._root_fd)

    def commit(
        self,
        snapshot: Snapshot,
        objects: Sequence[tuple[ArtifactKind, bytes]] = (),
    ) -> None:
        """CAS-commit objects first, then atomically replace authoritative state."""
        self._require_writable("commit")
        violations = validate_snapshot(snapshot)
        if violations:
            raise StoreError(
                "snapshot invariant violation(s): "
                + "; ".join(
                    f"{violation.code}: {violation.detail}"
                    for violation in violations
                )
            )
        data = encode_snapshot(snapshot)
        self._require_within_artifact_bound(data, "state")
        for kind, payload in objects:
            self._require_within_artifact_bound(payload, f"{kind.value} object")
        with self.write_locked():
            current = self.read_snapshot()
            expected = snapshot.revision - 1
            if current.revision != expected:
                raise CasConflict(
                    f"revision: expected on-disk {expected}, found {current.revision}"
                )
            self._step("cas-checked")

            for kind, payload in objects:
                self.put_object(kind, payload)

            self.replace_state_bytes(data)
