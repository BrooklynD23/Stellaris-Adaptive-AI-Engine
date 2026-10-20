"""Tests for the sae CLI dispatcher."""

import sys

import pytest

from sae.cli import _COMMANDS, main


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


def test_t9_commands_dispatched() -> None:
    assert _COMMANDS["profile"] == ("sae.profile.cli", "T9")
    assert _COMMANDS["play"] == ("sae.profile.launcher", "T9")


def _patch_module(monkeypatch: pytest.MonkeyPatch, obj: object) -> None:
    monkeypatch.setattr("sae.cli.importlib.import_module", lambda _name: obj)


def test_dispatch_returns_entry_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    mod = types.SimpleNamespace(main=lambda cmd, rest: 7 if (cmd, rest) == ("ctl", ["x"]) else 1)
    _patch_module(monkeypatch, mod)
    assert main(["ctl", "x"]) == 7


def test_module_without_main(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    _patch_module(monkeypatch, types.SimpleNamespace())
    assert main(["ctl"]) == 2
    assert "not available yet" in capsys.readouterr().err


def test_unrelated_module_not_found_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_name: str) -> None:
        raise ModuleNotFoundError("nope", name="some_other_dep")

    monkeypatch.setattr("sae.cli.importlib.import_module", boom)
    with pytest.raises(ModuleNotFoundError):
        main(["ctl"])


@pytest.mark.parametrize("target", ["sae", "sae.cli"])
def test_run_as_main(target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    monkeypatch.setattr(sys, "argv", ["sae", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module(target, run_name="__main__")
    assert exc.value.code == 0
