"""``AckRecord`` (C1) to ``IntentAck`` (C6)."""

from __future__ import annotations

from pydantic import ValidationError

from sae.adapters.health import HealthFn
from sae.contracts.c1_log import AckRecord
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import IntentAck


def to_intent_ack(rec: AckRecord, health: HealthFn) -> IntentAck | None:
    """Convert a mod ACK; an invalid one is dropped with ``invalid_event``."""
    try:
        return IntentAck(
            seq=rec.seq,
            country=rec.sid,
            status=rec.status,
            code=rec.code,
            game_date=rec.date,
            reason=rec.reason,
            applied=rec.applied,
        )
    except ValidationError as exc:
        health(HealthKind.INVALID_EVENT, f"ack: {exc.errors(include_url=False)[0]['msg']}"[:200])
        return None
