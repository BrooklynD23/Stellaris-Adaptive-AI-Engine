"""Deferred X3-G7 / X3-G8: real ``game.log`` grammar and LogTailer fidelity (docs/handoff/deferred/X3.md)."""

from __future__ import annotations

import itertools
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from e2e._bus import month_index

from sae.adapters.logtail import LogTailer
from sae.contracts.c1_log import LogLineError, LogRecord, parse_log_line


def _sae_lines(text: str) -> list[tuple[int, str]]:
    return [(n, ln[ln.find("SAE|") :]) for n, ln in enumerate(text.splitlines(), 1) if "SAE|" in ln]


def _decode(raw: bytes) -> tuple[str, bool]:
    """utf-8, else cp1252 (UNVERIFIED X3-U2: M0 checks the real encoding of game.log)."""
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), True


def _month(rec: LogRecord) -> str:
    return str(rec.date)[:7]


@pytest.mark.game("SAE_GAME_LOG", id="X3-G7")
def test_log_grammar(game_path: Callable[[str], Path]) -> None:
    """Zero LogLineError; HELLO, >=1 HB month with no month gaps > 1, SNAP with sid>=1; PLYR/ACK counts printed."""
    text, fallback = _decode(game_path("SAE_GAME_LOG").read_bytes())
    errors: list[int] = []
    recs: list[LogRecord] = []
    for n, line in _sae_lines(text):
        try:
            rec = parse_log_line(line)
        except LogLineError:
            errors.append(n)
            continue
        if rec is not None:
            recs.append(rec)
    count = {t: sum(1 for r in recs if r.type == t) for t in ("HELLO", "HB", "SNAP", "PLYR", "ACK")}
    months = {_month(r) for r in recs}
    hb_idx = sorted({month_index(str(r.date)) for r in recs if r.type == "HB"})
    hb_gaps = [b - a for a, b in itertools.pairwise(hb_idx)]
    print(f"X3-G7 counts={count} months={len(months)} hb_months={len(hb_idx)} cp1252_fallback={fallback}")
    assert not errors, f"{len(errors)} LogLineError at lines {errors[:10]}"
    assert count["HELLO"] >= 1, "no HELLO line"
    assert hb_idx, "no HB month"
    big = sorted(g for g in hb_gaps if g > 1)
    assert not big, f"HB month gaps > 1: {big} over {len(hb_idx)} months"
    assert any(r.type == "SNAP" and r.sid >= 1 for r in recs), "no SNAP with sid>=1"
    if os.environ.get("SAE_GAME_EXPECT_ACK") == "1":
        assert count["ACK"] >= 1, "SAE_GAME_EXPECT_ACK=1 but 0 ACK lines"


@pytest.mark.game("SAE_GAME_LOG", id="X3-G8")
def test_tailer_matches_parser(game_path: Callable[[str], Path], tmp_path: Path) -> None:
    """LogTailer over a copy yields what line-by-line parse_log_line yields; no parse_failed health."""
    src = game_path("SAE_GAME_LOG")
    copy = tmp_path / "game.log"
    shutil.copyfile(src, copy)
    health: list[str] = []
    tailer = LogTailer(copy, health=lambda kind, detail: health.append(str(kind.value)))
    got: list[LogRecord] = []
    while batch := tailer.poll():  # MAX_READ is 1 MiB per poll; drain every batch
        for line in batch:
            if "SAE|" in line:
                rec = parse_log_line(line[line.find("SAE|") :])
                if rec is not None:
                    got.append(rec)
    # LogTailer drops a trailing partial line (no LF) and strips one CR; mirror that.
    raw = copy.read_bytes().decode("utf-8", errors="replace")
    complete = raw[: raw.rfind("\n") + 1].replace("\r\n", "\n")
    want = [r for _, ln in _sae_lines(complete) if (r := parse_log_line(ln)) is not None]
    print(f"X3-G8 records={len(got)} health={len(health)}")
    assert got == want, f"tailer gave {len(got)} records, parser {len(want)}"
    assert not health, f"{len(health)} health events: {sorted(set(health))}"
