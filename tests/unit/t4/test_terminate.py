"""C8 section 6 CURRENT ownership in `sae.daemon.run` (every adapter mode) and the SIGTERM / console-close handler."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c5_bus import HealthKind, HealthPayload
from sae.contracts.c8_config import CURRENT_RUN_FILE, SaeConfig
from sae.daemon import loop as loop_mod
from sae.daemon import run, runs, wiring

from .fakes import FakePlatform, FakeSink, make_deps
from .test_loop import EchoAdapter, go

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGTERM path")


def _exit_details(sink: FakeSink) -> list[str | None]:
    return [d.payload.detail for d in sink.drafts if d.type == "health" and isinstance(d.payload, HealthPayload)
            and d.payload.kind is HealthKind.DAEMON_EXIT]


@posix_only
def test_sigterm_exits_cleanly_with_daemon_exit(tmp_path: Path) -> None:
    ad = EchoAdapter(10_000, delay=0.0)
    reexec: list[int] = []

    async def term() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.01)

    ad.hooks = {5: term}
    code, sink, _, _ = go(ad, tmp_path, reexec=lambda: reexec.append(1))
    assert code == 0 and _exit_details(sink) == ["sigterm"] and reexec == []
    assert (tmp_path / "state.json").exists() and ad.closed
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # handler removed again


def _patch_deps(monkeypatch: pytest.MonkeyPatch) -> FakeSink:
    deps = make_deps(FakePlatform())
    monkeypatch.setattr(wiring, "default_deps", lambda cfg, run_id, resume=False: deps)
    return deps.sink  # type: ignore[return-value]


def test_run_holds_current_for_synth_and_releases_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_deps(monkeypatch)
    seen: list[str] = []
    ad = EchoAdapter(12, delay=0.0)

    async def peek() -> None:
        seen.append((tmp_path / CURRENT_RUN_FILE).read_text(encoding="utf-8"))

    ad.hooks = {3: peek}
    cfg = SaeConfig(paths={"runs_dir": str(tmp_path)})  # type: ignore[arg-type]
    assert asyncio.run(run(cfg, ad, "20261019-000000-synth-abcd")) == 0  # type: ignore[arg-type]
    assert seen == [f"20261019-000000-synth-abcd {os.getpid()}"]
    assert not (tmp_path / CURRENT_RUN_FILE).exists()


@posix_only
def test_run_releases_current_on_sigterm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = _patch_deps(monkeypatch)
    ad = EchoAdapter(10_000, delay=0.0)

    async def term() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.01)

    ad.hooks = {4: term}
    cfg = SaeConfig(paths={"runs_dir": str(tmp_path)})  # type: ignore[arg-type]
    assert asyncio.run(run(cfg, ad, "20261019-000000-synth-abcd")) == 0  # type: ignore[arg-type]
    assert _exit_details(sink) == ["sigterm"] and not (tmp_path / CURRENT_RUN_FILE).exists()


def test_run_refuses_when_another_daemon_holds_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_deps(monkeypatch)
    (tmp_path / CURRENT_RUN_FILE).write_text("20261019-000000-live-0000 4242", encoding="utf-8")
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: True)
    cfg = SaeConfig(paths={"runs_dir": str(tmp_path)})  # type: ignore[arg-type]
    assert asyncio.run(run(cfg, EchoAdapter(2, delay=0.0), "20261019-000001-synth-abcd")) == 1  # type: ignore[arg-type]
    assert "sae daemon:" in capsys.readouterr().err
    assert (tmp_path / CURRENT_RUN_FILE).read_text(encoding="utf-8").endswith("4242")  # untouched


def test_windows_console_close_branch_mocked() -> None:
    """Windows branch (mocked; not executed in CI on this machine): SIGBREAK routes to the request."""

    class FakeSignal:
        SIGBREAK = 21
        SIGTERM = 15

        def __init__(self) -> None:
            self.handlers: dict[int, Any] = {21: "old"}

        def signal(self, num: int, handler: Any) -> Any:
            old, self.handlers[num] = self.handlers.get(num), handler
            return old

    async def main() -> tuple[list[int], FakeSignal]:
        fake, hits = FakeSignal(), []
        undo = loop_mod.install_terminate_handler(lambda: hits.append(1), platform="win32", sig=fake)
        fake.handlers[21](21, None)
        await asyncio.sleep(0)
        undo()
        return hits, fake

    hits, fake = asyncio.run(main())
    assert hits == [1] and fake.handlers[21] == "old"


def test_handler_is_noop_off_the_main_thread() -> None:
    import threading

    out: list[Any] = []

    def worker() -> None:
        async def main() -> None:
            out.append(loop_mod.install_terminate_handler(lambda: None))

        asyncio.run(main())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    out[0]()  # uninstaller is callable and harmless
