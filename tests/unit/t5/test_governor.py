"""Governor unit tests: C7 section 3 rows, section 4 rules and the CI-3 traces (pure, seeded, no sleeps)."""

from __future__ import annotations

import pytest

from sae.contracts.c7_governor import (
    GIB,
    MIB,
    GameState,
    Governor,
    GovernorConfig,
    GovernorDecision,
    HealthLevel,
    SensorSample,
    SpmSample,
    TelemetryTier,
    Trigger,
    WorkerOutcome,
)
from sae.governor import make_governor
from sae.governor.core import ResourceGovernor

H0, H1, H2, H3, S = HealthLevel.H0, HealthLevel.H1, HealthLevel.H2, HealthLevel.H3, HealthLevel.S
RAM_OK = 4 * GIB


class Rig:
    """Drives a governor with a simulated monotonic clock (2 s per poll)."""

    def __init__(self, gov: Governor | None = None, cfg: GovernorConfig | None = None, **kw: object) -> None:
        self.cfg = cfg or GovernorConfig()
        self.gov = gov or make_governor(self.cfg, cpu_count=8, **kw)  # type: ignore[arg-type]
        self.t = 0.0
        self.date = "2200.01.01"

    def poll(self, ram: int = RAM_OK, rate: float | None = None, *, running: bool = True, mini: float = 0.0,
             rss: int = 50 * MIB, dt: float = 2.0) -> GovernorDecision:
        self.t += dt
        return self.gov.poll(SensorSample(t_mono_s=self.t, available_ram_bytes=ram, hard_fault_rate=rate,
                                          game_running=running, game_minimized_s=mini, daemon_rss_bytes=rss,
                                          daemon_cpu_pct=0.5))

    def month(self, wall_s: float = 20.0, k: int = 1) -> None:
        self.gov.on_month(SpmSample(game_date=self.date, wall_s=wall_s, months=k))

    def months(self, n: int, ram: int = RAM_OK, wall_s: float = 20.0) -> list[GovernorDecision]:
        out = []
        for _ in range(n):
            self.month(wall_s)
            out.append(self.poll(ram))
        return out


def test_factory_defaults_and_protocol() -> None:
    g = make_governor(GovernorConfig())
    assert isinstance(g, ResourceGovernor)
    d = Rig(g).poll()
    assert (d.state, d.level, d.parse_every_n, d.telemetry_tier) == (GameState.PLAYING, H0, 1, TelemetryTier.T1)
    assert d.trigger is None and not d.kill_worker and d.knob_request is None and d.worker_threads == 1


def test_s_not_allowed_as_min_level() -> None:
    with pytest.raises(ValueError):
        make_governor(GovernorConfig(), "S")


@pytest.mark.parametrize(("ram", "lvl", "n", "win", "tier"), [
    (RAM_OK, H0, 1, False, TelemetryTier.T1),
    (int(1.9 * GIB), H1, 2, False, TelemetryTier.T1),
    (int(1.4 * GIB), H2, 1, True, TelemetryTier.T0),
    (int(0.9 * GIB), H3, 0, False, TelemetryTier.T0),
])
def test_ladder_rows_jump_same_poll(ram: int, lvl: HealthLevel, n: int, win: bool, tier: TelemetryTier) -> None:
    d = Rig().poll(ram)
    assert (d.level, d.parse_every_n, d.parse_window_only, d.telemetry_tier) == (lvl, n, win, tier)
    assert d.trigger == (None if lvl is H0 else Trigger.RAM)
    assert d.kill_worker == (lvl in (H2, H3))
    assert (d.knob_request is not None) == (lvl is H3)
    if lvl is H3:
        assert d.knob_request is not None and d.knob_request.emit_period == 12


def test_h1_becomes_every_third_after_six_months() -> None:
    r = Rig()
    assert r.poll(int(1.9 * GIB)).parse_every_n == 2
    ds = r.months(6, int(1.9 * GIB))
    assert [d.parse_every_n for d in ds] == [2] * 5 + [3]


