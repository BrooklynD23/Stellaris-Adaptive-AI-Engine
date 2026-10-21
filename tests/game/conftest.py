"""Fixtures for deferred game tests (X3 D3, D10). All inputs come from SAE_GAME_* env vars."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PARSE_TIMEOUT_S = 60


@pytest.fixture
def game_path() -> Callable[[str], Path]:
    """Factory: ``game_path("SAE_GAME_LOG")`` -> Path; skips when unset."""

    def _get(name: str) -> Path:
        value = os.environ.get(name)
        if not value:
            pytest.skip(f"game input missing: {name}")
        return Path(value)

    return _get


@pytest.fixture(scope="session")
def game_parser_exe() -> Path:
    """SAE_PARSER_EXE, else the resolved repo/PATH parser, else skip."""
    env = os.environ.get("SAE_PARSER_EXE")
    if env:
        return Path(env)
    from sae.adapters.paths import resolve_paths
    from sae.contracts.c8_config import PathsConfig

    exe = resolve_paths(PathsConfig()).parser_exe
    if exe is None:
        pytest.skip("game input missing: SAE_PARSER_EXE")
    return Path(exe)


@pytest.fixture(scope="session")
def game_saves() -> list[Path]:
    """Newest N ``*.sav`` in SAE_GAME_SAVES_DIR (N = SAE_GAME_MAX_SAVES or 24)."""
    d = os.environ.get("SAE_GAME_SAVES_DIR")
    if not d:
        pytest.skip("game input missing: SAE_GAME_SAVES_DIR")
    n = int(os.environ.get("SAE_GAME_MAX_SAVES") or 24)
    saves = sorted(Path(d).glob("*.sav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not saves:
        pytest.fail("SAE_GAME_SAVES_DIR set but no *.sav found", pytrace=False)
    return saves[:n]


@pytest.fixture(scope="session")
def parsed_saves(game_parser_exe: Path, game_saves: list[Path]) -> list[tuple[Path, int, str]]:
    """ONE full parse per save: (path, exit code, stdout). The only full-parse spawner [R4]."""
    out: list[tuple[Path, int, str]] = []
    for save in game_saves:
        try:
            r = subprocess.run(
                [str(game_parser_exe), "parse", "--save", str(save)],
                capture_output=True,
                text=True,
                timeout=_PARSE_TIMEOUT_S,
                check=False,
            )
            out.append((save, r.returncode, r.stdout))
        except subprocess.TimeoutExpired:
            out.append((save, -1, ""))
    return out


@pytest.fixture(scope="session")
def game_recording(tmp_path_factory: pytest.TempPathFactory, game_parser_exe: Path) -> Path:
    """SAE_GAME_RECORDING, else build via tools/game/make_recording.py (X3-2)."""
    env = os.environ.get("SAE_GAME_RECORDING")
    if env:
        return Path(env)
    log, saves = os.environ.get("SAE_GAME_LOG"), os.environ.get("SAE_GAME_SAVES_DIR")
    if not log or not saves:
        missing = [n for n, v in (("SAE_GAME_LOG", log), ("SAE_GAME_SAVES_DIR", saves)) if not v]
        pytest.skip(f"game input missing: {', '.join(missing)}")
    script = _REPO / "tools" / "game" / "make_recording.py"
    spec = importlib.util.spec_from_file_location("make_recording", script)
    if spec is None or spec.loader is None:
        pytest.skip(f"make_recording.py not loadable: {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path_factory.mktemp("recording")
    argv = ["--log", str(log), "--saves", str(saves), "--out", str(out), "--parser-exe", str(game_parser_exe)]
    rc = int(mod.main(argv))
    assert rc == 0, f"make_recording exited {rc}"
    return out
