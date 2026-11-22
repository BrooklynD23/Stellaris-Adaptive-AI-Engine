"""Hypothesis properties for the Governor (derandomized, no wall clock)."""

from __future__ import annotations

import random
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sae.contracts.c7_governor import (
    GIB,
    GovernorConfig,
    GovernorDecision,
    HealthLevel,
    SensorSample,
    SpmSample,
    Trigger,
)
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
            assert gov.spm_armed is (gov.spm_ratio is not None)
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


# -- C7 v1.2 section 4.3 spm trigger on a simulated HB/poll pipeline (Wave B review item 1, proposal 034) ----------

_POLL_S = 2.0  # GovernorConfig().poll_s, so the armed floor is 10 s/month


def _spm_fires(month_s: list[float], seed: int, flush_s: float = 0.0) -> list[int]:
    """Game months at which the real governor escalated on SPM.

    HB lines land at the month boundaries plus up to ``flush_s`` of log-flush jitter; the daemon polls every
    ~2 s and sends one ``SpmSample(months=k, wall_s=since_last_hb_poll / k)`` per poll that saw k HBs (C7 4.3).
    Polls without HB are skipped: they carry no spm data and no game months.
    """
    rnd = random.Random(seed)
    gov = ResourceGovernor(GovernorConfig(), HealthLevel.H0, "balanced", cpu_count=8, emit_period=1)
    t, hbs = 0.37, []
    for s in month_s:
        t += s
        hbs.append(t + rnd.uniform(0.0, flush_s))
    hbs.sort()
    fires: list[int] = []
    i, m, t = 0, 0, 0.0
    last: float | None = None
    while i < len(hbs):
        t += _POLL_S + rnd.uniform(-0.05, 0.05)
        k = 0
        while i < len(hbs) and hbs[i] <= t:
            i, k = i + 1, k + 1
        if k == 0:
            continue
        m += k
        if last is not None:
            gov.on_month(SpmSample(game_date="2200.01.01", wall_s=(t - last) / k, months=k))
        last = t
        d = gov.poll(SensorSample(t_mono_s=t, available_ram_bytes=4 * GIB, hard_fault_rate=None, game_running=True,
                                  daemon_rss_bytes=1, daemon_cpu_pct=0.0))
        if d.trigger is Trigger.SPM:
            fires.append(m)
    return fires


SEEDS = range(8)
MONTHS = 600


@pytest.mark.parametrize("base", [11.0, 23.0, 40.0])
def test_constant_spm_with_flush_jitter_never_fires(base: float) -> None:
    assert [len(_spm_fires([base] * MONTHS, s, flush_s=5.0)) for s in SEEDS] == [0] * 8


@pytest.mark.parametrize("base", [11.0, 23.0, 40.0])
def test_short_pauses_stay_within_false_h1_budget(base: float) -> None:
    for s in SEEDS:
        rnd = random.Random(1000 + s)
        months = [base + (rnd.uniform(2.0, 10.0) if rnd.random() < 0.25 else 0.0) for _ in range(MONTHS)]
        assert len(_spm_fires(months, s, flush_s=5.0)) <= MONTHS // 100, s  # <= 1 per 100 game months


@pytest.mark.parametrize("base", [11.0, 23.0, 40.0])
def test_twenty_percent_step_detected_within_eight_months(base: float) -> None:
    for s in SEEDS:
        fires = _spm_fires([base] * 60 + [base * 1.2] * 60, s)
        assert fires and 60 < fires[0] <= 68, (s, fires)


@pytest.mark.parametrize("base", [11.0, 23.0, 40.0])
def test_twelve_percent_step_never_fires(base: float) -> None:
    # Dilution: peak ratio 1.12 / (0.75 + 0.25 * 1.12) = 1.087 < 1.10 (C7 4.3).
    assert [_spm_fires([base] * 60 + [base * 1.12] * 60, s) for s in SEEDS] == [[]] * 8
