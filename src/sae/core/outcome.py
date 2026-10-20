"""Outcome of a commitment window from fog-legal ManagedState deltas (T4 plan U4; every weight is HYP).

Metrics read only managed AIs' own state (never player data). ``reward = tanh(sum w_i * m_i)``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from pydantic import Field

from sae.contracts.c5_bus import OutcomePayload
from sae.contracts.c6_adapter import ManagedState
from sae.contracts.common import CLAUSEWITZ_INT_MAX, Frozen, Sid
from sae.core.dates import date_of
from sae.core.strategy import WindowClosed

#: HYP: reward weights per averaged delta; calibrated in M2.
WEIGHTS: dict[str, float] = {"d_fleet_share": 1.0, "d_systems_owned": 0.1, "d_alloys_income": 0.01}


class AiMetrics(Frozen):
    """One managed AI's own numbers at a point in time (None = unobserved)."""

    sid: Sid
    fleet_share: float | None = Field(default=None, description="fleet_power / naval_cap.")
    systems_owned: int | None = None
    alloys_income: float | None = None


class WindowSnapshot(Frozen):
    """Metrics of the managed AIs in contact, taken when a window opens or closes."""

    ais: tuple[AiMetrics, ...] = ()


def _clamp_int(v: int | None) -> int | None:
    return None if v is None else max(-CLAUSEWITZ_INT_MAX, min(CLAUSEWITZ_INT_MAX, v))


def _clamp_float(v: float | None) -> float | None:
    """Clamp untrusted numbers (huge ints, inf, nan) so arithmetic downstream never raises."""
    if v is None:
        return None
    try:
        f = float(v)
    except OverflowError:
        f = math.inf if v > 0 else -math.inf
    if math.isnan(f):
        return None
    return max(-float(CLAUSEWITZ_INT_MAX), min(float(CLAUSEWITZ_INT_MAX), f))


def _share(power: float | None, cap: float | None) -> float | None:
    p, c = _clamp_float(power), _clamp_float(cap)
    return p / c if p is not None and c else None


def snapshot(managed: Iterable[ManagedState], in_contact: frozenset[Sid]) -> WindowSnapshot:
    """Snapshot managed AIs that are in contact (ascending sid)."""
    out = []
    for m in sorted(managed, key=lambda x: x.sid):
        if m.sid not in in_contact:
            continue
        share = _share(m.fleet_power, m.naval_cap)
        out.append(AiMetrics(sid=m.sid, fleet_share=share, systems_owned=_clamp_int(m.systems_owned),
                             alloys_income=_clamp_float(m.alloys_income)))
    return WindowSnapshot(ais=tuple(out))


def _mean_delta(opened: WindowSnapshot, now: WindowSnapshot, field: str) -> float | None:
    cur = {a.sid: a for a in now.ais}
    deltas = []
    for a in opened.ais:
        b = cur.get(a.sid)
        x, y = getattr(a, field), getattr(b, field) if b is not None else None
        if x is not None and y is not None:
            deltas.append(float(y) - float(x))
    return sum(deltas) / len(deltas) if deltas else None


def build(closed: WindowClosed, opened: WindowSnapshot | None, now: WindowSnapshot,
          weights: Mapping[str, float] | None = None) -> OutcomePayload:
    """Outcome payload for a closed window. No opening snapshot -> empty metrics, reward 0.

    ``weights`` come from the active policy (C10 section 4); None = the built-in ``WEIGHTS``.
    """
    w = WEIGHTS if weights is None else weights
    metrics: dict[str, float] = {}
    if opened is not None:
        for key, fld in (("d_fleet_share", "fleet_share"), ("d_systems_owned", "systems_owned"),
                         ("d_alloys_income", "alloys_income")):
            v = _mean_delta(opened, now, fld)
            if v is not None and math.isfinite(v):
                metrics[key] = v
        metrics["n_ais"] = float(len(opened.ais))
    raw = sum(w[k] * v for k, v in metrics.items() if k in w)
    reward = max(-1.0, min(1.0, math.tanh(raw)))
    return OutcomePayload(strategy=closed.strategy, window_from=date_of(closed.from_mi),
                          window_to=date_of(closed.to_mi), forced=closed.forced, metrics=metrics, reward=reward)
