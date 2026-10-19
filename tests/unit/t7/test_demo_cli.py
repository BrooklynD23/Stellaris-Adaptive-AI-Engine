"""Demo (fake and real runner), parse and spy tests."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from sae.bench.cli import main
from sae.bench.demo import ARCHETYPES, run_demo


def _ev(seq: int, typ: str, payload: dict[str, object]) -> str:
    return json.dumps({"seq": seq, "type": typ, "payload": payload})


class FakeRunner:
    """Creates run dirs like ``sae synth`` would; records commands."""

    def __init__(self, final: dict[str, str], telemetry_rc: int = 0, synth_rc: int = 0) -> None:
        self.cmds: list[list[str]] = []
        self.final, self.telemetry_rc, self.synth_rc = final, telemetry_rc, synth_rc

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        c = list(cmd)
        self.cmds.append(c)
        if c[3] == "synth":
            if self.synth_rc:
                return subprocess.CompletedProcess(c, self.synth_rc, "", "")
            arch = c[c.index("--archetype") + 1]
            d = Path(c[c.index("--runs-dir") + 1]) / f"run_{arch}"
            d.mkdir()
            lines = [_ev(1, "strategy_selected", {"strategy": "baseline"}),
                     _ev(2, "strategy_selected", {"strategy": self.final[arch]}),
                     _ev(3, "intent_ack", {"status": "APPLIED"}), _ev(4, "intent_ack", {"status": "LOST"})]
            (d / "bus.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(c, 0, "", "")
        return subprocess.CompletedProcess(c, self.telemetry_rc, "", "")


def test_demo_fake_runner(tmp_path: Path) -> None:
    final = dict(ARCHETYPES)
    final["balanced"] = "early_punish"  # mismatch must not change exit code
    lines: list[str] = []
    fr = FakeRunner(final)
    rc = run_demo(6, 2, tmp_path, Path("/x/sae-parse"), runner=fr, echo=lines.append)
    assert rc == 0 and len(lines) == 4
    assert "turtle final=outscale_siege expected=outscale_siege match=yes acks_applied=1" in lines[0]
    assert "match=no" in lines[3] and str(tmp_path / "balanced.rrd") in lines[3]
    synth = fr.cmds[0]
    assert synth[:4] == [sys.executable, "-m", "sae", "synth"]
    assert 'paths.parser_exe="/x/sae-parse"' in synth and "--seed" in synth


def test_demo_rrd_skipped_and_failure(tmp_path: Path) -> None:
    lines: list[str] = []
    assert run_demo(1, 2, tmp_path, Path("e"), runner=FakeRunner(dict(ARCHETYPES), telemetry_rc=1),
                    echo=lines.append) == 0
    assert all(ln.endswith("rrd skipped") for ln in lines)
    lines.clear()
    assert run_demo(1, 2, tmp_path / "o2", Path("e"), runner=FakeRunner({}, synth_rc=1), echo=lines.append) == 1
    assert lines[0] == "turtle: run FAILED"


def test_demo_no_parser_is_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sae.bench.cli.resolve_paths", lambda _cfg: SimpleNamespace(parser_exe=None))
    assert main("bench", ["demo", "--out", str(tmp_path)]) == 1


def test_parse_and_spy_with_fake_runner(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import os

    saves = tmp_path / "saves"
    saves.mkdir()
    dates = {"a.sav": ("2230.01.01", 1000), "b.sav": ("2230.07.01", 1060), "c.sav": ("2231.01.01", 1120)}
    for name, (_, mt) in dates.items():
        (saves / name).write_text("x")
        os.utime(saves / name, (mt, mt))

    def runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        date = dates[Path(cmd[cmd.index("--save") + 1]).name][0]
        out = {"meta": {"date": date}, "cost": {"wall_s": 1.0, "cpu_s": 0.5, "rss_peak_bytes": 10}}
        return subprocess.CompletedProcess(list(cmd), 0, json.dumps(out), "")

    exe = ["--parser-exe", "p"]
    assert main("bench", ["spy", str(saves), *exe], runner=runner) == 0
    assert "median=120" in capsys.readouterr().out  # 60 s per 6 months
    assert main("bench", ["parse", str(saves / "a.sav"), "--repeat", "2", *exe], runner=runner) == 0
    assert "wall_s median=1 max=1 n=2" in capsys.readouterr().out

