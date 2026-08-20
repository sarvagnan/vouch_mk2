"""Frozen acceptance pack for self-hosted unit strict-types@1 (D-1, D-21).

Every test here failed against the pre-change tree except tests marked ``guard``.
"""

from __future__ import annotations

import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYREFLY = Path(sys.executable).with_name("pyrefly")


def _run_pyrefly(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYREFLY), "check", *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def _pyrefly_section() -> tuple[str, ...]:
    text = (ROOT / "pyproject.toml").read_text()
    marker = "[tool.pyrefly]"
    assert marker in text
    lines: list[str] = []
    for line in text.split(marker, 1)[1].splitlines()[1:]:
        if line.startswith("["):
            break
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return tuple(lines)


def _python_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in (ROOT / "src", ROOT / "tests")
            for path in root.rglob("*.py")
        )
    )


def test_pyrefly_configuration_selects_unweakened_strict_preset() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    settings = _pyrefly_section()

    assert 'preset = "strict"' in settings
    assert 'project-includes = ["src", "tests"]' in settings
    assert {line.partition("=")[0].strip() for line in settings} == {
        "preset",
        "project-includes",
    }
    assert "[tool.pyrefly." not in pyproject


def test_explicit_strict_preset_is_green() -> None:
    completed = _run_pyrefly("--preset", "strict")

    assert completed.returncode == 0, completed.stdout
    assert "0 errors" in completed.stdout


def test_source_and_tests_need_no_any_or_type_suppression() -> None:
    violations: list[str] = []
    for path in _python_files():
        with tokenize.open(path) as stream:
            for token in tokenize.generate_tokens(stream.readline):
                relative = path.relative_to(ROOT)
                if token.type == tokenize.NAME and token.string == "Any":
                    violations.append(f"{relative}:{token.start[0]}: Any")
                if token.type == tokenize.COMMENT and (
                    "type: ignore" in token.string
                    or "pyrefly: ignore" in token.string
                ):
                    violations.append(
                        f"{relative}:{token.start[0]}: {token.string.strip()}"
                    )

    assert not violations, "\n".join(violations)


def test_ci_runs_the_authoritative_configured_strict_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    expected = (
        "      - name: Types (Pyrefly strict)\n"
        "        run: uv run pyrefly check\n"
    )

    assert expected in workflow
    assert "uv run pyrefly check --preset" not in workflow
    assert 'preset = "strict"' in _pyrefly_section()


def test_configured_type_gate_is_green() -> None:
    # guard: this passed under the misleading default preset before R1. Once R1 lands,
    # the same ordinary invocation proves that local and CI use the strict authority.
    completed = _run_pyrefly()

    assert completed.returncode == 0, completed.stdout
    assert "0 errors" in completed.stdout
