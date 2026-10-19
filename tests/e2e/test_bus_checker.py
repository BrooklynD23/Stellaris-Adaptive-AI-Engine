"""Unit tests for ``_bus``: hand-made event lists so the checkers cannot pass vacuously."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from e2e import _bus

Ev = dict[str, object]


def _ev(seq: int, kind: str, did: str | None, payload: dict[str, object], date: str = "2200.01.01") -> Ev:
    return {"v": 1, "run_id": "r", "seq": seq, "t_wall": "x", "t_mono_ns": seq, "game_date": date, "type": kind,
            "decision_id": did, "fow": "none", "payload": payload}


def _month(base: int, did: str, batch_seq: int | None) -> list[Ev]:
    """One decision: obs, belief, strategy (+ emitted when ``batch_seq`` is given). Envelope seqs start at ``base``."""
    out = [
        _ev(base, "observation", did, {}),
        _ev(base + 1, "belief_update", did, {}),
        _ev(base + 2, "strategy_selected", did, {"strategy": "baseline", "switched": False}),
    ]
    if batch_seq is not None:
        batch = {"seq": batch_seq, "intents": [{"country": 2}, {"country": 3}]}
        out.append(_ev(base + 3, "intent_emitted", did, {"batch": batch, "receipt": {"ok": True, "path": "/p"}}))
    return out


def _ack(seq: int, batch_seq: int, country: int = 2, status: str = "APPLIED") -> Ev:
    return _ev(seq, "intent_ack", None, {"seq": batch_seq, "country": country, "status": status})


def _good() -> list[Ev]:
    evs: list[Ev] = []
    for i in range(12):
        evs += _month(100 * i, f"d-2200.{i + 1:02d}-00", batch_seq=1 if i == 0 else None)
    evs.append(_ack(2000, 1))
    return evs


def test_clean_chain_passes() -> None:
    assert _bus.check_chain(_good()) == []


def test_missing_belief() -> None:
    evs = [e for e in _good() if not (e["type"] == "belief_update" and e["decision_id"] == "d-2200.01-00")]
    assert any("no belief_update" in p for p in _bus.check_chain(evs))


def test_orphan_ack() -> None:
    evs = [*_good(), _ack(2001, 77)]
    assert any("orphan intent_ack" in p for p in _bus.check_chain(evs))


def test_ack_before_emit() -> None:
    evs = _good()
    evs[-1] = _ack(1, 1)  # envelope seq 1 < emit's seq 3
    assert any("ack before emit" in p for p in _bus.check_chain(evs))


def test_missing_ack() -> None:
    assert any("no ack" in p for p in _bus.check_chain(_good()[:-1]))


def test_ack_for_other_country_does_not_count() -> None:
    evs = _good()
    evs[-1] = _ack(2000, 1, country=9)
    problems = _bus.check_chain(evs)
    assert any("no ack" in p for p in problems) and any("orphan" in p for p in problems)


def test_envelope_and_batch_seq_collision() -> None:
    """The ack carries the emit's *envelope* seq (3) instead of its batch seq (1): must be flagged, not matched."""
    evs = _good()
    evs[-1] = _ack(2000, 3)
    problems = _bus.check_chain(evs)
    assert any("orphan" in p for p in problems) and any("no ack" in p for p in problems)


def test_collision_where_batch_seq_equals_other_envelope_seq_still_links() -> None:
    evs: list[Ev] = []
    for i in range(12):
        evs += _month(100 * i, f"d-2200.{i + 1:02d}-00", batch_seq=102 if i == 1 else None)  # batch == envelope seq
    evs.append(_ack(2000, 102))
    assert _bus.check_chain(evs) == []


def test_emit_without_strategy_before_it() -> None:
    evs = _good()
    evs[2], evs[3] = evs[3], evs[2]  # swap seq positions' content: emit now precedes strategy by seq
    evs[2]["seq"], evs[3]["seq"] = 2, 3
    assert any("no strategy_selected before" in p for p in _bus.check_chain(evs))


def test_zero_decisions_fail_non_vacuity() -> None:
    problems = _bus.check_chain([])
    assert any("no intent_emitted" in p for p in problems)
    assert any("0 decisions" in p for p in problems)
    assert any("no APPLIED" in p for p in problems)


def test_lost_mode_does_not_need_applied() -> None:
    evs = _good()
    evs[-1] = _ack(2000, 1, status="LOST")
    assert _bus.check_chain(evs)  # strict: no APPLIED
    assert _bus.check_chain(evs, require_applied=False) == []


def test_switches_and_final_strategy() -> None:
    a = _ev(1, "strategy_selected", "d-1", {"strategy": "outscale_siege", "switched": True}, "2201.03.01")
    b = _ev(2, "strategy_selected", "d-2", {"strategy": "outscale_siege", "switched": False}, "2201.04.01")
    assert _bus.switches([a, b]) == [(2201 * 12 + 2, "outscale_siege")]
    assert _bus.final_strategy([a, b]) == "outscale_siege"
    with pytest.raises(ValueError):
        _bus.final_strategy([])


def test_ci1_matching_is_one_to_one_and_windowed() -> None:
    a = [(10, "x"), (11, "x")]
    assert _bus.ci1_matched(a, [(10, "x")], 24) == 1  # one-to-one
    assert _bus.ci1_rate(a, [(10, "x")], 24) == pytest.approx(2 / 3)
    assert _bus.ci1_matched([(0, "x")], [(24, "x")], 24) == 0  # |d| < window is strict
    assert _bus.ci1_matched([(0, "x")], [(23, "x")], 24) == 1
    assert _bus.ci1_matched([(0, "x")], [(1, "y")], 24) == 0  # same strategy only
    assert _bus.ci1_rate([], [], 24) == 1.0


@given(st.lists(st.tuples(st.integers(0, 400), st.sampled_from("abc")), max_size=12))
def test_ci1_rate_self_is_one_and_symmetric_bounds(sw: list[tuple[int, str]]) -> None:
    assert _bus.ci1_rate(sw, sw, 1) == 1.0
    assert 0.0 <= _bus.ci1_rate(sw, list(reversed(sw[1:])), 24) <= 1.0


def test_digest_ignores_wall_fields_health_and_receipt_path() -> None:
    evs = _good()
    other = copy.deepcopy(evs)
    for e in other:
        e["run_id"], e["t_wall"], e["t_mono_ns"] = "z", "y", 999_999
    other[3]["payload"]["receipt"]["path"] = "/elsewhere"  # type: ignore[index]
    other.append(_ev(5000, "health", None, {"kind": "monthly"}))
    assert _bus.digest(evs) == _bus.digest(other)
    other[0]["payload"] = {"changed": 1}
    assert _bus.digest(evs) != _bus.digest(other)


def test_load_ignores_partial_last_line(tmp_path: Path) -> None:
    (tmp_path / "bus.jsonl").write_text(json.dumps({"a": 1}) + "\n" + '{"b": ', encoding="utf-8")
    assert _bus.load(tmp_path) == [{"a": 1}]


def test_month_index() -> None:
    assert _bus.month_index("2200.03.01") - _bus.month_index("2200.01.01") == 2
