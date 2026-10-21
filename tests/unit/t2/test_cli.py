"""``sae replay`` / ``sae synth`` argument handling and factory wiring (T2 U5)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from sae.adapters import cli
from sae.adapters.replay import ReplayAdapter
from sae.adapters.synth import SynthAdapter
from sae.contracts.c6_adapter import AdapterFactory, AdapterMode, GodObservation
from sae.contracts.c8_config import SaeConfig

from .test_replay import FakeParser, build_session


class _Sink:
    def write_god(self, obs: GodObservation) -> None:
        return None


def test_replay_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = str(build_session(tmp_path))
    assert cli.main("replay", ["--speed", "bogus", s]) == 2
    assert cli.main("replay", [str(tmp_path / "missing")]) == 2
    assert cli.main("replay", []) == 2  # argparse: session_dir required
    assert cli.main("replay", ["--ack-model", "drop:7", s]) == 2
    assert cli.main("replay", ["--from", "nonsense", s]) == 2
    assert cli.main("replay", ["--unknown-flag", s]) == 2
    assert "session dir not found" in capsys.readouterr().err


def test_synth_usage_errors() -> None:
    assert cli.main("synth", ["--archetype", "nope"]) == 2
    assert cli.main("synth", ["--archetype", "turtle@x"]) == 2
    assert cli.main("synth", ["--years", "0"]) == 2
    assert cli.main("synth", ["--years", "abc"]) == 2
    assert cli.main("bogus", []) == 2


def test_bad_config_is_exit_3(tmp_path: Path) -> None:
    s = str(build_session(tmp_path))
    assert cli.main("replay", ["--set", "no_equals_sign", s]) == 3
    assert cli.main("synth", ["--set", "no_equals_sign"]) == 3
    assert cli.main("synth", ["--set", "engine.bogus=1"]) == 3
    assert cli.main("synth", ["--config", str(tmp_path / "missing.toml")]) == 3


def _install_daemon(monkeypatch: pytest.MonkeyPatch, run: object) -> None:
    """Stand-in ``sae.daemon`` (the real package imports sae.core, which may be mid-change)."""
    mod = types.ModuleType("sae.daemon")
    mod.run = run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sae.daemon", mod)


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    got: dict[str, object] = {}

    async def fake_run(config: SaeConfig, adapter: object, run_id: str) -> int:
        got.update(config=config, run_id=run_id)
        factory: AdapterFactory = adapter  # type: ignore[assignment]
        got["adapter"] = factory(FakeParser(), _Sink())
        return 0

    _install_daemon(monkeypatch, fake_run)
    return got


def test_replay_builds_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got = _capture(monkeypatch)
    s = str(build_session(tmp_path))
    runs = tmp_path / "runs"
    rc = cli.main(
        "replay",
        ["--runs-dir", str(runs), "--set", "engine.seed=9", "--speed", "2x", "--stop-at", "2200.05.01",
         "--ack-model", "delay:1", s],
    )
    assert rc == 0
    cfg, adapter = got["config"], got["adapter"]
    assert isinstance(cfg, SaeConfig) and cfg.adapter is AdapterMode.REPLAY
    assert cfg.paths.runs_dir == str(runs) and cfg.engine.seed == 9
    assert isinstance(adapter, ReplayAdapter) and adapter.mode is AdapterMode.REPLAY
    assert "-replay-" in str(got["run_id"])


def test_synth_builds_factory_with_seed_default_and_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = _capture(monkeypatch)
    monkeypatch.setattr(cli, "resolve_paths", lambda cfg: _FakePaths())
    assert cli.main("synth", ["--runs-dir", str(tmp_path), "--set", "engine.seed=4", "--archetype", "turtle@0"]) == 0
    assert isinstance(got["adapter"], SynthAdapter)
    cfg = got["config"]
    assert isinstance(cfg, SaeConfig) and cfg.adapter is AdapterMode.SYNTH
    assert cli.main("synth", ["--runs-dir", str(tmp_path), "--seed", "11", "--years", "1"]) == 0


class _FakePaths:
    parser_exe: Path | None = Path("/x/sae-parse")


def test_synth_without_parser_exe_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class NoExe:
        parser_exe: Path | None = None

    monkeypatch.setattr(cli, "resolve_paths", lambda cfg: NoExe())
    assert cli.main("synth", ["--runs-dir", str(tmp_path)]) == 1
    assert "sae-parse not found" in capsys.readouterr().err


def test_daemon_failure_is_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(config: SaeConfig, adapter: object, run_id: str) -> int:
        raise RuntimeError("component unavailable")

    _install_daemon(monkeypatch, boom)
    assert cli.main("replay", ["--runs-dir", str(tmp_path / "r"), str(build_session(tmp_path))]) == 1


def test_local_sae_toml_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got = _capture(monkeypatch)
    monkeypatch.delenv("SAE_CONFIG", raising=False)
    (tmp_path / "sae.toml").write_text("[engine]\nseed = 77\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main("replay", ["--runs-dir", str(tmp_path / "r"), str(build_session(tmp_path))]) == 0
    cfg = got["config"]
    assert isinstance(cfg, SaeConfig) and cfg.engine.seed == 77


def test_synth_default_finds_repo_build_and_hands_it_to_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sae.adapters.paths import _REPO_ROOT

    builds = [_REPO_ROOT / "parser/target" / k / "sae-parse" for k in ("release", "debug")]
    if not any(b.is_file() for b in builds):
        pytest.skip("no parser build under parser/target (cargo build -j 1 in parser/)")
    got = _capture(monkeypatch)
    from sae.adapters import paths

    monkeypatch.setattr(cli, "resolve_paths", lambda c: paths.resolve_paths(c, which=lambda _: None))
    assert cli.main("synth", ["--runs-dir", str(tmp_path), "--archetype", "turtle", "--years", "1"]) == 0
    cfg = got["config"]
    assert isinstance(cfg, SaeConfig)
    assert cfg.paths.parser_exe in {str(b) for b in builds}  # the daemon's governed parser gets the same exe
