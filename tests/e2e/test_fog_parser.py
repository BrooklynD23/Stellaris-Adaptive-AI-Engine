"""F2 parser boundary (plan section 3): fields no C4 section 3 legal definition reads never reach ``legal``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sae.contracts.c4_parse import FixtureSpec, ParseResult

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "parser" / "fixtures" / "balanced" / "spec.json"
PLAYER_ID = 0
#: Approved approximation channels (C4 section 3, report decision 3, A-19): legal features read these on purpose,
#: so F2 never perturbs them except in the control. The list changes only with C4.
APPROXIMATION_CHANNELS = ("fleet_power", "tech_researched", "alloys_income", "systems")


def _render_and_parse(spec: FixtureSpec, exe: Path, tmp: Path) -> ParseResult:
    spec_path, save = tmp / f"{spec.name}.json", tmp / f"{spec.name}.sav"
    spec_path.write_text(spec.model_dump_json(), encoding="utf-8")
    subprocess.run([str(exe), "fixture", "--spec", str(spec_path), "--out", str(save)], check=True, timeout=60)
    out = subprocess.run(
        [str(exe), "parse", "--save", str(save)], check=True, capture_output=True, text=True, timeout=60
    ).stdout
    return ParseResult.model_validate_json(out)


def _with_player(spec: FixtureSpec, **update: float) -> FixtureSpec:
    countries = tuple(c.model_copy(update=update) if c.country_id == PLAYER_ID else c for c in spec.countries)
    return spec.model_copy(update={"countries": countries})


def _perturbed(spec: FixtureSpec) -> FixtureSpec:
    """Player energy, minerals, alloys stockpile, naval cap; plus a fortified starbase away from the border."""
    spec = _with_player(spec, energy=987654, minerals=765432, alloys=543210, naval_cap_used=4321, naval_cap=8765)
    extra = spec.starbases[0].model_copy(update={"system": 1, "owner": PLAYER_ID, "defense_modules": 3, "platforms": 3})
    return spec.model_copy(update={"starbases": (*spec.starbases, extra), "name": "balanced_fog"})


@pytest.fixture(scope="module")
def base_spec() -> FixtureSpec:
    return FixtureSpec.model_validate_json(BASE.read_text(encoding="utf-8"))


def test_f2_legal_and_self_state_ignore_god_only_fields(
    parser_exe: Path, base_spec: FixtureSpec, tmp_path: Path
) -> None:
    a = _render_and_parse(base_spec, parser_exe, tmp_path)
    b = _render_and_parse(_perturbed(base_spec), parser_exe, tmp_path)
    assert a.legal and any(v.in_contact for v in a.legal), "vacuous: no in-contact legal view"
    assert a.legal == b.legal
    assert a.self_state == b.self_state
    assert a.god is not None and b.god is not None
    assert a.god != b.god, "vacuous: the perturbation did not reach the god view"


def test_f2_control_approximation_channel_does_change_legal(
    parser_exe: Path, base_spec: FixtureSpec, tmp_path: Path
) -> None:
    """The comparison can fail: moving the player's fleet power (an approved channel) moves ``legal``."""
    a = _render_and_parse(base_spec, parser_exe, tmp_path)
    moved = _with_player(base_spec, fleet_power=500000).model_copy(update={"name": "balanced_moved"})
    b = _render_and_parse(moved, parser_exe, tmp_path)
    assert a.legal != b.legal
