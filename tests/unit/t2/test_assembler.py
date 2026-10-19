from __future__ import annotations

from sae.contracts.c4_parse import GodView, SelfState
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import GameEvent, IntentAck, Observation

from ._asm_helpers import ack, hb, hello, make, obs_of, plyr, result, snap, state


def run(a, recs):  # type: ignore[no-untyped-def]
    out = []
    for r in recs:
        out.extend(a.on_record(r))
    out.extend(a.flush())
    return obs_of(out)


def test_first_hb_no_bump_no_event() -> None:
    a, _, _ = make()
    (o,) = run(a, [hb("2231.01.01")])
    assert o.session_epoch == 0 and o.events == ()


def test_hello_bumps_and_event() -> None:
    a, _, _ = make()
    (o,) = run(a, [hello("2231.01.01"), hb("2231.01.01")])
    assert o.session_epoch == 1 and o.events == (GameEvent.HELLO,)


def test_regression_bumps() -> None:
    a, _, _ = make()
    o1, o2 = run(a, [hb("2231.05.01", 4), hb("2231.02.01", 1)])
    assert (o1.session_epoch, o2.session_epoch) == (0, 1)
    assert o2.events == (GameEvent.DATE_REGRESSED,)


def test_hello_plus_regression_one_bump() -> None:
    a, _, _ = make()
    _o1, o2 = run(a, [hb("2231.05.01"), hello("2231.02.01"), hb("2231.02.01")])
    assert o2.session_epoch == 1
    assert o2.events == (GameEvent.HELLO,)  # HELLO resets the HB baseline: no second bump


def test_snap_cache_cleared_on_new_epoch() -> None:
    a, _, _ = make()
    o1, o2 = run(a, [hb("2231.01.01"), snap("2231.01.01"), hello("2231.01.01"), hb("2231.01.01")])
    assert [m.sid for m in o1.managed] == [2] and o2.managed == ()


def test_pending_includes_same_pulse_snaps() -> None:
    a, _, _ = make()
    assert a.on_record(hb("2231.01.01")) == []
    assert a.on_record(snap("2231.01.01", sid=3)) == []
    (o,) = obs_of(a.on_record(hb("2231.02.01", 1)))
    assert [m.sid for m in o.managed] == [3] and o.m == 0 and o.game_date == "2231.01.01"


def test_ack_precedes_obs() -> None:
    a, _, _ = make()
    a.on_record(hb("2231.01.01"))
    out = a.on_record(ack("2231.01.01")) + a.flush()
    assert isinstance(out[0], IntentAck) and isinstance(out[1], Observation)


def test_save_snap_merge_snap_newer() -> None:
    a, _, _ = make()
    a.on_parse(result("2231.01.01"), 0)
    (o,) = run(a, [hb("2231.03.01"), snap("2231.03.01", seq=5, fleet=11.0, name="Live")])
    (m,) = o.managed
    assert (m.name, m.applied_seq, m.fleet_power, m.pending_seq, m.snap_date) == ("Live", 5, 11.0, 9, "2231.03.01")
    assert m.systems_owned == 7


def test_save_snap_merge_save_newer() -> None:
    a, _, _ = make()
    run(a, [hb("2231.01.01"), snap("2231.01.01", seq=1, fleet=11.0)])
    (o,) = a.on_parse(result("2231.02.01"), 0)
    (m,) = o.managed
    assert (m.applied_seq, m.fleet_power, m.snap_date, m.name) == (3, 77.0, None, "AI")


def test_snap_only_and_save_only_sids_union() -> None:
    a, _, _ = make()
    a.on_parse(result("2231.01.01", sids=(4,)), 0)
    (o,) = run(a, [hb("2231.01.01"), snap("2231.01.01", sid=2)])
    assert [m.sid for m in o.managed] == [2, 4]


def test_carry_forward_legal_and_player_sid() -> None:
    a, _, _ = make()
    (s,) = a.on_parse(result("2231.01.01"), 0)
    assert s.source == "save" and s.events == (GameEvent.SAVE_PARSED,) and s.player_sid == 1
    (o,) = run(a, [hb("2231.02.01")])
    assert o.player_legal == s.player_legal and o.legal_as_of == "2231.01.01"


def test_player_sid_fallback_from_plyr() -> None:
    a, _, _ = make()
    (o,) = run(a, [plyr("2231.01.01", sid=8), hb("2231.01.01")])
    assert o.player_sid == 8


def test_monotonic_game_date() -> None:
    a, _, _ = make()
    run(a, [hb("2231.01.01")])
    (s,) = a.on_parse(result("2231.02.01"), 0)
    assert s.game_date == "2231.02.01"
    (o,) = run(a, [hb("2231.01.15", 1)])
    assert o.game_date == "2231.02.01"
    assert a.clock().game_date == "2231.01.15"


