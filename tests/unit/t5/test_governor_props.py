"""Hypothesis properties for the Governor (derandomized, no wall clock)."""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sae.contracts.c7_governor import GIB, GovernorConfig, GovernorDecision, HealthLevel, SensorSample, SpmSample
from sae.governor import make_governor
from sae.governor.core import ResourceGovernor

ORDER = (HealthLevel.H0, HealthLevel.H1, HealthLevel.H2, HealthLevel.H3)
RAMS = (4 * GIB, int(2.2 * GIB), int(1.9 * GIB), int(1.6 * GIB), int(1.4 * GIB), int(0.9 * GIB))

event = st.one_of(
    st.tuples(st.just("poll"), st.integers(0, len(RAMS) - 1), st.one_of(st.none(), st.integers(0, 200)),
              st.booleans()),
    st.tuples(st.just("month"), st.floats(1.0, 120.0), st.integers(1, 3), st.just(True)),
    st.tuples(st.just("breach"), st.just(0), st.none(), st.just(True)),
    st.tuples(st.just("suspend"), st.just(0), st.none(), st.booleans()),
)


@settings(derandomize=True, max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(events=st.lists(event, max_size=300), floor=st.sampled_from(("H0", "H1", "H2", "H3")))
def test_random_traces_hold_invariants(events: list[tuple[str, Any, Any, bool]], floor: str) -> None:
    cfg = GovernorConfig(fault_baseline_s=20.0)
    gov = make_governor(cfg, floor, cpu_count=4)
    assert isinstance(gov, ResourceGovernor)
    floor_i = ORDER.index(HealthLevel(floor))
    t, suspended = 0.0, False
    prev: int | None = None
    inc_at = 0.0
    for kind, a, b, c in events:
        if kind == "month":
            gov.on_month(SpmSample(game_date="2200.01.01", wall_s=float(a), months=int(b)))
        elif kind == "breach":
            from sae.contracts.c7_governor import WorkerOutcome

            gov.on_worker(WorkerOutcome(kind="budget_breach", exit_code=134, cost=None))
        elif kind == "suspend":
            suspended = c
            prev = None  # a hidden level change during S must not trip the oscillation check
            gov.suspend(c)
        else:
            t += 2.0
            d = gov.poll(SensorSample(
                t_mono_s=t, available_ram_bytes=RAMS[int(a)], hard_fault_rate=None if b is None else float(b),
                game_running=c, daemon_rss_bytes=1, daemon_cpu_pct=0.0))
            GovernorDecision.model_validate(d.model_dump())
            assert (d.level is HealthLevel.S) == suspended
            if d.level is HealthLevel.S:
                continue
            cur = ORDER.index(d.level)
            assert cur >= floor_i
            if prev is not None and cur > prev:
                inc_at = gov.game_months
            if prev is not None and cur < prev:
                assert gov.game_months - inc_at >= cfg.deescalate_after_months
            prev = cur
