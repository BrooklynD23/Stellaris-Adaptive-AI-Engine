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


def _spy(monkeypatch: pytest.MonkeyPatch, running: bool) -> dict[str, int]:
    calls = {"s1": 0, "prio": 0}

    def s1(args: argparse.Namespace, ctx: object) -> int:
        calls["s1"] += 1
        return 0

    def prio() -> None:
        calls["prio"] += 1

    monkeypatch.setattr(cli, "_game_running", lambda: running)
    monkeypatch.setattr(cli, "_lower_priority", prio)
    monkeypatch.setattr(cli, "cmd_stage1", s1)
    monkeypatch.setattr(cli, "_context", lambda a: object())
    return calls


def test_game_running_refuses_exit_5(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _spy(monkeypatch, True)
    assert cli.main("gate", ["stage1", "a" * 64]) == 5
    assert "Stellaris is running" in capsys.readouterr().err
    assert calls == {"s1": 0, "prio": 0}


def test_force_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch, True)
    assert cli.main("gate", ["stage1", "a" * 64, "--force"]) == 0
    assert calls == {"s1": 1, "prio": 1}


def test_priority_is_own_process_only() -> None:
    import inspect

    from sae.profile import cli as pcli

    src = inspect.getsource(pcli._lower_priority)
    assert "psutil.Process()" in src and "os.nice" in src  # no pid argument: self only
