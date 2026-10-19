from __future__ import annotations

from sae.adapters.acks import to_intent_ack
from sae.contracts.c1_log import AckRecord
from sae.contracts.c5_bus import HealthKind
from sae.contracts.common import AckCode, AckStatus


def test_ack_ok() -> None:
    rec = AckRecord(date="2231.04.01", sid=3, seq=17, status=AckStatus.APPLIED, code=AckCode.OK)
    ack = to_intent_ack(rec, lambda k, d: None)
    assert ack is not None and ack.country == 3 and ack.seq == 17 and ack.game_date == "2231.04.01"


def test_ack_invalid_dropped() -> None:
    ev: list[HealthKind] = []
    rec = AckRecord.model_construct(
        date="2231.04.01", sid=3, seq=0, status=AckStatus.APPLIED, code=AckCode.OK, reason=None, applied=None, extra={}
    )
    assert to_intent_ack(rec, lambda k, d: ev.append(k)) is None
    assert ev == [HealthKind.INVALID_EVENT]
