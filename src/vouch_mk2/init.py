"""Closed-set project initialization for the Phase 8 transport boundary."""

from __future__ import annotations

import ctypes
import datetime
import errno
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum

from vouch_mk2.core import AnchorMode, AnchorModeSet, EffectsRequired, Snapshot, Tier, TierSet
from vouch_mk2.io import read_regular_nofollow, write_atomic, write_atomic_at
from vouch_mk2.lock import lock_digest
from vouch_mk2.store import Store, StoreError
from vouch_mk2.store.event_log import ci_run_id, commit_events, default_actor


class InitError(StoreError):
    """Initialization refused before knowingly overwriting project content."""


class InitIncompletePublication(InitError):
    """Initialization stopped after project-visible publication began."""

    def __init__(self, detail: str, complete_paths: Sequence[str]) -> None:
        self.complete_paths = tuple(complete_paths)
        remnants = ", ".join(self.complete_paths) or "none currently visible"
        super().__init__(
            f"incomplete publication: {detail}; complete remnants: {remnants}; "
            "fix the cause and retry init"
        )


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


type _PublishedPath = str


_WORKFLOW_TEMPLATE = """name: vouch

on:
  push:
  pull_request:
  workflow_call:
    inputs:
      anchoring:
        description: Publish a Vouch anchor after the caller's required jobs pass
        required: false
        type: boolean
        default: false

concurrency:
  group: vouch-anchor-${{ github.repository_id }}-${{ github.ref }}-${{ inputs.anchoring && 'anchoring' || github.run_id }}
  cancel-in-progress: false

jobs:
  validation:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
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

  merge_check:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          fetch-depth: 0
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: '3.14'
      - name: Verify vouch.lock against the recorded digest
        shell: bash
        run: python vouch.lock self-check --expect @.vouch/config.json
      - name: Check pull request store merge
        shell: bash
        run: |
          set -euo pipefail
          export GIT_NO_REPLACE_OBJECTS=1
          parents="$(git rev-list --parents -n 1 HEAD)"
          IFS=' ' read -r -a commits <<< "$parents"
          if (( ${#commits[@]} != 3 )); then
            echo 'FAIL check-merge: expected a checkout with exactly two parents' >&2
            exit 1
          fi
          merged="${commits[0]}"
          base="${commits[1]}"
          head="${commits[2]}"
          base_state="$(git ls-tree --full-tree "$base" -- .vouch/state.json)"
          if [[ -z "$base_state" ]]; then
            echo 'PASS check-merge: adoption pull request (base has no .vouch/state.json)'
            exit 0
          fi

          scratch="$(mktemp -d "$RUNNER_TEMP/vouch-merge-check.XXXXXX")"
          trap 'rm -rf -- "$scratch"' EXIT
          # Copy committed blobs verbatim, independently of checkout attributes.
          extract_store() {
            local commit="$1" root="$2" entry metadata path mode kind object relative target
            git ls-tree -r -z --full-tree "$commit" -- .vouch > "$scratch/tree"
            if [[ ! -s "$scratch/tree" ]]; then
              echo "FAIL check-merge: $commit has no .vouch/ files" >&2
              return 1
            fi
            mkdir -- "$root"
            while IFS= read -r -d '' entry; do
              metadata="${entry%%$'\\t'*}"
              path="${entry#*$'\\t'}"
              IFS=' ' read -r mode kind object <<< "$metadata"
              if [[ "$path" != .vouch/* || "$kind" != blob || ( "$mode" != 100644 && "$mode" != 100755 ) ]]; then
                printf 'FAIL check-merge: %s has a non-regular store entry %q\\n' "$commit" "$path" >&2
                return 1
              fi
              relative="${path#.vouch/}"
              case "/$relative/" in
                *'/../'*|*'/./'*|*'//'*)
                  printf 'FAIL check-merge: %s has an invalid store path %q\\n' "$commit" "$path" >&2
                  return 1
                  ;;
              esac
              target="$root/$relative"
              mkdir -p -- "${target%/*}"
              if [[ -e "$target" ]]; then
                printf 'FAIL check-merge: %s has a duplicate store path %q\\n' "$commit" "$path" >&2
                return 1
              fi
              git cat-file blob "$object" > "$target"
            done < "$scratch/tree"
          }
          extract_store "$base" "$scratch/base"
          extract_store "$head" "$scratch/head"
          extract_store "$merged" "$scratch/merged"
          python vouch.lock check-merge \\
            --base "$scratch/base" --head "$scratch/head" --merged "$scratch/merged"

  anchor_plan:
    needs: [validation]
    if: inputs.anchoring == true && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork == false
    runs-on: ubuntu-latest
    permissions:
      contents: read
    outputs:
      changed: ${{ steps.candidate.outputs.changed }}
      candidate: ${{ steps.candidate.outputs.candidate }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          ref: ${{ github.sha }}
          fetch-depth: 0
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: '3.14'
      - name: Verify vouch.lock against the recorded digest
        shell: bash
        run: python vouch.lock self-check --expect @.vouch/config.json
      - name: Advance evidence anchors and prepare candidate
        id: candidate
        shell: bash
        run: |
          set -euo pipefail
          python - <<'PY' > "$RUNNER_TEMP/vouch-evidence-heads"
          import json
          from pathlib import Path

          state = json.loads(Path(".vouch/state.json").read_text())
          for head in state["evidence_heads"]:
              if not head["anchor_run_id"] or head["anchored"] != head["head"]:
                  print(f"{head['unit_name']}@{head['unit_version']}")
          PY
          if git show 'HEAD^1:.vouch/state.json' 2>/dev/null > "$RUNNER_TEMP/vouch-parent-state"; then
            python - "$RUNNER_TEMP/vouch-parent-state" "$RUNNER_TEMP/vouch-evidence-heads" <<'PY'
          import json
          import sys
          from pathlib import Path

          def previous_pending(path):
              try:
                  heads = json.loads(path.read_bytes())["evidence_heads"]
                  if not isinstance(heads, list):
                      return {}
                  previous = {}
                  for head in heads:
                      fields = tuple(
                          head[key]
                          for key in ("unit_name", "unit_version", "head", "anchored", "anchor_run_id")
                      )
                      if not all(isinstance(value, str) for value in fields):
                          return {}
                      name, version, digest, anchored, run_id = fields
                      label = f"{name}@{version}"
                      if label in previous:
                          return {}
                      previous[label] = digest if not run_id or anchored != digest else None
                  return previous
              except (OSError, ValueError, KeyError, TypeError, RecursionError):
                  return {}

          previous = previous_pending(Path(sys.argv[1]))
          if previous:
              state = json.loads(Path(".vouch/state.json").read_text())
              current = {
                  f"{head['unit_name']}@{head['unit_version']}": head["head"]
                  for head in state["evidence_heads"]
              }
              for label in Path(sys.argv[2]).read_text().splitlines():
                  digest = current[label]
                  if previous.get(label) == digest:
                      print(f"vouch: anchoring {label} late: its head {digest[:16]} "
                            "was already pending at the previous commit")
          PY
          fi
          while IFS= read -r label; do
            unit_name="${label%@*}"
            unit_version="${label##*@}"
            python vouch.lock advance-anchor --root .vouch --unit-name="$unit_name" --unit-version="$unit_version"
          done < "$RUNNER_TEMP/vouch-evidence-heads"
          python vouch.lock generate --root .vouch
          python vouch.lock check --ci --root .vouch --project .
          python vouch.lock formal
          git add -- .vouch/
          if git diff --cached --quiet; then
            printf 'changed=false\\n' >> "$GITHUB_OUTPUT"
            exit 0
          fi
          git config user.name "vouch-anchor-bot"
          git config user.email "vouch-anchor-bot@users.noreply.github.com"
          git commit -m "vouch: advance evidence anchors"
          candidate="$(git rev-parse HEAD)"
          git branch vouch-candidate "$candidate"
          git bundle create "$RUNNER_TEMP/vouch-candidate.bundle" refs/heads/vouch-candidate
          git branch -D vouch-candidate
          (cd "$RUNNER_TEMP" && sha256sum vouch-candidate.bundle > vouch-candidate.bundle.sha256)
          printf 'changed=true\\ncandidate=%s\\n' "$candidate" >> "$GITHUB_OUTPUT"
      - name: Upload same-run candidate transport
        if: steps.candidate.outputs.changed == 'true'
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: vouch-candidate-${{ github.run_id }}-${{ github.run_attempt }}
          path: |
            ${{ runner.temp }}/vouch-candidate.bundle
            ${{ runner.temp }}/vouch-candidate.bundle.sha256
          if-no-files-found: error
          retention-days: 1

  anchor_publish:
    needs: [anchor_plan]
    if: needs.anchor_plan.outputs.changed == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Download same-run candidate transport
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: vouch-candidate-${{ github.run_id }}-${{ github.run_attempt }}
          path: ${{ runner.temp }}/vouch-candidate
      - name: Validate candidate and publish
        shell: bash
        env:
          VOUCH_PUSH_TOKEN: ${{ github.token }}
          PLANNED_BASE: ${{ github.sha }}
          CANDIDATE: ${{ needs.anchor_plan.outputs.candidate }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          transport="$RUNNER_TEMP/vouch-candidate"
          (cd "$transport" && sha256sum -c vouch-candidate.bundle.sha256) ||
            { echo 'REFUSED publish: the candidate bundle does not match its checksum' >&2; exit 1; }
          [[ "$PLANNED_BASE" =~ ^[0-9a-f]{40}$ ]] ||
            { echo 'REFUSED publish: the planned base is not a full commit id' >&2; exit 1; }
          [[ "$CANDIDATE" =~ ^[0-9a-f]{40}$ ]] ||
            { echo 'REFUSED publish: the candidate is not a full commit id' >&2; exit 1; }

          publish="$RUNNER_TEMP/vouch-publish"
          git init -q "$publish"
          cd "$publish"
          git remote add origin "https://x-access-token:${VOUCH_PUSH_TOKEN}@github.com/${{ github.repository }}.git"
          git fetch -q --no-tags origin "$PLANNED_BASE"
          git fetch -q "$transport/vouch-candidate.bundle" refs/heads/vouch-candidate
          test "$(git rev-parse FETCH_HEAD)" = "$CANDIDATE" ||
            { echo 'REFUSED publish: the bundle does not hold the planned candidate' >&2; exit 1; }

          # The candidate has exactly one parent and it is the plan job's exact base.
          parents="$(git rev-list --parents -n 1 "$CANDIDATE")"
          test "$(printf '%s\\n' "$parents" | awk '{print NF - 1}')" -eq 1 ||
            { echo 'REFUSED publish: the candidate does not have exactly one parent' >&2; exit 1; }
          test "$(git rev-parse "$CANDIDATE^")" = "$PLANNED_BASE" ||
            { echo "REFUSED publish: the candidate's parent is not the planned base" >&2; exit 1; }

          git diff-tree --no-commit-id --name-only -r -z "$PLANNED_BASE" "$CANDIDATE" > "$RUNNER_TEMP/vouch-paths"
          git ls-tree -r -z "$CANDIDATE" -- .vouch > "$RUNNER_TEMP/vouch-tree"
          # Reject paths outside .vouch/, symlink mode 120000, submodule 160000,
          # or any other non-regular mode before the write credential is used.
          python - "$RUNNER_TEMP/vouch-paths" "$RUNNER_TEMP/vouch-tree" <<'PY'
          import sys
          from pathlib import Path

          paths = tuple(item for item in Path(sys.argv[1]).read_bytes().split(b"\\0") if item)
          if not paths or any(not item.startswith(b".vouch/") for item in paths):
              raise SystemExit("REFUSED publish: the candidate changes a path outside .vouch/")
          records = tuple(item for item in Path(sys.argv[2]).read_bytes().split(b"\\0") if item)
          for record in records:
              metadata, _path = record.split(b"\\t", 1)
              mode = metadata.split(b" ", 1)[0]
              if mode not in {b"100644", b"100755"}:
                  raise SystemExit("REFUSED publish: the candidate introduces a non-regular file under .vouch/")
          PY

          live="$(git ls-remote origin "refs/heads/$DEFAULT_BRANCH" | awk 'NR == 1 {print $1}')"
          test "$live" = "$PLANNED_BASE" ||
            { echo "REFUSED publish: $DEFAULT_BRANCH moved since this run planned its anchor; a later run will anchor it" >&2; exit 1; }
          git config core.hooksPath /dev/null
          git push origin "$CANDIDATE:refs/heads/$DEFAULT_BRANCH"
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
    _validated_test_jobs(test_jobs)
    return _WORKFLOW_TEMPLATE


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


# Open a directory for walking with search permission only (O_SEARCH on macOS,
# O_PATH on Linux), so an existing directory the user may traverse but not list
# still works, as it did when the walk was path-based.
_SEARCH_ONLY = getattr(os, "O_SEARCH", 0) or getattr(os, "O_PATH", 0) or os.O_RDONLY


def _ensure_owned_directory(root: str, relative: str) -> tuple[str, ...]:
    """Create ``root/relative`` component by component, refusing to traverse or
    create through anything that is not a real directory INSIDE the root.

    ``os.makedirs`` follows symlinked components, so a repo shipping
    ``.github -> /elsewhere`` would make init write outside the project root
    while reporting an in-root write (Phase 8 review). Every component is
    lstat'd: a symlink or non-directory is refused before any write. The walk
    holds each verified directory open, so creating a component and setting its
    mode both happen inside that directory, whatever happens to its ancestors."""
    current = root
    created: list[str] = []
    flags = _SEARCH_ONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        try:
            directory_fd = os.open(root, _SEARCH_ONLY | os.O_DIRECTORY)
        except OSError as error:
            raise InitError(f"{root}: cannot inspect: {error}") from error
        try:
            for component in relative.split("/"):
                current = os.path.join(current, component)
                try:
                    info = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    try:
                        os.mkdir(component, 0o755, dir_fd=directory_fd)
                        created.append(current)
                        os.chmod(
                            component, 0o755, dir_fd=directory_fd, follow_symlinks=False
                        )
                    except OSError as error:
                        raise InitError(
                            f"{current}: cannot create directory: {error}"
                        ) from error
                except OSError as error:
                    raise InitError(f"{current}: cannot inspect: {error}") from error
                else:
                    if stat.S_ISLNK(info.st_mode):
                        raise InitError(f"{current}: symlinked path component refused")
                    if not stat.S_ISDIR(info.st_mode):
                        raise InitError(f"{current}: path component is not a directory")
                try:
                    child_fd = os.open(component, flags, dir_fd=directory_fd)
                except OSError as error:
                    raise InitError(f"{current}: cannot inspect: {error}") from error
                os.close(directory_fd)
                directory_fd = child_fd
        finally:
            os.close(directory_fd)
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


WORKFLOW_PATH = ".github/workflows/vouch.yml"
_WORKFLOW_DIRECTORY, _WORKFLOW_NAME = os.path.split(WORKFLOW_PATH)


@contextmanager
def _open_workflow_directory(root: str, *, create: bool = False) -> Iterator[int]:
    """Anchor operations in the project and refuse symlinks below it."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    directory_fd = os.open(root, flags)
    try:
        for component in _WORKFLOW_DIRECTORY.split("/"):
            try:
                child_fd = os.open(component, flags | os.O_NOFOLLOW, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o755, dir_fd=directory_fd)
                except FileExistsError:
                    pass  # A concurrent refresh may have created it.
                else:
                    os.chmod(component, 0o755, dir_fd=directory_fd, follow_symlinks=False)
                child_fd = os.open(component, flags | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        yield directory_fd
    finally:
        os.close(directory_fd)


def _workflow_matches(directory_fd: int, expected: bytes) -> bool:
    try:
        info = os.stat(_WORKFLOW_NAME, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        raise StoreError(f"{WORKFLOW_PATH}: symlinked workflow refused")
    if not stat.S_ISREG(info.st_mode):
        return False
    try:
        fd = os.open(
            _WORKFLOW_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
    except PermissionError:
        # An unreadable regular file is not the generated workflow; refresh
        # replaces its directory entry, which needs only the directory's rights.
        return False
    with os.fdopen(fd, "rb") as stream:
        return (
            stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
            and stream.read(len(expected) + 1) == expected
        )


def workflow_is_current(project_root: str) -> bool:
    """Compare the owned workflow with the exact bytes written by init."""
    try:
        with _open_workflow_directory(os.path.abspath(project_root)) as directory_fd:
            return _workflow_matches(directory_fd, generated_workflow().encode("utf-8"))
    except FileNotFoundError:
        return False


def refresh_workflow(project_root: str) -> None:
    """Install the generated workflow in a project with init's config marker."""
    root = os.path.abspath(project_root)
    config = os.path.join(root, ".vouch", "config.json")
    try:
        os.lstat(config)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise StoreError(f"{config}: required to refresh the workflow") from error

    _preflight_owned_directory(root, _WORKFLOW_DIRECTORY)
    expected = generated_workflow().encode("utf-8")
    with _open_workflow_directory(root, create=True) as directory_fd:
        if not _workflow_matches(directory_fd, expected):
            write_atomic_at(
                directory_fd, os.path.join(root, WORKFLOW_PATH), expected, 0o644,
                "generated workflow",
            )


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


def _place_staged_file(staged: str, plan: _PlannedFile) -> _PublishedPath:
    """Atomically publish one complete file only if its pathname is absent."""
    directory = os.path.dirname(plan.destination)
    published = False
    try:
        # A hard link is a same-filesystem, atomic create-if-absent operation.
        # Unlike rename, EEXIST cannot replace a concurrent winner.
        os.link(staged, plan.destination, follow_symlinks=False)
        published = True
        _fsync_directory(directory)
    except OSError as error:
        if published:
            raise InitIncompletePublication(
                f"{plan.destination}: cannot place staged init target: {error}",
                (plan.report_path,),
            ) from error
        raise InitError(
            f"{plan.destination}: cannot place staged init target: {error}"
        ) from error
    return plan.destination


def _rename_directory_noreplace(source: str, destination: str) -> None:
    """Atomically move a directory to an absent name on supported platforms."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        renamex = libc.renamex_np
        renamex.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex.restype = ctypes.c_int
        result = renamex(source_bytes, destination_bytes, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        try:
            renameat2 = libc.renameat2
        except AttributeError as error:
            raise InitError(
                "platform: atomic no-replace directory rename is unavailable"
            ) from error
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100, source_bytes, -100, destination_bytes, 0x00000001
        )  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise InitError(
            "platform: atomic no-replace directory rename is unavailable"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == 0:
            error_number = errno.EIO
        raise OSError(error_number, os.strerror(error_number), destination)


def _rollback_prepublication_directories(directories: list[str]) -> None:
    """Remove directories created before any project file was published."""
    for path in reversed(directories):
        try:
            os.rmdir(path)
        except FileNotFoundError:
            pass


def _complete_visible_plans(plans: Sequence[_PlannedFile]) -> tuple[str, ...]:
    """Name complete planned files currently visible at their final paths."""
    complete: list[str] = []
    for plan in plans:
        try:
            current = read_regular_nofollow(
                plan.destination, "published init target"
            )
        except StoreError:
            continue
        if current == plan.data:
            complete.append(plan.report_path)
    return tuple(complete)


def init_project(
    project_root: str,
    lock_bytes: bytes,
    *,
    tier: Tier = Tier.LIGHTWEIGHT,
    anchor_mode: AnchorMode = AnchorMode.NONE,
    actor: str | None = None,
) -> tuple[InitResult, ...]:
    """Initialize exactly the D-7 store, lock, config, and CI workflow set.

    Target paths are planned and checked before a sibling staging directory is
    created. The prepared store is published last, after its lock and workflow,
    so an interruption cannot expose the re-initialization marker before the
    project is retryable. A caught refusal before publication removes newly
    created directories. Once publication begins, complete artifacts remain
    in place for a retry instead of being unlinked during unwind.

    """
    if tier is not Tier.LIGHTWEIGHT and anchor_mode is not AnchorMode.CI:
        raise InitError(
            f"tier {tier.value} requires anchor_mode=ci; supply --test-job "
            "when using the CLI"
        )

    root = os.path.abspath(project_root)
    try:
        root_info = os.lstat(root)
    except OSError as error:
        raise InitError(f"{root}: cannot inspect project root: {error}") from error
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise InitError(f"{root}: project root must be a non-symlink directory")

    _require_fresh_store(root)
    _preflight_owned_directory(root, _WORKFLOW_DIRECTORY)
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
            WORKFLOW_PATH,
            generated_workflow().encode("utf-8"),
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
    try:
        os.chmod(stage_root, 0o700, follow_symlinks=False)
    except OSError as error:
        # Still empty; rmdir needs no access to it, unlike the rmtree cleanup below.
        with suppress(OSError):
            os.rmdir(stage_root)
        raise InitError(f"init staging failed: {error}") from error

    staged_files: dict[str, str] = {}
    placed_files: list[_PublishedPath] = []
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
                initial_events = (
                    EffectsRequired(),
                    *((TierSet(tier),) if tier is not Tier.LIGHTWEIGHT else ()),
                    *(
                        (AnchorModeSet(anchor_mode),)
                        if anchor_mode is not AnchorMode.NONE
                        else ()
                    ),
                )
                if initial_events:
                    with store.write_locked():
                        initial = store.read_snapshot()
                        commit_events(
                            store,
                            initial,
                            initial_events,
                            actor=default_actor() if actor is None else actor,
                            run_id=ci_run_id(),
                            # The shared initial fact must not give independent
                            # stores a common history root within one second.
                            recorded_at=datetime.datetime.now(datetime.UTC).isoformat(
                                timespec="microseconds"
                            ),
                        )
            write_atomic(
                os.path.join(staged_store, "config.json"),
                config,
                0o600,
                "vouch config",
            )

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
            placed_files.append(
                _place_staged_file(staged_files[plan.destination], plan)
            )

        if collisions:
            return tuple(
                InitResult(plan.report_path, InitStatus.SUGGESTED)
                for plan in collisions
            )

        try:
            _rename_directory_noreplace(staged_store, store_path)
        except OSError as error:
            raise InitError(
                f"{store_path}: cannot place staged store: {error}"
            ) from error
        placed_store = store_path
        _fsync_directory(root)
    except BaseException as error:
        # Once the complete store is visible, preserve the success-equivalent
        # installation and its dependencies. Once any file is visible, unwind
        # never removes a project pathname because pathname deletion cannot be
        # made conditional on the identity previously observed.
        if placed_store is not None:
            pass
        elif placed_files or isinstance(error, InitIncompletePublication):
            if isinstance(error, InitIncompletePublication):
                raise
            complete_paths = _complete_visible_plans(write_plans)
            raise InitIncompletePublication(
                f"init failed: {error}", complete_paths
            ) from error
        elif not isinstance(error, InitError):
            complete_paths = _complete_visible_plans(write_plans)
            if complete_paths:
                raise InitIncompletePublication(
                    f"init failed: {error}", complete_paths
                ) from error
            try:
                _rollback_prepublication_directories(created_directories)
            except OSError as rollback_error:
                raise InitError(
                    f"init failed and rollback failed: {rollback_error}"
                ) from error
            if isinstance(error, StoreError):
                raise InitError(str(error)) from error
            if isinstance(error, OSError):
                raise InitError(f"init failed: {error}") from error
            raise
        else:
            try:
                _rollback_prepublication_directories(created_directories)
            except OSError as rollback_error:
                raise InitError(
                    f"init failed and rollback failed: {rollback_error}"
                ) from error
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
    "InitIncompletePublication",
    "InitResult",
    "InitStatus",
    "generated_workflow",
    "init_project",
]
