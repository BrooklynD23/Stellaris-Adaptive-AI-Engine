"""Sensors with fake psutil / vmstat / clock."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from sae.platform import make_sensors
from sae.platform.sensors import SystemSensors


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class FakeProc:
    def __init__(self, pid: int, name: str, ct: float = 1.0, cpu: float = 5.0) -> None:
        self.pid, self.ct, self.cpu = pid, ct, cpu
        self.info = {"name": name, "create_time": ct}
        self.gone = False

    def is_running(self) -> bool:
        return not self.gone

    def create_time(self) -> float:
        if self.gone:
            raise RuntimeError("gone")
        return self.ct

    def cpu_times(self) -> Any:
        return SimpleNamespace(user=self.cpu, system=1.0)


class FakeMe:
    def __init__(self) -> None:
        self.rss: Any = 1000
        self.cpu: Any = 3.0

    def memory_info(self) -> Any:
        if isinstance(self.rss, Exception):
            raise self.rss
        return SimpleNamespace(rss=self.rss)

    def cpu_percent(self, interval: float | None = None) -> float:
        if isinstance(self.cpu, Exception):
            raise self.cpu
        return float(self.cpu)


class FakePsutil:
    def __init__(self) -> None:
        self.ram: Any = 4_000_000_000
        self.procs: list[FakeProc] = []
        self.me = FakeMe()

    def virtual_memory(self) -> Any:
        if isinstance(self.ram, Exception):
            raise self.ram
        return SimpleNamespace(available=self.ram)

    def Process(self) -> FakeMe:
        return self.me

    def process_iter(self, _attrs: list[str]) -> list[FakeProc]:
        return list(self.procs)


def vm(n: int) -> str:
    return f"nr_free_pages 5\npgmajfault {n}\nother 1\n"


def build(ps: FakePsutil, clock: FakeClock, text: list[str] | None = None, **kw: Any) -> SystemSensors:
    box = text if text is not None else [vm(0)]
    return SystemSensors(("Stellaris",), clock=clock, psutil_mod=ps, read_text=lambda _p: box[0], system="linux", **kw)


def test_ram_probe_failure_at_construction_raises() -> None:
    ps = FakePsutil()
    ps.ram = OSError("no")
    with pytest.raises(RuntimeError):
        build(ps, FakeClock())


def test_ram_later_failure_returns_last_good_and_nan() -> None:
    ps, clk = FakePsutil(), FakeClock()
    s = build(ps, clk)
    assert s.sample().available_ram_bytes == 4_000_000_000
    ps.ram = RuntimeError("x")
    assert s.sample().available_ram_bytes == 4_000_000_000
    ps.ram = float("nan")
    assert s.sample().available_ram_bytes == 4_000_000_000


def test_fault_rate_first_none_then_delta() -> None:
    ps, clk, box = FakePsutil(), FakeClock(), [vm(100)]
    s = build(ps, clk, box)
    assert s.sample().hard_fault_rate is None
    clk.t += 2.0
    box[0] = vm(300)
    assert s.sample().hard_fault_rate == pytest.approx(100.0)
    clk.t += 2.0
    box[0] = "garbage"
    assert s.sample().hard_fault_rate is None


def test_fault_counter_reset_is_none() -> None:
    ps, clk, box = FakePsutil(), FakeClock(), [vm(100)]
    s = build(ps, clk, box)
    s.sample()
    clk.t += 2.0
    box[0] = vm(5)
    assert s.sample().hard_fault_rate is None


def test_game_detection_case_insensitive_and_cpu() -> None:
    ps, clk = FakePsutil(), FakeClock()
    s = build(ps, clk)
    assert s.sample().game_running is False
    ps.procs = [FakeProc(7, "other"), FakeProc(42, "STELLARIS")]
    assert s.sample().game_running is False  # rescan throttled to 10 s
    clk.t += 10
    r = s.sample()
    assert r.game_running and r.game_cpu_s == 6.0
    assert r.game_minimized_s is None  # non-Windows


def test_pid_reuse_triggers_rescan() -> None:
    ps, clk = FakePsutil(), FakeClock()
    old = FakeProc(42, "stellaris", ct=1.0, cpu=5.0)
    ps.procs = [old]
    s = build(ps, clk)
    assert s.sample().game_cpu_s == 6.0
    ps.procs = [FakeProc(42, "notgame", ct=2.0)]
    old.gone = True  # pid reused by another process: is_running() is False
    clk.t += 10
    assert s.sample().game_running is False
    ps.procs = [FakeProc(43, "stellaris", ct=9.0, cpu=1.0)]
    clk.t += 10
    assert s.sample().game_cpu_s == 2.0


def test_rescan_throttled_when_no_game() -> None:
    ps, clk = FakePsutil(), FakeClock()
    calls: list[int] = []

    class Counting(FakePsutil):
        def process_iter(self, _attrs: list[str]) -> list[FakeProc]:
            calls.append(1)
            return []

    ps = Counting()
    s = build(ps, clk)
    for _ in range(3):
        s.sample()
        clk.t += 2
    assert len(calls) == 1
    clk.t += 10
    s.sample()
    assert len(calls) == 2


def test_game_exit_clears_cache() -> None:
    ps, clk = FakePsutil(), FakeClock()
    g = FakeProc(1, "stellaris")
    ps.procs = [g]
    s = build(ps, clk)
    assert s.sample().game_running
    g.gone, ps.procs = True, []
    clk.t += 10
    assert not s.sample().game_running


def test_daemon_probe_failures_never_raise() -> None:
    ps, clk = FakePsutil(), FakeClock()
    s = build(ps, clk)
    assert s.sample().daemon_rss_bytes == 1000
    ps.me.rss, ps.me.cpu = OSError("x"), float("inf")
    r = s.sample()
    assert r.daemon_rss_bytes == 1000 and r.daemon_cpu_pct == 0.0
    ps.me.cpu = RuntimeError("x")
    assert s.sample().daemon_cpu_pct == 0.0


def test_nan_cpu_times_becomes_none() -> None:
    ps, clk = FakePsutil(), FakeClock()
    ps.procs = [FakeProc(1, "stellaris", cpu=math.nan)]
    assert build(ps, clk).sample().game_cpu_s is None


class FakeWin32:
    def __init__(self) -> None:
        self.mini: Any = True
        self.pdh_val: Any = 7.0

    def open_pages_input_counter(self) -> Any:
        def read() -> float:
            if isinstance(self.pdh_val, Exception):
                raise self.pdh_val
            return float(self.pdh_val)

        return read

    def is_minimized(self, _pid: int) -> Any:
        if isinstance(self.mini, Exception):
            raise self.mini
        return self.mini


def test_windows_path_minimized_accumulates_and_pdh() -> None:
    ps, clk, w = FakePsutil(), FakeClock(), FakeWin32()
    ps.procs = [FakeProc(1, "stellaris.exe")]
    s = SystemSensors(("stellaris.exe",), clock=clk, psutil_mod=ps, system="win32", win32=w)
    assert s.sample().game_minimized_s == 0.0
    clk.t += 5
    assert s.sample().game_minimized_s == 5.0
    clk.t += 5
    r = s.sample()
    assert r.game_minimized_s == 10.0 and r.hard_fault_rate == 7.0
    w.mini, w.pdh_val = False, math.nan
    clk.t += 1
    r = s.sample()
    assert r.game_minimized_s == 0.0 and r.hard_fault_rate is None
    w.mini = OSError("x")
    assert s.sample().game_minimized_s == 0.0


def test_windows_pdh_unavailable_is_none() -> None:
    class Bad(FakeWin32):
        def open_pages_input_counter(self) -> Any:
            raise OSError("nope")

    s = SystemSensors(None, clock=FakeClock(), psutil_mod=FakePsutil(), system="win32", win32=Bad())
    assert s.sample().hard_fault_rate is None


def test_real_sensors_smoke() -> None:
    r = make_sensors(("definitely-not-a-game",)).sample()
    assert r.available_ram_bytes > 0 and r.daemon_rss_bytes > 0 and not r.game_running


def test_cpu_times_denied_keeps_game_running() -> None:
    class Denied(FakeProc):
        def cpu_times(self) -> Any:
            raise PermissionError("AccessDenied")

    ps, clk = FakePsutil(), FakeClock()
    ps.procs = [Denied(1, "stellaris")]
    s = build(ps, clk)
    for _ in range(10):
        r = s.sample()
        assert r.game_running and r.game_cpu_s is None
        clk.t += 2


def test_cpu_times_fails_and_process_dead_reports_not_running() -> None:
    class Dying(FakeProc):
        def cpu_times(self) -> Any:
            self.gone = True
            raise RuntimeError("gone")

    ps, clk = FakePsutil(), FakeClock()
    ps.procs = [Dying(1, "stellaris")]
    r = build(ps, clk).sample()
    assert r.game_running is False and r.game_cpu_s is None
