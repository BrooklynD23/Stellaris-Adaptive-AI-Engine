"""IntentBatch -> C2 file -> simulator -> C1 ACK -> IntentAck, for every C2 §5 branch."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from sae.adapters.acks import to_intent_ack
from sae.adapters.simulator import ModSimulator, SimCountry
from sae.contracts.c1_log import AckRecord, parse_log_line
from sae.contracts.c2_ini import CountryCommand, IniFile, KnobCommand, parse_ini, render_ini
from sae.contracts.c3_intent import (
    DiploStance,
    IntentBatch,
    NoopHeartbeat,
    SetEconPlan,
    SetMilitaryPosture,
    compile_batch,
)
from sae.contracts.common import AckCode, AckStatus, EconPlan, Posture, ReasonKey, ShipSize, Stance

D = "2231.04.01"


def sim3() -> ModSimulator:
    sim = ModSimulator(
        [SimCountry(1, "H", is_ai=False)] + [SimCountry(i, f"A{i}", contact_with_human=True) for i in (2, 3)]
    )
    sim.pulse(D)  # sids 1 (human), 2, 3; m = 1
    return sim


def base(country: int = 2, **kw: object) -> dict[str, object]:
    d: dict[str, object] = {
        "intent_id": "i-000001",
        "decision_id": "d-0001.01-01",
        "country": country,
    }
    d.update(kw)
    return d


def batch(seq: int, *intents: object, m: int = 1) -> IntentBatch:
    return IntentBatch.model_validate({"seq": seq, "issued_date": D, "issued_m": m, "intents": list(intents)})


def run(sim: ModSimulator, b: IntentBatch) -> list[tuple[AckStatus, AckCode]]:
    sim.run_ini(render_ini(compile_batch(b)))
    out = []
    for line in sim.pulse(D):
        rec = parse_log_line(line)
        if isinstance(rec, AckRecord):
            ack = to_intent_ack(rec, lambda k, d: None)
            assert ack is not None and ack.seq == b.seq
            out.append((ack.status, ack.code))
    return out


OK = [(AckStatus.APPLIED, AckCode.OK)]


def test_applied_all_intent_kinds() -> None:
    sim = sim3()
    b = batch(
        1,
        SetEconPlan(**base(plan=EconPlan.RESEARCH, reason_key=ReasonKey.BASELINE)),  # type: ignore[arg-type]
        SetMilitaryPosture(**base(intent_id="i-000002", posture=Posture.OFFENSIVE, target=3)),  # type: ignore[arg-type]
        DiploStance(**base(intent_id="i-000003", stance=Stance.RIVAL, target=3)),  # type: ignore[arg-type]
    )
    assert run(sim, b) == OK
    c = sim.country(2)
    assert (c.econ, c.mil, c.dip, c.reason) == (3, 3, 3, 4)


def test_noop_heartbeat_applied() -> None:
    assert run(sim3(), batch(1, NoopHeartbeat(**base()))) == OK  # type: ignore[arg-type]


def test_duplicate_and_same_twice() -> None:
    sim = sim3()
    assert run(sim, batch(5, NoopHeartbeat(**base()))) == OK  # type: ignore[arg-type]
    assert run(sim, batch(5, NoopHeartbeat(**base()))) == []  # type: ignore[arg-type]
    assert run(sim, batch(3, NoopHeartbeat(**base()))) == [(AckStatus.DUPLICATE, AckCode.SEQ_OLD)]  # type: ignore[arg-type]


def test_not_managed_multiplayer_stale() -> None:
    sim = sim3()
    sim.set_player(2, False)
    assert run(sim, batch(1, NoopHeartbeat(**base()))) == [(AckStatus.REJECTED, AckCode.NOT_MANAGED)]  # type: ignore[arg-type]
    mp = sim3()
    mp.set_multiplayer(True)
    mp.pulse(D)
    assert run(mp, batch(1, NoopHeartbeat(**base()))) == [(AckStatus.REJECTED, AckCode.MULTIPLAYER)]  # type: ignore[arg-type]
    st_sim = sim3()
    stale = batch(1, NoopHeartbeat(**base(ttl_months=1)), m=0)  # type: ignore[arg-type]
    st_sim.pulse(D)
    assert run(st_sim, stale) == [(AckStatus.STALE, AckCode.EXPIRED)]


def test_bad_target() -> None:
    sim = sim3()
    b = batch(1, SetMilitaryPosture(**base(posture=Posture.DEFENSIVE, target=40)))  # type: ignore[arg-type]
    assert run(sim, b) == [(AckStatus.REJECTED, AckCode.BAD_TARGET)]


def test_range_and_schema_via_stage_raw() -> None:
    sim = sim3()
    sim.run_ini(render_ini(compile_batch(batch(1, NoopHeartbeat(**base())))))  # type: ignore[arg-type]
    sim.stage_raw(2, {"sae_p_econ": 9})
    recs = [parse_log_line(x) for x in sim.pulse(D)]
    assert [(r.status, r.code) for r in recs if isinstance(r, AckRecord)] == [(AckStatus.REJECTED, AckCode.RANGE)]
    sim.run_ini(render_ini(compile_batch(batch(2, NoopHeartbeat(**base())))))  # type: ignore[arg-type]
    sim.stage_raw(2, {"sae_p_schema": 2})
    recs = [parse_log_line(x) for x in sim.pulse(D)]
    assert [(r.status, r.code) for r in recs if isinstance(r, AckRecord)] == [
        (AckStatus.SCHEMA_MISMATCH, AckCode.SCHEMA)
    ]


def test_knob_batch_applies_to_hb() -> None:
    sim = sim3()
    b = IntentBatch.model_validate(
        {"seq": 1, "issued_date": D, "issued_m": 1, "knobs": KnobCommand(emit_period=6, quiet=0)}
    )
    sim.run_ini(render_ini(compile_batch(b)))
    sim.pulse(D)
    assert sim.period == 6


sids = st.integers(1, 40)


@st.composite
def commands(draw: st.DrawFn, seq: int | None = None) -> CountryCommand:
    sid = draw(sids)
    tgt = st.integers(1, 40).filter(lambda t: t != sid)
    mil = draw(st.sampled_from([-1, 1, 2, 3]))
    dip = draw(st.sampled_from([-1, 1, 2, 3, 4]))
    return CountryCommand(
        sid=sid,
        seq=seq or draw(st.integers(1, 1_000_000)),
        expires=draw(st.integers(0, 1_000_000)),
        reason=draw(st.integers(0, 6)),
        econ=draw(st.sampled_from([-1, 1, 2, 3, 4])),
        mil=mil,
        mil_target=-1 if mil == -1 else draw(tgt),
        dip=dip,
        dip_target=-1 if dip == -1 else draw(tgt),
    )


@st.composite
def inis(draw: st.DrawFn) -> IniFile:
    seq = draw(st.integers(1, 1_000_000))
    cs = draw(st.lists(commands(seq=seq), max_size=5, unique_by=lambda c: c.sid))
    knobs = draw(st.none() | st.builds(KnobCommand, emit_period=st.integers(1, 12), quiet=st.sampled_from([0, 1])))
    if knobs is None and not cs:
        knobs = KnobCommand(quiet=0)
    return IniFile(knobs=knobs, countries=tuple(sorted(cs, key=lambda c: c.sid)))


@settings(derandomize=True, deadline=None)  # deterministic; no wall-clock deadline under load
@given(inis())
def test_render_parse_roundtrip(ini: IniFile) -> None:
    assert parse_ini(render_ini(ini)) == ini


@settings(derandomize=True, deadline=None)  # deterministic; no wall-clock deadline under load
@given(inis())
def test_run_ini_staging_equals_command(ini: IniFile) -> None:
    sims = [SimCountry(1, "H", is_ai=False)]
    sims += [SimCountry(100 + i, f"A{i}", contact_with_human=True) for i in range(16)]
    sim = ModSimulator(sims)
    sim.pulse(D)  # sids 1..17; AIs 2..17 managed
    sim.run_ini(render_ini(ini))
    for cmd in ini.countries:
        if cmd.sid < 2 or cmd.sid > 17:
            continue
        c = sim.country(cmd.sid)
        assert c.seq_pending == cmd.seq
        s = c.staged
        assert (s["sae_p_expires"], s["sae_p_reason"], s["sae_p_econ"]) == (cmd.expires, cmd.reason, cmd.econ)
        assert (s["sae_p_mil"], s["sae_p_mil_target"]) == (cmd.mil, cmd.mil_target)
        assert (s["sae_p_dip"], s["sae_p_dip_target"]) == (cmd.dip, cmd.dip_target)
        for size in ShipSize:
            assert s[f"sae_p_ship_{size.value}"] == -1  # C2 v1.1: ship slots are always -1
