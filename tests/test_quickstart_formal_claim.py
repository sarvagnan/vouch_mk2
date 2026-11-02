"""Acceptance pack for self-hosted unit quickstart-formal-claim@1 (D-28, bead vouch_mk2-4zh.1).

Every test here except the one marked ``# guard:`` FAILED against the pre-change tree (RED
recorded before implementation) and must PASS at completion. The implementer may not edit this
file.

Intent: the quickstart states the verdict ``check`` actually produces for the formal model. With
a certificate recorded, ``check.formal-certificate`` is PASS for the narrowed obligation at every
tier, and its detail names what ``check`` did not perform.
"""

from __future__ import annotations

import re
from pathlib import Path

from vouch_mk2.core import Snapshot, Tier
from vouch_mk2.formal import FormalCertificate, UNIVERSES
from vouch_mk2.formal.certificate import CHECKER_VERSION
from vouch_mk2.formal.explore import emit_tables
from vouch_mk2.formal.invariants import INVARIANTS
from vouch_mk2.formal.witnesses import WITNESSES
from vouch_mk2.ir import mk2
from vouch_mk2.ir.schema import Verdict
from vouch_mk2.store import (
    ArtifactKind,
    Store,
    digest_of,
    encode_formal_certificate,
    encode_formal_tables,
)
from vouch_mk2.verify import run_artifact_checks

ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "docs" / "quickstart.md"

_FENCE = re.compile(r"^```.*?^```[^\n]*$", re.M | re.S)
_FORMAL_TOPIC = re.compile(r"invariant|witness|formal model|exploration|explore", re.I)
_NOT_CHECKED = re.compile(r"NOT[_-]CHECKED")


def _sentences() -> list[str]:
    """Prose sentences of the quickstart, fenced blocks removed, whitespace normalized."""
    prose = _FENCE.sub("\n\n", QUICKSTART.read_text())
    text = " ".join(prose.split())
    return [part for part in re.split(r"(?<=[.!?])\s+", text) if part]


def _row_sentences() -> list[str]:
    return [sentence for sentence in _sentences() if "`check.formal-certificate`" in sentence]


def _certificate_row(tmp_path: Path, tier: Tier):
    tables = encode_formal_tables(emit_tables())
    certificate = FormalCertificate(
        checker_version=CHECKER_VERSION,
        tables_digest=digest_of(ArtifactKind.FORMAL_TABLES, tables),
        universes=tuple((universe.name, "a" * 64, 1, 1) for universe in UNIVERSES),
        invariants=tuple(
            (universe.name, invariant_id, True)
            for universe in UNIVERSES
            for invariant_id in (
                universe.invariant_ids
                or tuple(invariant.invariant_id for invariant in INVARIANTS)
            )
        ),
        witnesses=tuple(
            (witness.witness_id, witness.invariant_id, True) for witness in WITNESSES
        ),
    )
    data = encode_formal_certificate(certificate)
    with Store.init(
        str(tmp_path / tier.value),
        initial_snapshot=Snapshot(
            tier=tier,
            formal_certificate_digest=digest_of(ArtifactKind.FORMAL_CERTIFICATE, data),
        ),
    ) as store:
        with store.write_locked():
            store.put_object(ArtifactKind.FORMAL_TABLES, tables)
            store.put_object(ArtifactKind.FORMAL_CERTIFICATE, data)
        return next(
            row
            for row in run_artifact_checks(store, store.read_snapshot())
            if row.check_id == mk2.CHECK_FORMAL_CERTIFICATE
        )


def test_quickstart_names_the_formal_certificate_row_with_the_verdict_check_reports(
    tmp_path: Path,
) -> None:
    """R1: the quickstart names the row and its PASS verdict, and the code produces exactly that."""
    for tier in Tier:
        row = _certificate_row(tmp_path, tier)
        assert row.verdict is Verdict.PASS, tier
        assert "not performed by `check`" in row.detail, tier
    rows = _row_sentences()
    assert rows, "the quickstart never names `check.formal-certificate`"
    assert any(re.search(r"\bPASS\b", sentence) for sentence in rows), rows


def test_quickstart_attaches_no_not_checked_verdict_to_formal_exploration() -> None:
    """R2: no sentence about invariants, witnesses or the exploration claims a NOT_CHECKED row."""
    offenders = [
        sentence
        for sentence in _sentences()
        if _FORMAL_TOPIC.search(sentence) and _NOT_CHECKED.search(sentence)
    ]
    assert offenders == []


def test_quickstart_does_not_limit_certificate_verification_to_high() -> None:
    """R3: certificate verification is stated for any tier, never scoped to `high` alone."""
    rows = _row_sentences()
    assert any(re.search(r"\b(any|every) tier\b", sentence) for sentence in rows), rows
    scoped = [
        sentence
        for sentence in _sentences()
        if "certificate" in sentence and re.search(r"\bat `?high`?\b", sentence, re.I)
    ]
    assert scoped == []


def test_quickstart_still_shows_how_to_record_the_exploration() -> None:
    # guard: R4 — the reader still learns how to run and record the exploration.
    assert any("formal --record" in sentence for sentence in _sentences())
