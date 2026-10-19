"""Schema drift check plus property tests for the C1 and C2 reference codecs."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sae.contracts import c1_log
from sae.contracts.c1_log import AckRecord, HeartbeatRecord, LogLineError, SnapRecord, format_log_line, parse_log_line
from sae.contracts.c2_ini import SHIP_SLOTS, CountryCommand, IniFile, KnobCommand, parse_ini, render_ini
from sae.contracts.c3_intent import INTENT_ADAPTER, RESERVED_KINDS
from sae.contracts.common import (
    COUNTER_TABLE,
    AckCode,
    AckStatus,
    Archetype,
    EconPlan,
    ReasonKey,
    Strategy,
    normalize_game_date,
)
from sae.contracts.export import render_schemas

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"


def test_committed_schemas_match_models() -> None:
    rendered = render_schemas()
    committed = {p.name: p.read_text(encoding="utf-8") for p in SCHEMAS.glob("*.schema.json")}
    assert committed == rendered, "run: uv run python -m sae.contracts.export"


def test_codes_are_stable() -> None:
    assert [e.code for e in EconPlan] == [1, 2, 3, 4]
    assert ReasonKey.SAFETY_FLOOR.code == 6
    assert EconPlan.from_code(2) is EconPlan.MILITARY
    assert set(COUNTER_TABLE) == set(Archetype)
    assert COUNTER_TABLE[Archetype.BALANCED] is Strategy.BASELINE


def test_date_normalisation() -> None:
    assert normalize_game_date("2231.4.1") == "2231.04.01"


_dates = st.builds(
    lambda y, m, d: f"{y}.{m:02d}.{d:02d}", st.integers(2200, 2600), st.integers(1, 12), st.integers(1, 30)
)
_names = st.text(
    st.characters(blacklist_categories=("Cc", "Cs"), blacklist_characters="\r\n"), min_size=1, max_size=40
).filter(lambda s: s.strip())


@given(_dates, st.integers(1, 10_000), st.integers(0, 10_000), _names, st.floats(0, 1e7, allow_nan=False))
def test_snap_roundtrip(date: str, sid: int, seq: int, name: str, fleet: float) -> None:
    rec = SnapRecord(date=date, sid=sid, seq=seq, name=name, fleet=round(fleet, 3))
    assert parse_log_line(format_log_line(rec)) == rec


@given(_dates, st.integers(0, 1_000_000), st.integers(1, 12), st.sampled_from([0, 1]))
def test_hb_roundtrip(date: str, m: int, period: int, quiet: int) -> None:
    rec = HeartbeatRecord.model_validate({"date": date, "m": m, "period": period, "quiet": quiet})
    assert parse_log_line("[12:00:00][x.cpp:1]: " + format_log_line(rec) + "\r\n") == rec


_ACK_PAIRS = [(st_, c) for st_, codes in c1_log._ACK_PAIRS.items() for c in codes]


@given(_dates, st.integers(1, 999), st.integers(1, 999), st.sampled_from(_ACK_PAIRS))
def test_ack_roundtrip(date: str, sid: int, seq: int, pair: tuple[AckStatus, AckCode]) -> None:
    rec = AckRecord(date=date, sid=sid, seq=seq, status=pair[0], code=pair[1])
    assert parse_log_line(format_log_line(rec)) == rec


def test_ack_pair_enforced() -> None:
    with pytest.raises(LogLineError):
        parse_log_line("SAE|v1|ACK|2231.04.01|sid=3|seq=1|status=APPLIED|code=BAD_TARGET")


def test_extra_cannot_inject_fields() -> None:
    with pytest.raises(ValueError):
        HeartbeatRecord.model_validate({"date": "2231.04.01", "m": 1, "period": 1, "quiet": 0, "extra": {"a": "1|m=9"}})


@given(st.text(max_size=200))
def test_parse_never_crashes(raw: str) -> None:
    try:
        parse_log_line(raw)
    except ValueError:
        pass


@given(
    st.lists(st.integers(1, 500), min_size=1, max_size=8, unique=True),
    st.integers(1, 999_999),
    st.integers(0, 999_999),
    st.sampled_from([-1, 1, 2, 3, 4]),
    st.one_of(st.none(), st.integers(1, 12)),
)
def test_ini_roundtrip(
    sids: list[int], seq: int, expires: int, econ: int, period: int | None
) -> None:
    countries = tuple(
        CountryCommand.model_validate({"sid": s, "seq": seq, "expires": expires, "econ": econ})
        for s in sorted(sids)
    )
    ini = IniFile(knobs=KnobCommand(emit_period=period) if period else None, countries=countries)
    assert parse_ini(render_ini(ini)) == ini


def test_ship_slots_reserved() -> None:
    """C2 v1.1: the engine writes -1 in every sae_p_ship_<size> slot; parse_ini rejects anything else."""
    text = render_ini(IniFile(countries=(CountryCommand(sid=3, seq=1, expires=5),)))
    assert all(f"which = {name} value = -1 " in text for name in SHIP_SLOTS)
    with pytest.raises(ValueError, match="reserved"):
        parse_ini(text.replace("sae_p_ship_cruiser value = -1", "sae_p_ship_cruiser value = 30"))


def test_fleet_bias_is_reserved_kind() -> None:
    """C3 v1.1: FLEET_COMPOSITION_BIAS is dropped from the Intent union and reserved until M0-7."""
    assert "FLEET_COMPOSITION_BIAS" in RESERVED_KINDS
    with pytest.raises(ValueError):
        INTENT_ADAPTER.validate_python(
            {"intent_id": "i-000001", "decision_id": "d-2231.04-01", "country": 3,
             "kind": "FLEET_COMPOSITION_BIAS", "weights": {"cruiser": 70}}
        )
