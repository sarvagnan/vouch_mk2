"""Record RED/GREEN evidence for a self-hosted unit from a real pytest run (D-11).

The harness — never a hand — records the outcome: it runs the given test path,
records GREEN if every test passed and RED if any failed, with the exact pytest
argv as the event's command_line. This is what makes red-before-green a fact
about the tree, not a claim.

Usage: python scripts/record_pytest_evidence.py --root .vouch --unit NAME --version V \
           --kind red|green|refactor -- tests/test_x.py [pytest args...]
Exit: 0 when the recorded kind is coherent with the run (red requires failures,
green/refactor require a clean pass); 1 otherwise — an incoherent record is refused,
not written (the same rule decide() enforces).
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys

from vouch_mk2.core import EvidenceEvent, EvidenceKind, EvidenceOutcome, UnitRef
from vouch_mk2.store import Store
from vouch_mk2.verify import record_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--kind", required=True, choices=[k.value for k in EvidenceKind])
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    pytest_args = [a for a in args.pytest_args if a != "--"]
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *pytest_args]
    proc = subprocess.run(argv, capture_output=True, text=True)
    passed = proc.returncode == 0
    outcome = EvidenceOutcome.TESTS_PASSED if passed else EvidenceOutcome.TESTS_FAILED
    kind = EvidenceKind(args.kind)
    tail = (proc.stdout.strip().splitlines() or [""])[-1]
    print(f"[evidence] pytest exit {proc.returncode}: {tail}")
    coherent = (kind is EvidenceKind.RED) == (not passed)
    if not coherent:
        print(f"[evidence] REFUSED: kind {kind.value} is incoherent with a run that "
              f"{'passed' if passed else 'failed'} — nothing recorded")
        return 1
    with Store.open(args.root) as store:
        head = store.read_snapshot().evidence_head(args.unit)
        sequence = 1 if head is None else head.head_sequence + 1
        parent = "GENESIS" if head is None else head.head
        event = EvidenceEvent(
            unit=UnitRef(args.unit, args.version), sequence=sequence, parent=parent, kind=kind,
            command_line=" ".join(argv), outcome=outcome,
            recorded_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        )
        digest = record_evidence(store, event)
    print(f"[evidence] recorded {kind.value} #{sequence} for {args.unit}@{args.version}: {digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
