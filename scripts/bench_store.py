"""Reproducible store/core cost measurement (bead sz2). Run: uv run python scripts/bench_store.py

Numbers are wall-clock on the machine that runs it; the shape (linear per-command apply cost,
tens of ms per CLI invocation at thousands of units) is what matters, not the digits.
"""

from __future__ import annotations

import os
import tempfile
import time

from vouch_mk2.checks import run_checks
from vouch_mk2.core import Accepted, decide
from vouch_mk2.core.commands import FreezeAcceptance, MarkDone, MarkReady, Publish, SpecifyUnit, StartWork
from vouch_mk2.core.model import Snapshot
from vouch_mk2.projections import agent_context, next_actions, system_map
from vouch_mk2.store import ArtifactKind, Store, decode_snapshot, encode_snapshot
from vouch_mk2.store.event_log import build_event_records


def build(
    n: int,
) -> tuple[Snapshot, tuple[tuple[ArtifactKind, bytes], ...]]:
    snapshot = Snapshot()
    objects: list[tuple[ArtifactKind, bytes]] = []
    for i in range(n):
        for command in (SpecifyUnit(f"u{i}", "v1"), FreezeAcceptance(f"u{i}", "p"), MarkReady(f"u{i}"), StartWork(f"u{i}"), MarkDone(f"u{i}"), Publish(f"u{i}")):
            decision = decide(snapshot, command)
            assert isinstance(decision, Accepted), (command, decision)
            snapshot, event_objects, _records = build_event_records(
                snapshot,
                decision.events,
                actor="benchmark",
                run_id="",
                recorded_at="2026-08-18T00:00:00+00:00",
            )
            objects.extend(event_objects)
    return snapshot, tuple(objects)


def _tree_bytes(root: str) -> int:
    return sum(
        os.stat(os.path.join(directory, name)).st_size
        for directory, _directories, names in os.walk(root)
        for name in names
    )


def main() -> None:
    for n in (8, 100, 500, 2000):
        t0 = time.perf_counter()
        snapshot, raw_objects = build(n)
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
        store_root = tempfile.mkdtemp() + "/s"
        with Store.init(store_root) as store:
            t0 = time.perf_counter()
            store.commit(
                snapshot,
                objects=raw_objects,
                expected_revision=0,
            )
            t_commit = time.perf_counter() - t0
        store_bytes = _tree_bytes(store_root)
        event_bytes = sum(len(payload) for _kind, payload in raw_objects)
        t0 = time.perf_counter()
        decide(snapshot, SpecifyUnit("zz", "v1"))
        t_one = time.perf_counter() - t0
        print(
            f"n={n:5} units | {n * 6} cmds {t_build:6.2f}s ({t_build / (n * 6) * 1000:5.2f} ms/cmd) | "
            f"decode {t_decode * 1000:6.1f}ms | checks {t_checks * 1000:6.1f}ms | projections {t_projections * 1000:6.1f}ms | "
            f"commit {t_commit * 1000:6.1f}ms | one decide {t_one * 1000:5.2f}ms | state {len(data) // 1024}KB | "
            f"event log {event_bytes // len(raw_objects)} B/event | store {store_bytes // 1024}KB"
        )


if __name__ == "__main__":
    main()
