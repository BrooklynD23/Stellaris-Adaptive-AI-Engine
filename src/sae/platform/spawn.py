"""OS controls: self priority and capped, idle-priority parse workers (C7 sections 7-8).

No function here accepts a PID. Targets are only ``0`` (self) or our own spawned child.
Windows paths are not executed in CI on this machine (tested with a fake ``_win32``).
UNVERIFIED A-27: Job commit limit makes the Rust worker abort cleanly; M0 runs a 2 GiB parse under a 1 GiB cap.
UNVERIFIED A-28: worker background mode (--background) lowers I/O and memory priority on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from sae.contracts.c4_parse import MAX_STDOUT_BYTES
from sae.contracts.c7_governor import WorkerHandle, WorkerSpec

from . import _win32

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
_CHUNK = 64 * 1024
STDERR_KEEP_BYTES = 64 * 1024
ExecFn = Callable[..., Awaitable[Any]]


def _read_controllers(path: str) -> str:
    return Path(path).read_text(encoding="ascii")


def probe_systemd_scope(read_controllers: Callable[[str], str] = _read_controllers) -> str | None:
    """One-time probe; returns the resolved systemd-run path, or None when unusable.

    Needs the ``memory`` controller delegated to the user manager, else MemoryMax is silently ignored.
    """
    exe = shutil.which("systemd-run")
    if exe is None or sys.platform == "win32":
        return None
    uid = os.getuid()
    try:
        ctl = read_controllers(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/cgroup.controllers")
        if "memory" not in ctl.split():
            return None
    except Exception:
        log.info("cgroup controllers unreadable; systemd scope unusable", exc_info=True)
        return None
    try:
        r = subprocess.run(
            [exe, "--user", "--scope", "-q", "true"], capture_output=True, timeout=2.0, check=False
        )
        return exe if r.returncode == 0 else None
    except Exception:
        log.info("systemd-run probe failed; falling back to RLIMIT_AS", exc_info=True)
        return None


class _Handle:
    """Running worker. ``kill`` is idempotent; a Job handle is closed exactly once."""

    def __init__(self, proc: Any, job: int | None, win32: ModuleType | Any) -> None:
        self._proc = proc
        self._job = job
        self._win32 = win32
        self._flooded = False

    def _close_job(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            try:
                self._win32.close_handle(job)
            except Exception:
                log.warning("closing job handle failed", exc_info=True)

    async def wait(self) -> tuple[int | None, bytes, bytes]:
        """Wait for exit; returncode is None when killed by a signal."""
        try:
            out, err = await asyncio.gather(self._read_stdout(), self._read_stderr())
            await self._proc.wait()
        except asyncio.CancelledError:
            self.kill()  # daemon shutdown / timeout must never orphan the worker
            raise
        finally:
            self._close_job()
        rc = self._proc.returncode
        if self._flooded:
            rc = None
        return (rc if rc is not None and rc >= 0 else None), out, err

    async def _read_stdout(self) -> bytes:
        """Read in 64 KiB chunks; past MAX_STDOUT_BYTES kill the worker and return the first cap+1 bytes."""
        buf = bytearray()
        while chunk := await self._proc.stdout.read(_CHUNK):
            buf += chunk
            if len(buf) > MAX_STDOUT_BYTES:
                self._flooded = True
                self.kill()
                return bytes(buf[: MAX_STDOUT_BYTES + 1])  # one past the cap so the caller's `> MAX` check fires
        return bytes(buf)

    async def _read_stderr(self) -> bytes:
        """Drain stderr concurrently (so the child never blocks), keeping only the first 64 KiB."""
        buf = bytearray()
        while chunk := await self._proc.stderr.read(_CHUNK):
            if len(buf) < STDERR_KEEP_BYTES:
                buf += chunk[: STDERR_KEEP_BYTES - len(buf)]
        return bytes(buf)

    def kill(self) -> None:
        """Terminate now; safe to call repeatedly or after exit."""
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass
        finally:
            self._close_job()


class SystemPlatform:
    """Real :class:`~sae.contracts.c7_governor.Platform`."""

    def __init__(
        self,
        *,
        system: str | None = None,
        use_systemd: str | bool = False,
        win32: ModuleType | Any = _win32,
        exec_fn: ExecFn = asyncio.create_subprocess_exec,
        getrlimit: Callable[[int], tuple[int, int]] | None = None,
    ) -> None:
        self._getrlimit = getrlimit  # None -> resource.getrlimit (POSIX only)
        self._system = system or sys.platform
        self._systemd: str | None = None
        if use_systemd is True:
            self._systemd = shutil.which("systemd-run") or "systemd-run"
        elif use_systemd:
            self._systemd = use_systemd
        self._win32 = win32
        self._exec = exec_fn
        self._job_warned = False

    def lower_self_priority(self) -> None:
        """Lower this process only. Errors are logged, never raised."""
        try:
            if self._system == "win32":
                self._win32.set_self_below_normal_and_eco()
            elif sys.platform != "win32":  # narrows for mypy on Windows
                os.setpriority(os.PRIO_PROCESS, 0, max(os.getpriority(os.PRIO_PROCESS, 0), 10))
        except Exception:
            log.warning("lower_self_priority failed", exc_info=True)

    def _posix_argv(self, spec: WorkerSpec) -> list[str]:
        argv: list[str] = []
        if (io := shutil.which("ionice")) is not None:
            argv += [io, "-c3", "-t"]
        if self._systemd:
            argv += [self._systemd, "--user", "--scope", "--quiet", "-p", f"MemoryMax={spec.mem_cap_bytes}",
                     "-p", "MemorySwapMax=0"]
        return [*argv, spec.exe, *spec.args]

    async def spawn_worker(self, spec: WorkerSpec) -> WorkerHandle:
        """Spawn ``spec`` at idle priority under a memory cap."""
        common: dict[str, Any] = {
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if self._system == "win32":
            flags = self._win32.IDLE_PRIORITY_CLASS | CREATE_NO_WINDOW
            proc = await self._exec(spec.exe, *spec.args, creationflags=flags, **common)
            return _Handle(proc, self._assign_job(proc, spec.mem_cap_bytes), self._win32)
        if sys.platform == "win32":  # unreachable at runtime; narrows the POSIX path for mypy on Windows
            raise OSError(f"POSIX spawn path reached on win32 (system={self._system!r})")
        cap = spec.mem_cap_bytes
        import resource  # POSIX only; never reached on win32

        hard = (self._getrlimit or resource.getrlimit)(resource.RLIMIT_AS)[1]
        if hard != resource.RLIM_INFINITY:
            cap = min(cap, hard)  # a soft limit above the hard one would fail setrlimit in the child
        use_rlimit = not self._systemd

        def preexec() -> None:  # child, after fork; two syscalls only
            try:
                os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
            except Exception:
                pass
            if use_rlimit:
                import resource

                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

        argv = self._posix_argv(spec)
        # RLIMIT_AS counts per-thread arena address space; cap glibc arenas
        env = {**os.environ, "MALLOC_ARENA_MAX": "2"}
        proc = await self._exec(*argv, preexec_fn=preexec, env=env, **common)
        return _Handle(proc, None, self._win32)

    def _assign_job(self, proc: Any, cap: int) -> int | None:
        # ponytail: Job assigned right after spawn, not CREATE_SUSPENDED; the child may run a few ms
        # uncapped. Add CREATE_SUSPENDED + resume if M0 shows early allocation matters.
        job: int | None = None
        try:
            job = self._win32.create_job(cap)
            self._win32.assign_to_job(job, self._win32.process_handle(proc))
            return job
        except Exception:
            if not self._job_warned:
                self._job_warned = True
                log.warning("Job Object unavailable; worker runs without memory cap", exc_info=True)
            if job is not None:
                try:
                    self._win32.close_handle(job)
                except Exception:
                    log.debug("job close after failed assign failed", exc_info=True)
            return None


def make_platform() -> SystemPlatform:
    """Build the production platform; probes systemd-run once on Linux."""
    return SystemPlatform(use_systemd=(probe_systemd_scope() or False) if sys.platform.startswith("linux") else False)
