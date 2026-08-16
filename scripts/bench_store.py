"""Reproducible store/core cost measurement (bead sz2). Run: uv run python scripts/bench_store.py

Numbers are wall-clock on the machine that runs it; the shape (linear per-command apply cost,
tens of ms per CLI invocation at thousands of units) is what matters, not the digits.
"""

from __future__ import annotations

import dataclasses
import tempfile
import time

from vouch_mk2.checks import run_checks
from vouch_mk2.core import Accepted, apply, decide
from vouch_mk2.core.commands import FreezeAcceptance, MarkDone, MarkReady, Publish, SpecifyUnit, StartWork
from vouch_mk2.core.model import Snapshot
from vouch_mk2.projections import agent_context, next_actions, system_map
from vouch_mk2.store import Store, decode_snapshot, encode_snapshot


def build(n: int) -> Snapshot:
    snapshot = Snapshot()
    for i in range(n):
        for command in (SpecifyUnit(f"u{i}", "v1"), FreezeAcceptance(f"u{i}", "p"), MarkReady(f"u{i}"), StartWork(f"u{i}"), MarkDone(f"u{i}"), Publish(f"u{i}")):
            decision = decide(snapshot, command)
            assert isinstance(decision, Accepted), (command, decision)
            snapshot = apply(snapshot, decision.events)
    return snapshot


def main() -> None:
    for n in (8, 100, 500, 2000):
        t0 = time.perf_counter()
        snapshot = build(n)
        t_build = time.perf_counter() - t0
        data = encode_snapshot(snapshot)
        t0 = time.perf_counter()
        decode_snapshot(data)
        t_decode = time.perf_counter() - t0
        t0 = time.perf_counter()
        run_checks(snapshot)
        t_checks = time.perf_counter() - t0
        t0 = time.perf_counter()
        system_map(snapshot)
        agent_context(snapshot)
        next_actions(snapshot)
        t_projections = time.perf_counter() - t0
        with Store.init(tempfile.mkdtemp() + "/s") as store:
            t0 = time.perf_counter()
            store.commit(dataclasses.replace(snapshot, revision=1))
            t_commit = time.perf_counter() - t0
        t0 = time.perf_counter()
        decide(snapshot, SpecifyUnit("zz", "v1"))
        t_one = time.perf_counter() - t0
        print(
            f"n={n:5} units | {n * 6} cmds {t_build:6.2f}s ({t_build / (n * 6) * 1000:5.2f} ms/cmd) | "
            f"decode {t_decode * 1000:6.1f}ms | checks {t_checks * 1000:6.1f}ms | projections {t_projections * 1000:6.1f}ms | "
            f"commit {t_commit * 1000:6.1f}ms | one decide {t_one * 1000:5.2f}ms | state {len(data) // 1024}KB"
        )


if __name__ == "__main__":
    main()