def test_dev_preset_tier_t2() -> None:
    assert Rig(preset="dev").poll().telemetry_tier is TelemetryTier.T2
    assert Rig(preset="dev").poll(int(1.9 * GIB)).telemetry_tier is TelemetryTier.T1


def test_min_level_floor_and_preset_trigger() -> None:
    r = Rig(min_level="H2")
    d = r.poll()
    assert (d.level, d.trigger, d.kill_worker, d.parse_window_only) == (H2, Trigger.PRESET, True, True)
    assert r.poll().trigger is None and not r.poll().kill_worker
    d3 = Rig(min_level="H3").poll()
    assert d3.level is H3 and d3.knob_request is not None and d3.knob_request.emit_period == 12


def test_floor_never_lowers_and_deescalation_stops_at_floor() -> None:
    r = Rig(min_level="H1")
    r.poll(int(1.4 * GIB))
    last = r.months(40)[-1]
    assert last.level is H1


def test_forced_level() -> None:
    r = Rig(cfg=GovernorConfig(forced_level=H1))
    assert r.poll(int(0.9 * GIB)).level is H1


def test_ram_ramp_reaches_h2_on_crossing_poll_with_single_kill() -> None:
    r = Rig()
    ram_series = [int(4 * GIB - i * (4 * GIB - 1.4 * GIB) / 15) for i in range(16)]
    kills, first_h2 = 0, None
    for i, ram in enumerate(ram_series):
        d = r.poll(ram)
        kills += d.kill_worker
        if ram < r.cfg.h2_ram_bytes and first_h2 is None:
            first_h2 = i
            assert d.level is H2 and d.kill_worker and d.trigger is Trigger.RAM  # same poll
    assert first_h2 is not None and kills == 1


def test_ram_ramp_to_h3_kills_on_each_entry_from_below() -> None:
    r = Rig()
    ds = [r.poll(x) for x in (int(1.4 * GIB), int(0.9 * GIB), int(0.9 * GIB))]
    assert [d.kill_worker for d in ds] == [True, True, False]


def _baseline(r: Rig, rate: float = 5.0) -> None:
    while r.t < r.cfg.fault_baseline_s:  # first poll is at t=2, baseline ends at 602
        assert r.poll(rate=rate).level is H0


def test_fault_spike_after_baseline_h2_after_exactly_three_polls() -> None:
    r = Rig()
    _baseline(r)
    ds = [r.poll(rate=100.0) for _ in range(3)]
    assert [d.level for d in ds] == [H0, H0, H2]
    assert ds[2].trigger is Trigger.FAULTS and ds[2].kill_worker


def test_fault_none_resets_consecutive_count() -> None:
    r = Rig()
    _baseline(r)
    levels = [r.poll(rate=x).level for x in (100.0, 100.0, None, 100.0, 100.0)]
    assert set(levels) == {H0}
    assert r.poll(rate=100.0).level is H2


def test_fault_inactive_during_baseline_and_without_rates() -> None:
    r = Rig()
    assert all(r.poll(rate=1e6).level is H0 for _ in range(20))  # still inside the baseline window
    r2 = Rig()
    for _ in range(400):
        assert r2.poll(rate=None).level is H0  # no baseline data at all, probe unavailable


def test_fault_trigger_is_median_plus_k_mad_with_no_floor() -> None:
    r = Rig()
    i = 0
    while r.t < 600:  # alternating 0/10: median 5, MAD 5, threshold 5 + 3*5 = 20
        r.poll(rate=(0.0, 10.0)[i % 2])
        i += 1
    assert [r.poll(rate=19.0).level for _ in range(5)] == [H0] * 5
    assert [r.poll(rate=21.0).level for _ in range(3)] == [H0, H0, H2]
    z = Rig()
    _baseline(z, 0.0)  # zero-MAD baseline: contract-exact, any positive rate counts
    assert [z.poll(rate=1.0).level for _ in range(3)] == [H0, H0, H2]


