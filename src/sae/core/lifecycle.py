"""Seq allocator and ack lifecycle (C3 section 5). Pure: every function returns a new state."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from pydantic import Field

from sae.contracts.c3_intent import ACK_TIMEOUT_MONTHS, MAX_ATTEMPTS, Intent, IntentBatch
from sae.contracts.c5_bus import HealthKind, IntentAckPayload
from sae.contracts.c6_adapter import EmitReceipt, IntentAck, ManagedState
from sae.contracts.common import CLAUSEWITZ_INT_MAX, AckCode, AckStatus, Frozen, Sid

log = logging.getLogger(__name__)

RECENT_ACKS_MAX = 64
CLOSED_MAX = 64
ALARM_REPEAT_MONTHS = 12


class InFlight(Frozen):
    """One (country, seq) batch slice awaiting its ACK."""

    country: Sid
    seq: int = Field(ge=1)
    attempt: int = Field(ge=1, le=MAX_ATTEMPTS)
    issued_mi: int = Field(ge=0)
    intent_ids: tuple[str, ...]
    intents: tuple[Intent, ...] = ()


class ReemitRequest(Frozen):
    """Ask the engine to re-emit the same intents once, as attempt 2 with a fresh seq."""

    country: Sid
    old_seq: int
    attempt: int = 2
    intent_ids: tuple[str, ...]
    intents: tuple[Intent, ...]


class LifecycleState(Frozen):
    """Everything the lifecycle needs between calls (serialisable)."""

    next_seq: int = Field(default=1, ge=1)
    in_flight: tuple[InFlight, ...] = ()
    closed: tuple[InFlight, ...] = Field(default=(), description="Recently ended slices, for late ACKs.")
    recent_acks: tuple[tuple[int, int, str], ...] = ()
    last_alarm_mi: int | None = None
    last_ack_mi: int | None = None
    resync_needed: bool = False
    invalid_seen: int = Field(default=0, ge=0, description="Out-of-range seq values ignored since last report.")


def _floor(state: LifecycleState, seq: int) -> LifecycleState:
    if seq > CLAUSEWITZ_INT_MAX:
        # Untrusted value from the mod/save: never let it poison the allocator (would crash every resume).
        log.warning("ignored out-of-range seq (> %d)", CLAUSEWITZ_INT_MAX)
        return state.model_copy(update={"invalid_seen": state.invalid_seen + 1})
    if seq + 1 > state.next_seq:
        return state.model_copy(update={"next_seq": seq + 1})
    return state


def observe_managed(state: LifecycleState, managed: Iterable[ManagedState]) -> LifecycleState:
    """Raise ``next_seq`` above every managed applied/pending seq."""
    for m in managed:
        state = _floor(state, max(m.applied_seq, m.pending_seq or 0))
    return state


def can_allocate(state: LifecycleState) -> bool:
    """False once the next seq would exceed the C3 ``Seq`` range (never build an invalid batch)."""
    return state.next_seq <= CLAUSEWITZ_INT_MAX


def take_invalid(state: LifecycleState) -> tuple[LifecycleState, int]:
    """Return (state with the invalid-value counter cleared, count to report)."""
    return state.model_copy(update={"invalid_seen": 0}), state.invalid_seen


def allocate(state: LifecycleState) -> tuple[LifecycleState, int]:
    """Hand out the next seq (strictly increasing)."""
    return state.model_copy(update={"next_seq": state.next_seq + 1}), state.next_seq


def register(
    state: LifecycleState, batch: IntentBatch, receipt: EmitReceipt, mi: int
) -> tuple[LifecycleState, HealthKind | None]:
    """Track an emitted batch. ``ok=False`` is not in flight: resync and an ``ini_write_failed`` marker."""
    state = _floor(state, batch.seq)
    if receipt.error == "ironman":
        # C6 v1.2 section 1.10: nothing was written; never tracked (no LOST/re-emit/ack_alarm/ini_write_failed),
        # no resync (it would only retry into the same refusal every month).
        log.info("emit refused seq=%d: ironman (observe only)", batch.seq)
        return state, None
    if not receipt.ok:
        log.warning("emit failed seq=%d error=%s", batch.seq, receipt.error)
        return state.model_copy(update={"resync_needed": True}), HealthKind.INI_WRITE_FAILED
    by_country: dict[int, list[Intent]] = {}
    for intent in batch.intents:
        by_country.setdefault(intent.country, []).append(intent)
    added = tuple(
        InFlight(
            country=c,
            seq=batch.seq,
            attempt=batch.attempt,
            issued_mi=mi,
            intent_ids=tuple(i.intent_id for i in items),
            intents=tuple(items),
        )
        for c, items in sorted(by_country.items())
    )
    return state.model_copy(update={"in_flight": state.in_flight + added}), None


def _payload(
    f: InFlight | None, ack_seq: int, country: int, status: AckStatus, code: AckCode, mi: int
) -> IntentAckPayload:
    if f is None:
        return IntentAckPayload(
            seq=ack_seq, country=country, status=status, code=code, intent_ids=(), attempt=1, ack_lag_months=None
        )
    return IntentAckPayload(
        seq=f.seq,
        country=f.country,
        status=status,
        code=code,
        intent_ids=f.intent_ids,
        attempt=f.attempt,
        ack_lag_months=max(0, mi - f.issued_mi),
    )


def _close(state: LifecycleState, ended: Iterable[InFlight]) -> LifecycleState:
    gone = {(f.country, f.seq) for f in ended}
    keep = tuple(f for f in state.in_flight if (f.country, f.seq) not in gone)
    closed = (state.closed + tuple(f for f in state.in_flight if (f.country, f.seq) in gone))[-CLOSED_MAX:]
    return state.model_copy(update={"in_flight": keep, "closed": closed})


def on_ack(state: LifecycleState, ack: IntentAck, mi: int) -> tuple[LifecycleState, list[IntentAckPayload]]:
    """Apply a mod ACK; returns payloads for the ACK itself and any older batches it supersedes."""
    state = _floor(state, ack.seq)
    if ack.status is AckStatus.DUPLICATE and ack.code is AckCode.SEQ_OLD and ack.applied is not None:
        state = _floor(state, ack.applied).model_copy(update={"resync_needed": True})
    key = (ack.country, ack.seq, ack.status.value)
    if key in state.recent_acks:
        return state, []
    state = state.model_copy(
        update={
            "recent_acks": (*state.recent_acks, key)[-RECENT_ACKS_MAX:],
            "last_ack_mi": mi,
            "last_alarm_mi": None,
        }
    )
    own = next((f for f in state.in_flight if (f.country, f.seq) == (ack.country, ack.seq)), None)
    if own is None:
        own_closed = next((f for f in reversed(state.closed) if (f.country, f.seq) == (ack.country, ack.seq)), None)
        payloads = [_payload(own_closed, ack.seq, ack.country, ack.status, ack.code, mi)]
    else:
        payloads = [_payload(own, ack.seq, ack.country, ack.status, ack.code, mi)]
        state = _close(state, [own])
    older = [f for f in state.in_flight if f.country == ack.country and f.seq < ack.seq]
    payloads += [_payload(f, f.seq, f.country, AckStatus.SUPERSEDED, AckCode.NEWER_BATCH, mi) for f in older]
    return _close(state, older), payloads


def on_month(
    state: LifecycleState, mi: int
) -> tuple[LifecycleState, list[IntentAckPayload], list[ReemitRequest], bool]:
    """Time out overdue batches. Returns (state, LOST payloads, re-emit requests, alarm)."""
    overdue = [f for f in state.in_flight if mi - f.issued_mi >= ACK_TIMEOUT_MONTHS]
    payloads = [_payload(f, f.seq, f.country, AckStatus.LOST, AckCode.TIMEOUT, mi) for f in overdue]
    reemits: list[ReemitRequest] = []
    lost_final = False
    for f in overdue:
        newer = any(g.country == f.country and g.seq > f.seq for g in state.in_flight)
        if f.attempt < MAX_ATTEMPTS and not newer:
            reemits.append(
                ReemitRequest(country=f.country, old_seq=f.seq, intent_ids=f.intent_ids, intents=f.intents)
            )
        elif f.attempt >= MAX_ATTEMPTS:
            lost_final = True
    state = _close(state, overdue)
    last = state.last_alarm_mi
    due = last is None or mi - last >= ALARM_REPEAT_MONTHS
    repeat = last is not None and mi - last >= ALARM_REPEAT_MONTHS and (bool(state.in_flight) or lost_final)
    alarm = (lost_final and due) or repeat
    if alarm:
        state = state.model_copy(update={"last_alarm_mi": mi})
        log.warning("ack_alarm mi=%d", mi)
    return state, payloads, reemits, alarm


def reanchor(state: LifecycleState, mi: int) -> LifecycleState:
    """Session reset: every in-flight (and recently closed) batch is re-dated to ``mi``."""
    return state.model_copy(
        update={
            "in_flight": tuple(f.model_copy(update={"issued_mi": mi}) for f in state.in_flight),
            "closed": tuple(f.model_copy(update={"issued_mi": mi}) for f in state.closed),
        }
    )


def in_flight_batches(state: LifecycleState) -> int:
    """Distinct seqs in flight."""
    return len({f.seq for f in state.in_flight})
