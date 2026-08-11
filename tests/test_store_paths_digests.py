from __future__ import annotations

import pytest

from vouch_mk2.store import ArtifactKind, PathError, RelPath, digest_of


@pytest.mark.parametrize(
    "text",
    (
        "/absolute",
        "a/../b",
        "a/./b",
        "a//b",
        "",
        "nul\x00byte",
        "a\\b",
        "x" * 256,
    ),
)
def test_relative_path_refusals(text: str) -> None:
    with pytest.raises(PathError):
        RelPath.parse(text)


def test_relative_path_accepts_safe_segments() -> None:
    assert RelPath.parse("objects/sha256/abc").segments == (
        "objects",
        "sha256",
        "abc",
    )


def test_digest_domain_separation() -> None:
    payload = b"identical bytes"
    assert digest_of(ArtifactKind.STATE, payload) != digest_of(
        ArtifactKind.OBJECT, payload
    )


def test_digest_determinism() -> None:
    payload = b"stable"
    assert digest_of(ArtifactKind.OBJECT, payload) == digest_of(
        ArtifactKind.OBJECT, payload
    )


def test_every_artifact_kind_label_preserves_domain_separation() -> None:
    # Framing is prefix + kind + NUL + payload; injectivity requires NUL-free,
    # non-empty ASCII labels. A future enum member violating this must fail here.
    for kind in ArtifactKind:
        label = kind.value.encode("ascii")
        assert label
        assert b"\x00" not in label
