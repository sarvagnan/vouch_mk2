"""Closed-set project initialization for the Phase 8 transport boundary."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from vouch_mk2.io import read_regular_nofollow, write_atomic
from vouch_mk2.lock import lock_digest
from vouch_mk2.core import Snapshot, Tier, TierSet
from vouch_mk2.store import Store, StoreError
from vouch_mk2.store.event_log import ci_run_id, commit_events, default_actor


class InitError(StoreError):
    """Initialization refused before knowingly overwriting project content."""


class InitStatus(Enum):
    WRITTEN = "written"
    UNCHANGED = "unchanged"
    SUGGESTED = "suggested"


@dataclass(frozen=True, slots=True)
class InitResult:
    path: str
    status: InitStatus


@dataclass(frozen=True, slots=True)
class _PlannedFile:
    report_path: str
    destination: str
    data: bytes
    mode: int
    status: InitStatus
    needs_write: bool


_WORKFLOW_TEMPLATE = """name: vouch

on:
  push:
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

jobs:
  validation:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: '3.14'
      - name: Verify vouch.lock against the recorded digest
        shell: bash
        run: python vouch.lock self-check --expect @.vouch/config.json
      - name: Vouch check
        shell: bash
        run: python vouch.lock check --ci --root .vouch --project . --pending-anchor-ok
      - name: Exhaustive Vouch formal check
        shell: bash
        run: python vouch.lock formal
__PROJECT_TEST_PLACEHOLDER__
  anchor:
    needs: [__ANCHOR_NEEDS__]
    if: github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork == false
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: '3.14'
      - name: Advance evidence anchors and commit projections
        shell: bash
        run: |
          python - <<'PY' > "$RUNNER_TEMP/vouch-evidence-heads"
          import json
          from pathlib import Path

          state = json.loads(Path(".vouch/state.json").read_text())
          for head in state["evidence_heads"]:
              if not head["anchor_run_id"]:
                  print(head["unit_name"])
          PY
          while IFS= read -r unit_name; do
            python vouch.lock advance-anchor --root .vouch --unit-name "$unit_name"
          done < "$RUNNER_TEMP/vouch-evidence-heads"
          python vouch.lock generate --root .vouch
          git add -- .vouch/
          if git diff --cached --quiet; then
            exit 0
          fi
          git config user.name "vouch-anchor-bot"
          git config user.email "vouch-anchor-bot@users.noreply.github.com"
          git commit -m "vouch: advance evidence anchors"
      - name: Push anchored projections
        shell: bash
        env:
          VOUCH_PUSH_TOKEN: ${{ github.token }}
        run: git push "https://x-access-token:${VOUCH_PUSH_TOKEN}@github.com/${{ github.repository }}.git" "HEAD:${{ github.event.repository.default_branch }}"
"""


_PROJECT_TEST_PLACEHOLDER = """
  project-tests-required:
    runs-on: ubuntu-latest
    steps:
      - name: Refuse an anchor not gated by project tests
        shell: bash
        run: |
          echo "No project test job is wired into Vouch anchoring; rerun init with --test-job NAME."
          exit 1
