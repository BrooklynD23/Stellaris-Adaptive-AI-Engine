"""Static checks on ``.github/workflows/ci.yml`` (the actions never ran here)."""

from __future__ import annotations

import re

import pytest

from .conftest import REPO

TEXT = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_job_names() -> None:
    """The four jobs exist (stdlib only: two-space keys under ``jobs:``; actionlint validated the YAML)."""
    jobs = TEXT.split("\njobs:\n", 1)[1]
    assert set(re.findall(r"^  ([\w-]+):$", jobs, re.M)) == {"python-linux", "mod", "rust-linux", "windows"}


@pytest.mark.parametrize("cmd", [
    "uv sync --all-extras", "cargo build --release -j 1", "uv run pytest -q", "uv run mypy --strict src",
    "uv run ruff check", "tests/e2e/test_ci1.py", "tests/unit/t5", "ci3 or ram_ramp or fault_spike",
    "--co -q", "tests/integration/test_ci4_*.py", "tests/e2e/test_ci5.py", "cargo fmt --check",
    "cargo clippy -j 1 -- -D warnings", "cargo test -j 1", "rustup target add x86_64-pc-windows-gnu",
    "cargo check -j 1 --target x86_64-pc-windows-gnu", "mod/sae_bridge/tools/check_mod.py",
    "uv run pytest -q mod/sae_bridge/tests", "cargo clippy -j 1 --all-targets -- -D warnings", "feat/**",
])
def test_gate_command_present(cmd: str) -> None:
    """Each gate command appears in the workflow."""
    assert cmd in TEXT


def test_uses_pinned_to_sha() -> None:
    """Every ``uses:`` is a full 40-hex commit SHA with a version comment; no SAE_* env var."""
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", TEXT, re.M)
    assert len(uses) >= 6
    for ref, rest in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref
        assert "#" in rest
    assert "SAE_" not in TEXT.replace("T7_E2E_FULL", "")


def test_named_ci_steps_never_run_tests_twice() -> None:
    """CI-1/3/4/5 steps are collection guards only; the single full run is the ``pytest`` step."""
    # linux: the full run carries the coverage gate (maintainer target 90 %); windows: plain full run
    assert TEXT.count("uv run pytest -q --cov --cov-fail-under=90\n") == 1
    assert TEXT.count("uv run pytest -q\n") == 1
    for line in TEXT.splitlines():
        if "pytest" in line and "tests/" in line:
            assert "--co -q" in line, line
