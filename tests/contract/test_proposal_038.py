"""Proposal 038: ``LegalView.player_offensive_war_count`` (C4 v1.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sae.contracts.c4_parse import LegalView

OLD_JSON = '{"observer_sid": 3, "in_contact": true, "at_war_with_observer": false, "player_war_count": 2}'


def test_json_without_the_field_loads_as_none() -> None:
    """Parser <= 0.1.2 output, recorded buses and old state files stay valid."""
    assert LegalView.model_validate_json(OLD_JSON).player_offensive_war_count is None


@pytest.mark.parametrize("offensive", [0, 1, 2])
def test_offensive_count_up_to_war_count(offensive: int) -> None:
    view = LegalView(observer_sid=3, in_contact=True, player_war_count=2, player_offensive_war_count=offensive)
    assert view.player_offensive_war_count == offensive


def test_offensive_count_above_war_count_rejected() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        LegalView(observer_sid=3, in_contact=True, player_war_count=1, player_offensive_war_count=2)


def test_offensive_count_without_war_count_allowed() -> None:
    """A-55 fallback may drop the public count while the observer's own defensive war stays legal."""
    assert LegalView(observer_sid=3, in_contact=True, player_offensive_war_count=1).player_war_count is None


def test_not_in_contact_rejects_the_field() -> None:
    with pytest.raises(ValidationError, match="not in contact"):
        LegalView(observer_sid=3, in_contact=False, player_offensive_war_count=0)
