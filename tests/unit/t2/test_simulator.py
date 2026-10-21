"""Conformance tests for the mod simulator against SEMANTICS.md §§4, 6."""

from __future__ import annotations

import pytest

from sae.adapters.simulator import ModSimulator, SimCountry
from sae.contracts.c1_log import (
    AckRecord,
    HeartbeatRecord,
    HelloRecord,
    LogRecord,
    PlayerRecord,
    SnapRecord,
    parse_log_line,
)
from sae.contracts.c2_ini import CountryCommand, IniFile, render_ini
from sae.contracts.common import AckCode, AckStatus

D = "2231.04.01"


def parse(lines: list[str]) -> list[LogRecord]:
    out: list[LogRecord] = []
    for line in lines:
        rec = parse_log_line(line)
        assert rec is not None
        out.append(rec)
    return out


def acks(lines: list[str]) -> list[AckRecord]:
    return [r for r in parse(lines) if isinstance(r, AckRecord)]


def make(n_ai: int = 2, **kw: bool) -> ModSimulator:
    """Human id 1 plus AIs ids 2.. all in contact; run one pulse so they have sids 1.. and are managed."""
    cs = [SimCountry(1, "Human", is_ai=False)]
    cs += [SimCountry(i + 2, f"AI{i}", contact_with_human=True, fleet=10.0) for i in range(n_ai)]
    sim = ModSimulator(cs, **kw)
    sim.pulse(D)
    return sim


def cmd(sid: int, seq: int, **kw: object) -> str:
    fields: dict[str, object] = {"sid": sid, "seq": seq, "expires": 100}
    fields.update(kw)
    return render_ini(IniFile(countries=(CountryCommand.model_validate(fields),)))


def one(lines: list[str]) -> AckRecord:
    a = acks(lines)
    assert len(a) == 1
    return a[0]


def test_hello_first_pulse_and_sid_order() -> None:
    sim = ModSimulator([SimCountry(9, "Z"), SimCountry(3, "A"), SimCountry(5, "H", is_ai=False)])
    recs = parse(sim.pulse(D))
    assert isinstance(recs[0], HelloRecord) and isinstance(recs[1], HeartbeatRecord)
    assert recs[1].m == 1
    assert [sim.country(s).name for s in (1, 2, 3)] == ["A", "H", "Z"]
    assert not any(isinstance(r, HelloRecord) for r in parse(sim.pulse(D)))


def test_game_start_hello_then_no_second_hello() -> None:
    sim = ModSimulator([SimCountry(1, "H", is_ai=False)])
    assert isinstance(parse(sim.game_start(D))[0], HelloRecord)
    assert not any(isinstance(r, HelloRecord) for r in parse(sim.pulse(D)))


def test_knob_affects_same_pulse_hb_r8() -> None:
    sim = make()
    sim.run_ini(
        "effect set_variable = { which = sae_emit_period value = 4 } set_variable = { which = sae_quiet value = 1 }\n"
    )
    hb = parse(sim.pulse(D))[0]
    assert isinstance(hb, HeartbeatRecord) and (hb.period, hb.quiet) == (4, 1)


def test_knob_ignored_out_of_range_r5() -> None:
    sim = make()
    h = sim._first_human()
    assert h is not None
    h.knob_period, h.knob_quiet = 2.5, 2
    sim.pulse(D)
    assert (sim.period, sim.quiet) == (1, 0)
    h.knob_period = 13
    sim.pulse(D)
    assert sim.period == 1


def test_knob_observer_and_multiplayer_no_effect() -> None:
    obs = ModSimulator([SimCountry(1, "AI", contact_with_human=True)])
    obs.pulse(D)
    obs.run_ini("effect set_variable = { which = sae_quiet value = 1 }\n")
    obs.pulse(D)
    assert obs.quiet == 0
    mp = make(multiplayer=True)
    mp.run_ini("effect set_variable = { which = sae_quiet value = 1 }\n")
    mp.pulse(D)
    assert mp.quiet == 0


