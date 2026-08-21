"""Acceptance pack for ``projection-anchor-ordering@1``; Codex may not edit."""

from __future__ import annotations

import json

import pytest

from vouch_mk2.core import EvidenceHead, Snapshot, Unit, UnitStatus
from vouch_mk2.projections import system_map
from vouch_mk2.store import DecodeError, decode_system_map, encode_system_map


def _claimed_snapshot() -> Snapshot:
    first_digest = "a" * 64
    second_digest = "b" * 64
    return Snapshot(
        units=(
            Unit("ci-anchor", "1", UnitStatus.IN_PROGRESS),
            Unit("ci-anchor-closure", "1", UnitStatus.IN_PROGRESS),
        ),
        evidence_heads=(
            EvidenceHead(
                "ci-anchor",
                first_digest,
                1,
                first_digest,
                "1",
                anchor_run_id="32468660968",
            ),
            EvidenceHead(
                "ci-anchor-closure",
                second_digest,
                1,
                second_digest,
                "1",
                anchor_run_id="32468660968",
            ),
        ),
    )


def test_prefix_related_anchor_claims_round_trip_in_unit_ref_order() -> None:
    encoded = encode_system_map(system_map(_claimed_snapshot()), "c" * 64)
    decoded, _source_digest = decode_system_map(encoded)
    expected = (
        ("ci-anchor@1", "32468660968"),
        ("ci-anchor-closure@1", "32468660968"),
    )
    assert tuple(
        (head.ref().label(), head.anchor_run_id) for head in decoded.evidence_heads
    ) == expected
    assert decoded.status_facts.anchor_run_claims == expected


def test_disagreeing_anchor_claim_remains_rejected() -> None:
    document = json.loads(
        encode_system_map(
            system_map(
                Snapshot(
                    units=(Unit("u", "1", UnitStatus.IN_PROGRESS),),
                    evidence_heads=(
                        EvidenceHead(
                            "u",
                            "d" * 64,
                            1,
                            "d" * 64,
                            "1",
                            anchor_run_id="42",
                        ),
                    ),
                )
            ),
            "e" * 64,
        )
    )
    document["map"]["status_facts"]["anchor_run_claims"] = [["u@1", "99"]]
    tampered = (
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    with pytest.raises(DecodeError, match="anchor_run_claims"):
        decode_system_map(tampered)
