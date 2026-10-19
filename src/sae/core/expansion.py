"""Desired-state expansion: strategy -> full per-AI intents (C3 section 6; T4 plan U2 expansion).

Pure. Every call restates every knob (econ, posture) for each managed AI that
is in contact with the player; AIs out of contact get nothing (fairness, C3 section 4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sae.contracts.c3_intent import Intent, SetEconPlan, SetMilitaryPosture
from sae.contracts.c4_parse import LegalView
from sae.contracts.c8_config import EngineConfig
from sae.contracts.common import EconPlan, Posture, ReasonKey, Sid, Strategy


@dataclass(frozen=True)
class Template:
    """What one strategy asks of each AI in contact (C3 v1.1: no fleet bias)."""

    econ: EconPlan
    posture: Posture
    reason_key: ReasonKey = ReasonKey.BASELINE


# UNVERIFIED (A-21/A-23): C3 v1.1 section 6 defaults; M0-8/M0-6 test whether the knobs they drive
# (econ flag, opinion modifier) change AI behaviour at all. Fleet bias was dropped from v0 (C3 v1.1).
DEFAULT_EXPANSIONS: Mapping[Strategy, Template] = {
    Strategy.OUTSCALE_SIEGE: Template(EconPlan.MILITARY, Posture.OFFENSIVE, ReasonKey.TURTLE_SIEGE),
    Strategy.EARLY_PUNISH: Template(EconPlan.MILITARY, Posture.OFFENSIVE, ReasonKey.TECH_RUSH_PUNISH),
    Strategy.HOLD_COUNTERPUNCH: Template(EconPlan.MILITARY, Posture.DEFENSIVE, ReasonKey.AGGRESSION_HOLD),
    Strategy.BASELINE: Template(EconPlan.BALANCED, Posture.DETERRENT, ReasonKey.BASELINE),
}


def desired_intents(
    strategy: Strategy,
    reason_key: ReasonKey,
    managed: Sequence[Sid],
    legal: Sequence[LegalView],
    player_sid: Sid | None,
    cfg: EngineConfig,
    decision_id: str,
    next_id: int,
    templates: Mapping[Strategy, Template] = DEFAULT_EXPANSIONS,
) -> tuple[tuple[Intent, ...], int]:
    """Full desired state for every in-contact managed AI; returns ``(intents, advanced next_id)``.

    ``reason_key`` goes on each econ intent (the caller picks strategy/operator_forced/safety_floor).
    """
    if player_sid is None:
        return (), next_id
    tpl = templates[strategy]
    contact = {v.observer_sid for v in legal if v.in_contact}
    out: list[Intent] = []
    n = next_id
    for sid in managed:
        if sid not in contact or sid == player_sid:
            continue
        common = {"decision_id": decision_id, "country": sid, "ttl_months": cfg.ttl_months}
        out.append(SetEconPlan(intent_id=f"i-{n:06d}", plan=tpl.econ, reason_key=reason_key, **common))
        out.append(SetMilitaryPosture(intent_id=f"i-{n + 1:06d}", posture=tpl.posture, target=player_sid, **common))
        n += 2
    return tuple(out), n
