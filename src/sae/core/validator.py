"""Intent validator: C3 section 4 rules in exact order. Pure; never raises on bad intents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from sae.contracts.c3_intent import INTENT_ADAPTER, RESERVED_KINDS, Intent
from sae.contracts.c5_bus import RejectRule
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c8_config import EngineConfig
from sae.contracts.common import Sid

MAX_DETAIL = 500

Rejected = tuple["Intent | Mapping[str, Any]", RejectRule, str]


@dataclass(frozen=True)
class ValidationContext:
    """Everything the rules need; frozen."""

    mode: AdapterMode
    managed: frozenset[Sid]
    player_sid: Sid | None
    in_contact: frozenset[Sid]
    cfg: EngineConfig
    batches_this_month: int = 0
    is_reemission: bool = False
    observe_only: bool = False  # Ironman (Wave 3 N6): same rule id as release mode


def _check(intent: Intent, ctx: ValidationContext) -> tuple[RejectRule, str] | None:
    """Per-intent rules mode .. safety_floor (schema/reserved handled by the caller)."""
    if ctx.observe_only:
        return RejectRule.MODE, "ironman: observe only (no live input)"
    if ctx.mode == AdapterMode.RELEASE:
        return RejectRule.MODE, "release mode has no live input"
    if intent.country not in ctx.managed:
        return RejectRule.NOT_MANAGED, f"country {intent.country} not managed"
    target = getattr(intent, "target", None)
    if target is not None:
        if target == intent.country or target != ctx.player_sid:
            return RejectRule.BAD_TARGET, f"target {target} is not the player"
        if intent.country not in ctx.in_contact:
            return RejectRule.NOT_IN_CONTACT, f"country {intent.country} not in contact"
    # safety_floor: no v1.1 intent kind can fail it (fleet bias dropped, C3 v1.1 section 4); rule id kept.
    return None


def validate(
    intents: Sequence[Intent | Mapping[str, Any]], ctx: ValidationContext
) -> tuple[tuple[Intent, ...], tuple[Rejected, ...]]:
    """Return ``(accepted, rejected)``; rejected carries the original object, rule and detail <= 500 chars."""
    ok: list[Intent] = []
    bad: list[Rejected] = []
    for raw in intents:
        if isinstance(raw, Mapping) and raw.get("kind") in RESERVED_KINDS:
            bad.append((raw, RejectRule.RESERVED_KIND, f"kind {raw['kind']} is reserved"))
            continue
        try:
            data = raw if isinstance(raw, Mapping) else raw.model_dump(mode="python")
            intent = INTENT_ADAPTER.validate_python(data)
        except ValidationError as exc:
            bad.append((raw, RejectRule.SCHEMA, str(exc)[:MAX_DETAIL]))
            continue
        hit = _check(intent, ctx)
        if hit is None:
            ok.append(intent)
        else:
            bad.append((intent, hit[0], hit[1][:MAX_DETAIL]))
    if ok and not ctx.is_reemission and ctx.batches_this_month >= 1:
        bad.extend((i, RejectRule.RATE_LIMIT, "second batch this game month") for i in ok)
        return (), tuple(bad)
    allowed = set(sorted({i.country for i in ok})[: ctx.cfg.max_managed])
    final = [i for i in ok if i.country in allowed]
    over = f"more than {ctx.cfg.max_managed} countries"
    bad.extend((i, RejectRule.RATE_LIMIT, over) for i in ok if i.country not in allowed)
    return tuple(final), tuple(bad)
