"""Windows ctypes wrappers driven through a faked ctypes/WinDLL layer (runs on Linux, no real Win32 calls)."""

from __future__ import annotations

import ctypes
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from sae.platform import _win32


class Fn:
    """Callable stand-in for a DLL function; records calls, accepts argtypes/restype assignment."""

    def __init__(self, ret: Any = 1, side: Callable[..., Any] | None = None) -> None:
        self.ret, self.side, self.calls = ret, side, []
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *a: Any) -> Any:
        self.calls.append(a)
        return self.side(*a) if self.side else self.ret


class Dll:
    def __init__(self, **fns: Fn) -> None:
        self.fns = fns

    def __getattr__(self, name: str) -> Fn:
        if name == "fns":
            raise AttributeError(name)
        return self.fns.setdefault(name, Fn())


class WinErr(OSError):
    pass


@pytest.fixture
def fake_win(monkeypatch: pytest.MonkeyPatch) -> dict[str, Dll]:
    """Pretend to be win32: patch module-level sys and ctypes; DLLs keyed by name."""
    dlls: dict[str, Dll] = {"kernel32": Dll(), "pdh": Dll(), "user32": Dll()}
    fake_ctypes = types.SimpleNamespace(**{k: getattr(ctypes, k) for k in dir(ctypes) if not k.startswith("__")})
    fake_ctypes.WinDLL = lambda name, **_: dlls[name]
    fake_ctypes.WinError = lambda code=0: WinErr(f"winerr {code}")
    fake_ctypes.get_last_error = lambda: 5
    fake_ctypes.WINFUNCTYPE = lambda *_a: (lambda f: f)
    monkeypatch.setattr(_win32, "ctypes", fake_ctypes)
    monkeypatch.setattr(_win32, "sys", types.SimpleNamespace(platform="win32"))
    return dlls


def test_require_win32_raises_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")  # also exercise the guard when the host is Windows
    with pytest.raises(OSError, match="unavailable"):
        _win32.set_self_below_normal_and_eco()
    with pytest.raises(OSError):
        _win32.create_job(1)
    with pytest.raises(OSError):
        _win32.assign_to_job(1, 2)
    with pytest.raises(OSError):
        _win32.open_pages_input_counter()
    with pytest.raises(OSError):
        _win32.is_minimized(1)