def test_stagger_period_3_and_quiet() -> None:
    sim = make(2)  # sids: human 1, AI 2, AI 3; m == 1 after the setup pulse
    sim.run_ini("effect set_variable = { which = sae_emit_period value = 3 }\n")
    got = []
    for _ in range(6):  # m = 2..7
        recs = parse(sim.pulse(D))
        got.append([(r.type, r.sid) for r in recs[1:] if isinstance(r, (SnapRecord, PlayerRecord))])
    snap2, snap3, plyr1 = ("SNAP", 2), ("SNAP", 3), ("PLYR", 1)
    assert got == [[plyr1], [snap3], [snap2], [plyr1], [snap3], [snap2]]
    sim.run_ini("effect set_variable = { which = sae_quiet value = 1 }\n")
    sim.run_ini(cmd(2, 1, reason=0, econ=2))
    assert [r.type for r in parse(sim.pulse(D))] == ["HB", "ACK"]


def test_quiet_keeps_hb_and_ack() -> None:
    sim = make()
    sim.run_ini("effect set_variable = { which = sae_quiet value = 1 }\n" + cmd(2, 1))
    assert [r.type for r in parse(sim.pulse(D))] == ["HB", "ACK"]


def test_sticky_managed_r4() -> None:
    sim = make(1)
    sim.set_contact(2, False)
    sim.run_ini(cmd(2, 1))
    assert one(sim.pulse(D)).status is AckStatus.APPLIED


def test_managed_cap_16() -> None:
    sim = ModSimulator([SimCountry(i, f"A{i}", contact_with_human=True) for i in range(1, 19)])
    sim.pulse(D)
    assert sum(sim.country(s).managed for s in range(1, 19)) == 16
    assert not sim.country(17).managed


def test_r10_popup_cap_two_ais() -> None:
    sim = make(2)
    sim.run_ini(cmd(2, 1, reason=1) + cmd(3, 1, reason=2))
    out = acks(sim.pulse(D))
    assert [a.status for a in out] == [AckStatus.APPLIED] * 2
    assert len(sim.flavour_events) == 1 and sim.flavour_events[0][2:] == (2, 1)
    assert sim.country(3).reason == 2
    sim.run_ini(cmd(3, 2, reason=3))
    sim.pulse(D)
    assert len(sim.flavour_events) == 2  # flag cleared next pulse


def test_reason_zero_and_same_reason_no_popup() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1, reason=4))
    sim.pulse(D)
    sim.run_ini(cmd(2, 2, reason=0))
    sim.pulse(D)
    sim.run_ini(cmd(2, 3, reason=4))
    sim.pulse(D)
    assert len(sim.flavour_events) == 1 and sim.country(2).reason == 4


def test_rule1_r2_silent_reset() -> None:
    sim = make(1)
    sim.stage_raw(2, {"sae_seq_pending": 1_000_001})
    assert acks(sim.pulse(D)) == []
    assert sim.country(2).seq_pending == 0
    sim.stage_raw(2, {"sae_seq_pending": 0})
    assert acks(sim.pulse(D)) == []


def test_rule2_same_file_twice_one_ack() -> None:
    sim = make(1)
    text = cmd(2, 5, econ=2)
    sim.run_ini(text)
    assert one(sim.pulse(D)).status is AckStatus.APPLIED
    sim.run_ini(text)
    assert acks(sim.pulse(D)) == []


def test_rule3_duplicate_older_file() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 5))
    sim.pulse(D)
    sim.run_ini(cmd(2, 3))
    a = one(sim.pulse(D))
    assert (a.status, a.code, a.seq, a.applied) == (AckStatus.DUPLICATE, AckCode.SEQ_OLD, 3, 5)
    assert sim.country(2).seq_pending == 5 and sim.country(2).seq_applied == 5


def test_rule4_not_managed_human_played() -> None:
    sim = make(1)
    sim.set_player(2, False)
    sim.run_ini(cmd(2, 1))
    a = one(sim.pulse(D))
    assert (a.status, a.code) == (AckStatus.REJECTED, AckCode.NOT_MANAGED)
    assert sim.country(2).seq_applied == 1


