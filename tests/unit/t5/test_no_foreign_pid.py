"""SAE must never write to a process other than itself or its own children."""

from __future__ import annotations

import asyncio
import os
import re
import resource
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import psutil
import pytest

from sae.contracts.c7_governor import MIB, WorkerSpec
from sae.platform.sensors import SystemSensors
from sae.platform.spawn import SystemPlatform

ROOT = Path(__file__).resolve().parents[3] / "src" / "sae"
GAME_PID = 424242


class GameProc:
    pid = GAME_PID
    info: ClassVar[dict[str, Any]] = {"name": "stellaris", "create_time": 1.0}

    def __init__(self, log: list[tuple[str, int | None]]) -> None:
        self._log = log
        self.attrs: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        self.attrs.add(name)  # only reached for names not defined on the class
        raise AttributeError(name)

    def is_running(self) -> bool:
        self.attrs.add("is_running")
        return True

    def create_time(self) -> float:
        self._log.append(("create_time", GAME_PID))
        return 1.0

    def cpu_times(self) -> Any:
        self._log.append(("cpu_times", GAME_PID))
        return SimpleNamespace(user=1.0, system=1.0)


class PS:
    """psutil facade: real self, fake game."""

    def __init__(self, game: GameProc) -> None:
        self.game = game

    def virtual_memory(self) -> Any:
        return psutil.virtual_memory()

    def Process(self) -> Any:
        return psutil.Process()

    def process_iter(self, _a: list[str]) -> list[GameProc]:
        return [self.game]


def test_only_self_or_child_pids_are_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[str, int | None]] = []
    reads: list[tuple[str, int | None]] = []

    def rec(name: str, real: Any, pos: int = 0) -> Any:
        def f(*a: Any, **k: Any) -> Any:
            writes.append((name, a[pos] if len(a) > pos else None))
            return real(*a, **k)

        return f

    monkeypatch.setattr(os, "kill", rec("os.kill", os.kill))
    monkeypatch.setattr(os, "setpriority", rec("setpriority", os.setpriority, 1))
    which: list[int] = []
    real_sp = os.setpriority

    def sp(w: int, i: int, n: int) -> None:
        which.append(w)
        writes.append(("setpriority", i))
        real_sp(w, i, n)

    monkeypatch.setattr(os, "setpriority", sp)
    monkeypatch.setattr(os, "sched_setscheduler", rec("sched_setscheduler", os.sched_setscheduler))
    monkeypatch.setattr(resource, "prlimit", rec("prlimit", resource.prlimit))
    real_init = psutil.Process.__init__

    def init(self: Any, pid: int | None = None, *a: Any, **k: Any) -> None:
        reads.append(("Process", pid))
        real_init(self, pid, *a, **k)

    monkeypatch.setattr(psutil.Process, "__init__", init)

    children: list[int] = []
    game_obj: list[Any] = []

    async def go() -> None:
        sensors = SystemSensors(("stellaris",), psutil_mod=PS(GameProc(reads)), system="linux")
        assert sensors.sample().game_running
        game_obj.append(sensors._game)
        p = SystemPlatform(use_systemd=False)
        p.lower_self_priority()
        h = await p.spawn_worker(
            WorkerSpec(
                exe=sys.executable, args=("-c", "import time;time.sleep(30)"), mem_cap_bytes=2048 * MIB,
                timeout_s=5, threads=1,
            )
        )
        children.append(h._proc.pid)  # type: ignore[attr-defined]
        h.kill()
        await h.wait()

    asyncio.run(go())
    allowed = {0, os.getpid(), *children}
    assert writes, "expected at least one control call"
    for name, pid in writes:
        assert pid in allowed, (name, pid)
    assert all(pid is None or pid in allowed for n, pid in reads if n == "Process")
    # game pid: read-only methods only
    game_calls = {n for n, pid in reads if pid == GAME_PID}
    assert game_calls <= {"cpu_times", "create_time", "name"}
    assert game_obj[0].attrs <= {"cpu_times", "create_time", "is_running", "name", "info"}
    assert which == [os.PRIO_PROCESS]


# Tripwire, not a proof: a text scan catches the obvious foreign-control APIs only.
FORBIDDEN = [
    r"cpu_affinity", r"sched_setaffinity", r"\.nice\(", r"\.ionice\(", r"OpenProcess",
    r"killpg", r"send_signal", r"TerminateProcess", r"taskkill",
]


@pytest.mark.parametrize("pkg", ["platform", "governor"])
def test_source_scan(pkg: str) -> None:
    for f in (ROOT / pkg).rglob("*.py"):
        code = "\n".join(
            ln for ln in f.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith(("#", '"'))
        )
        for pat in FORBIDDEN:
            # comments/docstrings mention the names; only code lines are scanned
            hits = [ln for ln in code.splitlines() if re.search(pat, ln)]
            assert not hits, (f, pat, hits)
        for m in re.finditer(r"SetPriorityClass\(([^,]+),", code):
            assert m.group(1).strip() == "me", (f, m.group(0))
