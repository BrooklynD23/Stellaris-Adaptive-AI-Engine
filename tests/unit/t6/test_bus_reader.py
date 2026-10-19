"""Bus reader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import sae.bus.reader as reader_mod
from sae.bus import BusTail, final_acks, iter_events, read_events
from sae.contracts.c5_bus import BUS_EVENT_ADAPTER, IntentAckEvent

from ._fixtures import RUN_ID

_DOC = (Path(__file__).parents[3] / "contracts" / "C5_bus.md").read_text(encoding="utf-8")


def _lines() -> list[str]:
    block = _DOC.split("```jsonl model=BusEvent\n", 1)[1].split("```", 1)[0]
    return block.splitlines()


def test_read_counts_invalid_gap_and_partial(tmp_path: Path) -> None:
    ls = _lines()
    p = tmp_path / "bus.jsonl"
    text = "\n".join([ls[0], "garbage", ls[1], ls[3], ls[3].replace('"v":1', '"v":2'), ls[3]])
    p.write_text(text, encoding="utf-8")  # last line has no LF
    res = read_events(p)
    assert res.invalid == 2 and res.partial_tail
    assert [e.seq for e in res.events] == [0, 1, 3]
    assert [(i.expected, i.got) for i in res.seq_issues] == [(2, 3)]
    assert len(list(iter_events(p))) == 3


def test_repeat_reported_not_reordered(tmp_path: Path) -> None:
    ls = _lines()
    p = tmp_path / "bus.jsonl"
    p.write_text("\n".join([ls[0], ls[1], ls[1], ls[2]]) + "\n", encoding="utf-8")
    res = read_events(p)
    assert [e.seq for e in res.events] == [0, 1, 1, 2]
    assert [(i.expected, i.got) for i in res.seq_issues] == [(2, 1)]


def test_tail_chunked_and_truncation(tmp_path: Path) -> None:
    ls = _lines()
    data = ("\n".join(ls[:4]) + "\n").encode()
    p = tmp_path / "bus.jsonl"
    tail = BusTail(p)
    assert tail.poll() == []
    got = []
    for i in range(0, len(data), 97):
        with p.open("ab") as fh:
            fh.write(data[i : i + 97])
        got += tail.poll()
    assert [e.seq for e in got] == [0, 1, 2, 3] and not tail.restarted
    p.write_bytes(data[:100])
    assert tail.poll() == [] and tail.restarted
    p.write_bytes(data)
    assert [e.seq for e in tail.poll()] == [0, 1, 2, 3]


def test_tail_oversized_line_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader_mod, "TAIL_BATCH_BYTES", 64)
    p = tmp_path / "bus.jsonl"
    p.write_bytes(b"x" * 200 + b"\n" + _lines()[0].encode() + b"\n")
    tail = BusTail(p)
    got = []
    for _ in range(10):
        got += tail.poll()
    assert (tail.invalid >= 1 and got == []) or tail.offset == p.stat().st_size


def test_final_acks_last_wins() -> None:
    ack = next(e for e in map(BUS_EVENT_ADAPTER.validate_json, _lines()) if isinstance(e, IntentAckEvent))
    later = ack.model_copy(update={"seq": 99, "payload": ack.payload.model_copy(update={"attempt": 2})})
    res = final_acks([ack, later])
    assert res == {(ack.payload.country, ack.payload.seq): later} and RUN_ID