def test_on_parse_does_not_flush_pending() -> None:
    a, _, _ = make()
    a.on_record(hb("2231.01.01"))
    out = a.on_parse(result("2231.01.01"), 0)
    assert [o.source for o in out] == ["save"]
    assert [o.source for o in a.flush()] == ["log"]


def test_stale_epoch_parse_dropped() -> None:
    a, sink, _ = make()
    run(a, [hb("2231.05.01"), hello("2231.05.01"), hb("2231.06.01", 1)])
    assert (
        a.on_parse(
            result(
                "2231.04.01",
                god=GodView(fleet_power=1, systems_owned=1, tech_researched=1, alloys_income=1, fortified_starbases=1),
            ),
            0,
        )
        == []
    )
    assert sink.got == []
    (o,) = run(a, [hb("2231.07.01", 2)])
    assert o.legal_as_of is None and o.game_date == "2231.07.01"


def test_save_cleared_on_new_epoch() -> None:
    a, _, _ = make()
    run(a, [hb("2231.05.01")])
    a.on_parse(result("2231.05.01", ss={"2": state(seq=3)}), 0)
    (o,) = run(a, [hb("2231.02.01", 1), snap("2231.02.01", seq=1)])
    assert o.session_epoch == 1 and o.managed[0].applied_seq == 1 and o.legal_as_of is None


def test_hello_save_older_hb_bumps_once() -> None:
    a, _, _ = make()
    run(a, [hb("2231.05.01")])
    run(a, [hello("2231.02.01")])
    a.on_parse(result("2231.02.01"), 1)
    (o,) = run(a, [hb("2231.02.01", 1)])
    assert o.session_epoch == 1


def test_snap_cache_cleared_on_regressed_hb() -> None:
    a, _, _ = make()
    o1, o2 = run(a, [hb("2231.05.01"), snap("2231.05.01"), hb("2231.02.01", 1)])
    assert o1.managed and o2.managed == () and o2.session_epoch == 1


def test_observation_validation_error_dropped() -> None:
    a, _, ev = make()
    a._date = "2231.01.01"
    out = a._build("log", "bad-date", None, 0, ())
    assert out is None and any(k is HealthKind.INVALID_EVENT for k, _ in ev)


def test_out_of_range_dropped_invalid_event() -> None:
    a, _, ev = make()
    bad = SelfState.model_construct(**{**state().model_dump(), "applied_seq": 2_000_000})
    (o,) = a.on_parse(result("2231.01.01", sids=(2, 3), ss={"2": bad, "3": state()}), 0)
    assert [m.sid for m in o.managed] == [3]
    assert any(k is HealthKind.INVALID_EVENT for k, _ in ev)


def test_mp_blocked_follows_latest_hello() -> None:
    from sae.contracts.c1_log import HelloRecord

    a, _, _ = make()
    assert not a.mp_blocked
    a.on_record(HelloRecord(date="2231.01.01", mod="0.1.0", schema=1, period=1, quiet=0, mp=1))
    assert a.mp_blocked and not a.emit_blocked
    a.on_record(HelloRecord(date="2231.01.01", mod="0.1.0", schema=1, period=1, quiet=0, mp=0))
    assert not a.mp_blocked


def test_emit_blocked_and_cleared() -> None:
    a, _, ev = make()
    a.on_record(hello("2231.01.01", schema=2))
    assert a.emit_blocked and [k for k, _ in ev] == [HealthKind.VERSION_SKEW]
    a.on_record(hello("2231.01.01", schema=1))
    assert not a.emit_blocked


def test_god_sink_failure_does_not_raise() -> None:
    a, sink, _ = make()

    def boom(obs: object) -> None:
        raise OSError("disk")

    sink.write_god = boom  # type: ignore[method-assign]
    a.on_record(plyr("2231.01.01"))


def test_save_far_ahead_of_clock_is_skipped() -> None:
    a, sink, ev = make()
    run(a, [hb("2231.01.01")])
    n = len(sink.got)
    assert a.on_parse(result("2231.04.01"), 0) == []
    assert (HealthKind.SAVE_SKIPPED, "save_ahead") in ev and len(sink.got) == n


def test_hello_without_bump_clears_snaps_and_save() -> None:
    from sae.contracts.c1_log import HelloRecord

    a, _, _ = make()
    run(a, [hello("2231.01.01"), snap("2231.01.01", sid=5)])
    a.on_record(HelloRecord(date="2231.01.01", mod="0.1.0", schema=1, period=1, quiet=0))
    (o,) = run(a, [hb("2231.01.01", 1)])
    assert o.managed == ()


def test_snap_placeholder_name_does_not_override_save_name() -> None:
    a, _, _ = make()
    run(a, [hb("2231.01.01"), snap("2231.01.01", sid=2, name="?")])
    (o,) = a.on_parse(result("2231.01.01"), 0)
    assert all(m.name != "?" for m in o.managed if m.sid in {c.sid for c in result("2231.01.01").countries})
