"""``sae gate`` argument handling (parser, sha type, dispatch through ``sae.cli``)."""

from __future__ import annotations

import argparse

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sae import cli as sae_cli
from sae.bench.gate import cli

HEX = "0123456789abcdef"


@given(st.text(alphabet=HEX, min_size=64, max_size=64))
def test_sha_accepts_any_64_lowercase_hex(text: str) -> None:
    assert cli._sha(text) == text


@given(st.text(max_size=80).filter(lambda t: not (len(t) == 64 and set(t) <= set(HEX))))
def test_sha_rejects_everything_else(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli._sha(text)


@pytest.mark.parametrize("argv", [[], ["stage3", "a" * 64], ["stage1"], ["stage1", "xyz"], ["stage2", "a" * 64]])
def test_usage_errors_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main("gate", argv) == 2
    capsys.readouterr()


def test_stage2_objective_has_no_default() -> None:
    parser = cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["stage2", "a" * 64])
    args = parser.parse_args(["stage2", "a" * 64, "--objective", "challenge_band"])
    assert args.objective == "challenge_band" and args.include_synth is False


def test_sae_dispatch_reaches_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert sae_cli.main(["gate"]) == 2
    assert "usage" in capsys.readouterr().err.lower()
