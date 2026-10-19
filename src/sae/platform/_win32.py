"""Windows-only ctypes calls, isolated here. Not executed in CI on this machine (Linux/WSL).

Every function targets the current process, a Job we created, or a handle taken from our own child
``Popen``. Nothing here opens a foreign process.
UNVERIFIED A-26/A-27/A-28: counter name, Job commit-limit behaviour and background mode are checked in M0.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from typing import Any

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
IDLE_PRIORITY_CLASS = 0x00000040
_JOB_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS ProcessPowerThrottling
_THROTTLE_EXECUTION_SPEED = 0x1
_PDH_FMT_DOUBLE = 0x00000200
_JOB_EXTENDED_LIMIT_INFO_CLASS = 9


def configure_kernel32(k32: Any) -> Any:
    """Declare argtypes/restype on every kernel32 call used (64-bit handles must not pass through a C long)."""
    from ctypes import wintypes

    sigs: dict[str, tuple[list[Any], Any]] = {
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "SetPriorityClass": ([wintypes.HANDLE, wintypes.DWORD], wintypes.BOOL),
        "SetProcessInformation": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
        "CreateJobObjectW": ([wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (args, res) in sigs.items():
        fn = getattr(k32, name)
        fn.argtypes = args
        fn.restype = res
    return k32


def configure_user32(u32: Any, enum_proto: Any) -> Any:
    """Declare signatures for the read-only window queries."""
    from ctypes import wintypes

    u32.EnumWindows.argtypes = [enum_proto, wintypes.LPARAM]
    u32.EnumWindows.restype = wintypes.BOOL
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsWindowVisible.restype = wintypes.BOOL
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.IsIconic.restype = wintypes.BOOL
    return u32


def configure_pdh(pdh: Any) -> Any:
    """Declare signatures for the PDH calls used."""
    from ctypes import wintypes

    sigs: dict[str, list[Any]] = {
        "PdhOpenQueryW": [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)],
        "PdhAddEnglishCounterW": [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)],
        "PdhCollectQueryData": [wintypes.HANDLE],
        "PdhGetFormattedCounterValue": [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID],
        "PdhCloseQuery": [wintypes.HANDLE],
    }
    for name, args in sigs.items():
        fn = getattr(pdh, name)
        fn.argtypes = args
        fn.restype = wintypes.DWORD
    return pdh


def _require_win32() -> None:
    if sys.platform != "win32":
        raise OSError("Windows API unavailable on this platform")


def set_self_below_normal_and_eco() -> None:
    """BELOW_NORMAL priority class plus EcoQoS on the current process only."""
    _require_win32()
    if sys.platform == "win32":
        from ctypes import wintypes

        k32 = configure_kernel32(ctypes.WinDLL("kernel32", use_last_error=True))
        me = k32.GetCurrentProcess()
        if not k32.SetPriorityClass(me, BELOW_NORMAL_PRIORITY_CLASS):
            raise ctypes.WinError(ctypes.get_last_error())

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        st = _State(1, _THROTTLE_EXECUTION_SPEED, _THROTTLE_EXECUTION_SPEED)
        if not k32.SetProcessInformation(me, _PROCESS_POWER_THROTTLING, ctypes.byref(st), ctypes.sizeof(st)):
            raise ctypes.WinError(ctypes.get_last_error())


def create_job(mem_cap_bytes: int) -> int:
    """Create a Job Object limited to ``mem_cap_bytes`` per process, kill-on-close. Returns the handle."""
    _require_win32()
    if sys.platform == "win32":
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("a", "b", "c", "d", "e", "f")]

        class _Ext(ctypes.Structure):
            _fields_ = [
                ("Basic", _Basic), ("Io", _Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = configure_kernel32(ctypes.WinDLL("kernel32", use_last_error=True))
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _Ext()
        info.Basic.LimitFlags = _JOB_LIMIT_PROCESS_MEMORY | _JOB_LIMIT_KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = mem_cap_bytes
        size = ctypes.sizeof(info)
        if not k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT_INFO_CLASS, ctypes.byref(info), size):
            err = ctypes.get_last_error()
            k32.CloseHandle(job)
            raise ctypes.WinError(err)
        return int(job)
    return 0


def process_handle(proc: Any) -> int:
    """OS handle of our own spawned child, taken from its Popen object."""
    sub = proc._transport.get_extra_info("subprocess")  # asyncio Process -> Popen
    return int(sub._handle)


def assign_to_job(job: int, child_handle: int) -> None:
    """Assign our own child (by handle) to the Job."""
    _require_win32()
    if sys.platform == "win32":
        k32 = configure_kernel32(ctypes.WinDLL("kernel32", use_last_error=True))
        if not k32.AssignProcessToJobObject(job, child_handle):
            raise ctypes.WinError(ctypes.get_last_error())


def close_handle(handle: int) -> None:
    """CloseHandle on a Job we created."""
    if sys.platform == "win32":
        configure_kernel32(ctypes.WinDLL("kernel32")).CloseHandle(handle)


def open_pages_input_counter() -> Callable[[], float]:
    """PDH ``\\Memory\\Pages Input/sec`` (English name, locale-proof). Returns a reader; raises if unavailable."""
    _require_win32()
    if sys.platform == "win32":
        from ctypes import wintypes

        pdh = configure_pdh(ctypes.WinDLL("pdh"))
        query = wintypes.HANDLE()
        counter = wintypes.HANDLE()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        if pdh.PdhAddEnglishCounterW(query, "\\Memory\\Pages Input/sec", 0, ctypes.byref(counter)) != 0:
            pdh.PdhCloseQuery(query)
            raise OSError("PdhAddEnglishCounterW failed")
        pdh.PdhCollectQueryData(query)

        class _Val(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("v", ctypes.c_double)]

        def read() -> float:
            if pdh.PdhCollectQueryData(query) != 0:
                raise OSError("PdhCollectQueryData failed")
            val = _Val()
            if pdh.PdhGetFormattedCounterValue(counter, _PDH_FMT_DOUBLE, None, ctypes.byref(val)) != 0:
                raise OSError("PdhGetFormattedCounterValue failed")
            return float(val.v)

        return read
    raise OSError("unreachable")


def is_minimized(pid: int) -> bool | None:
    """True if every visible top-level window of ``pid`` is iconic; None if it has none. Read-only."""
    _require_win32()
    if sys.platform == "win32":
        from ctypes import wintypes

        states: list[bool] = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd: int, _lp: int) -> bool:
            owner = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and u32.IsWindowVisible(hwnd):
                states.append(bool(u32.IsIconic(hwnd)))
            return True

        u32 = configure_user32(ctypes.WinDLL("user32"), proto)
        u32.EnumWindows(proto(cb), 0)
        return all(states) if states else None
    return None
