"""Adapter health reporting: a callback type, a logging default and rate-limit helpers (T2 U1)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sae.contracts.c5_bus import HealthKind

HealthFn = Callable[[HealthKind, str | None], None]
"""Sink for adapter health events: ``(kind, detail)``. The daemon wraps it into a bus event."""

#: Rate-limit state: plain dict, created by :func:`new_gate`.
HealthGate = dict[str, set[object]]

MAX_DETAIL = 200
_log = logging.getLogger("sae.adapters")


def truncate(detail: str | None) -> str | None:
    """Cap ``detail`` at 200 characters."""
    return None if detail is None else detail[:MAX_DETAIL]


def log_health(kind: HealthKind, detail: str | None) -> None:
    """Default :data:`HealthFn`: structured log only."""
    _log.warning("adapter health kind=%s detail=%s", kind.value, truncate(detail))


def safe_health(fn: HealthFn) -> HealthFn:
    """Wrap a health callback so a raising sink can never abort the caller (a poll, a pulse)."""

    def wrapped(kind: HealthKind, detail: str | None) -> None:
        try:
            fn(kind, detail)
        except Exception:
            _log.exception("health callback failed (%s)", kind.value)

    return wrapped


def new_gate() -> HealthGate:
    """Fresh rate-limit state."""
    return {"parse": set(), "skew": set(), "reconnect": set()}


def parse_error(health: HealthFn, gate: HealthGate, month_key: object, code: str, detail: str) -> None:
    """Emit ``log_parse_error`` once per (game month, code)."""
    key = (month_key, code)
    if key not in gate["parse"]:
        gate["parse"].add(key)
        health(HealthKind.LOG_PARSE_ERROR, truncate(f"{code}: {detail}"))


def version_skew(health: HealthFn, gate: HealthGate, detail: str) -> None:
    """Emit ``version_skew`` once per session (see :func:`reset_session`)."""
    if not gate["skew"]:
        gate["skew"].add(True)
        health(HealthKind.VERSION_SKEW, truncate(detail))


def reset_session(gate: HealthGate) -> None:
    """New game session: allow ``version_skew`` again."""
    gate["skew"].clear()


def reconnect(health: HealthFn, gate: HealthGate, cause: str, detail: str | None = None) -> None:
    """Emit ``reconnect`` once per cause until :func:`recovered`."""
    if cause not in gate["reconnect"]:
        gate["reconnect"].add(cause)
        health(HealthKind.RECONNECT, truncate(detail or cause))


def recovered(gate: HealthGate) -> None:
    """The source works again: every cause may be reported anew."""
    gate["reconnect"].clear()
