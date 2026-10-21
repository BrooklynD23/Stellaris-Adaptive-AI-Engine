"""Campaign summaries: exact features, reloads, fog guard."""

from __future__ import annotations

import builtins
import io
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.common import Archetype
from sae.profile.index import CampaignState
from sae.profile.summary import build_summary

from ._fixtures import T, live_key, make_run, month, posterior, run_id

K = live_key(1)


def state(rids: tuple[str, ...], start: str, last: str, key: str = K) -> CampaignState:
    return CampaignState("c-" + "0" * 16, key, start, last, rids, ("v4.4.4",), ("ab" * 32,), "closed", None, None, 0)


KNOWN = [
    month("2200.01.01", 5, False, (0, 1, 2, 1, 2)),
    month("2201.01.01", 9, False, (2, 1, 0, 1, 4)),
    month("2202.01.01", 11, True, (1, 2, 0, 0, 0)),
    month("2231.01.01", 20, False, (0, 0, 0, 1, 1)),
    month("2232.01.01", 24, False, (0, 0, 0, 0, 0)),
    month("2233.01.01", 30, False, (0, 0, 0, 0, 0)),
]


def test_known_fixture_exact_values(tmp_path: Path) -> None:
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), K, KNOWN, posterior(Archetype.TURTLE, 0.7))
    s = build_summary(state((run_id(1),), "2200.01.01", "2233.01.01"), tmp_path)
    assert s is not None
    f = {k: (v.value, v.n_obs) for k, v in s.features.items()}
    assert f["expansion_rate_early"] == (3.0, 3)
    assert f["expansion_rate_late"] == (5.0, 3)
    border = f["border_fort_ratio"]
    assert border[0] == pytest.approx((0.5 + 0.25 + 1.0) / 3) and border[1] == 3
    assert f["first_war_year"] == (2.0, 6)
    assert f["war_share"] == (pytest.approx(1 / 6), 6)
    assert f["fleet_power_cat_mean"] == (pytest.approx(0.5), 6)
    assert f["tech_cat_mean"] == (pytest.approx(4 / 6), 6)
    assert f["economy_cat_mean"] == (pytest.approx(2 / 6), 6)
    assert s.final_belief is not None and s.final_belief[Archetype.TURTLE] == pytest.approx(0.7)
    assert (s.start_date, s.last_date, s.builder_version) == ("2200.01.01", "2233.01.01", "0.1.0")


def test_one_sample_per_month_last_wins_and_short_span_is_none(tmp_path: Path) -> None:
    months = [month("2200.01.01", 5), month("2200.01.15", 50), month("2200.06.01", 7)]
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), K, months)
    s = build_summary(state((run_id(1),), "2200.01.01", "2200.06.01"), tmp_path)
    assert s is not None
    assert s.features["expansion_rate_early"].value is None  # span 5 months < 1 year
    assert s.features["expansion_rate_early"].n_obs == 2  # the 15th replaced the 1st
    assert s.features["first_war_year"].value is None and s.features["war_share"].value == 0.0
    assert s.features["expansion_rate_late"].value is None and s.features["expansion_rate_late"].n_obs == 0


def test_reload_drops_abandoned_months_and_beliefs(tmp_path: Path) -> None:
    a = [month("2200.01.01", 5), month("2201.01.01", 9), month("2202.01.01", 50), month("2203.01.01", 60)]
    turtle, balanced = posterior(Archetype.TURTLE), posterior(Archetype.BALANCED)
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), K, a, beliefs=[turtle, turtle, balanced, balanced])
    make_run(tmp_path, run_id(2), "live", T(2026, 10, 2), K, [month("2201.06.01", 10), month("2202.06.01", 12)])
    s = build_summary(state((run_id(2), run_id(1)), "2200.01.01", "2203.01.01"), tmp_path)
    assert s is not None
    early = s.features["expansion_rate_early"]
    assert early.n_obs == 4 and early.value == pytest.approx(7 / (29 / 12))  # 2200.01 .. 2202.06, no 50/60
    assert s.final_belief is not None and s.final_belief[Archetype.TURTLE] == pytest.approx(0.7)


def test_in_contact_false_is_ignored(tmp_path: Path) -> None:
    hidden = month("2201.01.01", 99, True, (2, 2, 2, 9, 9), in_contact=False)
    months = [month("2200.01.01", 5), hidden, month("2202.01.01", 9)]
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), K, months)
    s = build_summary(state((run_id(1),), "2200.01.01", "2202.01.01"), tmp_path)
    assert s is not None
    assert s.features["expansion_rate_early"].value == pytest.approx(2.0)
    assert s.features["war_share"].value == 0.0 and s.features["war_share"].n_obs == 2
    assert s.features["fleet_power_cat_mean"].n_obs == 2


def test_other_folder_missing_run_and_beliefless_cases(tmp_path: Path) -> None:
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), live_key(2), [month("2200.01.01")])
    assert build_summary(state((run_id(1),), "2200.01.01", "2200.01.01"), tmp_path) is None  # other folder
    assert build_summary(state((run_id(9),), "2200.01.01", "2200.01.01"), tmp_path) is None  # no bus file
    (tmp_path / run_id(8)).mkdir()
    (tmp_path / run_id(8) / "bus.jsonl").write_text("junk\n")
    assert build_summary(state((run_id(8),), "2200.01.01", "2200.01.01"), tmp_path) is None  # no run_started
    make_run(tmp_path, run_id(2), "live", T(2026, 10, 1), K, [month("2200.01.01")])
    s = build_summary(state((run_id(2),), "2200.01.01", "2200.01.01"), tmp_path)
    assert s is not None and s.final_belief is None


def test_belief_after_foreign_observation_not_attached(tmp_path: Path) -> None:
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), None, [month("2200.01.01"), month("2200.02.01")],
             posterior(), keys=[K, live_key(7)])
    s = build_summary(state((run_id(1),), "2200.01.01", "2200.02.01"), tmp_path)
    assert s is not None and s.features["war_share"].n_obs == 1
    assert s.final_belief is not None  # from the kept first observation only


def test_bad_posterior_is_ignored(tmp_path: Path) -> None:
    bad = {a: 0.1 for a in Archetype}
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), K, [month("2200.01.01")], bad)
    s = build_summary(state((run_id(1),), "2200.01.01", "2200.01.01"), tmp_path)
    assert s is not None and s.final_belief is None


def test_bus_god_is_never_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = (run_id(1), run_id(2))
    make_run(tmp_path, ids[0], "live", T(2026, 10, 1), K, KNOWN[:3], posterior())
    make_run(tmp_path, ids[1], "live", T(2026, 10, 2), K, KNOWN[3:], posterior())
    for rid in ids:
        god = tmp_path / rid / "bus.god.jsonl"
        assert god.exists() and god.stat().st_size > 1

    def guard(real: Any) -> Any:
        def wrapper(*a: Any, **k: Any) -> Any:
            if a and "bus.god" in str(a[0]):
                raise AssertionError(f"opened {a[0]}")
            return real(*a, **k)

        return wrapper

    real_path_open = Path.open

    def path_open(self: Path, *a: Any, **k: Any) -> Any:
        if "bus.god" in str(self):
            raise AssertionError(f"opened {self}")
        return real_path_open(self, *a, **k)

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    monkeypatch.setattr(Path, "open", path_open)
    s = build_summary(state(ids, "2200.01.01", "2233.01.01"), tmp_path)
    assert s is not None