def test_budget_breach_escalates_to_h2_on_next_poll() -> None:
    r = Rig()
    r.gov.on_worker(WorkerOutcome(kind="ok", exit_code=0, cost=None))
    assert r.poll().level is H0
    r.gov.on_worker(WorkerOutcome(kind="budget_breach", exit_code=134, cost=None))
    d = r.poll()
    assert (d.level, d.trigger, d.kill_worker) == (H2, Trigger.BUDGET_BREACH, True)
    assert d.trigger is not Trigger.RAM


def test_sustained_h2_goes_h3_then_no_flapping_at_steady_1_6_gib() -> None:
    r = Rig(emit_period=2)
    assert r.poll(int(1.4 * GIB)).level is H2
    ds = r.months(6, int(1.4 * GIB))
    assert [d.level for d in ds] == [H2] * 5 + [H3]
    assert ds[5].trigger is Trigger.SUSTAINED_H2 and ds[5].kill_worker
    assert ds[5].knob_request is not None and ds[5].knob_request.emit_period == 12
    after = r.months(60, int(1.6 * GIB))
    levels = [d.level for d in after]
    assert levels[:5] == [H3] * 5 and levels[5] is H2 and set(levels[5:]) == {H2}
    assert after[5].trigger is Trigger.DEESCALATE
    knobs = [d.knob_request for d in after if d.knob_request is not None]
    assert len(knobs) == 1 and knobs[0].emit_period == 2


def test_h2_months_without_h2_trigger_do_not_count() -> None:
    r = Rig()
    r.poll(int(1.4 * GIB))
    for _ in range(10):  # alternate a clean month (RAM ok at H2 entry margin) -> reset each time
        r.months(3, int(1.4 * GIB))
        r.months(2, int(1.7 * GIB))  # second month has no H2 poll at all
    assert r.poll(int(1.7 * GIB)).level is H2


def test_knob_on_h3_entry_and_exit_exactly_once() -> None:
    r = Rig(emit_period=3)
    ds = [r.poll(int(0.9 * GIB)) for _ in range(3)]
    assert [d.knob_request is not None for d in ds] == [True, False, False]
    out = r.months(6, RAM_OK)
    exits = [d for d in out if d.knob_request is not None]
    assert len(exits) == 1 and exits[0].knob_request is not None and exits[0].knob_request.emit_period == 3


def test_deescalation_needs_six_clean_months_and_margin() -> None:
    r = Rig()
    assert r.poll(int(1.9 * GIB)).level is H1
    assert {d.level for d in r.months(30, int(2.2 * GIB))} == {H1}  # inside the 0.5 GiB margin
    ds = r.months(6, RAM_OK)
    assert [d.level for d in ds] == [H1] * 5 + [H0]
    assert ds[5].trigger is Trigger.DEESCALATE


def test_one_level_down_per_six_months() -> None:
    r = Rig()
    r.poll(int(0.9 * GIB))
    lv = [d.level for d in r.months(18, RAM_OK)]
    assert lv == [H3] * 5 + [H2] * 6 + [H1] * 6 + [H0]


def test_suspend_latches_kill_and_knob_until_resume() -> None:
    r = Rig()
    r.gov.suspend(True)
    d = r.poll(int(0.9 * GIB))
    assert (d.level, d.trigger, d.kill_worker, d.knob_request) == (S, Trigger.OPERATOR, False, None)
    assert d.parse_every_n == 0  # parse fields of the underlying level
    assert r.poll(int(0.9 * GIB)).trigger is None
    r.gov.suspend(False)
    d = r.poll(int(0.9 * GIB))
    assert (d.level, d.trigger, d.kill_worker) == (H3, Trigger.OPERATOR, True)
    assert d.knob_request is not None and d.knob_request.emit_period == 12
    d2 = r.poll(int(0.9 * GIB))
    assert not d2.kill_worker and d2.knob_request is None


