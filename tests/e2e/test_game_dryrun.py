"""D9: dry run of the whole deferred harness on a synthetic campaign (no real game input)."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

from e2e import _synth

REPO = Path(__file__).resolve().parents[2]
# G5 stays skipped: synth saves are quarterly (month gaps of 3), so the real cadence assert cannot pass on them.
_ALLOWED_SKIP = (("X3-G6", "SAE_GAME_IRONMAN_SAVE"), ("X3-G5", "no autosave_"))


def _make_recording(log: Path, saves: Path, out: Path, exe: Path) -> None:
    spec = importlib.util.spec_from_file_location("make_recording", REPO / "tools" / "game" / "make_recording.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main(["--log", str(log), "--saves", str(saves), "--out", str(out), "--parser-exe", str(exe)])
    assert rc == 0, f"make_recording exit {rc}"


def test_deferred_dry_run(tmp_path: Path, parser_exe: Path) -> None:
    rec = _synth.record(tmp_path / "synth", parser_exe, "turtle", 2, 1)
    rebuilt = tmp_path / "rebuilt"
    _make_recording(rec / "game.log", rec / "saves", rebuilt, parser_exe)
    sys_tmp = tmp_path / "sys_tmp"
    sys_tmp.mkdir()
    out = tmp_path / "out"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAE_GAME_")}
    env.update(
        SAE_GAME_LOG=str(rec / "game.log"),
        SAE_GAME_SAVES_DIR=str(rec / "saves"),
        SAE_GAME_RECORDING=str(rebuilt),
        SAE_PARSER_EXE=str(parser_exe),
        TMPDIR=str(sys_tmp),
        TEMP=str(sys_tmp),
        TMP=str(sys_tmp),
    )
    env.pop("SAE_GAME_IRONMAN_SAVE", None)
    if b"|ACK|" in (rec / "game.log").read_bytes():
        env["SAE_GAME_EXPECT_ACK"] = "1"
    p = subprocess.run(
        [sys.executable, str(REPO / "tools" / "game" / "run_deferred.py"), "--out", str(out)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=900, check=False,
    )
    assert p.returncode == 0, f"run_deferred exit {p.returncode}; stdout tail: {p.stdout[-600:]}"
    summary = (out / "summary.md").read_text(encoding="utf-8")
    print(summary)
    m = re.search(r"pass (\d+) / fail (\d+) / skip (\d+) / error (\d+)", summary)
    assert m and m[2] == "0" and m[4] == "0", "summary counts"
    for ln in summary.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[2] == "skip":
            assert any(cells[0] == d and r in cells[3] for d, r in _ALLOWED_SKIP), f"unexpected skip: {ln[:200]}"
    assert not list(sys_tmp.rglob("*.sav")), "a .sav landed in the system temp dir (default basetemp used)"
    assert (out / "private" / "tmp").exists(), "private/tmp missing"