def test_rule4_before_mp_and_unmanaged_not_staged() -> None:
    sim = ModSimulator([SimCountry(1, "H", is_ai=False), SimCountry(2, "A")])  # never in contact
    sim.pulse(D)
    sim.run_ini(cmd(2, 1))
    assert acks(sim.pulse(D)) == []  # A-10: not managed, nothing staged
    sim.stage_raw(2, {"sae_seq_pending": 1})
    assert acks(sim.pulse(D)) == []
    c = sim._by_id(2)
    c.seq_pending = 1
    assert one(sim.pulse(D)).code is AckCode.NOT_MANAGED


def test_rule5_multiplayer() -> None:
    sim = make(1, multiplayer=True)
    sim.run_ini(cmd(2, 1))
    a = one(sim.pulse(D))
    assert (a.status, a.code) == (AckStatus.REJECTED, AckCode.MULTIPLAYER)


def test_rule6_schema_mismatch() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1))
    sim.stage_raw(2, {"sae_p_schema": 2})
    a = one(sim.pulse(D))
    assert (a.status, a.code) == (AckStatus.SCHEMA_MISMATCH, AckCode.SCHEMA)


def test_rule7_expiry_boundary() -> None:
    sim = make(1)  # m == 1
    sim.run_ini(cmd(2, 1, expires=2))
    assert one(sim.pulse(D)).status is AckStatus.APPLIED  # m == 2 == expires
    sim.run_ini(cmd(2, 2, expires=2))
    a = one(sim.pulse(D))  # m == 3
    assert (a.status, a.code) == (AckStatus.STALE, AckCode.EXPIRED)


def test_rule8_self_target_range_r1() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1))
    sim.stage_raw(2, {"sae_p_mil": 1, "sae_p_mil_target": 2, "sae_seq_pending": 2})
    a = one(sim.pulse(D))
    assert (a.status, a.code) == (AckStatus.REJECTED, AckCode.RANGE)


@pytest.mark.parametrize(
    "vals",
    [
        {"sae_p_econ": 5},
        {"sae_p_reason": 7},
        {"sae_p_mil": 1, "sae_p_mil_target": -1},
        {"sae_p_dip": 5, "sae_p_dip_target": 1},
        {"sae_p_ship_frigate": 101},
        {"sae_p_expires": 1_000_001},
        {"sae_p_mil": -1, "sae_p_mil_target": 1},
    ],
)
def test_rule8_range_cases(vals: dict[str, int]) -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1))
    sim.stage_raw(2, {**vals, "sae_seq_pending": 2})
    assert one(sim.pulse(D)).code is AckCode.RANGE


def test_rule8_partial_line_unset_reads_zero_a41() -> None:
    sim = make(1)
    sim.stage_raw(2, {"sae_p_schema": 1, "sae_p_expires": 10, "sae_seq_pending": 1})
    assert one(sim.pulse(D)).code is AckCode.RANGE  # econ 0 is illegal


def test_rule9_bad_target_unknown_and_dead() -> None:
    sim = make(2)
    sim.run_ini(cmd(2, 1, mil=1, mil_target=77))
    assert one(sim.pulse(D)).code is AckCode.BAD_TARGET
    sim.set_alive(3, False)
    sim.run_ini(cmd(2, 2, dip=3, dip_target=3))
    assert one(sim.pulse(D)).code is AckCode.BAD_TARGET
    assert sim.country(2).seq_applied == 2 and sim.country(2).dip == 0


