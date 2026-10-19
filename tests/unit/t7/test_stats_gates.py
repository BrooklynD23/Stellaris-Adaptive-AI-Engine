"""Stats and gate tests over synthetic CSV and bus.jsonl fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sae.bench import gates
from sae.bench.cli import main
from sae.bench.stats import bootstrap_slowdown_ci, ols_slope, percentile


def test_percentile_and_ols() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile(list(range(101)), 99) == pytest.approx(99.0)
    slope, icpt = ols_slope([0, 1, 2, 3], [1, 3, 5, 7])
    assert slope == pytest.approx(2.0)
    assert icpt == pytest.approx(1.0)
    with pytest.raises(ValueError):
        ols_slope([1, 1], [2, 3])


def test_bootstrap_deterministic() -> None:
    a, c = [100.0, 101.0, 99.0, 100.5], [101.0, 102.0, 100.0, 101.5]
    u1 = bootstrap_slowdown_ci(a, c, seed=7)[1]
    assert u1 == bootstrap_slowdown_ci(a, c, seed=7)[1]
    assert bootstrap_slowdown_ci(a, c, seed=7, resamples=20) != bootstrap_slowdown_ci(
        a, c, seed=8, resamples=20
    )
    assert 0.0 < u1 < 0.05


@given(st.lists(st.floats(0, 1e6), min_size=1, max_size=30), st.floats(0, 100))
def test_percentile_bounds(vals: list[float], q: float) -> None:
    assert min(vals) <= percentile(vals, q) <= max(vals)


def _csv(p: Path, header: str, rows: list[str]) -> Path:
    p.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _r1(tmp: Path, c_val: float, n: int = 5) -> Path:
    rows = [f"a,{i},100" for i in range(n)] + [f"c,{i},{c_val}" for i in range(n)]
    return _csv(tmp / "r1.csv", "condition,run,seconds_per_year", rows)


def test_r1(tmp_path: Path) -> None:
    assert gates.judge_r1(_r1(tmp_path, 100.5)).status == gates.PASS
    assert gates.judge_r1(_r1(tmp_path, 110)).status == gates.FAIL
    assert gates.judge_r1(_r1(tmp_path, 100, n=4)).status == gates.INSUFFICIENT
    assert gates.judge_r1(_r1(tmp_path, 100, n=5)).status == gates.PASS
    assert "boot_width_pct" in gates.judge_r1(_r1(tmp_path, 100.5)).render()


def _frames(p: Path, base: float, n: int = 200) -> Path:
    return _csv(p, "MsBetweenPresents", [str(base + (i % 10) * 0.1) for i in range(n)])


def test_r2(tmp_path: Path) -> None:
    a = _frames(tmp_path / "a.csv", 16.0)
    assert gates.judge_r2(a, _frames(tmp_path / "c.csv", 16.1)).status == gates.PASS
    assert gates.judge_r2(a, _frames(tmp_path / "c2.csv", 20.0)).status == gates.FAIL
    assert gates.judge_r2(a, _frames(tmp_path / "c3.csv", 16, n=5)).status == gates.INSUFFICIENT


def test_r3(tmp_path: Path) -> None:
    ok = _csv(tmp_path / "s.csv", "emits_per_month,seconds_per_year",
              ["0,100", "10,100.1", "20,100.2", "40,100.4"])
    assert gates.judge_r3(ok, shipped_emits=30).status == gates.PASS
    assert "emits_per_month=30" in gates.judge_r3(ok, shipped_emits=30).render()
    bad = _csv(tmp_path / "b.csv", "emits_per_month,seconds_per_year", ["0,100", "10,110", "20,120"])
    assert gates.judge_r3(bad, shipped_emits=30).status == gates.FAIL
    few = _csv(tmp_path / "f.csv", "emits_per_month,seconds_per_year", ["0,100", "10,101"])
    assert gates.judge_r3(few, shipped_emits=30).status == gates.INSUFFICIENT


def _ev(seq: int, typ: str, payload: dict[str, Any], t_s: float = 0.0, date: str | None = None) -> dict[str, Any]:
    return {"v": 1, "run_id": "r", "seq": seq, "t_wall": "2026-01-01T00:00:00Z",
            "t_mono_ns": int(t_s * 1e9), "game_date": date, "type": typ, "payload": payload}


def _health(seq: int, level: str, t: float, trigger: str | None = None, kind: str = "level_change") -> dict[str, Any]:
    return _ev(seq, "health", {"kind": kind, "level": level, "trigger": trigger}, t)


def _write(run_dir: Path, events: list[dict[str, Any]]) -> Path:
    run_dir.mkdir(exist_ok=True)
    (run_dir / "bus.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return run_dir


def test_r4(tmp_path: Path) -> None:
    ack = _ev(3, "intent_ack", {"status": "APPLIED"}, 6)
    good = [_health(1, "H1", 1, "ram"), _health(2, "H2", 5, "ram"), ack]
    assert gates.judge_r4(gates.read_bus(_write(tmp_path / "g", good) / "bus.jsonl")).status == gates.PASS
    slow = [_health(1, "H1", 1, "ram"), _health(2, "H2", 20, "ram"), ack]
    assert gates.judge_r4(slow).status == gates.FAIL
    assert gates.judge_r4(good[:2]).status == gates.FAIL  # no ack
    early = [_health(1, "H2", 1), _health(2, "H1", 2, "ram"), _ev(3, "intent_ack", {}, 3)]
    assert gates.judge_r4(early).status == gates.FAIL  # H2 precedes the ram event
    assert gates.judge_r4([_health(1, "H0", 1)]).status == gates.INSUFFICIENT


def test_r5(tmp_path: Path) -> None:
    obs = _ev(3, "observation", {}, 3, "2230.01.01")
    br = _health(2, "H3", 2, "budget_breach", "budget_breach")
    assert gates.judge_r5([br, obs]).status == gates.PASS
    assert gates.judge_r5([{**obs, 'seq': 1}, br]).status == gates.FAIL
    assert gates.judge_r5([obs]).status == gates.INSUFFICIENT


def _obs_span(months: int) -> list[dict[str, Any]]:
    return [_ev(10 + m, "observation", {}, m, f"{2230 + m // 12}.{m % 12 + 1:02d}.01") for m in range(months + 1)]


def test_r6() -> None:
    assert gates.judge_r6([_health(1, "H2", 1), *_obs_span(12)]).status == gates.PASS
    assert gates.judge_r6([_health(1, "H3", 1), *_obs_span(12)]).status == gates.FAIL
    assert gates.judge_r6([_health(1, "H1", 1), *_obs_span(3)]).status == gates.FAIL
    months = [1, 2, 3, 6, 7, 8, 9, 10, 11, 12]
    gappy = [_ev(10 + i, "observation", {}, i, f"2230.{m:02d}.01") for i, m in enumerate(months)]
    gappy.append(_ev(30, "observation", {}, 30, "2231.01.01"))
    assert gates.judge_r6([_health(1, "H1", 1), *gappy]).status == gates.FAIL
    assert gates.judge_r6([]).status == gates.INSUFFICIENT


def test_cli_gates_and_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main("bench", ["overhead", str(_r1(tmp_path, 100.5))]) == 0
    assert "R-1 PASS" in capsys.readouterr().out
    assert main("bench", ["overhead", str(_r1(tmp_path, 120))]) == 1
    run = _write(tmp_path / "run", [_health(1, "H0", 1)])
    assert main("bench", ["bus", str(run), "--gate", "R-4"]) == 1
    assert "INSUFFICIENT" in capsys.readouterr().out
    assert main("bench", []) == 2
    assert main("bench", ["bus", str(run), "--gate", "R-9"]) == 2
    assert main("bench", ["overhead"]) == 2
    s = tmp_path / "s.csv"
    s.write_text("emits_per_month,seconds_per_year\n0,100\n10,100.1\n20,100.2\n")
    assert main("bench", ["script-cost", str(s)]) == 2
    assert main("bench", ["script-cost", str(s), "--emits", "30"]) == 0
    assert main("bench", ["overhead", str(tmp_path / "missing.csv")]) == 1
    assert main("bench", ["--help"]) == 0


def test_read_bus_ignores_partial_final_line(tmp_path: Path) -> None:
    """A final line without LF (a writer mid-append) is dropped, not parsed."""
    f = tmp_path / "bus.jsonl"
    f.write_text('{"seq": 1}\n{"seq": 2}\n{"seq": 3, "trunc', encoding="utf-8")
    assert gates.read_bus(f) == [{"seq": 1}, {"seq": 2}]
    f.write_text('{"seq": 1}', encoding="utf-8")
    assert gates.read_bus(f) == []
