"""Shared builders for assembler tests."""

from __future__ import annotations

from sae.adapters.assembler import Assembler
from sae.adapters.health import new_gate
from sae.contracts.c1_log import AckRecord, HeartbeatRecord, HelloRecord, PlayerRecord, SnapRecord
from sae.contracts.c4_parse import Cost, CountryInfo, GodView, LegalView, ParseResult, SaveInfo, SaveMeta, SelfState
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import GodObservation, IntentAck, Observation
from sae.contracts.common import AckCode, AckStatus


class Sink:
    def __init__(self) -> None:
        self.got: list[GodObservation] = []

    def write_god(self, obs: GodObservation) -> None:
        self.got.append(obs)


def make() -> tuple[Assembler, Sink, list[tuple[HealthKind, str | None]]]:
    sink = Sink()
    ev: list[tuple[HealthKind, str | None]] = []
    return Assembler(sink, lambda k, d: ev.append((k, d)), new_gate()), sink, ev


def hello(date: str, schema: int = 1) -> HelloRecord:
    return HelloRecord(date=date, mod="0.1.0", schema=schema, period=1, quiet=0)


def hb(date: str, m: int = 0) -> HeartbeatRecord:
    return HeartbeatRecord(date=date, m=m, period=1, quiet=0)


def snap(date: str, sid: int = 2, seq: int = 0, fleet: float = 10.0, name: str = "AI") -> SnapRecord:
    return SnapRecord(date=date, sid=sid, seq=seq, fleet=fleet, name=name, econ=1, mil=1)


def plyr(date: str, sid: int = 1, fleet: float = 5.0) -> PlayerRecord:
    return PlayerRecord(date=date, sid=sid, fleet=fleet, name="P")


def ack(date: str) -> AckRecord:
    return AckRecord(date=date, sid=2, seq=1, status=AckStatus.APPLIED, code=AckCode.OK)


def state(seq: int = 3, fleet: float = 77.0) -> SelfState:
    return SelfState(
        fleet_power=fleet,
        naval_cap_used=1,
        naval_cap=2,
        energy=3,
        minerals=4,
        alloys=5,
        alloys_income=6,
        systems_owned=7,
        wars=0,
        applied_seq=seq,
        pending_seq=9,
        applied_econ=2,
        applied_mil=2,
    )


def result(
    date: str, sids: tuple[int, ...] = (2,), god: GodView | None = None, ss: dict[str, SelfState] | None = None
) -> ParseResult:
    countries = (
        CountryInfo(country_id=0, name="Player", sid=1, is_player=True, managed=False),
        *(CountryInfo(country_id=s, name=f"Save{s}", sid=s, is_player=False, managed=True) for s in sids),
    )
    return ParseResult(
        parser_version="t",
        save=SaveInfo(path="x.sav", size_bytes=1, mtime_ns=1),
        meta=SaveMeta(game_version="v", date=date),
        countries=countries,
        self_state=ss if ss is not None else {str(s): state() for s in sids},
        legal=tuple(LegalView(observer_sid=s, in_contact=False) for s in sids),
        god=god,
        sections=("meta",),
        cost=Cost(rss_peak_bytes=1, cpu_s=0, wall_s=0),
    )


def obs_of(items: list[object]) -> list[Observation]:
    return [i for i in items if isinstance(i, Observation)]


def acks_of(items: list[object]) -> list[IntentAck]:
    return [i for i in items if isinstance(i, IntentAck)]
