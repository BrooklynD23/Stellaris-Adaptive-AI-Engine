from __future__ import annotations

from sae.contracts.c4_parse import GodView
from sae.contracts.c6_adapter import Observation

from ._asm_helpers import hb, make, obs_of, plyr, result, snap

SENT = 987654.321
GOD = GodView(
    fleet_power=SENT, systems_owned=424242, tech_researched=313131, alloys_income=SENT + 1, fortified_starbases=171717
)


def test_god_reaches_sink_and_never_observation() -> None:
    a, sink, _ = make()
    items: list[object] = []
    items += a.on_record(plyr("2231.01.01", fleet=SENT))
    items += a.on_record(hb("2231.01.01"))
    items += a.on_record(snap("2231.01.01"))
    items += a.on_parse(result("2231.01.01", god=GOD), 0)
    items += a.on_record(hb("2231.02.01", 1))
    items += a.flush()
    obs = obs_of(items)
    assert len(obs) >= 3
    dumped = "".join(o.model_dump_json() for o in obs)
    for s in ("987654", "424242", "313131", "171717"):
        assert s not in dumped
    assert any(g.plyr.get("fleet") == SENT for g in sink.got)
    assert any(g.player == GOD and g.source == "save" for g in sink.got)


def test_observation_has_no_god_fields() -> None:
    assert not {"plyr", "god", "player"} & set(Observation.model_fields)