"""


def _validated_test_jobs(test_jobs: Sequence[str]) -> tuple[str, ...]:
    jobs = tuple(test_jobs)
    for job in jobs:
        if not job or not (job[0].isalpha() or job[0] == "_") or any(
            not (character.isalnum() or character in "_-") for character in job
        ):
            raise InitError(
                f"invalid --test-job {job!r}: expected a GitHub job id"
            )
    return jobs


def generated_workflow(test_jobs: Sequence[str] = ()) -> str:
    """Return the exact GitHub Actions workflow fragment written by ``init``."""
    jobs = _validated_test_jobs(test_jobs)
    placeholder = "" if jobs else _PROJECT_TEST_PLACEHOLDER
    anchor_needs = ", ".join(("validation", *(jobs or ("project-tests-required",))))
    return (
        _WORKFLOW_TEMPLATE.replace("__PROJECT_TEST_PLACEHOLDER__", placeholder)
        .replace("__ANCHOR_NEEDS__", anchor_needs)
    )


def _canonical_json(document: object) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _require_fresh_store(root: str) -> None:
    store_path = os.path.join(root, ".vouch")
    try:
        info = os.lstat(store_path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise InitError(f"{store_path}: cannot inspect store target: {error}") from error
    if stat.S_ISLNK(info.st_mode):
        raise InitError(f"{store_path}: symlinked store target refused")
    raise InitError(f"{store_path}: store already exists; refusing re-initialization")


def _plan_file(
    root: str,
    report_path: str,
    data: bytes,
    mode: int,
) -> _PlannedFile:
    target = os.path.join(root, *report_path.split("/"))
    current = read_regular_nofollow(target, "init target")
    if current is None:
        return _PlannedFile(
            report_path,
            target,
            data,
            mode,
            InitStatus.WRITTEN,
            True,
        )
    if current == data:
        return _PlannedFile(
            report_path,
            target,
            data,
            mode,
            InitStatus.UNCHANGED,
            False,
        )
    suggested = target + ".vouch-suggested"
    existing_suggestion = read_regular_nofollow(
        suggested, "init suggestion target"
    )
    if existing_suggestion == data:
        return _PlannedFile(
            report_path + ".vouch-suggested",
            suggested,
            data,
            mode,
            InitStatus.SUGGESTED,
            False,
        )
    if existing_suggestion is not None:
        raise InitError(
            f"{suggested}: existing suggestion differs; refusing to overwrite"
        )
    return _PlannedFile(
        report_path + ".vouch-suggested",
        suggested,
        data,
        mode,
        InitStatus.SUGGESTED,
        True,
    )


def _ensure_owned_directory(root: str, relative: str) -> tuple[str, ...]:
    """Create ``root/relative`` component by component, refusing to traverse or
    create through anything that is not a real directory INSIDE the root.

    ``os.makedirs`` follows symlinked components, so a repo shipping
    ``.github -> /elsewhere`` would make init write outside the project root
    while reporting an in-root write (Phase 8 review). Every component is
    lstat'd: a symlink or non-directory is refused before any write."""
    current = root
    created: list[str] = []
    try:
        for component in relative.split("/"):
            current = os.path.join(current, component)
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                try:
                    os.mkdir(current, 0o755)
                    created.append(current)
                except OSError as error:
                    raise InitError(
                        f"{current}: cannot create directory: {error}"
                    ) from error
                continue
            except OSError as error:
                raise InitError(f"{current}: cannot inspect: {error}") from error
            if stat.S_ISLNK(info.st_mode):
                raise InitError(f"{current}: symlinked path component refused")
            if not stat.S_ISDIR(info.st_mode):
                raise InitError(f"{current}: path component is not a directory")
    except BaseException:
        for path in reversed(created):
            try:
                os.rmdir(path)
            except FileNotFoundError:
                pass
        raise
    return tuple(created)


