"""Closed-set project initialization for the Phase 8 transport boundary."""

from __future__ import annotations

import json
import os
import stat
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


_WORKFLOW = """name: vouch

on:
  push:
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

jobs:
  validation:
    # Add your own test job to this validation job's `needs:` before adopting it.
    # needs: [your-test-job]
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

  anchor:
    needs: validation
    if: github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork == false
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
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
          git push origin "HEAD:${{ github.event.repository.default_branch }}"
"""


def generated_workflow() -> str:
    """Return the exact GitHub Actions workflow fragment written by ``init``."""
    return _WORKFLOW


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
        )
    if current == data:
        return _PlannedFile(
            report_path,
            target,
            data,
            mode,
            InitStatus.UNCHANGED,
        )
    suggested = target + ".vouch-suggested"
    existing_suggestion = read_regular_nofollow(
        suggested, "init suggestion target"
    )
    if existing_suggestion == data:
        return _PlannedFile(
            report_path,
            suggested,
            data,
            mode,
            InitStatus.UNCHANGED,
        )
    if existing_suggestion is not None:
        raise InitError(
            f"{suggested}: existing suggestion differs; refusing to overwrite"
        )
    return _PlannedFile(
        report_path,
        suggested,
        data,
        mode,
        InitStatus.SUGGESTED,
    )


def _ensure_owned_directory(root: str, relative: str) -> None:
    """Create ``root/relative`` component by component, refusing to traverse or
    create through anything that is not a real directory INSIDE the root.

    ``os.makedirs`` follows symlinked components, so a repo shipping
    ``.github -> /elsewhere`` would make init write outside the project root
    while reporting an in-root write (Phase 8 review). Every component is
    lstat'd: a symlink or non-directory is refused before any write."""
    current = root
    for component in relative.split("/"):
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o755)
            except OSError as error:
                raise InitError(f"{current}: cannot create directory: {error}") from error
            continue
        except OSError as error:
            raise InitError(f"{current}: cannot inspect: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise InitError(f"{current}: symlinked path component refused")
        if not stat.S_ISDIR(info.st_mode):
            raise InitError(f"{current}: path component is not a directory")


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


def init_project(
    project_root: str,
    lock_bytes: bytes,
    *,
    tier: Tier = Tier.LIGHTWEIGHT,
    actor: str | None = None,
) -> tuple[InitResult, ...]:
    """Initialize exactly the D-7 store, lock, config, and CI workflow set.

    Every precondition — fresh store, sane project root, no symlinked or
    non-directory component on any owned path — is proven BEFORE the first
    write, so a refusal never leaves a half-initialized project (a store with
    no lock/config that then blocks every retry)."""
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
            generated_workflow().encode("utf-8"),
            0o644,
        )
    except StoreError as error:
        raise InitError(str(error)) from error

    store_path = os.path.join(root, ".vouch")
    try:
        # Owned directories first (they cannot half-initialize anything), then
        # the store, then the files that reference it.
        _ensure_owned_directory(root, ".github/workflows")
        try:
            store = Store.init(
                store_path,
                initial_snapshot=Snapshot(),
            )
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
        except StoreError as error:
            raise InitError(str(error)) from error
        store.close()
        write_atomic(
            os.path.join(store_path, "config.json"),
            config,
            0o600,
            "vouch config",
        )
        for plan in (lock_plan, workflow_plan):
            if plan.status is not InitStatus.UNCHANGED:
                write_atomic(plan.destination, plan.data, plan.mode, "init target")
    except OSError as error:
        raise InitError(f"init failed: {error}") from error

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
