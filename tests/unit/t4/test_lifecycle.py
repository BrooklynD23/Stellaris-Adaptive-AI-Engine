"""Lifecycle: seq floor, ack tracking, LOST/re-emit/alarm."""

from __future__ import annotations

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from sae.contracts.c3_intent import IntentBatch, NoopHeartbeat
from sae.contracts.c5_bus import HealthKind, IntentAckPayload
from sae.contracts.c6_adapter import EmitReceipt, IntentAck, ManagedState
from sae.contracts.common import AckCode, AckStatus
from sae.core import lifecycle as lc

C = 7


def batch(seq: int, attempt: int = 1, country: int = C, n: int = 1) -> IntentBatch:
    intents = tuple(
        NoopHeartbeat(intent_id=f"i-{seq * 10 + k:06d}", decision_id="d-2210.01-00", country=country + k)
        for k in range(n)
    )
    return IntentBatch(seq=seq, attempt=attempt, issued_date="2210.01.01", issued_m=0, intents=intents)


def ok(seq: int) -> EmitReceipt:
    return EmitReceipt(seq=seq, ok=True)


def ack(seq: int, status: AckStatus = AckStatus.APPLIED, code: AckCode = AckCode.OK, country: int = C,
        applied: int | None = None) -> IntentAck:
    return IntentAck(seq=seq, country=country, status=status, code=code, game_date="2210.02.01", applied=applied)


def issued(seq: int, mi: int = 100, attempt: int = 1) -> lc.LifecycleState:
    s, _ = lc.register(lc.LifecycleState(), batch(seq, attempt), ok(seq), mi)
    return s


def test_applied() -> None:
    s, out = lc.on_ack(issued(1), ack(1), 101)
    assert [(p.status, p.ack_lag_months, p.attempt) for p in out] == [(AckStatus.APPLIED, 1, 1)]
    assert out[0].intent_ids == ("i-000010",)
    assert lc.in_flight_batches(s) == 0


def test_superseded_by_higher_ack() -> None:
    s, _ = lc.register(issued(1), batch(2), ok(2), 101)
    assert lc.in_flight_batches(s) == 2
    s, out = lc.on_ack(s, ack(2), 102)
    assert [(p.seq, p.status, p.code) for p in out] == [
        (2, AckStatus.APPLIED, AckCode.OK),
        (1, AckStatus.SUPERSEDED, AckCode.NEWER_BATCH),
    ]
    assert lc.in_flight_batches(s) == 0


def test_lost_reemit_lost_alarm() -> None:
    s = issued(1, 100)
    s, out, re, alarm = lc.on_month(s, 101)
    assert (out, re, alarm) == ([], [], False)
    s, out, re, alarm = lc.on_month(s, 102)
    assert out[0].status is AckStatus.LOST and out[0].code is AckCode.TIMEOUT
    assert len(re) == 1 and re[0].attempt == 2 and re[0].intent_ids == ("i-000010",) and not alarm
    s, _ = lc.register(s, batch(2, attempt=2), ok(2), 102)
    s, out, re, alarm = lc.on_month(s, 104)
    assert out[0].attempt == 2 and re == [] and alarm


def test_no_reemit_when_newer_batch() -> None:
    s, _ = lc.register(issued(1, 100), batch(2), ok(2), 101)
    _, out, re, _ = lc.on_month(s, 102)
    assert [p.seq for p in out] == [1] and re == []


def test_alarm_repeat_not_sooner_than_12() -> None:
    s = issued(1, 100, attempt=2)
    s, _, _, alarm = lc.on_month(s, 102)
    assert alarm
    s, _ = lc.register(s, batch(2, attempt=2), ok(2), 103)
    s, _, _, alarm = lc.on_month(s, 105)  # second attempt-2 LOST, only 3 months later
    assert not alarm
    for mi in (106, 113):
        s, _, _, alarm = lc.on_month(s, mi)
        assert not alarm
    s, _ = lc.register(s, batch(3, attempt=2), ok(3), 113)  # something in flight keeps the alarm repeating
    s, _, _, alarm = lc.on_month(s, 114)
    assert alarm


def test_late_ack_after_lost() -> None:
    s, _, _, _ = lc.on_month(issued(1, 100), 102)
    s, out = lc.on_ack(s, ack(1), 103)
    assert len(out) == 1 and out[0].status is AckStatus.APPLIED
    assert out[0].intent_ids == ("i-000010",) and out[0].ack_lag_months == 3


def test_duplicate_ack_deduped() -> None:
    s, out = lc.on_ack(issued(1), ack(1), 101)
    s, out2 = lc.on_ack(s, ack(1), 101)
    assert len(out) == 1 and out2 == []


def test_unknown_ack() -> None:
    s, out = lc.on_ack(lc.LifecycleState(), ack(5), 10)
    assert out[0].intent_ids == () and out[0].attempt == 1 and out[0].ack_lag_months is None
    assert s.next_seq == 6


def test_floor_from_managed() -> None:
    m1 = ManagedState(sid=1, name="a", applied_seq=4)
    m2 = ManagedState(sid=2, name="b", applied_seq=2, pending_seq=9)
    s = lc.observe_managed(lc.LifecycleState(), [m1, m2])
    assert s.next_seq == 10
    assert lc.observe_managed(s, [m1]).next_seq == 10


def test_floor_from_ack_and_seq_old() -> None:
    s, _ = lc.on_ack(lc.LifecycleState(), ack(3), 1)
    assert s.next_seq == 4 and not s.resync_needed
    s, _ = lc.on_ack(s, ack(1, AckStatus.DUPLICATE, AckCode.SEQ_OLD, applied=20), 1)
    assert s.next_seq == 21 and s.resync_needed


def test_receipt_failure() -> None:
    bad = EmitReceipt(seq=1, ok=False, error="write_failed")
    s, h = lc.register(lc.LifecycleState(), batch(1), bad, 5)
    assert h is HealthKind.INI_WRITE_FAILED and s.resync_needed and lc.in_flight_batches(s) == 0
    assert s.next_seq == 2


def test_reanchor_no_negative_age() -> None:
    s = issued(1, 500)
    s = lc.reanchor(s, 10)
    _, out = lc.on_ack(s, ack(1), 11)
    assert out[0].ack_lag_months == 1
    _, _, _, _ = lc.on_month(s, 11)


@given(st.lists(st.tuples(st.sampled_from("aoc"), st.integers(0, 50)), max_size=40))
@settings(max_examples=100, derandomize=True)
def test_seq_strictly_increasing(ops: list[tuple[str, int]]) -> None:
    s = lc.LifecycleState()
    got: list[int] = []
    for op, x in ops:
        if op == "a":
            s, seq = lc.allocate(s)
            got.append(seq)
        elif op == "o":
            s = lc.observe_managed(s, [ManagedState(sid=1, name="n", applied_seq=x)])
        else:
            s, out = lc.on_ack(s, ack(max(1, x)), 5)
            for p in out:
                IntentAckPayload.model_validate(p.model_dump())
    assert all(a < b for a, b in itertools.pairwise(got))


def test_alarm_repeat_needs_something_in_flight() -> None:
    s = issued(1, 100, attempt=2)
    s, _, _, alarm = lc.on_month(s, 102)
    assert alarm and not s.in_flight
    s, _, _, alarm = lc.on_month(s, 130)  # nothing in flight, no new final LOST
    assert not alarm
