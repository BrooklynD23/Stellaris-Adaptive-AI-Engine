"""make_recording: rebuild a synth recording from its own game.log + saves (needs a built sae-parse)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from e2e._cli import run_sae
from e2e._synth import record

from sae.adapters.paths import resolve_paths
from sae.contracts.c8_config import PathsConfig

REPO = Path(__file__).resolve().parents[3]
_SAVE = re.compile(r"^(\d{12})-(.+\.sav)$")
_PLYR_STUB = re.compile(r"^SAE\|v1\|PLYR\|\d{4}\.\d{2}\.\d{2}\|sid=\d+$")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_recording", REPO / "tools" / "game" / "make_recording.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def exe() -> Path:
    found = resolve_paths(PathsConfig()).parser_exe
    if found is None or not found.is_file():
        pytest.skip("build parser/")
    return found.resolve()


@pytest.fixture(scope="module")
def original(tmp_path_factory: pytest.TempPathFactory, exe: Path) -> Path:
    return record(tmp_path_factory.mktemp("orig"), exe, "balanced", 2, 1)


def _saves(d: Path) -> list[tuple[int, str]]:
    out = []
    for p in sorted((d / "saves").glob("*.sav")):
        m = _SAVE.match(p.name)
        assert m is not None, p.name
        out.append((int(m.group(1)), m.group(2)))
    return out


def test_rebuild_matches_original(
    original: Path, exe: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load()
    noisy = tmp_path / "noisy.log"  # junk lines, an older HELLO segment and a game-log prefix must all be dropped
    lines = (original / "game.log").read_text(encoding="utf-8").splitlines()
    old = [
        "SAE|v1|HELLO|2300.01.01|mod=0.1.0|schema=1|period=1|quiet=0|mp=0",
        "SAE|v1|HB|2300.02.01|m=1|period=1|quiet=0",
    ]
    body = ["noise", *old, "not sae", *("[12:00:00][x]: " + ln for ln in lines)]
    noisy.write_text("\n".join(body) + "\n", encoding="utf-8")
    out = tmp_path / "rebuilt"
    argv = ["--log", str(noisy), "--saves", str(original / "saves"), "--out", str(out), "--parser-exe", str(exe)]
    rc = mod.main(argv)
    assert rc == 0, capsys.readouterr().err
    assert _saves(out) == _saves(original)
    offsets = [o for o, _ in _saves(out)]
    assert offsets == sorted(offsets) and offsets[-1] > 0
    got = (out / "game.log").read_text(encoding="utf-8").splitlines()
    assert got == lines
    plyr = [ln for ln in got if "|PLYR|" in ln]
    assert plyr and all(_PLYR_STUB.match(ln) for ln in plyr)
    ts = [tuple(map(int, ln.split())) for ln in (out / "game.log.ts").read_text(encoding="utf-8").splitlines()]
    assert [t[0] for t in ts] == sorted({t[0] for t in ts}) and [t[1] for t in ts] == sorted({t[1] for t in ts})
    assert ts[-1][0] == (out / "game.log").stat().st_size
    r = run_sae(["replay", str(out), "--speed", "max"], tmp_path / "run", parser_exe=exe)
    assert r.code == 0, r.stderr[-500:]


def test_non_monotonic_warns(original: Path, exe: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load()
    log = tmp_path / "g.log"
    lines = (original / "game.log").read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join([*lines, "SAE|v1|HB|2200.01.01|m=1|period=1|quiet=0"]) + "\n", encoding="utf-8")
    argv = ["--log", str(log), "--saves", str(tmp_path), "--out", str(tmp_path / "o"), "--parser-exe", str(exe)]
    assert mod.main(argv) == 0
    assert "not monotonic" in capsys.readouterr().err


def test_out_must_be_empty_or_new(tmp_path: Path) -> None:
    mod = _load()
    (tmp_path / "o").mkdir()
    (tmp_path / "o" / "x").write_text("1")
    log = tmp_path / "g.log"
    log.write_text("SAE|v1|HELLO|2200.01.01\n")
    assert mod.main(["--log", str(log), "--saves", str(tmp_path), "--out", str(tmp_path / "o")]) == 2


def test_bad_inputs_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    assert mod.main(["--log", str(tmp_path / "none"), "--saves", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
    log = tmp_path / "g.log"
    log.write_text("x\n")
    monkeypatch.delenv("SAE_PARSER_EXE", raising=False)
    monkeypatch.setattr(mod, "resolve_paths", lambda cfg: type("R", (), {"parser_exe": None})())
    assert mod.main(["--log", str(log), "--saves", str(tmp_path), "--out", str(tmp_path / "o")]) == 2


def test_parse_failure_exit_1(tmp_path: Path, exe: Path) -> None:
    mod = _load()
    log = tmp_path / "g.log"
    log.write_text("SAE|v1|HELLO|2200.01.01|mod=0.1.0|schema=1|period=1|quiet=0|mp=0\n")
    saves = tmp_path / "s"
    saves.mkdir()
    (saves / "bad.sav").write_bytes(b"not a save")
    argv = ["--log", str(log), "--saves", str(saves), "--out", str(tmp_path / "o"), "--parser-exe", str(exe)]
    assert mod.main(argv) == 1
