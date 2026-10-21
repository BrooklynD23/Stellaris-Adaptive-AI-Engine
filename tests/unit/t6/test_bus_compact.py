"""God compaction tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sae.bus import CompactRefused, compact_god, open_sink, read_events
from sae.contracts.c5_bus import GOD_FILE, ObservationGodEvent
from sae.contracts.c6_adapter import GodObservation

from ._fixtures import RUN_ID, cfg


def _god(d: Path, months: list[int]) -> None:
    w = open_sink(d, RUN_ID, cfg())
    for m in months:
        y, mo = divmod(m, 12)
        w.write_god(GodObservation(game_date=f"{2200 + y}.{mo + 1:02d}.01", source="save"))
    w.close()


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(months=st.lists(st.integers(0, 600), max_size=40).map(sorted), n=st.integers(1, 24))
def test_invariants(tmp_path_factory: pytest.TempPathFactory, months: list[int], n: int) -> None:
    d = tmp_path_factory.mktemp("cg") / RUN_ID
    _god(d, months)
    before = read_events(d / GOD_FILE, god=True).events
    kept = compact_god(d, keep_every_months=n)
    res = read_events(d / GOD_FILE, god=True)
    assert res.invalid == 0 and not res.seq_issues and kept == len(res.events)
    assert res.events[0].type == "run_started"
    assert [e.seq for e in res.events] == list(range(kept))
    it = iter(before)
    assert all(any(e.payload == b.payload for b in it) for e in res.events)  # ordered subset
    buckets = []
    for e in res.events[1:]:
        assert isinstance(e, ObservationGodEvent)
        buckets.append((int(e.payload.game_date[:4]) * 12 + int(e.payload.game_date[5:7]) - 1) // n)
    assert len(buckets) == len(set(buckets))
    snap = (d / GOD_FILE).read_bytes()
    compact_god(d, keep_every_months=n)
    assert (d / GOD_FILE).read_bytes() == snap


def test_refuses_live_run(tmp_path: Path) -> None:
    d = tmp_path / RUN_ID
    _god(d, [0, 1])
    cur = tmp_path / "CURRENT"
    cur.write_text(f"{RUN_ID} {os.getpid()}")
    with pytest.raises(CompactRefused):
        compact_god(d, keep_every_months=2)
    cur.write_text(f"{RUN_ID} 99999999")
    assert compact_god(d, keep_every_months=2) == 2
    cur.write_text(f"other-run {os.getpid()}")
    compact_god(d, keep_every_months=2)


def test_bad_arg(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        compact_god(tmp_path, keep_every_months=0)


def test_fresh_empty_current_is_live(tmp_path: Path) -> None:
    d = tmp_path / RUN_ID
    _god(d, [0])
    (tmp_path / "CURRENT").write_text("")
    with pytest.raises(CompactRefused):
        compact_god(d, keep_every_months=1)


def test_tmp_removed_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / RUN_ID
    _god(d, [0])

    def boom(a: object, b: object) -> None:
        raise OSError("replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        compact_god(d, keep_every_months=1)
    assert not (d / "bus.god.jsonl.tmp").exists()
