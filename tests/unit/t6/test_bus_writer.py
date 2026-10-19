"""Bus writer tests (deterministic: injected clocks, no sleeps)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sae.bus import BusValidationError, BusWriter, open_sink, read_events
from sae.contracts.c5_bus import BUS_FILE, GOD_FILE, HealthEvent
from sae.contracts.c6_adapter import GodObservation
from sae.contracts.c8_config import PresetName
from sae.core.state import EventDraft
from sae.daemon.ports import BusSink
from sae.daemon.runs import config_sha256 as t4_sha

from ._fixtures import RUN_ID, cfg, clocks, example_drafts, health_draft

DRAFTS = example_drafts()


def _open(path: Path, preset: PresetName = "balanced", resume: bool = False) -> BusWriter:
    wall, mono = clocks()
    return open_sink(path, RUN_ID, cfg(preset), resume=resume, wall=wall, mono=mono)


def test_satisfies_bus_sink(tmp_path: Path) -> None:

    sink: BusSink = _open(tmp_path)
    sink.close()


def test_sha_matches_t4() -> None:
    from sae.bus.writer import config_sha256


    assert config_sha256(cfg()) == t4_sha(cfg())


def test_examples_cover_every_type() -> None:
    assert {d.type for d in DRAFTS} == {
        "observation", "belief_update", "strategy_selected", "intent_emitted", "intent_rejected",
        "intent_ack", "outcome", "control", "health"}


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(idx=st.lists(st.integers(0, len(DRAFTS) - 1), min_size=1, max_size=15))
def test_round_trip(tmp_path_factory: pytest.TempPathFactory, idx: list[int]) -> None:
    d = tmp_path_factory.mktemp("rt")
    w = _open(d)
    for i in idx:
        w.emit(DRAFTS[i])
    w.close()
    res = read_events(d / BUS_FILE)
    assert res.invalid == 0 and not res.seq_issues and not res.partial_tail
    assert [e.seq for e in res.events] == list(range(len(idx) + 1))
    assert res.events[0].type == "run_started"
    for ev, i in zip(res.events[1:], idx, strict=True):
        assert ev.payload == DRAFTS[i].payload and ev.type == DRAFTS[i].type
        assert ev.fow in {"legal", "none"}


def test_god_file_own_seq_and_run_started(tmp_path: Path) -> None:
    w = _open(tmp_path)
    w.emit(DRAFTS[0])
    w.write_god(GodObservation(game_date="2231.04.01", source="save"))
    w.close()
    god = read_events(tmp_path / GOD_FILE, god=True)
    assert [e.type for e in god.events] == ["run_started", "observation_god"]
    assert [e.seq for e in god.events] == [0, 1]
    assert [e.seq for e in read_events(tmp_path / BUS_FILE).events] == [0, 1]


def test_non_resume_refuses_existing(tmp_path: Path) -> None:
    _open(tmp_path).close()
    with pytest.raises(FileExistsError):
        _open(tmp_path)


def test_resume_continues_seq(tmp_path: Path) -> None:
    w = _open(tmp_path)
    w.emit(DRAFTS[0])
    w.close()
    w2 = _open(tmp_path, resume=True)
    w2.emit(DRAFTS[1])
    w2.close()
    res = read_events(tmp_path / BUS_FILE)
    assert [e.type for e in res.events].count("run_started") == 1
    assert [e.seq for e in res.events] == [0, 1, 2]


def test_resume_wrong_run_id(tmp_path: Path) -> None:
    _open(tmp_path).close()
    wall, mono = clocks()
    with pytest.raises(ValueError, match="another run"):
        open_sink(tmp_path, "20261019-031500-synth-ffff", cfg(), resume=True, wall=wall, mono=mono)


def test_resume_missing_files_is_fresh(tmp_path: Path) -> None:
    w = _open(tmp_path, resume=True)
    w.close()
    assert [e.seq for e in read_events(tmp_path / BUS_FILE).events] == [0]


def test_crash_truncation_every_offset(tmp_path: Path) -> None:
    src = tmp_path / "src"
    w = _open(src)
    w.emit(DRAFTS[1])
    w.close()
    data = (src / BUS_FILE).read_bytes()
    for cut in range(len(data) + 1):
        d = tmp_path / f"c{cut}"
        d.mkdir()
        (d / BUS_FILE).write_bytes(data[:cut])
        w2 = _open(d, resume=True)
        w2.emit(DRAFTS[2])
        w2.close()
        res = read_events(d / BUS_FILE)
        assert res.invalid == 0 and not res.seq_issues and not res.partial_tail, cut
        assert res.events[0].type == "run_started" and res.events[-1].type == DRAFTS[2].type


def test_dev_raises_on_invalid(tmp_path: Path) -> None:
    w = _open(tmp_path, "dev")
    bad = EventDraft(type="belief_update", payload=DRAFTS[2].payload)  # decision_id missing
    with pytest.raises(BusValidationError):
        w.emit(bad)
    with pytest.raises(BusValidationError):
        w.emit(EventDraft(type="nope", payload=DRAFTS[0].payload))
    w.close()
    with pytest.raises(RuntimeError):
        w.emit(DRAFTS[0])


def test_balanced_drops_and_reports(tmp_path: Path) -> None:
    w = _open(tmp_path)
    w.emit(health_draft(date="2231.04.01"))
    w.emit(EventDraft(type="belief_update", payload=DRAFTS[2].payload))
    w.emit(EventDraft(type="observation_god", payload=DRAFTS[0].payload))
    w.emit(DRAFTS[0])
    w.close()
    ev = read_events(tmp_path / BUS_FILE).events
    assert [e.type for e in ev] == ["run_started", "health", "health", "observation"]
    inv = ev[2]
    assert isinstance(inv, HealthEvent)
    assert inv.payload.kind == "invalid_event" and inv.payload.component == "bus"
    assert "belief_update" in (inv.payload.detail or "")
    assert [e.seq for e in ev] == list(range(4))


def test_oversize_line_dropped(tmp_path: Path) -> None:
    w = _open(tmp_path)
    big = DRAFTS[2].payload.model_copy(update={"features": {f"f{i}": 1.0 for i in range(60000)}})
    w.emit(EventDraft(type="belief_update", payload=big, decision_id="d-2231.04-07"))
    w.close()
    ev = read_events(tmp_path / BUS_FILE).events
    assert [e.type for e in ev] == ["run_started", "health"]
    assert (tmp_path / BUS_FILE).stat().st_size < 4096
    d = _open(tmp_path / "dev", "dev")
    with pytest.raises(BusValidationError):
        d.emit(EventDraft(type="belief_update", payload=big, decision_id="d-2231.04-07"))


def test_fsync_once_per_month(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    w = _open(tmp_path)
    base = len(calls)
    for date in ("2231.04.01", "2231.04.15", "2231.05.01", "2231.05.02"):
        w.emit(health_draft(date=date))
    assert len(calls) - base == 2
    w.close()
    assert len(calls) - base == 2 + 2  # bus + god on close


def test_fsync_error_only_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(fd: int) -> None:
        raise OSError("fsync")

    monkeypatch.setattr(os, "fsync", boom)
    w = _open(tmp_path)
    w.emit(health_draft(date="2231.04.01"))
    w.close()
    assert len(read_events(tmp_path / BUS_FILE).events) == 2


def test_oserror_on_write_leaves_file_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _open(tmp_path)
    w.emit(DRAFTS[0])
    real = os.write
    state = {"n": 0}

    def flaky(fd: int, data: Any) -> int:
        state["n"] += 1
        if state["n"] == 1:
            raise OSError(28, "disk full")
        if state["n"] == 2:
            real(fd, bytes(data)[: len(data) // 2])
            return len(data) // 2  # short write
        return real(fd, data)

    monkeypatch.setattr(os, "write", flaky)
    w.emit(DRAFTS[1])
    w.emit(DRAFTS[1])
    w.emit(DRAFTS[2])
    monkeypatch.undo()
    w.close()
    assert w.dropped_io == 2
    res = read_events(tmp_path / BUS_FILE)
    assert res.invalid == 0 and not res.seq_issues and not res.partial_tail
    assert [e.type for e in res.events] == ["run_started", "observation", DRAFTS[2].type]


def test_write_god_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _open(tmp_path)

    def boom(fd: int, data: Any) -> int:
        raise OSError("eio")

    monkeypatch.setattr(os, "write", boom)
    w.write_god(GodObservation(game_date="2231.04.01", source="save"))
    monkeypatch.undo()
    w.close()
    w.write_god(GodObservation(game_date="2231.04.01", source="save"))
    assert json.loads((tmp_path / GOD_FILE).read_text().splitlines()[0])["seq"] == 0


def test_lost_first_write_still_starts_with_run_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.write
    state = {"n": 0}

    def first_fails(fd: int, data: Any) -> int:
        state["n"] += 1
        if state["n"] == 1:
            raise OSError(5, "eio")
        return real(fd, data)

    monkeypatch.setattr(os, "write", first_fails)
    w = _open(tmp_path)
    monkeypatch.undo()
    w.emit(DRAFTS[0])
    w.close()
    res = read_events(tmp_path / BUS_FILE)
    assert res.events[0].type == "run_started" and res.events[0].seq == 0
    assert not res.seq_issues and res.invalid == 0


def test_failed_truncate_isolates_garbage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _open(tmp_path)
    real = os.write

    def partial(fd: int, data: Any) -> int:
        real(fd, bytes(data)[:20])
        return 20

    def no_trunc(fd: int, n: int) -> None:
        raise OSError("trunc")

    monkeypatch.setattr(os, "write", partial)
    monkeypatch.setattr(os, "ftruncate", no_trunc)
    w.emit(DRAFTS[0])
    monkeypatch.undo()
    w.emit(DRAFTS[1])
    w.close()
    res = read_events(tmp_path / BUS_FILE)
    assert res.invalid == 1 and [e.seq for e in res.events] == [0, 1]


def test_resume_skips_garbage_tail(tmp_path: Path) -> None:
    w = _open(tmp_path)
    w.emit(DRAFTS[0])
    w.close()
    with (tmp_path / BUS_FILE).open("ab") as fh:
        fh.write(b"\x00garbage\n\xff\xfe\n")
    w2 = _open(tmp_path, resume=True)
    w2.emit(DRAFTS[1])
    w2.close()
    seqs = [e.seq for e in read_events(tmp_path / BUS_FILE).events]
    assert seqs == [0, 1, 2]  # garbage lines are skipped; seq continues after the last valid line


def test_invalid_event_health_throttled_with_count(tmp_path: Path) -> None:
    t = {"ns": 1_000}
    w = open_sink(tmp_path, RUN_ID, cfg(), wall=clocks()[0], mono=lambda: t["ns"])
    bad = EventDraft(type="belief_update", payload=DRAFTS[2].payload)
    for _ in range(4):
        w.emit(bad)
    t["ns"] += 61 * 1_000_000_000
    w.emit(bad)
    w.close()
    ev = [e for e in read_events(tmp_path / BUS_FILE).events if isinstance(e, HealthEvent)]
    assert len(ev) == 2
    assert "(+3 suppressed)" in (ev[1].payload.detail or "")
