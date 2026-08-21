"""Acceptance pack for ``ci-action-node24@1``; Codex may not edit."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from vouch_mk2.init import generated_workflow


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

REVIEWED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "astral-sh/setup-uv": "c771a70e6277c0a99b617c7a806ffedaca235ff9",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
}


def _remote_action_references(text: str) -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    for value in re.findall(r"uses:\s*([^\s#]+)", text):
        if value.startswith("./"):
            continue
        action, separator, revision = value.partition("@")
        assert separator and action and revision, f"invalid remote action reference: {value}"
        references.append((action, revision))
    return tuple(references)


def test_generated_and_dogfood_workflows_use_only_reviewed_node24_actions() -> None:
    surfaces = {
        "generated adopter workflow": generated_workflow(),
        "mk2 dogfood workflow": CI.read_text(),
    }
    observed_actions: set[str] = set()
    for surface, text in surfaces.items():
        for action, revision in _remote_action_references(text):
            observed_actions.add(action)
            assert action in REVIEWED_ACTIONS, f"{surface}: unreviewed action {action}"
            assert revision == REVIEWED_ACTIONS[action], (
                f"{surface}: {action}@{revision} is not the reviewed Node 24 identity"
            )
            assert re.fullmatch(r"[0-9a-f]{40}", revision)
    assert observed_actions == set(REVIEWED_ACTIONS)


def test_workflows_do_not_suppress_deprecated_node_runtime_warnings() -> None:
    combined = generated_workflow() + "\n" + CI.read_text()
    assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in combined


def test_setup_uv_keeps_cache_pruning_explicit() -> None:
    document = yaml.safe_load(CI.read_text())
    setup_uv_steps = [
        step
        for job in document["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    ]
    assert setup_uv_steps
    for step in setup_uv_steps:
        assert step.get("with", {}).get("prune-cache") is True

