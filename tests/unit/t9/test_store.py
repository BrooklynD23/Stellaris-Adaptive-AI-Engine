"""Index IO: lock, partial lines, gaps, canonical bytes (proposal 025 sections 3.6, 3.9)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from sae.contracts.c10_profile import CampaignEvent
from sae.profile import store

CID = "c-0123456789abcdef"
KEY = "f-0123456789abcdef"


def seen(i: int = 0, last: str = "2200.02.01") -> CampaignEvent:
    return CampaignEvent(
        seq=i, at=datetime(2026, 10, 1, tzinfo=UTC), campaign_id=CID, event="seen", folder_key=KEY,
        start_date="2200.01.01", last_date=last, run_ids=("20261001-120001-live-0001",),
    )  # fmt: skip


def closed(i: int = 0) -> CampaignEvent:
    return CampaignEvent(seq=i, at=datetime(2026, 10, 2, tzinfo=UTC), campaign_id=CID, event="closed")


def test_missing_index_reads_empty(tmp_path: Path) -> None:
    assert store.read_index(tmp_path / "campaigns.jsonl") == ([], 0, False)


def test_append_assigns_seq_and_roundtrips(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "campaigns.jsonl"
    first = store.append_events(p, [seen(99), closed(99)])
    assert [r.seq for r in first] == [0, 1]
    second = store.append_events(p, [closed(7)])
    assert second[0].seq == 2
    events, invalid, gap = store.read_index(p)
    assert [e.seq for e in events] == [0, 1, 2] and invalid == 0 and not gap
    assert not (tmp_path / "sub" / store.LOCK_FILE).exists()


def test_lock_present_raises_and_leaves_everything(tmp_path: Path) -> None:
    p = tmp_path / "campaigns.jsonl"
    store.append_events(p, [seen()])
    before = p.read_bytes()
    lock = tmp_path / store.LOCK_FILE
    lock.write_bytes(b"held")
    with pytest.raises(store.IndexLocked, match=r"index locked: campaigns\.jsonl\.lock"):
        store.append_events(p, [closed()])
    assert lock.read_bytes() == b"held" and p.read_bytes() == before


def test_lock_removed_after_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "campaigns.jsonl"

    def boom(fd: int) -> None:
        raise OSError("disk")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk"):
        store.append_events(p, [seen()])
    assert not (tmp_path / store.LOCK_FILE).exists()


def test_partial_line_gets_lf_first(tmp_path: Path) -> None:
    p = tmp_path / "campaigns.jsonl"
    store.append_events(p, [seen(), closed()])
    with p.open("ab") as fh:
        fh.write(b'{"v":1,"seq":2,"at":"2026-10-03T00:00:00Z","campaign_id":"c-0123')
    events, invalid, gap = store.read_index(p)
    assert len(events) == 2 and invalid == 1 and not gap
    written = store.append_events(p, [closed()])
    assert written[0].seq == 2
    events, invalid, gap = store.read_index(p)
    assert [e.seq for e in events] == [0, 1, 2] and invalid == 1 and not gap


def test_valid_final_line_without_lf_counts(tmp_path: Path) -> None:
    p = tmp_path / "campaigns.jsonl"
    store.append_events(p, [seen()])
    p.write_bytes(p.read_bytes().rstrip(b"\n"))
    written = store.append_events(p, [closed()])
    assert written[0].seq == 1
    events, invalid, gap = store.read_index(p)
    assert len(events) == 2 and invalid == 0 and not gap


def test_invalid_complete_line_is_skipped_and_counted(tmp_path: Path) -> None:
    p = tmp_path / "campaigns.jsonl"
    store.append_events(p, [seen()])
    with p.open("ab") as fh:
        fh.write(b"not json\n")
    events, invalid, gap = store.read_index(p)
    assert len(events) == 1 and invalid == 1 and not gap


@pytest.mark.parametrize("seqs", [[0, 2], [0, 0], [1]])
def test_gap_or_repeat_is_corrupt(tmp_path: Path, seqs: list[int]) -> None:
    p = tmp_path / "campaigns.jsonl"
    p.write_bytes(b"".join(seen(s).model_dump_json(exclude_none=True).encode() + b"\n" for s in seqs))
    assert store.read_index(p)[2] is True
    with pytest.raises(store.IndexCorrupt, match="corrupted"):
        store.append_events(p, [closed()])
    assert not (tmp_path / store.LOCK_FILE).exists()


class _M(BaseModel):
    b: int = 2
    a: str = "x"


def test_canonical_bytes_stable_and_sorted() -> None:
    assert store.canonical_bytes(_M()) == b'{\n "a": "x",\n "b": 2\n}\n'
    assert store.canonical_bytes(_M()) == store.canonical_bytes(_M())


def test_write_atomic_replaces_and_leaves_no_tmp(tmp_path: Path) -> None:
    p = tmp_path / "a" / "f.json"
    store.write_atomic(p, b"1")
    store.write_atomic(p, b"2")
    assert p.read_bytes() == b"2" and [x.name for x in p.parent.iterdir()] == ["f.json"]


def test_now_is_utc_aware() -> None:
    assert store.now().tzinfo is not None


def test_profile_dir(tmp_path: Path) -> None:
    assert store.profile_dir(tmp_path, "k") == tmp_path / "profile" / "k"