def test_closed_threads_and_memory_cap() -> None:
    cfg = GovernorConfig()
    for cpus, thr in ((8, 6), (4, 2), (3, 1), (2, 1), (1, 1)):
        r = Rig(make_governor(cfg, cpu_count=cpus))
        d = r.poll(running=False)
        cap = cfg.worker_mem_cap_closed_bytes
        assert (d.state, d.worker_threads, d.worker_mem_cap_bytes) == (GameState.CLOSED, thr, cap)
    d = Rig().poll()
    assert (d.state, d.worker_threads, d.worker_mem_cap_bytes) == (GameState.PLAYING, 1, cfg.worker_mem_cap_bytes)


def test_closed_when_minimized_long_enough() -> None:
    r = Rig()
    assert r.poll(mini=599.0).state is GameState.PLAYING
    assert r.poll(mini=600.0).state is GameState.CLOSED


def test_pause_detection_behaves_as_playing() -> None:
    cfg = GovernorConfig(pause_detection=True, paused_cpu_max_pct=1.0)
    assert Rig(cfg=cfg).poll().state is GameState.PLAYING  # UNVERIFIED A-25 until M1-P


def test_daemon_should_exit_above_300_mib() -> None:
    r = Rig()
    assert not r.poll(rss=300 * MIB).daemon_should_exit
    assert r.poll(rss=300 * MIB + 1).daemon_should_exit


def test_closed_months_from_wall_time() -> None:
    r = Rig()
    r.poll(running=False)
    for _ in range(15):
        r.poll(running=False)  # 30 s of closed wall time = 1 month at the 30 s default
    assert isinstance(r.gov, ResourceGovernor) and r.gov.game_months == pytest.approx(1.0)


def test_clock_jump_backward_and_forward_add_nothing() -> None:
    r = Rig()
    r.t = 5000.0
    r.poll(running=False)
    r.poll(running=False, dt=-500.0)
    r.poll(running=False, dt=1000.0)
    r.poll(running=False, dt=60.001)
    assert isinstance(r.gov, ResourceGovernor) and r.gov.game_months == 0.0
    r.poll(running=False, dt=60.0)
    assert r.gov.game_months == pytest.approx(2.0)


def test_ci3_drifting_spm_never_above_h1() -> None:
    r = Rig()
    worst = H0
    for i in range(120):
        r.month(20.0 * 1.01**i)
        d = r.poll()
        worst = max(worst, d.level, key=lambda x: (H0, H1, H2, H3).index(x))
    assert worst in (H0, H1)


def test_ci3_spm_ratio_escalates_to_h1_only() -> None:
    r = Rig()
    r.months(18, wall_s=20.0)
    levels = []
    for _ in range(6):  # fast drift: short median up 30 %
        r.month(20.0 * 1.3)
        levels.append(r.poll().level)
    assert levels[-1] is H1 and H2 not in levels and H3 not in levels
    assert r.poll().trigger is None


def test_ci3_step_clears_windows_and_ratio_needs_24_new_samples() -> None:
    r = Rig()
    r.months(24, wall_s=20.0)
    r.month(30.0)  # +50 %: step reset clears the window (this sample is sample 1)
    for _ in range(17):
        r.month(30.0)  # 18 samples
    for i in range(5):
        r.month(40.0)  # 23 samples: short median 40 vs long 30 would trigger if allowed
        assert r.poll().level is H0, i
    r.month(40.0)  # 24th sample: ratio now defined and 1.33
    d = r.poll()
    assert (d.level, d.trigger) == (H1, Trigger.SPM)


def test_spm_step_skipped_when_either_sample_tiny() -> None:
    r = Rig()
    r.months(23, wall_s=20.0)
    r.month(2.0)  # tiny: step test skipped (prev >= 10 s but new < 10 s), window kept and extended to 24
    for _ in range(5):
        r.month(2.0)
    assert r.poll().level is H0


