"""Test-local synthetic game: raw LegalView numbers from per-archetype generators (NOT LikelihoodModel).

``Sim`` drives the pure engine month by month: a log observation every month, a save observation
every ``save_every`` months (``keep_every=2`` drops every 2nd save), and a fake ACK echo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sae.contracts.c4_parse import LegalView
from sae.contracts.c6_adapter import AdapterMode, EmitReceipt, IntentAck, ManagedState, Observation
from sae.contracts.c8_config import EngineConfig
from sae.contracts.common import AckCode, AckStatus, Archetype
from sae.core.engine import EngineCtx, Outputs, initial_state, on_ack, on_emitted, on_observation
from sae.core.state import EngineState

START_YEAR = 2200
PLAYER = 1
AIS = (2, 3)
# (border fort ratio, systems per year, fleet cat, tech cat, first-war month or None). Same archetype scale
# as T2's synth PROFILES (the shared HYP ground truth until M2), with this module's own noise.
PARAMS: dict[Archetype, tuple[float, float, int, int, int | None]] = {
    Archetype.TURTLE: (0.9, 0.5, 0, 0, None),
    Archetype.TECH_RUSH: (0.1, 1.2, -1, 2, None),
    Archetype.EARLY_AGGRESSION: (0.5, 3.0, 2, 0, 6),
    Archetype.BALANCED: (0.33, 1.8, 0, 0, None),
}

#: Per-save noise multiplier: the balanced player is erratic (mixed signals).
SPREAD: dict[Archetype, float] = {a: 1.0 for a in Archetype} | {Archetype.BALANCED: 3.0}


def date_for(t: int) -> str:
    """Game date of month ``t`` since 2200.01."""
    return f"{START_YEAR + t // 12:04d}.{t % 12 + 1:02d}.01"


def _cat(rng: random.Random, base: int, spread: float) -> int:
    return max(-2, min(2, round(base + rng.gauss(0, 0.7 * spread))))


@dataclass
class Sim:
    """Mutable synthetic world plus the engine driver."""

    archetype: Archetype
    seed: int
    cfg: EngineConfig = field(default_factory=EngineConfig)
    mode: AdapterMode = AdapterMode.SYNTH
    save_every: int = 12
    keep_every: int = 1
    m_none: frozenset[int] = frozenset()
    t: int = 0
    epoch: int = 0
    applied: dict[int, int] = field(default_factory=dict)
    views: tuple[LegalView, ...] = ()
    as_of: str | None = None
    saves: int = 0
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def _views(self) -> tuple[LegalView, ...]:
        fort, growth, fleet, tech, war_t = PARAMS[self.archetype]
        rng, out = self.rng, []
        spread = SPREAD[self.archetype]
        for sid in AIS:
            war = war_t is not None and self.t >= war_t
            out.append(LegalView(
                observer_sid=sid, in_contact=True,
                systems_owned=max(0, round(10 + growth * self.t / 12 + rng.gauss(0, 1))),
                frontier_systems=6,
                fortified_border_starbases=max(0, min(6, round(fort * 6 + rng.gauss(0, 0.8 * spread)))),
                fleet_power_cat=_cat(rng, fleet, spread), tech_cat=_cat(rng, tech, spread),
                at_war_with_observer=war, player_war_count=1 if war else 0))
        return tuple(out)

    def obs(self, source: str, in_contact: bool = True) -> Observation:
        """Build the current observation."""
        managed = tuple(ManagedState(sid=s, name=f"ai{s}", applied_seq=self.applied.get(s, 0)) for s in AIS)
        views = self.views if in_contact else tuple(
            LegalView(observer_sid=v.observer_sid, in_contact=False) for v in self.views)
        return Observation(
            game_date=date_for(self.t), m=None if self.t in self.m_none else self.t, session_epoch=self.epoch,
            source="save" if source == "save" else "log", player_sid=PLAYER, managed=managed,
            player_legal=views, legal_as_of=self.as_of if in_contact else None)

    def feed(self, state: EngineState, obs: Observation, ctx: EngineCtx) -> tuple[EngineState, list[Outputs]]:
        """One observation through the engine, emit receipt and ACK echo."""
        state, out = on_observation(state, obs, ctx)
        outs = [out]
        if out.batch is not None:
            b = out.batch
            state, o2 = on_emitted(state, b, EmitReceipt(seq=b.seq, ok=self.mode != AdapterMode.RELEASE))
            outs.append(o2)
            for c in sorted({i.country for i in b.intents}):
                ack = IntentAck(seq=b.seq, country=c, status=AckStatus.APPLIED, code=AckCode.OK,
                                game_date=obs.game_date)
                self.applied[c] = b.seq
                state, o3 = on_ack(state, ack)
                outs.append(o3)
        return state, outs

    def ctx(self) -> EngineCtx:
        """Engine context for this sim."""
        return EngineCtx(mode=self.mode, cfg=self.cfg)

    def step(self, state: EngineState) -> tuple[EngineState, list[Outputs]]:
        """Advance one game month: log observation, then maybe a save observation."""
        ctx = self.ctx()
        state, outs = self.feed(state, self.obs("log"), ctx)
        if self.t % self.save_every == self.save_every // 2:
            if self.saves % self.keep_every == 0:
                self.views, self.as_of = self._views(), date_for(self.t)
                state, more = self.feed(state, self.obs("save"), ctx)
                outs += more
            self.saves += 1
        self.t += 1
        return state, outs

    def run(self, months: int, state: EngineState | None = None) -> tuple[EngineState, list[Outputs]]:
        """Run ``months`` more months."""
        st = state if state is not None else initial_state(self.cfg)
        allo: list[Outputs] = []
        for _ in range(months):
            st, o = self.step(st)
            allo += o
        return st, allo