def test_rules10_11_apply_all_values_and_modifiers() -> None:
    sim = make(2)
    sim.run_ini(cmd(2, 1, reason=1, econ=3, mil=3, mil_target=3, dip=4, dip_target=1))
    sim.stage_raw(2, {"sae_p_ship_cruiser": 30})  # mod still applies slots (SEMANTICS); C2 always sends -1
    a = one(sim.pulse(D))
    assert (a.status, a.code, a.reason) == (AckStatus.APPLIED, AckCode.OK, 1)
    c = sim.country(2)
    assert (c.econ, c.mil, c.mil_target, c.dip, c.dip_target, c.reason) == (3, 3, 3, 4, 1, 1)
    assert c.ship["cruiser"] == 30 and c.ship["corvette"] == 50
    assert {"sae_econ_research", "sae_mil_offensive", "sae_dip_hostile"} <= c.flags
    assert ("sae_posture_offensive", 3) in c.modifiers
    sim.run_ini(cmd(2, 2, mil=1, mil_target=1))
    sim.pulse(D)
    c = sim.country(2)
    assert ("sae_posture_offensive", 3) not in c.modifiers and ("sae_posture_defensive", 1) in c.modifiers
    assert c.econ == 3  # KEEP


def test_reload_lowers_applied_seq_and_reack() -> None:
    sim = make(1)
    snap = sim.snapshot()
    text = cmd(2, 4, econ=2)
    sim.run_ini(text)
    first = one(sim.pulse(D))
    sim.pulse(D)
    sim.restore(snap)
    assert sim.m == 1 and sim.country(2).seq_applied == 0
    sim.run_ini(text)
    again = one(sim.pulse(D))
    assert (first.status, again.status, again.seq) == (AckStatus.APPLIED, AckStatus.APPLIED, 4)


def test_invalid_lines_skipped_and_knob_on_human() -> None:
    sim = make(1)
    sim.run_ini(
        "effect add_resource = { energy = 1000 }\nbogus\n"
        + "effect set_variable = { which = sae_emit_period value = 13 }\n"
    )
    assert sim.human() is not None
    assert acks(sim.pulse(D)) == []
    assert sim.period == 1


def test_snap_and_plyr_format() -> None:
    sim = make(1)
    out = sim.pulse(D)
    plyr, snap = out[1], out[2]
    assert plyr.startswith("SAE|v1|PLYR|") and snap.startswith("SAE|v1|SNAP|") and snap.endswith("name=AI0")
    for banned in ("|en=", "|mi=", "|al=", "|al_inc="):
        assert banned not in snap and banned not in plyr
    assert "|contact=1|" in snap and "|seq=" not in plyr and "|contact" not in plyr


def test_snap_seq_after_ack_same_pulse() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 7))
    recs = parse(sim.pulse(D))
    assert [r.type for r in recs] == ["HB", "ACK", "PLYR", "SNAP"]


def test_hot_join_new_country_gets_next_sid() -> None:
    sim = make(1)
    sim.add_country(SimCountry(50, "New"))
    sim.pulse(D)
    assert sim.country(sim.sid_next).name == "New" and sim.sid_next == 3


def test_dead_country_not_staged_or_applied() -> None:
    sim = make(2)
    sim.run_ini(cmd(3, 1))
    sim.set_alive(3, False)
    assert acks(sim.pulse(D)) == []
    sim.run_ini(cmd(3, 2))  # dead: not staged either
    assert sim.country(3).seq_pending == 1


def test_lax_schema_mismatch_line() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1).replace("sae_p_schema value = 1", "sae_p_schema value = 2"))
    assert one(sim.pulse(D)).code is AckCode.SCHEMA


def test_lax_self_target_line_is_range() -> None:
    sim = make(1)
    text = (
        cmd(2, 1)
        .replace("sae_p_mil value = -1", "sae_p_mil value = 1")
        .replace("sae_p_mil_target value = -1", "sae_p_mil_target value = 2")
    )
    sim.run_ini(text)
    assert one(sim.pulse(D)).code is AckCode.RANGE


def test_lax_out_of_range_code_is_range() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1).replace("sae_p_econ value = -1", "sae_p_econ value = 9"))
    assert one(sim.pulse(D)).code is AckCode.RANGE


def test_lax_partial_line_unset_zero() -> None:
    sim = make(1)
    sim.run_ini(
        "effect every_country = { limit = { has_country_flag = sae_managed check_variable = "
        "{ which = sae_sid value = 2 } } set_variable = { which = sae_seq_pending value = 1 } }\n"
    )
    assert one(sim.pulse(D)).code is AckCode.SCHEMA  # unset schema reads 0


