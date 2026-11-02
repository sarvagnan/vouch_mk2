"""Acceptance pack for self-hosted unit quickstart-formal-claim@1 (D-28, bead vouch_mk2-4zh.1).

Every test here except the one marked ``# guard:`` FAILED against the pre-change tree (RED
recorded before implementation) and must PASS at completion. The implementer may not edit this
file.

pack/2 (DEV-qfc-01): R1 checked only that the word PASS appeared, so a sentence asserting the
opposite verdict passed every test. R1 now pins the sentence and binds the verdict it names to
the verdict the code reports.

pack/3 (DEV-qfc-02): pack/2 required the pinned sentence to exist but let another sentence state
a different verdict. R1 now also requires that every quickstart sentence mentioning the
certificate and naming a verdict is the pinned sentence.

pack/4 (DEV-qfc-03): sentence splitting broke at "e.g.", and the verdict pattern missed other
dash characters and HTML entities. R1 now pins the whole paragraph and normalizes every other
paragraph before looking for a verdict. The pack guards against accidental misstatement
(threat level 1); deliberately disguised text is left to review.

pack/5 (DEV-qfc-04): a list lead-in or heading could carry the certificate condition and a
following paragraph the false verdict. R1 now pins every prose paragraph of §8, where the
formal verdict is stated. Elsewhere the paragraph rule stands; a verdict split from its
certificate condition across paragraphs outside §8 is a recorded residual.

Intent: the quickstart states the verdict ``check`` actually produces for the formal model. With
a certificate recorded, ``check.formal-certificate`` is PASS for the narrowed obligation at every
tier, and its detail names what ``check`` did not perform.
"""

from __future__ import annotations

import html
import re
import unicodedata
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
_VERDICT = re.compile(r"\b(pass(es|ed)?|fail(s|ed)?|not[- ]checked|not[- ]applicable)\b")
_DASHES = re.compile(r"[_\u2010-\u2015\u2212\ufe58\ufe63\uff0d]")


def _sentences() -> list[str]:
    """Prose sentences of the quickstart, fenced blocks removed, whitespace normalized."""
    prose = _FENCE.sub("\n\n", QUICKSTART.read_text())
    text = " ".join(prose.split())
    return [part for part in re.split(r"(?<=[.!?])\s+", text) if part]


def _paragraphs() -> list[str]:
    """Prose paragraphs of the quickstart, fenced blocks removed, whitespace normalized."""
    prose = _FENCE.sub("\n\n", QUICKSTART.read_text())
    return [" ".join(part.split()) for part in re.split(r"\n\s*\n", prose) if part.strip()]


def _normalized(text: str) -> str:
    """Entities decoded, compatibility forms folded, every dash and underscore made a hyphen."""
    folded = unicodedata.normalize("NFKC", html.unescape(text))
    return _DASHES.sub("-", folded).lower()


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


_ROW_SENTENCE = (
    "With a valid recorded certificate, `check.formal-certificate` reports `PASS` at every "
    "tier for the narrowed obligation in "
    "[claim-boundary.md](claim-boundary.md#formal-certificate)."
)
_PARAGRAPH = (
    "`check` does not re-explore the bounded formal model. "
    + _ROW_SENTENCE
    + " Run `python3 vouch.lock formal --record --root .vouch` when you intend to perform and "
    "record that expensive exploration."
)


_SECTION_8 = (
    '## 8. Check and read the report',
    '`check --ci` runs every mechanical check and exits 0 only if the composed verdict is PASS. `generate` writes the system map and agent context, and `check` verifies both are fresh. `check.event-log` replays the event log and requires it to reproduce the committed snapshot, which is a derived cache, byte for byte.',
    '`check` does not re-explore the bounded formal model. With a valid recorded certificate, `check.formal-certificate` reports `PASS` at every tier for the narrowed obligation in [claim-boundary.md](claim-boundary.md#formal-certificate). Run `python3 vouch.lock formal --record --root .vouch` when you intend to perform and record that expensive exploration.',
    'Mechanical rows come first: PASS, FAIL, NOT-CHECKED or NOT-APPLICABLE, with the applicability reason where there is one. A chain never anchored under `ci` mode, for example, is a mechanical NOT-CHECKED. Organizational rows follow, and are always NOT-CHECKED because no artifact can prove them.',
)


def _section_8() -> tuple[str, ...]:
    """The prose paragraphs of quickstart §8, heading included, whitespace normalized."""
    match = re.search(r"^## 8\. .*?(?=^## )", QUICKSTART.read_text(), re.M | re.S)
    assert match is not None, "the quickstart has no §8"
    section = _FENCE.sub("\n\n", match.group(0))
    return tuple(" ".join(part.split()) for part in re.split(r"\n\s*\n", section) if part.strip())


def test_quickstart_names_the_formal_certificate_row_with_the_verdict_check_reports(
    tmp_path: Path,
) -> None:
    """R1: the quickstart's only verdict about the certificate is the pinned, true paragraph."""
    assert _PARAGRAPH in _SECTION_8
    assert _section_8() == _SECTION_8
    paragraphs = _paragraphs()
    assert _PARAGRAPH in paragraphs, [p for p in paragraphs if "certificate" in p]
    others = [
        paragraph
        for paragraph in paragraphs
        if paragraph != _PARAGRAPH
        and "certificate" in _normalized(paragraph)
        and _VERDICT.search(_normalized(paragraph))
    ]
    assert others == [], others
    claimed = re.search(r"reports `([A-Z_]+)`", _ROW_SENTENCE)
    assert claimed is not None
    for tier in Tier:
        row = _certificate_row(tmp_path, tier)
        assert row.verdict.name == claimed.group(1), tier
        assert row.verdict is Verdict.PASS, tier
        assert "not performed by `check`" in row.detail, tier


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