def test_spm_escalation_rate_limited_between_escalations() -> None:
    gap = 30
    r = Rig(cfg=GovernorConfig(spm_min_months_between_escalations=gap))
    gov = r.gov
    assert isinstance(gov, ResourceGovernor)
    r.months(18, wall_s=20.0)
    w = 20.0
    for _ in range(6):
        w *= 1.04
        r.month(w)
        d = r.poll()
    assert d.level is H1 and d.trigger is Trigger.SPM
    esc = gov.game_months
    for _ in range(60):  # flat: ratio falls below 1.05, then 6 clean months
        if d.level is H0:
            break
        r.month(w)
        d = r.poll()
    assert d.level is H0 and d.trigger is Trigger.DEESCALATE
    saw_ratio_while_limited = False
    while gov.game_months < esc + gap:
        w *= 1.12
        r.month(w)
        d = r.poll()
        ratio = gov._ratio()
        if gov.game_months < esc + gap and ratio is not None and ratio >= 1.10:
            saw_ratio_while_limited = True
            assert d.level is H0
    assert saw_ratio_while_limited
    for _ in range(3):
        w *= 1.12
        r.month(w)
        d = r.poll()
    assert d.level is H1


def test_closed_months_ignore_floored_backlog_samples() -> None:
    r = Rig()
    r.poll(int(0.9 * GIB), running=False)  # H3
    for _ in range(30):
        r.gov.on_month(SpmSample(game_date="2200.01.01", wall_s=0.001, months=1))
    assert isinstance(r.gov, ResourceGovernor)
    before = r.gov.game_months
    ds = [r.poll(int(0.9 * GIB), running=False) for _ in range(30)]  # 60 s
    assert r.gov.game_months - before <= 6.1
    assert {d.level for d in ds} == {H3}


def test_sustained_h2_resets_per_clean_month_not_per_poll() -> None:
    r = Rig()
    r.poll(int(1.4 * GIB))
    last = H2
    for _ in range(6):
        r.poll(int(1.7 * GIB))  # clean poll inside the month
        r.month()
        last = r.poll(int(1.4 * GIB)).level  # h2 trigger seen this month
    assert last is H3


def test_set_next_boundary_is_accepted() -> None:
    r = Rig()
    r.gov.set_next_boundary("2205.01.01")
    r.gov.set_next_boundary(None)
    assert r.poll().level is H0


def test_game_date_jump_is_a_discontinuity_not_play_time() -> None:
    r = Rig()
    assert r.poll(int(1.4 * GIB)).level is H2
    r.months(6, RAM_OK, wall_s=30.0)
    r.month(0.001, 1000)  # T4 sends months=k for a save load, wall_s floored
    assert r.poll(RAM_OK).level is not H3
    for _ in range(6):
        r.month(30.0)
        assert r.poll(RAM_OK).trigger is not Trigger.SPM
    assert r.poll(RAM_OK).level is not H3


def test_jump_does_not_poison_spm_window() -> None:
    r = Rig()
    r.months(24, wall_s=30.0)
    r.month(0.001, 1000)
    for _ in range(6):
        r.month(30.0)
        assert r.poll().level is H0


def test_fault_baseline_freezes_on_wall_time_when_dt_is_clamped() -> None:
    r = Rig()
    for _ in range(4):  # 1 h apart: every dt is clamped to 0, wall time still passes
        assert r.poll(rate=0.0, dt=3600.0).level is H0
    assert [r.poll(rate=5.0, dt=3600.0).level for _ in range(3)] == [H0, H0, H2]


def test_fault_baseline_needs_three_samples_when_clamped() -> None:
    r = Rig()
    levels = [r.poll(rate=100.0, dt=3600.0).level for _ in range(3)]  # all still baseline
    assert levels == [H0, H0, H0]


def test_spm_medians_exposed_for_monthly_health() -> None:
    gov = make_governor(GovernorConfig())
    assert gov.spm_med6 is None and gov.spm_med24 is None
    for i, w in enumerate([20.0] * 18 + [26.0] * 6):
        gov.on_month(SpmSample(game_date=f"2200.{i % 12 + 1:02d}.01", wall_s=w))
    assert gov.spm_med6 == 26.0
    assert gov.spm_med24 == 20.0
