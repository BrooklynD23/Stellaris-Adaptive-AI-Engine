"""Daemon main loop (T4-U6): one serialised engine consumer plus observation, ACK, governor and inbox feeders.

All engine calls happen on a single task fed by one priority queue (no shared mutation). C9 commands and the
exit request jump the queue and are applied between two engine calls (v2 rule 10).
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c5_bus import HealthKind, HealthPayload
from sae.contracts.c6_adapter import AdapterMode, EmitReceipt, GameAdapter, Observation
from sae.contracts.c7_governor import GameState, HealthLevel, SensorSample, SpmSample, Trigger
from sae.contracts.c8_config import SaeConfig
from sae.contracts.c9_control import ControlResult, command_expired
from sae.core import engine
from sae.core.dates import mi_of
from sae.core.state import EngineCtx, EngineState, EventDraft, Outputs
from sae.daemon import disk
from sae.daemon.control import apply_item
from sae.daemon.ports import AdapterFactory, DaemonDeps, InboxItem
from sae.daemon.saveparser import GovernedSaveParser
from sae.daemon.statefile import save_state

log = logging.getLogger(__name__)
REEXEC_FILE = "reexec.json"
REEXEC_MAX_PER_HOUR = 3

__all__ = ["run_engine"]

_CTL, _OBS = 0, 1  # queue priorities: control/exit first
_MAX_DETAIL = 500
_FAIL_HEALTH_S = 60.0
Component = Literal["daemon", "governor", "parser", "adapter"]
#: C6 section 1.7 (proposal 011): observation N+1 is pulled only after N is fully handled.
LOCKSTEP_MODES = frozenset({AdapterMode.REPLAY, AdapterMode.SYNTH})


def is_ironman(adapter: GameAdapter, o: Observation | None) -> bool:
    """Wave 3 N6: the adapter reports Ironman (T2 LiveAdapter ``ironman_detected``; an ``ironman`` observation
    event is also accepted in case the C6 rule adds one). Meta probe itself is UNVERIFIED (A-20).
    """
    if getattr(adapter, "ironman_detected", False) is True:
        return True
    return o is not None and any(str(e) == "ironman" for e in o.events)


def _expire(item: InboxItem, started_at: datetime) -> InboxItem:
    """C9 section 3.1 at pickup (defence in depth; T6's inbox applies the same rule): stale -> ``expired``."""
    cmd = item.command
    if cmd is None or item.error is not None:
        return item
    detail = command_expired(cmd.issued_at, datetime.now(UTC), started_at)
    if detail is None:
        return item
    err = ControlResult(command_id=cmd.command_id, accepted=False, error="expired", detail=detail,
                        applied_at=None, status=None)
    return InboxItem(command=cmd, error=err, handle=item.handle)


def _accepts_health(factory: Any) -> bool:
    """True if the factory takes a ``health=`` keyword (C6 section 1.8, proposal 009)."""
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return False
    return "health" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class _Loop:
    """Mutable loop context. Engine state is only replaced on the consumer task."""

    def __init__(self, config: SaeConfig, adapter: GameAdapter | AdapterFactory, run_id: str, deps: DaemonDeps,
                 run_dir: Path, state: EngineState | None, poll_s: float, inbox_s: float) -> None:
        self.cfg, self.run_id, self.deps, self.run_dir = config, run_id, deps, run_dir
        self.poll_s, self.inbox_s = poll_s, inbox_s
        self.state = state if state is not None else engine.initial_state(config.engine)
        self.gstate, self.level = GameState.PLAYING, HealthLevel.H0
        self.knob: KnobCommand | None = None
        self.sample: SensorSample | None = None
        self.parser: GovernedSaveParser | None = None
        self.adapter: GameAdapter | None = None
        self.adapter_closed = False
        self.q: asyncio.PriorityQueue[tuple[int, int, str, Any]] = asyncio.PriorityQueue()
        self.seq = itertools.count()
        self.stop = asyncio.Event()
        self.code = 0
        self.reexec = False
        self.terminated = False  # SIGTERM / console close received
        self.guard_key: tuple[int | None] | None = None  # (game month,) of the last reexec_guard event
        self.spm: float | None = None  # latest seconds per game month (monthly health)
        self.prev_log: tuple[int, float] | None = None  # (m, monotonic) of the last log observation
        self.breach_key: tuple[int | None] | None = None  # (game month,) of the last RSS breach event
        self.health_mi: int | None = None
        self.exit_logged = False
        self.pending: set[str] = set()  # inbox handles queued or in progress
        self.last_fail_health: float | None = None
        if isinstance(adapter, GameAdapter):
            self.adapter = adapter
        else:
            self.parser = GovernedSaveParser(deps, config, self._parser_health, self._boundary, self._date)
            if _accepts_health(adapter):
                build: Any = adapter
                self.adapter = build(self.parser, deps.sink, health=self.adapter_health)
            else:
                self.adapter = adapter(self.parser, deps.sink)

    # -- helpers ---------------------------------------------------------------------------------------------
    def _boundary(self) -> str | None:
        return engine.next_boundary(self.state)

    def _date(self) -> str | None:
        a = self.adapter
        return a.clock().game_date if a is not None else self.state.last_obs_date

    def _put(self, prio: int, kind: str, item: Any = None) -> None:
        self.q.put_nowait((prio, next(self.seq), kind, item))

    def _health(self, kind: HealthKind, component: Component, detail: str | None = None,
                metrics: dict[str, float] | None = None, trigger: Trigger | None = None) -> None:
        payload = HealthPayload(
            kind=kind, component=component, state=self.gstate, level=self.level,
            trigger=trigger, metrics=metrics or {}, detail=None if detail is None else detail[:_MAX_DETAIL])
        self.deps.sink.emit(EventDraft(type="health", payload=payload, game_date=self.state.last_obs_date))

    def _parser_health(self, kind: HealthKind, detail: str | None, metrics: dict[str, float]) -> None:
        self._health(kind, "parser", detail, metrics)

    def adapter_health(self, kind: HealthKind, detail: str | None) -> None:
        """C6 section 1.8 hook: stamp component/state/level/game date; never raises into the adapter."""
        try:
            payload = HealthPayload(kind=kind, component="adapter", state=self.gstate, level=self.level,
                                    detail=None if detail is None else detail[:_MAX_DETAIL])
            date = self._date() if self.adapter is not None else self.state.last_obs_date
            self.deps.sink.emit(EventDraft(type="health", payload=payload, game_date=date))
        except Exception:
            log.exception("adapter health hook failed")

    def _publish(self, out: Outputs) -> None:
        for d in out.drafts:
            self.deps.sink.emit(d)

    def save(self) -> None:
        try:
            save_state(self.run_dir / "state.json", self.state)
        except OSError:
            log.exception("state.json write failed")

    # -- feeders ---------------------------------------------------------------------------------------------
    async def _feed_obs(self, adapter: GameAdapter) -> None:
        lockstep = adapter.mode in LOCKSTEP_MODES
        async for o in adapter.observations():
            if not lockstep:
                self._put(_OBS, "obs", (o, None))
                continue
            done = asyncio.Event()
            self._put(_OBS, "obs", (o, done))
            await done.wait()  # C6 section 1.7a: engine step, publish, emit and receipt all done
        self._put(_OBS, "end")

    async def _feed_acks(self, adapter: GameAdapter) -> None:
        async for a in adapter.acks():
            self._put(_OBS, "ack", a)

    def _feeder_failed(self, what: str) -> None:
        """Log a feeder exception, at most once a minute (no bus event: it is not a CI-5 budget breach)."""
        now = self.deps.monotonic()
        if self.last_fail_health is None or now - self.last_fail_health >= _FAIL_HEALTH_S:
            self.last_fail_health = now
            log.exception("%s failed", what)

    async def _governor_task(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:
                self._feeder_failed("governor_poll")
            await asyncio.sleep(self.poll_s)

    def inbox_once(self) -> None:
        """One inbox poll; handles already queued or in progress are skipped."""
        for raw in self.deps.inbox.poll():
            if raw.handle in self.pending:
                continue
            item = _expire(raw, self.deps.started_at)
            self.pending.add(item.handle)
            self._put(_CTL, "ctl", item)

    async def _inbox_task(self) -> None:
        while True:
            try:
                self.inbox_once()
            except Exception:
                self._feeder_failed("inbox_poll")
            await asyncio.sleep(self.inbox_s)

    def poll_once(self) -> None:
        """One governor poll: sample, decide, publish level/state changes, route the decision."""
        s = self.deps.sensors.sample()
        self.sample = s
        d = self.deps.governor.poll(s)
        if d.level != self.level:
            self.level = d.level
            self._health(HealthKind.LEVEL_CHANGE, "governor", metrics=self._metrics(s), trigger=d.trigger)
        if d.state != self.gstate:
            self.gstate = d.state
            self._health(HealthKind.STATE_CHANGE, "governor", metrics=self._metrics(s))
        if self.parser is not None:
            self.parser.set_decision(d)
            if d.kill_worker:
                self.parser.kill_current()
        if d.knob_request is not None:
            self.knob = d.knob_request
        mi = self.state.last_obs_mi
        if s.daemon_rss_bytes > self.cfg.governor.daemon_rss_breach_bytes and self.breach_key != (mi,):
            self.breach_key = (mi,)
            self._health(HealthKind.BUDGET_BREACH, "daemon", f"rss={s.daemon_rss_bytes}", self._metrics(s))
        if d.daemon_should_exit:
            if self._reexec_allowed():
                self.reexec = True
                self._put(_CTL, "exit")
            else:
                if self.guard_key != (mi,):
                    self.guard_key = (mi,)
                    log.error("re-exec guard: %d re-execs in the last hour; staying up", REEXEC_MAX_PER_HOUR)
                    self._health(HealthKind.BUDGET_BREACH, "daemon", "reexec_guard")

    def _reexec_allowed(self) -> bool:
        """Record this re-exec (wall clock) in ``reexec.json``; False when 3 already happened in the last hour."""
        path = self.run_dir / REEXEC_FILE
        now = time.time()
        try:
            stamps = [float(t) for t in json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            stamps = []
        recent = [t for t in stamps if 0 <= now - t < 3600]
        if len(recent) >= REEXEC_MAX_PER_HOUR:
            return False
        try:
            path.write_text(json.dumps([*recent, now]), encoding="utf-8")
        except OSError:
            log.exception("could not record re-exec timestamp")
        return True

    def _metrics(self, s: SensorSample) -> dict[str, float]:
        """Monthly health metrics with the C5 section 3 keys."""
        m = {"daemon_rss_bytes": float(s.daemon_rss_bytes), "daemon_cpu_pct": s.daemon_cpu_pct,
             "avail_ram_bytes": float(s.available_ram_bytes)}
        if s.hard_fault_rate is not None:
            m["fault_rate"] = s.hard_fault_rate
        if self.spm is not None:
            m["spm"] = self.spm
        for key in ("spm_med6", "spm_med24"):  # T5 ResourceGovernor read-only accessors; omitted when None (C5)
            v = getattr(self.deps.governor, key, None)
            if isinstance(v, int | float):
                m[key] = float(v)
        cost = self.parser.last_cost if self.parser is not None else None
        if cost is not None:
            m.update(parse_rss_peak_bytes=float(cost.rss_peak_bytes), parse_cpu_s=cost.cpu_s, parse_wall_s=cost.wall_s)
        return m

    # -- consumer --------------------------------------------------------------------------------------------
    async def _consume(self, adapter: GameAdapter) -> None:
        while True:
            _, _, kind, item = await self.q.get()
            if kind == "obs":
                o, done = item
                try:
                    await self._on_obs(adapter, o)
                finally:
                    if done is not None:
                        done.set()
            elif kind == "ack":
                self.state, out = engine.on_ack(self.state, item)
                self._publish(out)
            elif kind == "ctl":
                self._on_ctl(adapter, item)
            else:  # "end" or "exit"
                self._drain(adapter)
                self.save()
                break
        self.stop.set()

    def _drain(self, adapter: GameAdapter) -> None:
        """Apply already-queued ACK and control items (cheap, no engine emit) before the final save."""
        rest: list[tuple[int, int, str, Any]] = []
        while not self.q.empty():
            rest.append(self.q.get_nowait())
        for _, _, kind, item in sorted(rest, key=lambda t: t[:2]):
            if kind == "ack":
                self.state, out = engine.on_ack(self.state, item)
                self._publish(out)
            elif kind == "ctl":
                self._on_ctl(adapter, item)

    def _ctx(self, adapter: GameAdapter, o: Observation | None = None) -> EngineCtx:
        return EngineCtx(mode=adapter.mode, cfg=self.cfg.engine, knob_request=self.knob,
                         emit_period=self.cfg.bridge.emit_period, game_state=self.gstate, level=self.level,
                         observe_only=is_ironman(adapter, o))

    def _spm(self, o: Observation) -> None:
        if o.source != "log" or o.m is None:
            return
        now = self.deps.monotonic()
        if self.prev_log is not None and o.m > self.prev_log[0]:
            k = o.m - self.prev_log[0]
            wall = max((now - self.prev_log[1]) / k, 0.001)
            self.deps.governor.on_month(SpmSample(game_date=o.game_date, wall_s=wall, months=k))
            self.spm = wall
        self.prev_log = (o.m, now)

    async def _on_obs(self, adapter: GameAdapter, o: Observation) -> None:
        self._spm(o)
        before = self.state.last_decided_mi
        self.state, out = engine.on_observation(self.state, o, self._ctx(adapter, o))
        self._publish(out)
        self.deps.governor.set_next_boundary(engine.next_boundary(self.state))
        mi = mi_of(o.game_date)
        if self.health_mi != mi:
            self.health_mi = mi
            self.check_disk()
            if self.sample is not None:
                self._health(HealthKind.MONTHLY, "daemon", metrics=self._metrics(self.sample))
        if out.batch is not None:
            self.knob = None
            try:
                receipt = await adapter.emit(out.batch)
            except Exception as e:
                log.exception("adapter emit raised")
                receipt = EmitReceipt(seq=out.batch.seq, ok=False, error="write_failed", detail=type(e).__name__)
            self.state, more = engine.on_emitted(self.state, out.batch, receipt)
            self._publish(more)
        if self.state.last_decided_mi != before:
            self.save()

    def _on_ctl(self, adapter: GameAdapter, item: InboxItem) -> None:
        r = apply_item(self.state, item, self.run_id, self.gstate, self.level, adapter.clock().game_date)
        self.state = r.state
        if r.suspend is not None:
            self.deps.governor.suspend(r.suspend)
        for d in r.drafts:
            self.deps.sink.emit(d)
        self.deps.inbox.complete(item, r.result)
        self.pending.discard(item.handle)
        self.deps.governor.set_next_boundary(engine.next_boundary(self.state))

    # -- lifecycle -------------------------------------------------------------------------------------------
    def check_disk(self) -> None:
        """Warn when ``runs/`` exceeds the threshold (start + once per game month). Never deletes anything.

        Uses the existing C5 health kind ``budget_breach`` (component ``daemon``): C5 v1 has no disk kind and adding
        one is a contract change; the detail ``runs_dir_bytes`` tells it apart from the RSS breach.
        """
        try:
            used = disk.dir_bytes(self.run_dir.parent)
        except Exception:
            log.exception("runs dir size check failed")
            return
        if used > disk.RUNS_DIR_WARN_BYTES:
            self._health(HealthKind.BUDGET_BREACH, "daemon",
                         f"runs_dir_bytes={used} > {disk.RUNS_DIR_WARN_BYTES}; nothing is deleted",
                         metrics={"runs_dir_bytes": float(used)})

    async def run(self) -> None:
        adapter = self.adapter
        assert adapter is not None
        self.check_disk()
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(c) for c in (
                    self._consume(adapter), self._feed_obs(adapter), self._feed_acks(adapter),
                    self._governor_task(), self._inbox_task())]
                await self.stop.wait()
                for t in tasks:
                    t.cancel()
        except ExceptionGroup as eg:
            raise eg.exceptions[0] from eg

    def request_terminate(self) -> None:
        """SIGTERM (or the Windows console close): stop at the next engine boundary, exit cleanly."""
        if not self.terminated:
            self.terminated = True
            self._put(_CTL, "exit")

    def exit_event(self, detail: str) -> None:
        if not self.exit_logged:
            self.exit_logged = True
            self._health(HealthKind.DAEMON_EXIT, "daemon", detail)

    async def close(self) -> None:
        if self.parser is not None:
            self.parser.kill_current()  # never leave a worker running past shutdown or re-exec (C7 section 4.6)
        # Proposal 017: adapter.close() (which neutralises sae_in.ini, T2) runs BEFORE the sink closes on every
        # exit path, so its ini_neutralised health event still reaches the bus.
        if self.adapter is not None and not self.adapter_closed:
            self.adapter_closed = True
            try:
                await self.adapter.close()
            except Exception:
                log.exception("adapter close failed")
        for step in (self.deps.sink.flush, self.deps.sink.close):
            try:
                step()
            except Exception:
                log.exception("sink shutdown failed")


def install_terminate_handler(
    request: Callable[[], None], platform: str = sys.platform, sig: Any = signal,
) -> Callable[[], None]:
    """Route SIGTERM (POSIX) or CTRL_BREAK/CTRL_CLOSE (Windows ``SIGBREAK``) to ``request``; return an uninstaller.

    Only possible on the main thread of the running loop; elsewhere (tests, embedded use) it is a no-op.
    """
    try:
        aloop = asyncio.get_running_loop()
        if platform == "win32":  # Windows branch: mocked in tests, not executed in CI on this machine
            brk = getattr(sig, "SIGBREAK", None)
            if brk is None:
                return lambda: None
            old = sig.signal(brk, lambda *_: aloop.call_soon_threadsafe(request))
            return lambda: sig.signal(brk, old)
        aloop.add_signal_handler(sig.SIGTERM, request)

        def remove() -> None:
            aloop.remove_signal_handler(sig.SIGTERM)

        return remove
    except (RuntimeError, ValueError, NotImplementedError):
        log.debug("terminate handler not installed (not the main thread)")
        return lambda: None


async def run_engine(
    config: SaeConfig, adapter: GameAdapter | AdapterFactory, run_id: str, deps: DaemonDeps, run_dir: Path,
    state: EngineState | None = None, *, poll_s: float | None = None, inbox_s: float = 2.0,
) -> int:
    """Run the engine until the adapter ends, a shutdown is requested or an error occurs.

    ``adapter`` may be a built :class:`GameAdapter` or a factory (proposal 004) called with the daemon-owned
    governed parser and the bus sink. Returns 0 on clean exit (end of input, re-exec, interrupt), 1 on error.
    ``poll_s`` / ``inbox_s`` override the poll intervals (tests); default ``config.governor.poll_s`` / 2 s.
    """
    poll = config.governor.poll_s if poll_s is None else poll_s
    loop = _Loop(config, adapter, run_id, deps, run_dir, state, poll, inbox_s)
    code = 0
    uninstall = install_terminate_handler(loop.request_terminate)
    try:
        await loop.run()
        loop.exit_event("sigterm" if loop.terminated else "exit_requested" if loop.reexec else "adapter_ended")
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("interrupted; exiting cleanly")
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        loop.save()
        loop.exit_event("interrupted")
    except Exception as e:
        log.exception("engine failed")
        loop.exit_event(type(e).__name__)  # full message stays in the log (fog/secret hygiene)
        code = 1
    finally:
        uninstall()
        await loop.close()
    if code == 0 and loop.reexec and not loop.terminated:
        deps.reexec()
    return code
