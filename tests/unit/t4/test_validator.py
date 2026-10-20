"""Validator tests: one per RejectRule plus rule order."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from sae.contracts.c3_intent import SetEconPlan, SetMilitaryPosture
from sae.contracts.c5_bus import RejectRule
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.common import EconPlan, Posture
from sae.core.validator import ValidationContext, validate

from .fakes_cfg import eff

CFG = eff()
BASE: dict[str, Any] = {"decision_id": "d-2200.01-01", "ttl_months": 3}


def ctx(**kw: Any) -> ValidationContext:
    d: dict[str, Any] = {"mode": AdapterMode.SYNTH, "managed": frozenset({2, 3}), "player_sid": 1,
                         "in_contact": frozenset({2, 3}), "cfg": CFG}
    d.update(kw)
    return ValidationContext(**d)


def econ(n: int = 1, country: int = 2) -> SetEconPlan:
    return SetEconPlan(intent_id=f"i-{n:06d}", country=country, plan=EconPlan.MILITARY, **BASE)


def posture(target: int = 1, country: int = 2) -> SetMilitaryPosture:
    return SetMilitaryPosture(intent_id="i-000002", country=country, posture=Posture.OFFENSIVE, target=target, **BASE)


def fleet(w: int = 50, country: int = 2) -> dict[str, Any]:
    """A raw v1 fleet-bias intent; C3 v1.1 moved FLEET_COMPOSITION_BIAS to RESERVED_KINDS."""
    return {"intent_id": "i-000003", "country": country, "kind": "FLEET_COMPOSITION_BIAS",
            "weights": {"corvette": w}, **BASE}


def rule(intents: list[Any], c: ValidationContext) -> RejectRule:
    acc, rej = validate(intents, c)
    assert not acc and len(rej) == 1
    assert len(rej[0][2]) <= 500
    return rej[0][1]


def test_accept() -> None:
    acc, rej = validate([econ(), posture()], ctx())
    assert len(acc) == 2 and not rej


def test_schema_model_and_dict() -> None:
    assert rule([{"kind": "NOOP_HEARTBEAT", "console": "x"}], ctx()) == RejectRule.SCHEMA
    acc, _ = validate([econ().model_dump()], ctx())
    assert len(acc) == 1


def test_reserved() -> None:
    assert rule([{"kind": "PRIORITIZE_TECH", "intent_id": "i-000001"}], ctx()) == RejectRule.RESERVED_KIND


def test_mode() -> None:
    assert rule([econ()], ctx(mode=AdapterMode.RELEASE)) == RejectRule.MODE


def test_not_managed() -> None:
    assert rule([econ(country=9)], ctx()) == RejectRule.NOT_MANAGED


def test_bad_target() -> None:
    assert rule([posture(target=2)], ctx()) == RejectRule.BAD_TARGET
    assert rule([posture(target=3)], ctx()) == RejectRule.BAD_TARGET


def test_not_in_contact() -> None:
    assert rule([posture()], ctx(in_contact=frozenset())) == RejectRule.NOT_IN_CONTACT
    acc, _ = validate([econ()], ctx(in_contact=frozenset()))
    assert len(acc) == 1


def test_fleet_bias_reserved_in_v11() -> None:
    assert rule([fleet(9)], ctx()) == RejectRule.RESERVED_KIND
    assert rule([fleet(50)], ctx()) == RejectRule.RESERVED_KIND


def test_safety_floor_cannot_fire_in_v11() -> None:
    acc, rej = validate([econ(), posture()], ctx(cfg=eff(safety_floor_ship_min=100)))
    assert len(acc) == 2 and not rej


def test_rate_limit_second_batch_and_reemission() -> None:
    assert rule([econ()], ctx(batches_this_month=1)) == RejectRule.RATE_LIMIT
    acc, _ = validate([econ()], ctx(batches_this_month=1, is_reemission=True))
    assert len(acc) == 1


def test_rate_limit_country_cap() -> None:
    cfg = eff(max_managed=2)
    ids = frozenset(range(2, 6))
    intents = [econ(n, c) for n, c in [(1, 5), (2, 3), (3, 2), (4, 4)]]
    acc, rej = validate(intents, ctx(managed=ids, cfg=cfg))
    assert sorted(i.country for i in acc) == [2, 3]
    assert sorted(i.country for i, r, _ in rej if r == RejectRule.RATE_LIMIT) == [4, 5]  # type: ignore[union-attr]


def test_rule_order() -> None:
    # fails not_managed, bad_target, not_in_contact: first rule wins at each step
    p = posture(target=2, country=9)
    assert rule([p], ctx()) == RejectRule.NOT_MANAGED
    assert rule([p], ctx(mode=AdapterMode.RELEASE)) == RejectRule.MODE
    assert rule([posture(target=3)], ctx(in_contact=frozenset())) == RejectRule.BAD_TARGET
    assert rule([fleet(1, country=9)], ctx()) == RejectRule.RESERVED_KIND
    assert rule([econ()], ctx(batches_this_month=1, mode=AdapterMode.RELEASE)) == RejectRule.MODE
    _, rej = validate([fleet(1)], ctx(batches_this_month=1))
    assert rej[0][1] == RejectRule.RESERVED_KIND


@given(st.dictionaries(st.text(max_size=8), st.integers() | st.text(max_size=600), max_size=5))
def test_arbitrary_dicts_never_raise(d: dict[str, Any]) -> None:
    acc, rej = validate([d], ctx())
    assert len(acc) + len(rej) == 1 and all(len(r[2]) <= 500 for r in rej)
