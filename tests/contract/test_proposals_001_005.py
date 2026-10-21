"""Accepted proposals 002-T3 (strict FixtureSpec), 003-T3 (unique observer_sid), 005-T4 (null command)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from sae.contracts.c4_parse import FixtureSpec, ParseResult
from sae.contracts.c5_bus import ControlPayload

SPEC: dict[str, Any] = {
    "name": "x", "date": "2231.04.01", "hyperlanes": [[1, 2]], "contacts": [[0, 7]],
    "countries": [{"country_id": 0, "name": "A", "fleet_power": 5, "systems": [1, 2]},
                  {"country_id": 7, "name": "B", "sid": 3, "managed": True}],
}


def _both(data: dict[str, Any]) -> None:
    for validate in (FixtureSpec.model_validate, lambda d: FixtureSpec.model_validate_json(json.dumps(d))):
        with pytest.raises(ValidationError):
            validate(data)


def test_fixture_spec_accepts_lists_and_int_for_float() -> None:
    spec = FixtureSpec.model_validate(SPEC)
    assert spec.hyperlanes == ((1, 2),) and spec.countries[0].fleet_power == 5.0
    assert FixtureSpec.model_validate_json(json.dumps(SPEC)) == spec


@pytest.mark.parametrize(("key", "value"), [
    ("schema_version", 1.0), ("pad_bytes", 1000.0), ("pad_bytes", 2 * 1024**3 + 1), ("ironman", "true"),
    ("ironman", 1), ("hyperlanes", [[-1, 2]]), ("hyperlanes", [[2, 2]]), ("contacts", [[0, 0]]),
])
def test_fixture_spec_rejects_lax_top_level(key: str, value: object) -> None:
    _both({**SPEC, key: value})


@pytest.mark.parametrize(("key", "value"), [
    ("country_id", 3.0), ("fleet_power", "1.5"), ("is_player", 1), ("sid", 2.0), ("systems", [-1]),
])
def test_fixture_spec_rejects_lax_country(key: str, value: object) -> None:
    _both({**SPEC, "countries": [{**SPEC["countries"][0], key: value}, SPEC["countries"][1]]})


def test_parse_result_rejects_duplicate_observer() -> None:
    doc = {"parser_version": "0", "save": {"path": "x", "size_bytes": 1, "mtime_ns": 1},
           "meta": {"game_version": "v", "date": "2231.04.01"}, "countries": [], "self_state": {},
           "legal": [{"observer_sid": 3, "in_contact": False}] * 2, "god": None, "sections": ["meta"],
           "cost": {"rss_peak_bytes": 1, "cpu_s": 0, "wall_s": 0}}
    with pytest.raises(ValidationError, match="unique"):
        ParseResult.model_validate(doc)
    ParseResult.model_validate({**doc, "legal": doc["legal"][:1]})


def test_control_payload_null_command_only_for_unreadable() -> None:
    result = {"command_id": "unknown", "accepted": False, "error": "too_large", "applied_at": None, "status": None}
    assert ControlPayload.model_validate({"command": None, "result": result}).command is None
    for bad in ({**result, "accepted": True, "error": None}, {**result, "error": "not_running"}):
        with pytest.raises(ValidationError):
            ControlPayload.model_validate({"command": None, "result": bad})