def test_exact_snap_plyr_text_plain_and_clausewitz() -> None:
    cs = [SimCountry(1, "Hum", is_ai=False, fleet=5.0), SimCountry(2, "AI", contact_with_human=True, fleet=10.5)]
    sim = ModSimulator(cs)
    out = sim.pulse(D)
    assert out[2] == "SAE|v1|PLYR|2231.04.01|sid=1|fleet=5|navcap_used=0|navcap=0|wars=0|name=Hum"
    assert out[3] == (
        "SAE|v1|SNAP|2231.04.01|sid=2|seq=0|fleet=10.5|navcap_used=0|navcap=0|wars=0|econ=0|mil=0|contact=1|name=AI"
    )
    cw = ModSimulator(cs, int_style="clausewitz")
    cout = cw.pulse(D)
    assert cout[3] == (
        "SAE|v1|SNAP|2231.04.01|sid=2.000|seq=0.000|fleet=10.500|navcap_used=0.000|navcap=0.000|wars=0.000"
        "|econ=0.000|mil=0.000|contact=1.000|name=AI"
    )
    for line in (*out, *cout):
        assert parse_log_line(line) is not None
    snap = parse_log_line(cout[3])
    assert isinstance(snap, SnapRecord) and snap.sid == 2 and snap.contact == 1


def test_console_hook_sae2_applies_one_country_keeps_popup_flag() -> None:
    sim = make(2)
    sim.run_ini(cmd(2, 1, reason=1) + cmd(3, 1, reason=2))
    out = parse(sim.console_event_sae2(2, D))
    assert [r.type for r in out] == ["ACK"] and sim.country(3).seq_applied == 0
    assert sim.human() is not None and sim.human().popup_flag  # type: ignore[union-attr]
    sim.console_event_sae2(3, D)  # popup flag not cleared: no second popup before next pulse
    assert len(sim.flavour_events) == 1
    assert sim.console_event_sae2(3, D) == []


def test_console_hook_sae1_ignores_stagger_and_quiet() -> None:
    sim = make(1)
    sim.run_ini("effect set_variable = { which = sae_quiet value = 1 }\n")
    sim.pulse(D)
    assert parse_log_line(sim.console_event_sae1(2, D)[0]) is not None
    assert sim.console_event_sae1(1, D)[0].startswith("SAE|v1|PLYR|")


def test_fractional_staged_value_is_range_not_applied() -> None:
    # SEMANTICS §6 step 8 is integers only; the 0.1.0 script's >=/<= check (T1 issue) is not copied.
    sim = make(1)
    sim.run_ini(cmd(2, 1))
    sim.stage_raw(2, {"sae_p_econ": 2.5})
    a = one(sim.pulse(D))
    assert (a.status, a.code) == (AckStatus.REJECTED, AckCode.RANGE)
    assert sim.country(2).econ == 0


def test_r12_fractional_pending_resets_silently() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1))
    sim.stage_raw(2, {"sae_seq_pending": 17.5})
    assert acks(sim.pulse(D)) == []
    assert sim.country(2).seq_pending == sim.country(2).seq_applied == 0


def test_r12_fractional_value_from_console_line_is_range() -> None:
    sim = make(1)
    line = cmd(2, 3).replace("which = sae_p_reason value = 0", "which = sae_p_reason value = 1.5")
    assert "value = 1.5" in line
    sim.run_ini(line)
    a = one(sim.pulse(D))
    assert (a.status, a.code) == (AckStatus.REJECTED, AckCode.RANGE)
    assert sim.country(2).seq_applied == 3


def test_ship_slots_minus_one_keep_and_value_stored_unused() -> None:
    sim = make(1)
    sim.run_ini(cmd(2, 1))
    assert one(sim.pulse(D)).status is AckStatus.APPLIED
    assert sim.country(2).ship["cruiser"] == 50  # -1 = keep
    sim.stage_raw(2, {"sae_p_ship_cruiser": 80, "sae_seq_pending": 2})
    assert one(sim.pulse(D)).status is AckStatus.APPLIED
    assert sim.country(2).ship["cruiser"] == 80
