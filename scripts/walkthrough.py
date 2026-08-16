"""The DoD walkthrough (IMPLEMENTATION-PLAN.md §7): init → unit lifecycle → evidence
→ release → bundle → formal certificate → check, driven ONLY through the vendored
``vouch.lock`` as a subprocess, with NO network and NO repository on the import path.

Run:  python scripts/walkthrough.py --lock PATH/vouch.lock [--keep DIR]
Exit 0 iff every step succeeds and the final check composes to PASS.

Self-containment is proved three ways: every lifecycle mutation is a
`python <lock> <verb>` subprocess with PYTHONPATH=""; the sole artifact-construction
helper imports only the lock's OWN packaged modules (never the source tree); and CI
runs the walkthrough under a network-disabled sandbox (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


def _run(lock: str, *args: str, cwd: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, lock, *args], cwd=cwd, env=env, capture_output=True, text=True
    )


def _step(name: str, proc: subprocess.CompletedProcess[str], expect: int = 0) -> None:
    status = "ok" if proc.returncode == expect else f"FAILED (exit {proc.returncode})"
    print(f"[walkthrough] {name}: {status}")
    if proc.returncode != expect:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(1)


def _bundle_object(lock: str, project: str) -> str:
    """Build and place one inventory object using only lock-packaged modules."""
    code = r'''
import os, tempfile
import sys
from vouch_mk2.bundle import build_inventory
from vouch_mk2.store import ArtifactKind, Store, digest_of, encode_bundle_inventory
root = sys.argv[1]
tree = tempfile.mkdtemp()
with open(os.path.join(tree, "a.txt"), "w") as stream:
    stream.write("hello")
data = encode_bundle_inventory(build_inventory(tree, ()))
digest = digest_of(ArtifactKind.BUNDLE_INVENTORY, data)
with Store.open(root) as store:
    with store.write_locked():  # object placement is a write; the lock is required
        assert store.put_object(ArtifactKind.BUNDLE_INVENTORY, data) == digest
print(digest)
'''
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": lock, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", code, os.path.join(project, ".vouch")],
        cwd=project, env=env, capture_output=True, text=True,
    )
    _step("bundle object via lock-packaged artifact builder", proc)
    return proc.stdout.strip()


def _drive_lifecycle(lock: str, project: str) -> None:
    root = ".vouch"
    tests = os.path.join(project, "tests")
    os.makedirs(tests, exist_ok=True)
    for name in ("core", "api"):
        with open(
            os.path.join(tests, f"test_{name}.py"), "w", encoding="utf-8"
        ) as stream:
            stream.write(f"def test_{name}():\n    assert True\n")

    def verb(name: str, *args: str) -> None:
        _step(name, _run(lock, name, "--root", root, *args, cwd=project))

    verb("specify", "--name", "core", "--version", "v1")
    verb("specify", "--name", "api", "--version", "v1", "--depends-on", "core")
    for name in ("core", "api"):
        verb(
            "freeze",
            "--name",
            name,
            "--pack-version",
            f"pack-{name}",
            "--pack-file",
            f"tests/test_{name}.py",
        )
        verb("mark-ready", "--name", name)
        verb("start", "--name", name)

    python = sys.executable
    verb(
        "record-evidence", "--unit", "core@v1", "--kind", "red", "--",
        python, "-c", "raise SystemExit(1)",
    )
    verb(
        "record-evidence", "--unit", "core@v1", "--kind", "green", "--",
        python, "-c", "raise SystemExit(0)",
    )
    for name in ("core", "api"):
        verb("mark-done", "--name", name)
    verb("set-anchor-mode", "--mode", "ci")
    verb("advance-anchor", "--unit-name", "core")
    verb("publish", "--name", "core")
    verb("publish", "--name", "api")
    verb("prepare-release", "--release-id", "r1", "--includes", "api", "--includes", "core")
    verb(
        "set-criteria", "--release-id", "r1", "--launch-criteria", "healthy",
        "--verification-window", "1h", "--rollback-trigger", "errors",
        "--go-no-go-authority", "ops",
    )
    digest = _bundle_object(lock, project)
    verb("bind-bundle", "--release-id", "r1", "--inventory-digest", digest)
    verb("start-deploy", "--release-id", "r1")
    verb("start-verification", "--release-id", "r1")
    verb("complete", "--release-id", "r1")
    verb("record-formal-certificate")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--keep", default=None, help="keep the scratch project here")
    parser.add_argument(
        "--assert-no-network",
        action="store_true",
        help="first prove the environment denies a plain HTTP request; exit 1 if it does not",
    )
    args = parser.parse_args()
    if args.assert_no_network:
        import urllib.request

        try:
            urllib.request.urlopen("http://example.com", timeout=3)
        except Exception:  # any failure means the network is denied, which is the point
            print("[walkthrough] network denied: ok")
        else:
            print("[walkthrough] network REACHABLE — sandbox ineffective")
            return 1
    lock = os.path.abspath(args.lock)
    scratch = args.keep or tempfile.mkdtemp(prefix="vouch-walkthrough-")
    project = os.path.join(scratch, "project")
    os.makedirs(project, exist_ok=True)

    _step("self-check", _run(lock, "self-check", cwd=scratch))
    _step("init", _run(lock, "init", "--project", project, "--lock", lock, cwd=scratch))
    with open(os.path.join(project, ".vouch", "config.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    _step(
        "self-check --expect (pinned against init's recorded digest)",
        _run(lock, "self-check", "--expect", config["lock_digest"], cwd=project),
    )
    _drive_lifecycle(lock, project)
    _step("generate map", _run(lock, "generate", "--root", ".vouch", cwd=project))
    _step("emit-context", _run(lock, "emit-context", "--root", ".vouch", cwd=project))
    check = _run(
        lock, "check", "--ci", "--root", ".vouch", "--project", ".", cwd=project
    )
    _step("check --ci", check)
    if not check.stdout.rstrip().endswith("composed: PASS"):
        print(check.stdout)
        raise SystemExit(1)
    mechanical_not_checked = [
        line for line in check.stdout.splitlines()
        if line.startswith("NOT-CHECKED") and not line.split()[1].startswith("org.")
    ]
    if mechanical_not_checked:
        print("mechanical NOT_CHECKED rows present:", mechanical_not_checked)
        raise SystemExit(1)
    print(f"[walkthrough] complete: composed PASS, 0 mechanical NOT_CHECKED, project at {project}")
    if not args.keep:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
