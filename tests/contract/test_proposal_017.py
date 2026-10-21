"""Proposal 017: neutral ``sae_in.ini`` and Ironman observe-only."""

from sae.contracts.c2_ini import IniFile, KnobCommand, parse_ini, render_ini
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import EmitReceipt

NEUTRAL = (
    "effect set_variable = { which = sae_emit_period value = 3 } "
    "set_variable = { which = sae_quiet value = 0 }\n"
)


def test_neutral_file_is_knob_only_and_round_trips() -> None:
    text = render_ini(IniFile(knobs=KnobCommand(emit_period=3, quiet=0)))
    assert text == NEUTRAL
    parsed = parse_ini(text)
    assert not parsed.countries
    assert "sae_seq_pending" not in text


def test_new_kinds_and_receipt() -> None:
    assert HealthKind("ini_neutralised") and HealthKind("ironman_observe_only")
    assert EmitReceipt(seq=18, ok=False, error="ironman").error == "ironman"
