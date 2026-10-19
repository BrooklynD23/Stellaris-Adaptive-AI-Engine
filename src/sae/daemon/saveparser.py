"""Governor-gated :class:`~sae.contracts.c6_adapter.SaveParser` (C4 section 2, C7 section 7).

Fog rule 12: health details carry only exit code, kind and sizes, never stdout/ParseResult text.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any, Literal

from pydantic import ValidationError

from sae.contracts.c4_parse import (
    CLEAN_FAILURE_CODES,
    EXIT_OK,
    MAX_STDOUT_BYTES,
    PARSE_OUTPUT_ADAPTER,
    Cost,
    ParseError,
    ParseResult,
)
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c7_governor import GovernorDecision, WorkerHandle, WorkerOutcome, WorkerSpec
from sae.contracts.c8_config import SaeConfig
from sae.contracts.common import GameDate
from sae.core.dates import mi_of
from sae.daemon.ports import DaemonDeps

log = logging.getLogger(__name__)

UNSTABLE_RETRY_CAP = 3
_UNSTABLE_MEMO_MAX = 256


def _stat_key(path: str) -> tuple[str, int, int] | tuple[str]:
    """Memo key (path, size, mtime_ns); a stat failure keys by path only."""
    try:
        st = os.stat(path)
    except OSError:
        return (path,)
    return (path, st.st_size, st.st_mtime_ns)

Kind = Literal["ok", "clean_failure", "budget_breach", "killed_by_governor"]
HealthFn = Callable[[HealthKind, str | None, dict[str, float]], None]


class GovernedSaveParser:
    """Runs ``sae-parse`` under Governor control; returns None on skip/failure, never raises."""

    def __init__(
        self,
        deps: DaemonDeps,
        cfg: SaeConfig,
        emit_health: HealthFn,
        next_boundary: Callable[[], GameDate | None],
        current_date: Callable[[], GameDate | None],
    ) -> None:
        self._deps = deps
        self._cfg = cfg
        self._health = emit_health
        self._boundary = next_boundary
        self._now = current_date
        self._decision: GovernorDecision | None = None
        self._eligible = 0
        self._lock = asyncio.Lock()
        self._handle: WorkerHandle | None = None
        self._killed = False
        self._unstable: dict[tuple[Any, ...], int] = {}
        self._key: tuple[Any, ...] = ()
        self.last_cost: Cost | None = None  # cost of the latest finished worker (ok or not)

    def set_decision(self, d: GovernorDecision) -> None:
        """Latest decision (the loop calls this each poll)."""
        self._decision = d

    def kill_current(self) -> None:
        """Kill the running worker (decision.kill_worker); outcome becomes killed_by_governor."""
        if self._handle is not None:
            self._killed = True
            self._handle.kill()

    def _skip(self, why: str) -> None:
        self._health(HealthKind.SAVE_SKIPPED, why, {})

    def _in_window(self) -> bool:
        b, c = self._boundary(), self._now()
        if b is None or c is None:
            return False
        return 0 <= mi_of(b) - mi_of(c) <= self._cfg.governor.h2_parse_window_months

    def _gate(self, d: GovernorDecision | None) -> str | None:
        if d is None:
            return "no_decision"
        if d.parse_every_n == 0:
            return "parse_disabled"
        if d.parse_window_only and not self._in_window():
            return "outside_window"
        self._eligible += 1
        if self._eligible % d.parse_every_n != 0:
            return "every_n"
        return None

    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        """Return the ParseResult, or None if skipped or failed (reported as health).

        ``sections`` becomes ``sae-parse --sections a,b`` (C4 section 1; C6 v1.2 section 1.10). A section-limited
        probe (e.g. the Ironman ``("meta",)`` check) is cheap: it bypasses the every-n / H2-window gate and does
        not advance the every-n counter; busy, retry-cap and ``parse_every_n == 0`` still apply.
        """
        try:
            return await self._parse(save_path, sections)
        except Exception:
            log.exception("save parser internal error")
            self._health(HealthKind.PARSE_FAILED, "internal_error", {})
            return None

    async def _parse(self, save_path: str, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        if self._lock.locked():  # busy skips must not advance the every_n counter
            self._skip("busy")
            return None
        self._key = _stat_key(save_path)
        if self._unstable.get(self._key, 0) >= UNSTABLE_RETRY_CAP:
            self._skip("retry_cap")
            return None
        d = self._decision
        if sections is not None and d is not None and d.parse_every_n > 0:
            why: str | None = None
        else:
            why = self._gate(d)
        if why is not None or d is None:
            self._skip(why or "no_decision")
            return None
        async with self._lock:
            self._killed = False
            spec = WorkerSpec(
                exe=self._deps.parser_exe,
                args=("parse", "--save", save_path,
                      *(("--sections", ",".join(sections)) if sections is not None else ()),
                      "--threads", str(max(1, d.worker_threads)), "--background"),
                mem_cap_bytes=max(1, d.worker_mem_cap_bytes),
                timeout_s=self._cfg.governor.parse_timeout_s,
                threads=max(1, d.worker_threads),
            )
            handle = await self._deps.platform.spawn_worker(spec)
            self._handle = handle
            try:
                return await self._run(handle, spec.timeout_s)
            except BaseException:
                handle.kill()  # cancellation or crash must never orphan the worker (C7 section 4.6)
                raise
            finally:
                self._handle = None

    async def _run(self, h: WorkerHandle, timeout_s: float) -> ParseResult | None:
        try:
            code, out, _err = await asyncio.wait_for(h.wait(), timeout_s)
        except TimeoutError:
            h.kill()
            return self._fail("budget_breach", None, None, HealthKind.BUDGET_BREACH, "timeout", {})
        sizes = {"stdout_bytes": float(len(out))}
        if self._killed:
            return self._fail("killed_by_governor", code, None, HealthKind.WORKER_KILLED, f"exit={code}", sizes)
        if len(out) > MAX_STDOUT_BYTES:
            return self._fail("budget_breach", code, None, HealthKind.BUDGET_BREACH, f"oversize exit={code}", sizes)
        try:
            parsed = PARSE_OUTPUT_ADAPTER.validate_json(out)
        except ValidationError:
            parsed = None
        if code == EXIT_OK and isinstance(parsed, ParseResult):
            self._unstable.pop(self._key, None)
            self._finish("ok", code, parsed.cost, None, None, sizes)
            return parsed
        if code in CLEAN_FAILURE_CODES and isinstance(parsed, ParseError):
            if parsed.error == "unstable":
                if len(self._unstable) >= _UNSTABLE_MEMO_MAX:
                    self._unstable.pop(next(iter(self._unstable)))
                self._unstable[self._key] = self._unstable.get(self._key, 0) + 1
            return self._fail(
                "clean_failure", code, parsed.cost, HealthKind.PARSE_FAILED, f"exit={code} kind={parsed.error}", sizes
            )
        return self._fail("budget_breach", code, None, HealthKind.BUDGET_BREACH, f"exit={code} bad_output", sizes)

    def _fail(self, *a: Any) -> ParseResult | None:
        """Finish with a failure outcome; always None."""
        self._finish(*a)
        return None

    def _finish(
        self,
        kind: Kind,
        code: int | None,
        cost: Cost | None,
        health: HealthKind | None,
        detail: str | None,
        metrics: dict[str, float],
    ) -> None:
        if cost is not None:
            self.last_cost = cost  # published in the monthly health (C5 parse_* keys)
        self._deps.governor.on_worker(WorkerOutcome(kind=kind, exit_code=code, cost=cost, detail=detail))
        if health is not None:
            self._health(health, detail, metrics)

