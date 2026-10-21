"""Proposal 016: C9 command expiry and the canonical ``--set`` layer."""

from datetime import UTC, datetime, timedelta

import pytest

from sae.contracts.c8_config import env_layer, set_layer
from sae.contracts.c9_control import COMMAND_MAX_AGE_S, ControlResult, command_expired

START = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("issued", "now", "expired"),
    [
        (START + timedelta(seconds=1), START + timedelta(seconds=2), False),
        (START - timedelta(seconds=1), START + timedelta(seconds=1), True),  # previous session
        (START + timedelta(seconds=10), START + timedelta(seconds=10 + COMMAND_MAX_AGE_S + 1), True),
        (START + timedelta(seconds=10), START + timedelta(seconds=10 + COMMAND_MAX_AGE_S), False),
        (START + timedelta(seconds=30), START + timedelta(seconds=20), True),  # future beyond skew
        (START + timedelta(seconds=24), START + timedelta(seconds=20), False),  # within skew
    ],
)
def test_command_expired(issued: datetime, now: datetime, expired: bool) -> None:
    assert (command_expired(issued, now, START) is not None) is expired


def test_expired_result_validates() -> None:
    ControlResult(command_id="c-20261019T031800Z-a1b2c3", accepted=False, error="expired",
                  detail="x", applied_at=None, status=None)


def test_set_layer() -> None:
    assert set_layer(["engine.seed=3", "paths.user_dir=abc", "preset=dev"]) == {
        "engine": {"seed": 3}, "paths": {"user_dir": "abc"}, "preset": "dev"}
    for bad in (["noequals"], ["=1"], ["a=1", "a.b=2"]):
        with pytest.raises(ValueError):
            set_layer(bad)
    assert env_layer({"SAE_ENGINE__SEED": "4", "SAE_CONFIG": "x"}) == {"engine": {"seed": 4}}
