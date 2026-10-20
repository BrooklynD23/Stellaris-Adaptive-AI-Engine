"""Platform: real Linux spawn tests plus a fake-_win32 Windows path."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Any, cast

import pytest

from sae.contracts.c4_parse import MAX_STDOUT_BYTES
from sae.contracts.c7_governor import MIB, WorkerSpec
from sae.platform import make_platform, spawn
from sae.platform.spawn import SystemPlatform

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux spawn")


def spec(code: str, cap: int = 4 * 1024 * MIB) -> WorkerSpec:
    return WorkerSpec(exe=sys.executable, args=("-c", code), mem_cap_bytes=cap, timeout_s=30, threads=1)


@linux_only
def test_stdout_and_exit_codes() -> None:
    async def go() -> None:
        p = SystemPlatform(use_systemd=False)
        h = await p.spawn_worker(spec("import sys;print('hi');sys.stderr.write('e');sys.exit(3)"))
        assert await h.wait() == (3, b"hi\n", b"e")

    asyncio.run(go())


@linux_only
def test_kill_idempotent_and_wait_after_kill() -> None:
    async def go() -> None:
        h = await SystemPlatform(use_systemd=False).spawn_worker(spec("import time;time.sleep(60)"))
        h.kill()
        h.kill()
        rc, _o, _e = await h.wait()
        assert rc is None  # killed by signal
        h.kill()  # after exit: no raise

    asyncio.run(go())


@linux_only
def test_rlimit_as_cap_breach_nonzero() -> None:
    async def go() -> None:
        code = "x = bytearray(1024*1024*1024); print('ok')"
        h = await SystemPlatform(use_systemd=False).spawn_worker(spec(code, cap=512 * MIB))
        rc, out, _ = await h.wait()
        assert rc != 0 and b"ok" not in out

    asyncio.run(go())


@linux_only
def test_systemd_probe_failure_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spawn, "probe_systemd_scope", lambda: False)
    p = make_platform()
    argv = p._posix_argv(spec("pass"))
    assert "systemd-run" not in argv and argv[-2:] == ["-c", "pass"]
    with_sd = SystemPlatform(use_systemd=True)._posix_argv(spec("pass", cap=123))
    assert "MemoryMax=123" in with_sd and "MemorySwapMax=0" in with_sd
    if shutil.which("ionice"):
        assert argv[:3][1:] == ["-c3", "-t"]
    assert os.path.isabs(with_sd[with_sd.index("--user") - 1]) or shutil.which("systemd-run") is None


@linux_only
def test_systemd_probe_requires_memory_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("systemd-run") is None:
        monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/systemd-run")
    assert spawn.probe_systemd_scope(lambda _p: "cpu pids\n") is None
    seen: list[str] = []

    def no_file(p: str) -> str:
        seen.append(p)
        raise FileNotFoundError(p)

    assert spawn.probe_systemd_scope(no_file) is None
    assert f"user@{os.getuid()}.service/cgroup.controllers" in seen[0]


@linux_only
def test_lower_self_priority_nice10(monkeypatch: pytest.MonkeyPatch) -> None:
    rec: list[tuple[int, int, int]] = []
    monkeypatch.setattr(os, "getpriority", lambda _w, _i: 0)
    monkeypatch.setattr(os, "setpriority", lambda w, i, n: rec.append((w, i, n)))
    SystemPlatform().lower_self_priority()
    assert rec == [(os.PRIO_PROCESS, 0, 10)]


# --- Windows path with fakes (not executed on a real Windows host here) -------------------------
class FakeWin32:
    IDLE_PRIORITY_CLASS = 0x40

    def __init__(self, fail_assign: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail_assign = fail_assign

    def create_job(self, cap: int) -> int:
        self.calls.append(("create_job", cap))
        return 99

    def process_handle(self, _p: Any) -> int:
        return 5

    def assign_to_job(self, job: int, h: int) -> None:
        self.calls.append(("assign", (job, h)))
        if self.fail_assign:
            raise OSError("denied")

    def close_handle(self, h: int) -> None:
        self.calls.append(("close", h))

    def set_self_below_normal_and_eco(self) -> None:
        self.calls.append(("self_prio", None))


class FakeProc:
    returncode: int | None = 0
    pid = 1234

    def __init__(self) -> None:
        self.killed = 0
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(b"o")
        self.stdout.feed_eof()
        self.stderr.feed_data(b"e")
        self.stderr.feed_eof()

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        self.killed += 1


def win_platform(w: FakeWin32, seen: list[dict[str, Any]]) -> SystemPlatform:
    async def fake_exec(*argv: Any, **kw: Any) -> FakeProc:
        seen.append({"argv": argv, **kw})
        return FakeProc()

    return SystemPlatform(system="win32", win32=w, exec_fn=fake_exec)


def test_windows_wait_closes_job_once() -> None:
    w, seen = FakeWin32(), cast("list[dict[str, Any]]", [])

    async def go() -> None:
        h = await win_platform(w, seen).spawn_worker(spec("x", cap=77))
        assert await h.wait() == (0, b"o", b"e")
        h.kill()
        await h.wait()

    asyncio.run(go())
    assert seen[0]["creationflags"] == 0x40 | 0x08000000 and seen[0]["stdin"] == asyncio.subprocess.DEVNULL
    assert ("create_job", 77) in w.calls and ("assign", (99, 5)) in w.calls
    assert w.calls.count(("close", 99)) == 1


def test_windows_kill_closes_job_once() -> None:
    w, seen = FakeWin32(), cast("list[dict[str, Any]]", [])

    async def go() -> None:
        h = await win_platform(w, seen).spawn_worker(spec("x"))
        h.kill()
        h.kill()
        await h.wait()

    asyncio.run(go())
    assert w.calls.count(("close", 99)) == 1


def test_windows_assign_failure_still_runs() -> None:
    w, seen = FakeWin32(fail_assign=True), cast("list[dict[str, Any]]", [])

    async def go() -> None:
        p = win_platform(w, seen)
        for _ in range(2):
            h = await p.spawn_worker(spec("x"))
            assert (await h.wait())[0] == 0

    asyncio.run(go())
    assert w.calls.count(("close", 99)) == 2  # orphan job closed on failure, each time


def test_windows_self_priority_and_errors_swallowed() -> None:
    w = FakeWin32()
    win_platform(w, []).lower_self_priority()
    assert ("self_prio", None) in w.calls

    class Boom(FakeWin32):
        def set_self_below_normal_and_eco(self) -> None:
            raise OSError("x")

    win_platform(Boom(), []).lower_self_priority()


def test_win32_argtypes_declared_on_every_function() -> None:
    from sae.platform import _win32

    class Fn:
        pass

    class Dll:
        def __init__(self) -> None:
            self.fns: dict[str, Fn] = {}

        def __getattr__(self, name: str) -> Fn:
            return self.fns.setdefault(name, Fn())

    k32, u32, pdh = Dll(), Dll(), Dll()
    _win32.configure_kernel32(k32)
    _win32.configure_user32(u32, object)
    _win32.configure_pdh(pdh)
    assert set(k32.fns) >= {"GetCurrentProcess", "SetPriorityClass", "SetProcessInformation", "CreateJobObjectW",
                            "SetInformationJobObject", "AssignProcessToJobObject", "CloseHandle"}
    assert set(u32.fns) == {"EnumWindows", "GetWindowThreadProcessId", "IsWindowVisible", "IsIconic"}
    assert set(pdh.fns) >= {"PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
                            "PdhGetFormattedCounterValue", "PdhCloseQuery"}
    for dll in (k32, u32, pdh):
        for name, fn in dll.fns.items():
            assert hasattr(fn, "argtypes") and hasattr(fn, "restype"), name


@linux_only
def test_stdout_flood_is_capped_and_killed() -> None:
    async def go() -> None:
        code = "import sys\nb=b'x'*65536\nwhile True: sys.stdout.buffer.write(b)"
        h = await SystemPlatform(use_systemd=False).spawn_worker(spec(code))
        rc, out, err = await asyncio.wait_for(h.wait(), 20)
        assert rc is None
        assert len(out) == MAX_STDOUT_BYTES + 1 and err == b""

    asyncio.run(go())


@linux_only
def test_stderr_kept_to_64k() -> None:
    async def go() -> None:
        code = "import sys;sys.stderr.write('e'*300000)"
        rc, _o, err = await (await SystemPlatform(use_systemd=False).spawn_worker(spec(code))).wait()
        assert rc == 0 and len(err) == 64 * 1024

    asyncio.run(go())


@linux_only
def test_cap_clamped_to_hard_limit_and_env_passed() -> None:
    seen: list[dict[str, Any]] = []
    caps: list[int] = []

    async def fake_exec(*argv: Any, **kw: Any) -> Any:
        seen.append(kw)
        return FakeProc()

    p = SystemPlatform(use_systemd=False, exec_fn=fake_exec, getrlimit=lambda _r: (1, 100 * MIB))
    asyncio.run(p.spawn_worker(spec("x", cap=900 * MIB)))
    assert seen[0]["env"]["MALLOC_ARENA_MAX"] == "2"
    # preexec closes over the clamped cap: run it with setrlimit recorded
    import resource

    orig = resource.setrlimit
    resource.setrlimit = lambda _w, lim: caps.append(lim[0])
    try:
        seen[0]["preexec_fn"]()
    finally:
        resource.setrlimit = orig
    assert caps == [100 * MIB]


@linux_only
def test_cancelled_wait_kills_child() -> None:
    async def go() -> None:
        h = await SystemPlatform(use_systemd=False).spawn_worker(spec("import time;time.sleep(60)"))
        pid = h._proc.pid  # type: ignore[attr-defined]
        t = asyncio.create_task(h.wait())
        await asyncio.sleep(0.3)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        await asyncio.wait_for(h._proc.wait(), 5)  # type: ignore[attr-defined]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(go())
