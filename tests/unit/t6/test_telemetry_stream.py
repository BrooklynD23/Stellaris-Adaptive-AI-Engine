"""run_stream with ListSink: fixture bus, growing file with fake sleep, dead pid, RSS cap, real .rrd."""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

import sae.telemetry.stream as stream_mod
from sae.contracts.c7_governor import TelemetryTier
from sae.telemetry.stream import ListSink, RerunSink, run_stream

from .test_telemetry_mapping import doc_lines

T1, T2 = TelemetryTier.T1, TelemetryTier.T2



@pytest.fixture(autouse=True)
def _no_rss_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pytest process (duckdb, rerun, hypothesis loaded) is far above the 150 MiB telemetry cap."""
    monkeypatch.setattr(stream_mod, "RSS_LIMIT", 1 << 40)


def bus(tmp_path: Path, n: int | None = None) -> Path:
    path = tmp_path / "bus.jsonl"
    lines = doc_lines()[:n]
    path.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    return path


def test_offline_one_pass(tmp_path: Path) -> None:
    bus(tmp_path)
    sink = ListSink()
    assert run_stream(tmp_path, sink, T2, is_live=lambda: False, sleep=lambda s: pytest.fail("slept")) == 0
    assert sink.started == 1 and sink.closed and sink.records
    assert {"status/header", "belief/entropy", "strategy/selected"} <= {r.entity for r in sink.records}


def test_t1_scalars_only(tmp_path: Path) -> None:
    bus(tmp_path)
    sink = ListSink()
    run_stream(tmp_path, sink, T1, is_live=lambda: False)
    assert sink.records and all(r.kind == "scalar" for r in sink.records)


def test_follow_growing_file_stops_on_dead_pid(tmp_path: Path) -> None:
    path = bus(tmp_path, 3)
    rest = [ln + "\n" for ln in doc_lines()[3:5]]
    sink = ListSink()
    sleeps: list[float] = []
    def sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) == 1:
            with path.open("a", encoding="utf-8") as f:
                f.writelines(rest)

    # daemon "dies" after the second idle poll; the final drain must still read everything
    assert run_stream(tmp_path, sink, T2, is_live=lambda: len(sleeps) < 2, sleep=sleep) == 0
    assert sleeps == [2.0, 2.0]
    assert any(r.seq == 4 for r in sink.records)  # events appended mid-follow were read


def test_rss_over_limit_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stream_mod, "RSS_LIMIT", 150 * 1024 * 1024)  # C8 section 2 cap
    bus(tmp_path)
    sink = ListSink()
    assert run_stream(tmp_path, sink, T2, is_live=lambda: False, rss=lambda: 151 * 1024 * 1024) == 1
    assert run_stream(tmp_path, ListSink(), T2, is_live=lambda: False, rss=lambda: 149 * 1024 * 1024) == 0
    assert sink.closed


def test_god_stream_at_t2(tmp_path: Path) -> None:
    bus(tmp_path, 1)
    god = ('{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":1,"t_wall":"2026-10-19T03:15:01.000Z",'
           '"t_mono_ns":2000,"game_date":"2231.04.01","type":"observation_god","fow":"god","payload":'
           '{"game_date":"2231.04.01","source":"save","plyr":{"x":1.0}}}\n')
    (tmp_path / "bus.god.jsonl").write_text(god, encoding="utf-8")
    sink = ListSink()
    run_stream(tmp_path, sink, T2, is_live=lambda: False)
    assert [r.entity for r in sink.records if r.entity.startswith("god/")] == ["god/plyr/x"]
    sink1 = ListSink()
    run_stream(tmp_path, sink1, T1, is_live=lambda: False)
    assert not [r for r in sink1.records if r.entity.startswith("god/")]


def test_rrd_written(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    bus(tmp_path)
    out = tmp_path / "t.rrd"
    sink = RerunSink("20261019-031500-synth-0a1b", out, url=False)
    assert run_stream(tmp_path, sink, T2, is_live=lambda: False) == 0
    assert out.stat().st_size > 0


def test_drain_beyond_one_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    import sae.bus.reader as reader_mod

    health = json.loads(next(ln for ln in doc_lines() if '"type":"health"' in ln))
    rows = []
    for i in range(20):
        health["seq"] = i
        rows.append(json.dumps(health, separators=(",", ":")) + "\n")
    assert len(rows[0]) < 600 < len(rows[0]) * 2
    (tmp_path / "bus.jsonl").write_text("".join(rows), encoding="utf-8")
    monkeypatch.setattr(reader_mod, "TAIL_BATCH_BYTES", 600)
    for live in (False, True):
        sink = ListSink()
        is_live = functools.partial(next, iter([live, False]), False)
        run_stream(tmp_path, sink, T2, is_live=is_live, sleep=lambda s: None)
        assert {r.seq for r in sink.records} == set(range(20))


def test_rerun_close_survives_flush_runtimeerror(caplog: pytest.LogCaptureFixture) -> None:
    pytest.importorskip("rerun")
    sink = RerunSink.__new__(RerunSink)
    calls: list[str] = []

    class Rec:
        def flush(self) -> None:
            calls.append("flush")
            raise RuntimeError("gRPC has been unable to connect for 6s")

        def disconnect(self) -> None:
            calls.append("disconnect")
            raise RuntimeError("again")

    sink._rec = Rec()
    sink.close()
    assert calls == ["flush", "disconnect"]
    assert "unable to connect" in caplog.text


def test_missing_month_disables_timeline() -> None:
    pytest.importorskip("rerun")
    from datetime import UTC, datetime

    from sae.telemetry.mapping import Record

    sink = RerunSink.__new__(RerunSink)
    seen: list[tuple[str, str]] = []

    class Rec:
        def set_time(self, name: str, **kw: object) -> None:
            seen.append(("set", name))

        def disable_timeline(self, name: str) -> None:
            seen.append(("disable", name))

        def log(self, *a: object, **k: object) -> None:
            pass

    class RR:
        Scalars = staticmethod(lambda v: v)

    sink._rec = Rec()
    sink._rr = RR()
    when = datetime(2026, 1, 1, tzinfo=UTC)
    sink.log(Record(seq=1, t_wall=when, game_month=None, kind="scalar", entity="a", value=1.0))
    assert ("disable", "game_month") in seen
