"""Owner regressions for merge-concurrent-settings@1 (D-45), from Astra's review.

MEDIUM: the amended D-41 R17 refusal also passes if an independent mark is wrongly classified as a
copy (the staged-value check refuses later with the same code), so D-41's deduplication point needs
a success case that shows the independent mark replays under its own origin. LOW: the frozen pack
finds each setting's key only as a substring that the command verb also contains (``ci`` in
``set-ci-policy``), so a detail that dropped the key would pass; here the key must precede the
OURS side.
"""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import commands
from vouch_mk2.core.decide import Accepted, decide
from vouch_mk2.store import Store, decode_event_record
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "merge-concurrent-settings"
T = "2026-11-21T00:00:00+00:00"
_CI_RUN_ID_VARIABLES = ("GITHUB_RUN_ID", "CI_PIPELINE_ID", "BUILDKITE_BUILD_ID", "CIRCLE_WORKFLOW_ID")


@pytest.fixture(autouse=True)
def _no_ci_run_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _CI_RUN_ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _ex(
    root: Path,
    command: commands.Command,
    *,
    actor: str | None = None,
    objects: tuple[tuple[ArtifactKind, bytes], ...] = (),
) -> None:
    with Store.open(str(root)) as store, store.write_locked():
        snapshot = store.read_snapshot()
        decision = decide(snapshot, command)
        assert isinstance(decision, Accepted), (root.name, command, decision)
        commit_events(
            store, snapshot, decision.events,
            actor=actor or root.name, run_id="", recorded_at=T, objects=objects,
        )


def _base(tmp_path: Path, *forks: str) -> tuple[Path, ...]:
    """Store A with x@1 specified and label-frozen, and one copy of it per fork name."""
    a = tmp_path / "A"
    Store.init(str(a)).close()
    _ex(a, commands.SpecifyUnit("x", "1"))
    _ex(a, commands.FreezeAcceptance("x", "x-pack/1"))
    copies = [a]
    for name in forks:
        shutil.copytree(a, tmp_path / name)
        (tmp_path / name / ".lock").unlink(missing_ok=True)
        copies.append(tmp_path / name)
    return tuple(copies)


def _marks(root: Path) -> list[tuple[str, str]]:
    """Every OpenInCiMarked record on the chain as (digest, origin), oldest first."""
    items: list[tuple[str, str]] = []
    with Store.open(str(root), read_only=True) as store:
        digest = store.read_snapshot().event_head
        while digest and digest != "GENESIS":
            record = decode_event_record(store.get_object(ArtifactKind.EVENT, digest))
            if type(record.folded_event).__name__ == "OpenInCiMarked":
                items.append((digest, record.origin))
            digest = record.prev
    return items[::-1]


def test_an_independent_identical_mark_replays_under_its_own_origin(tmp_path: Path) -> None:
    """D-41 R17's point, as a success: B holds C's mark; D marks the same unit with the same actor,
    time and reason on another parent. The values agree, the merge succeeds, and D's mark is
    replayed with D's record as its origin rather than deduplicated as a copy of C's."""
    _a, b, c, d = _base(tmp_path, "B", "C", "D")
    _ex(b, commands.SpecifyUnit("b", "1"))
    _ex(d, commands.SpecifyUnit("d", "1"))
    _ex(c, commands.MarkOpenInCi("x", "old"))
    assert isinstance(merge_store_roots(str(b), str(c), actor="B", run_id=""), MergeOutcome)
    _ex(d, commands.MarkOpenInCi("x", "old"), actor="C")
    (d_mark, _) = _marks(d)[-1]
    (c_mark, _) = _marks(c)[-1]
    assert d_mark != c_mark
    outcome = merge_store_roots(str(b), str(d), actor="B", run_id="")
    assert isinstance(outcome, MergeOutcome), outcome
    assert outcome.mode == "replayed"
    origins = [origin for _, origin in _marks(b)]
    assert d_mark in origins and c_mark in origins, origins


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


_CERTS = {
    label: (
        (FIXTURES / f"certificate-{label}.json").read_bytes(),
        (FIXTURES / f"tables-{label}.json").read_bytes(),
    )
    for label in "ab"
}


def _record_certificate(root: Path, label: str) -> None:
    certificate, tables = _CERTS[label]
    digest = digest_of(ArtifactKind.FORMAL_CERTIFICATE, certificate)
    _ex(
        root,
        commands.RecordFormalCertificate(digest),
        objects=(
            (ArtifactKind.FORMAL_CERTIFICATE, certificate),
            (ArtifactKind.FORMAL_TABLES, tables),
        ),
    )


@pytest.mark.parametrize(
    ("key", "ours_command", "theirs_command"),
    (
        ("ci-policy", ("set-ci-policy", "--policy", "strict"), ("set-ci-policy", "--policy", "ledger")),
        ("anchor-mode", ("set-anchor-mode", "--mode", "ci"), ("set-anchor-mode", "--mode", "none")),
        ("tier", ("set-tier", "--tier", "standard"), ("set-tier", "--tier", "high")),
        ("formal-certificate", ("a",), ("b",)),
    ),
)
def test_the_refusal_detail_names_the_key_before_the_sides(
    tmp_path: Path, key: str, ours_command: tuple[str, ...], theirs_command: tuple[str, ...]
) -> None:
    _a, ours, theirs = _base(tmp_path, "ours", "theirs")
    for root, command in ((ours, ours_command), (theirs, theirs_command)):
        if key == "formal-certificate":
            _record_certificate(root, command[0])
        else:
            code, output = _run(command[0], "--root", str(root), "--actor", root.name, *command[1:])
            assert code == 0, output
    code, output = _run(
        "merge", "--root", str(ours), "--other", str(theirs), "--actor", "ours", "--json"
    )
    assert code == 2, output
    detail = json.loads(output)["violations"][0]["detail"]
    assert isinstance(detail, str)
    assert key in detail and detail.index(key) < detail.index("OURS"), detail