def test_close_handle_noop_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the platform: on a real Windows host close_handle(7) would CloseHandle a live handle of the pytest
    # process (breaking later child-process creation with 0xC0000142).
    monkeypatch.setattr(_win32, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setattr(_win32.ctypes, "WinDLL", lambda *_a, **_k: pytest.fail("WinDLL touched"), raising=False)
    _win32.close_handle(7)  # must not raise or touch ctypes.WinDLL


def test_below_normal_and_eco_sets_priority_then_throttling(fake_win: dict[str, Dll]) -> None:
    k = fake_win["kernel32"]
    k.fns["GetCurrentProcess"] = Fn(ret=99)
    _win32.set_self_below_normal_and_eco()
    assert k.fns["SetPriorityClass"].calls == [(99, _win32.BELOW_NORMAL_PRIORITY_CLASS)]
    call = k.fns["SetProcessInformation"].calls[0]
    assert call[0] == 99 and call[1] == _win32._PROCESS_POWER_THROTTLING


@pytest.mark.parametrize("failing", ["SetPriorityClass", "SetProcessInformation"])
def test_below_normal_failure_raises(fake_win: dict[str, Dll], failing: str) -> None:
    fake_win["kernel32"].fns[failing] = Fn(ret=0)
    with pytest.raises(WinErr):
        _win32.set_self_below_normal_and_eco()


def test_create_job_sets_limits_and_returns_handle(fake_win: dict[str, Dll]) -> None:
    k = fake_win["kernel32"]
    k.fns["CreateJobObjectW"] = Fn(ret=1234)
    assert _win32.create_job(1 << 30) == 1234
    job, cls, _info, size = k.fns["SetInformationJobObject"].calls[0]
    assert job == 1234 and cls == _win32._JOB_EXTENDED_LIMIT_INFO_CLASS and size > 0


def test_create_job_create_fails(fake_win: dict[str, Dll]) -> None:
    fake_win["kernel32"].fns["CreateJobObjectW"] = Fn(ret=0)
    with pytest.raises(WinErr):
        _win32.create_job(1)


def test_create_job_set_info_fails_closes_handle(fake_win: dict[str, Dll]) -> None:
    k = fake_win["kernel32"]
    k.fns["CreateJobObjectW"] = Fn(ret=55)
    k.fns["SetInformationJobObject"] = Fn(ret=0)
    with pytest.raises(WinErr):
        _win32.create_job(1)
    assert k.fns["CloseHandle"].calls == [(55,)]


def test_assign_to_job_ok_and_failure(fake_win: dict[str, Dll]) -> None:
    k = fake_win["kernel32"]
    _win32.assign_to_job(1, 2)
    assert k.fns["AssignProcessToJobObject"].calls == [(1, 2)]
    k.fns["AssignProcessToJobObject"] = Fn(ret=0)
    with pytest.raises(WinErr):
        _win32.assign_to_job(1, 2)


def test_close_handle_calls_kernel32(fake_win: dict[str, Dll]) -> None:
    _win32.close_handle(8)
    assert fake_win["kernel32"].fns["CloseHandle"].calls == [(8,)]


def test_process_handle_digs_through_transport() -> None:
    sub = types.SimpleNamespace(_handle=321)
    proc = types.SimpleNamespace(_transport=types.SimpleNamespace(get_extra_info=lambda k: sub))
    assert _win32.process_handle(proc) == 321


def test_pages_input_counter_reads_value(fake_win: dict[str, Dll]) -> None:
    p = fake_win["pdh"]

    def fmt(_c: Any, _f: Any, _x: Any, ref: Any) -> int:
        ref._obj.v = 12.5
        return 0

    p.fns["PdhOpenQueryW"] = Fn(ret=0)
    p.fns["PdhAddEnglishCounterW"] = Fn(ret=0)
    p.fns["PdhCollectQueryData"] = Fn(ret=0)
    p.fns["PdhGetFormattedCounterValue"] = Fn(side=fmt)
    read = _win32.open_pages_input_counter()
    assert read() == 12.5
    assert len(p.fns["PdhCollectQueryData"].calls) == 2  # prime + read


def test_pages_input_counter_open_fails(fake_win: dict[str, Dll]) -> None:
    fake_win["pdh"].fns["PdhOpenQueryW"] = Fn(ret=1)
    with pytest.raises(OSError, match="PdhOpenQueryW"):
        _win32.open_pages_input_counter()


def test_pages_input_counter_add_fails_closes_query(fake_win: dict[str, Dll]) -> None:
    p = fake_win["pdh"]
    p.fns["PdhOpenQueryW"] = Fn(ret=0)
    p.fns["PdhAddEnglishCounterW"] = Fn(ret=1)
    with pytest.raises(OSError, match="PdhAddEnglishCounterW"):
        _win32.open_pages_input_counter()
    assert len(p.fns["PdhCloseQuery"].calls) == 1


@pytest.mark.parametrize(("collect", "fmt", "msg"), [(1, 0, "Collect"), (0, 1, "GetFormatted")])
def test_pages_input_reader_errors(fake_win: dict[str, Dll], collect: int, fmt: int, msg: str) -> None:
    p = fake_win["pdh"]
    results = iter([0, collect])  # prime succeeds, read uses `collect`
    p.fns["PdhOpenQueryW"] = Fn(ret=0)
    p.fns["PdhAddEnglishCounterW"] = Fn(ret=0)
    p.fns["PdhCollectQueryData"] = Fn(side=lambda *_: next(results))
    p.fns["PdhGetFormattedCounterValue"] = Fn(ret=fmt)
    read = _win32.open_pages_input_counter()
    with pytest.raises(OSError, match=msg):
        read()


def _enum(windows: list[tuple[int, int, bool, bool]]) -> Dll:
    """windows: (hwnd, owner pid, visible, iconic)."""
    info = {h: (pid, vis, ico) for h, pid, vis, ico in windows}

    def owner(hwnd: int, ref: Any) -> int:
        ref._obj.value = info[hwnd][0]
        return 0

    def enum_windows(cb: Callable[[int, int], bool], lp: int) -> int:
        for h in info:
            cb(h, lp)
        return 1

    return Dll(
        EnumWindows=Fn(side=enum_windows),
        GetWindowThreadProcessId=Fn(side=owner),
        IsWindowVisible=Fn(side=lambda h: info[h][1]),
        IsIconic=Fn(side=lambda h: info[h][2]),
    )


@pytest.mark.parametrize(
    ("windows", "expected"),
    [
        ([(1, 10, True, True), (2, 10, True, True)], True),
        ([(1, 10, True, True), (2, 10, True, False)], False),
        ([(1, 10, False, False), (2, 99, True, False)], None),  # hidden / foreign windows ignored
        ([], None),
    ],
)
def test_is_minimized(
    fake_win: dict[str, Dll], windows: list[tuple[int, int, bool, bool]], expected: bool | None
) -> None:
    fake_win["user32"] = _enum(windows)
    assert _win32.is_minimized(10) is expected
