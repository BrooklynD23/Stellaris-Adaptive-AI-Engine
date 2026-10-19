"""Expansion tests."""

from __future__ import annotations

from sae.contracts.c3_intent import IntentBatch, SetEconPlan, SetMilitaryPosture
from sae.contracts.c4_parse import LegalView
from sae.contracts.c8_config import EngineConfig
from sae.contracts.common import EconPlan, Posture, ReasonKey, Strategy
from sae.core.expansion import DEFAULT_EXPANSIONS, desired_intents

CFG = EngineConfig()
D = "d-2200.01-01"


def run(strategy: Strategy, key: ReasonKey, n: int = 1):  # type: ignore[no-untyped-def]
    legal = [LegalView(observer_sid=2, in_contact=True), LegalView(observer_sid=3, in_contact=False)]
    return desired_intents(strategy, key, [2, 3], legal, 1, CFG, D, n)


def test_complete_and_contract_valid() -> None:
    for strat, tpl in DEFAULT_EXPANSIONS.items():
        intents, nxt = run(strat, tpl.reason_key, 7)
        assert [type(i) for i in intents] == [SetEconPlan, SetMilitaryPosture]  # C3 v1.1: no fleet bias
        assert nxt == 9 and [i.intent_id for i in intents] == ["i-000007", "i-000008"]
        assert intents[0].reason_key == tpl.reason_key and intents[1].reason_key is None
        IntentBatch(seq=1, issued_date="2200.01.01", issued_m=0, intents=intents)
        assert all(i.country == 2 and i.ttl_months == CFG.ttl_months for i in intents)


def test_table_values() -> None:
    t = DEFAULT_EXPANSIONS[Strategy.OUTSCALE_SIEGE]
    assert t.econ == EconPlan.MILITARY and t.posture == Posture.OFFENSIVE
    b = DEFAULT_EXPANSIONS[Strategy.BASELINE]
    assert b.econ == EconPlan.BALANCED and b.posture == Posture.DETERRENT
    assert not hasattr(t, "weights")


def test_not_in_contact_and_no_player() -> None:
    legal = [LegalView(observer_sid=3, in_contact=False)]
    assert desired_intents(Strategy.BASELINE, ReasonKey.BASELINE, [3], legal, 1, CFG, D, 5) == ((), 5)
    assert desired_intents(Strategy.BASELINE, ReasonKey.BASELINE, [2], [LegalView(observer_sid=2, in_contact=True)],
                           None, CFG, D, 5) == ((), 5)


def test_forced_reason_key() -> None:
    intents, _ = run(Strategy.EARLY_PUNISH, ReasonKey.OPERATOR_FORCED)
    assert intents[0].reason_key == ReasonKey.OPERATOR_FORCED
