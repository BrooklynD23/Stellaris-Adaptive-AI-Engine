"""Offline DuckDB console views over a fixture bus pinned to the C5 example run (run_id + seq), so new C5
examples do not change it."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

duckdb = pytest.importorskip("duckdb")

_ROOT = Path(__file__).parents[3]
_DOC = _ROOT / "contracts" / "C5_bus.md"


def _query_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("console_query", _ROOT / "tools" / "console" / "query.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["console_query"] = mod
    spec.loader.exec_module(mod)
    return mod


query = _query_module()


_RUN = '"run_id":"20261019-031500-synth-0a1b"'
_SEQS = range(12)  # the original C5 section 4 run: seq 0-10 plus the null-command control (seq 11)


def _lines() -> list[str]:
    text = _DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```jsonl model=BusEvent\n(.*?)```", text, re.S)  # valid examples only
    pinned: dict[int, str] = {}
    for ln in (ln for b in blocks for ln in b.splitlines() if _RUN in ln):
        m = re.search(r'"seq":(\d+)', ln)
        seq = int(m.group(1)) if m else -1
        if seq in _SEQS:
            pinned.setdefault(seq, ln)
    assert sorted(pinned) == list(_SEQS), "C5 example run changed; update the pinned fixture"
    return [pinned[s] for s in _SEQS]


def _bus(tmp_path: Path, name: str = "run") -> Path:
    run = tmp_path / name
    run.mkdir()
    body = "\n".join(_lines()[:-1]) + "\nnot json at all\n" + _lines()[-1] + '\n{"v":1,"run_id":"x","seq'
    (run / "bus.jsonl").write_text(body, encoding="utf-8")
    return run


def _rows(run: Path, sql: str) -> list[dict[str, object]]:
    conn = duckdb.connect(":memory:")
    query.load_views(conn, run / "bus.jsonl")
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def test_decision_row(tmp_path: Path) -> None:
    rows = _rows(_bus(tmp_path), "SELECT * FROM v_decisions")
    assert len(rows) == 1
    r = rows[0]
    assert r["decision_id"] == "d-2231.04-07"
    assert r["strategy"] == "outscale_siege" and r["switched"] is True
    assert r["top_archetype"] == "turtle" and r["max_posterior"] == pytest.approx(0.68)
    assert (r["n_intents"], r["n_rejected"], r["emit_ok"]) == (1, 1, True)
    assert r["final_ack_status"] == "3:APPLIED"
    assert r["outcome_reward"] == pytest.approx(0.35)  # late outcome still parsed (JSON payload)
    assert r["observation_date"] == "2231.04.01"


def test_acks_join_via_batch_with_null_decision_id(tmp_path: Path) -> None:
    run = _bus(tmp_path)
    ack = next(ln for ln in _lines() if '"type":"intent_ack"' in ln)
    nulled = ack.replace('"decision_id":"d-2231.04-07",', "")
    assert "decision_id" not in nulled
    other = nulled.replace('"seq":6', '"seq":50').replace('"country":3', '"country":5').replace(
        '"APPLIED"', '"FAILED"')
    # emitted batch 17 only has country 3; add a country-5 intent so both acks link
    emit = next(ln for ln in _lines() if '"type":"intent_emitted"' in ln)
    emit2 = emit.replace('"seq":4', '"seq":60').replace('"country":3', '"country":5').replace(
        "i-000381", "i-000399")
    lines = [ln for ln in (run / "bus.jsonl").read_text(encoding="utf-8").split("\n") if ln != ack]
    (run / "bus.jsonl").write_text("\n".join([*lines, nulled, other, emit2]) + "\n", encoding="utf-8")
    assert _rows(run, "SELECT final_ack_status FROM v_decisions")[0]["final_ack_status"] == "3:APPLIED,5:FAILED"
    assert len(_rows(run, "SELECT * FROM v_acks_final")) == 2


def test_last_ack_wins(tmp_path: Path) -> None:
    run = _bus(tmp_path)
    ack = next(ln for ln in _lines() if '"type":"intent_ack"' in ln)
    early = ack.replace('"seq":6', '"seq":40').replace('"APPLIED"', '"LOST"')
    late = ack.replace('"seq":6', '"seq":41')  # same (country, batch seq), later event, APPLIED
    with (run / "bus.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("\n" + early + "\n" + late + "\n")
    final = _rows(run, "SELECT * FROM v_acks_final")
    assert len(final) == 1 and final[0]["status"] == "APPLIED" and final[0]["event_seq"] == 41
    assert _rows(run, "SELECT final_ack_status FROM v_decisions")[0]["final_ack_status"] == "3:APPLIED"


def test_glob_in_run_dir_rejected(tmp_path: Path) -> None:
    assert query.main([str(tmp_path / "r*")]) == 2


def test_other_views(tmp_path: Path) -> None:
    run = _bus(tmp_path)
    assert {r["disposition"] for r in _rows(run, "SELECT * FROM v_intents")} == {"emitted", "rejected"}
    health = _rows(run, "SELECT * FROM v_health ORDER BY seq")
    assert [h["level"] for h in health] == ["H0", "H2"] and health[1]["trigger"] == "ram"
    belief = _rows(run, "SELECT * FROM v_belief")
    assert len(belief) == 4 and all(b["change_score_diagnostic"] == pytest.approx(0.4) for b in belief)


def test_control_null_command(tmp_path: Path) -> None:
    ctl = _rows(_bus(tmp_path), "SELECT * FROM v_control ORDER BY seq")
    assert ctl[0]["cmd"] == "pause" and ctl[0]["accepted"] is True
    assert ctl[1]["cmd"] is None and ctl[1]["error"] == "invalid"


def test_quote_in_path(tmp_path: Path) -> None:
    run = _bus(tmp_path, "it's a run")
    assert len(_rows(run, "SELECT * FROM v_decisions")) == 1


def test_cli_prints_and_missing_bus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert query.main([str(_bus(tmp_path))]) == 0
    assert "outscale_siege" in capsys.readouterr().out
    assert query.main([str(tmp_path / "nope")]) == 2
    assert query.main([str(_bus(tmp_path, "r2")), "SELECT nope FROM"]) == 2


def test_missing_duckdb_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(sys.modules, "duckdb", None)
    assert query.main([str(tmp_path)]) == 1
    assert "uv sync --extra console" in capsys.readouterr().err


def test_governor_and_deferred_views(tmp_path: Path) -> None:
    run = _bus(tmp_path)
    monthly = next(ln for ln in _lines() if '"kind":"monthly"' in ln)
    cost = monthly.replace('"seq":7', '"seq":70').replace(
        '"daemon_cpu_pct":0.4', '"daemon_cpu_pct":0.4,"fault_rate":1.5,"parse_wall_s":2.5,"parse_rss_peak_bytes":4e8')
    rej = next(ln for ln in _lines() if '"type":"intent_rejected"' in ln)
    deferred = re.sub(r'"rule":"[a-z_]+","detail":"[^"]*"',
                      '"rule":"rate_limit","detail":"deferred: one batch per game month; re-sent next month"',
                      rej.replace('"seq":5', '"seq":71'))
    with (run / "bus.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("\n" + cost + "\n" + deferred + "\n")
    gov = _rows(run, "SELECT * FROM v_governor ORDER BY seq")
    assert gov[0]["spm_med6"] == pytest.approx(4.0) and gov[0]["spm_med24"] == pytest.approx(3.9)
    assert gov[0]["avail_ram_bytes"] == pytest.approx(3.2e9)
    assert gov[-1]["parse_wall_s"] == pytest.approx(2.5) and gov[-1]["fault_rate"] == pytest.approx(1.5)
    assert [r["seq"] for r in _rows(run, "SELECT * FROM v_deferred")] == [71]
    whys = {r["seq"]: r["why"] for r in _rows(run, "SELECT * FROM v_unemitted")}
    assert whys == {5: "rejected", 71: "deferred"}
    dec = _rows(run, "SELECT n_rejected, n_deferred FROM v_decisions WHERE decision_id = 'd-2231.04-07'")
    assert dec == [{"n_rejected": 1, "n_deferred": 1}]
    rule_018 = deferred.replace('"seq":71', '"seq":72').replace('"rule":"rate_limit"', '"rule":"deferred"')
    with (run / "bus.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(rule_018 + "\n")
    assert [r["seq"] for r in _rows(run, "SELECT * FROM v_deferred ORDER BY seq")] == [71, 72]


def test_v_runs_policy_sha(tmp_path: Path) -> None:
    from sae.bus import open_sink
    from sae.contracts.c5_bus import PolicyRef

    from ._fixtures import RUN_ID, cfg

    ref = PolicyRef(version="0.1.0", sha256="b" * 64, source="builtin", fallback_reason="pin missing")
    open_sink(tmp_path, RUN_ID, cfg("balanced"), policy=ref).close()
    conn = duckdb.connect()
    query.load_views(conn, tmp_path / "bus.jsonl")
    row = conn.execute(
        "SELECT policy_version, policy_sha256, policy_source, policy_fallback_reason FROM v_runs").fetchone()
    assert row == ("0.1.0", "b" * 64, "builtin", "pin missing")


def test_v_runs_without_policy_is_null(tmp_path: Path) -> None:
    from sae.bus import open_sink

    from ._fixtures import RUN_ID, cfg

    open_sink(tmp_path, RUN_ID, cfg("balanced")).close()
    conn = duckdb.connect()
    query.load_views(conn, tmp_path / "bus.jsonl")
    rows = conn.execute("SELECT policy_version, policy_sha256 FROM v_runs").fetchall()
    assert rows == [(None, None)]
