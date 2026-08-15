"""The DoD walkthrough (IMPLEMENTATION-PLAN.md §7): init → unit lifecycle → evidence
→ release → bundle → formal certificate → check, driven ONLY through the vendored
``vouch.lock`` as a subprocess, with NO network and NO repository on the import path.

Run:  python scripts/walkthrough.py --lock PATH/vouch.lock [--keep DIR]
Exit 0 iff every step succeeds and the final check composes to PASS.

Self-containment is proved three ways: the lock is executed with PYTHONPATH="" so
nothing outside it is importable; the walkthrough's own scripting of the store goes
through `python <lock> ...` subcommands where one exists and through the lock's OWN
packaged modules (imported from the zip via sys.path) where the CLI has no mutation
surface — never through the source tree; and CI runs it under a network-disabled
sandbox (see .github/workflows/ci.yml).
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


def _drive_lifecycle(lock: str, project: str) -> None:
    """Mutations have no CLI surface by design (D-7: MCP is read-only; the CLI
    exposes check/formal/projections/transports). Drive them through the lock's
    OWN packaged core, imported from the zip — still no source tree, no network."""
    code = r'''
import sys
from vouch_mk2.core import *
from vouch_mk2.store import Store
from vouch_mk2.verify import record_evidence, record_formal_certificate
from vouch_mk2.bundle import build_inventory, bind_bundle
import os, tempfile
root = sys.argv[1]
with Store.open(root) as store:
    def run(cmd):
        s = store.read_snapshot(); d = decide(s, cmd)
        assert isinstance(d, Accepted), (cmd, d)
        store.commit(apply(s, d.events))
    run(SpecifyUnit("core", "v1"))
    run(SpecifyUnit("api", "v1", depends_on=("core",)))
    for name in ("core", "api"):
        run(FreezeAcceptance(name, f"pack-{name}")); run(MarkReady(name)); run(StartWork(name)); run(MarkDone(name))
    red = record_evidence(store, EvidenceEvent(UnitRef("core","v1"), 1, "GENESIS", EvidenceKind.RED, "pytest", EvidenceOutcome.TESTS_FAILED, "t1"))
    record_evidence(store, EvidenceEvent(UnitRef("core","v1"), 2, red, EvidenceKind.GREEN, "pytest", EvidenceOutcome.TESTS_PASSED, "t2"))
    run(SetAnchorMode(AnchorMode.CI)); run(AdvanceAnchor("core"))
    run(Publish("core")); run(Publish("api"))
    run(PrepareRelease("r1", ("api", "core")))
    run(SetReleaseCriteria("r1", LaunchCriteria(("healthy",), "1h", ("errors",), "ops")))
    tree = tempfile.mkdtemp(); open(os.path.join(tree, "a.txt"), "w").write("hello")
    bind_bundle(store, "r1", build_inventory(tree, ()))
    run(StartDeploy("r1")); run(StartVerification("r1")); run(CompleteRelease("r1"))
    record_formal_certificate(store)
print("lifecycle complete")
'''
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": lock, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", code, os.path.join(project, ".vouch")],
        cwd=project, env=env, capture_output=True, text=True,
    )
    _step("lifecycle via lock-packaged core (PYTHONPATH=<lock only>)", proc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--keep", default=None, help="keep the scratch project here")
    args = parser.parse_args()
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
    check = _run(lock, "check", "--ci", "--root", ".vouch", cwd=project)
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
