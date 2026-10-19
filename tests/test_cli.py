"""Tests for the sae CLI dispatcher."""

import sys

import pytest

from sae.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == "sae 0.1.0"


def test_unavailable_subcommand(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sae.daemon.cli", None)  # simulate a team module not built yet
    assert main(["daemon", "--x"]) == 2
    assert "sae daemon: not available yet (owner T4)" in capsys.readouterr().err


def test_no_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out