def _preflight_owned_directory(root: str, relative: str) -> None:
    """Refuse (before ANY write) if an existing component of ``relative`` is a
    symlink or a non-directory. Missing components are fine — created later."""
    current = root
    for component in relative.split("/"):
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as error:
            raise InitError(f"{current}: cannot inspect: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise InitError(f"{current}: symlinked path component refused")
        if not stat.S_ISDIR(info.st_mode):
            raise InitError(f"{current}: path component is not a directory")


def _has_write_and_search_permission(info: os.stat_result) -> bool:
    """Check the caller's applicable Unix permission bits.

    The explicit bit check keeps refusal deterministic when tests or containers
    run as root, for which ``os.access`` would otherwise bypass a read-only
    directory's mode. ``os.access`` additionally accounts for ACLs where the
    platform exposes them.
    """
    if os.geteuid() == info.st_uid:
        permissions = stat.S_IMODE(info.st_mode) >> 6
    elif info.st_gid == os.getegid() or info.st_gid in os.getgroups():
        permissions = stat.S_IMODE(info.st_mode) >> 3
    else:
        permissions = stat.S_IMODE(info.st_mode)
    return permissions & 0o3 == 0o3


def _preflight_writable_parent(path: str) -> None:
    """Require the nearest existing parent to permit creation below it."""
    parent = os.path.dirname(path)
    while True:
        try:
            info = os.lstat(parent)
        except FileNotFoundError:
            next_parent = os.path.dirname(parent)
            if next_parent == parent:
                raise InitError(f"{path}: no existing parent directory")
            parent = next_parent
            continue
        except OSError as error:
            raise InitError(f"{parent}: cannot inspect: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise InitError(f"{parent}: symlinked path component refused")
        if not stat.S_ISDIR(info.st_mode):
            raise InitError(f"{parent}: path component is not a directory")
        if not _has_write_and_search_permission(info) or not os.access(
            parent, os.W_OK | os.X_OK
        ):
            raise InitError(f"{parent}: cannot write init target")
        return


def _fsync_directory(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage_file(stage_root: str, index: int, plan: _PlannedFile) -> str:
    staged = os.path.join(stage_root, f"file-{index}")
    write_atomic(staged, plan.data, plan.mode, "staged init target")
    return staged


def _place_staged_file(staged: str, plan: _PlannedFile) -> None:
    """Publish one complete file by renaming a same-directory temporary."""
    directory = os.path.dirname(plan.destination)
    temporary = os.path.join(
        directory,
        f".{os.path.basename(plan.destination)}.{secrets.token_hex(8)}.tmp",
    )
    temporary_exists = False
    placed = False
    try:
        os.link(staged, temporary, follow_symlinks=False)
        temporary_exists = True
        try:
            os.lstat(plan.destination)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise InitError(
                f"{plan.destination}: cannot inspect placement target: {error}"
            ) from error
        else:
            raise InitError(
                f"{plan.destination}: init target appeared during placement; "
                "refusing to overwrite"
            )
        os.rename(temporary, plan.destination)
        temporary_exists = False
        placed = True
        _fsync_directory(directory)
    except OSError as error:
        if placed:
            try:
                os.unlink(plan.destination)
            except FileNotFoundError:
                pass
        raise InitError(
            f"{plan.destination}: cannot place staged init target: {error}"
        ) from error
    finally:
        if temporary_exists:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _init_step(_step: str) -> None:
    """Module-private fault-injection seam for recovery tests."""


def _rollback_placements(
    files: list[str],
    store_path: str | None,
    staged_store: str,
    directories: list[str],
) -> None:
    """Remove only paths successfully created by this invocation."""
    if store_path is not None:
        # Rename the complete store back out of the project atomically. If the
        # process dies on either side of this boundary, the store is either
        # fully installed with all dependencies or wholly absent and retryable.
        os.rename(store_path, staged_store)
    for path in reversed(files):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    for path in reversed(directories):
        try:
            os.rmdir(path)
        except FileNotFoundError:
            pass


def init_project(
    project_root: str,
    lock_bytes: bytes,
    *,
    tier: Tier = Tier.LIGHTWEIGHT,
    actor: str | None = None,
    test_jobs: Sequence[str] = (),
) -> tuple[InitResult, ...]:
    """Initialize exactly the D-7 store, lock, config, and CI workflow set.

    Target paths are planned and checked before a sibling staging directory is
    created. The prepared store is published last, after its lock and workflow,
    so an interruption cannot expose the re-initialization marker before the
    project is retryable. A caught refusal rolls back paths created by this run.

    ``test_jobs`` names the project's own GitHub Actions jobs that must pass
    before the anchor job can run.
    """
    root = os.path.abspath(project_root)
    try:
        root_info = os.lstat(root)
    except OSError as error:
        raise InitError(f"{root}: cannot inspect project root: {error}") from error
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise InitError(f"{root}: project root must be a non-symlink directory")

    _require_fresh_store(root)
    _preflight_owned_directory(root, ".github/workflows")
    digest = lock_digest(lock_bytes)
    config = _canonical_json(
        {
            "lock_digest": digest,
            "store": ".vouch",
            "config_format": 1,
        }
    )
    try:
        lock_plan = _plan_file(root, "vouch.lock", lock_bytes, 0o755)
        workflow_plan = _plan_file(
            root,
            ".github/workflows/vouch.yml",
            generated_workflow(test_jobs).encode("utf-8"),
            0o644,
        )
    except StoreError as error:
        raise InitError(str(error)) from error

    store_path = os.path.join(root, ".vouch")
    plans = (lock_plan, workflow_plan)
    collisions = tuple(
        plan for plan in plans if plan.status is InitStatus.SUGGESTED
    )
    write_plans = tuple(
        plan
        for plan in (collisions if collisions else plans)
        if plan.needs_write
    )
    if not collisions:
        _preflight_writable_parent(store_path)
    for plan in write_plans:
        _preflight_writable_parent(plan.destination)

    stage_parent = os.path.dirname(root)
    try:
        stage_root = tempfile.mkdtemp(prefix=".vouch-init-", dir=stage_parent)
    except OSError as error:
        raise InitError(f"init staging failed: {error}") from error

    staged_files: dict[str, str] = {}
    placed_files: list[str] = []
    created_directories: list[str] = []
    placed_store: str | None = None
    staged_store = os.path.join(stage_root, "store")
    try:
        for index, plan in enumerate(write_plans):
            staged_files[plan.destination] = _stage_file(stage_root, index, plan)

        if not collisions:
            with Store.init(
                staged_store,
                initial_snapshot=Snapshot(),
            ) as store:
                if tier is not Tier.LIGHTWEIGHT:
                    with store.write_locked():
                        initial = store.read_snapshot()
                        commit_events(
                            store,
                            initial,
                            (TierSet(tier),),
                            actor=default_actor() if actor is None else actor,
                            run_id=ci_run_id(),
                        )
            write_atomic(
                os.path.join(staged_store, "config.json"),
                config,
                0o600,
                "vouch config",
            )

        _init_step("staged")

        if any(
            plan.needs_write
            and os.path.dirname(plan.destination)
            == os.path.join(root, ".github", "workflows")
            for plan in write_plans
        ):
            created_directories.extend(
                _ensure_owned_directory(root, ".github/workflows")
            )

        for plan in write_plans:
            _place_staged_file(staged_files[plan.destination], plan)
            placed_files.append(plan.destination)

        if collisions:
            return tuple(
                InitResult(plan.report_path, InitStatus.SUGGESTED)
                for plan in collisions
            )

        try:
            os.rename(staged_store, store_path)
        except OSError as error:
            raise InitError(
                f"{store_path}: cannot place staged store: {error}"
            ) from error
        placed_store = store_path
        _fsync_directory(root)
    except BaseException as error:
        try:
            _rollback_placements(
                placed_files,
                placed_store,
                staged_store,
                created_directories,
            )
        except OSError as rollback_error:
            raise InitError(
                f"init failed and rollback failed: {rollback_error}"
            ) from error
        if isinstance(error, InitError):
            raise
        if isinstance(error, StoreError):
            raise InitError(str(error)) from error
        if isinstance(error, OSError):
            raise InitError(f"init failed: {error}") from error
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    return (
        InitResult(".vouch", InitStatus.WRITTEN),
        InitResult("vouch.lock", lock_plan.status),
        InitResult(".vouch/config.json", InitStatus.WRITTEN),
        InitResult(".github/workflows/vouch.yml", workflow_plan.status),
    )


__all__ = [
    "InitError",
    "InitResult",
    "InitStatus",
    "generated_workflow",
    "init_project",
]
