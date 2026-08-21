"""Shared test-environment isolation."""

from __future__ import annotations

import pytest

from vouch_mk2.store.event_log import _CI_RUN_ID_VARIABLES


@pytest.fixture(autouse=True)
def _isolate_ci_run_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make CI identity opt-in for every repository test."""
    for name in _CI_RUN_ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)
