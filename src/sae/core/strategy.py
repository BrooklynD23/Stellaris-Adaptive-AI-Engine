"""Strategy selection: counter table + commitment + operator force + safety floor (T4 plan U2, v2 rules 1-2).

Order each tick: forced > commitment hold > table / below_threshold. The safety floor is a static
check of the chosen template; strategy never reads validator results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from sae.contracts.c5_bus import SelectReason, StrategySelectedPayload
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c9_control import ForceStrategy
from sae.contracts.common import COUNTER_TABLE, ReasonKey, Strategy
from sae.core.belief import ARCHS
from sae.core.dates import date_of
from sae.core.expansion import DEFAULT_EXPANSIONS, Template


@dataclass(frozen=True)
class StrategyState:
    """Strategy in force, its commitment window and any operator force."""

    strategy: Strategy | None = None
    commit_from_mi: int | None = None
    commit_until_mi: int | None = None
    opened_by: str | None = None
    opened_key: ReasonKey | None = None  # reason_key of the decision that opened the window
    window_forced: bool = False
    forced: tuple[Strategy, int] | None = None  # (strategy, until_mi)
    last_mi: int | None = None
    reopen: bool = False  # a force/clear asks for a fresh window next tick


@dataclass(frozen=True)
class WindowClosed:
    """A commitment window that ended this tick (feeds the outcome event)."""

    strategy: Strategy
    from_mi: int
    to_mi: int
    forced: bool
    opened_by: str | None


def apply_force(state: StrategyState, cmd: ForceStrategy, mi: int) -> StrategyState:
    """Apply a C9 force. Flags the current window as ended so the next tick opens a fresh one immediately."""
    if cmd.strategy is None:
        # A no-op clear (nothing forced) must not reopen the commitment window.
        return replace(state, forced=None, reopen=state.reopen or state.forced is not None)
    return replace(state, forced=(cmd.strategy, mi + cmd.months), reopen=True)


def reanchor(state: StrategyState, mi: int) -> StrategyState:
    """Session reset (v2 rule 2): keep remaining commitment/force months, anchored at ``mi``."""
    last = state.last_mi if state.last_mi is not None else mi
    commit_from, commit_until = state.commit_from_mi, state.commit_until_mi
    if commit_until is not None:
        commit_from, commit_until = mi, mi + max(0, commit_until - last)
    forced = state.forced
    if forced is not None:
        forced = (forced[0], mi + max(0, forced[1] - last))
    return replace(state, commit_from_mi=commit_from, commit_until_mi=commit_until, forced=forced, last_mi=mi)


def _floor_ok(strategy: Strategy, cfg: EngineConfig, templates: Mapping[Strategy, Template]) -> bool:
    """Static check: a template exists for the strategy (C3 v1.1 has no fleet weights to floor-check)."""
    return strategy in templates


def select(
    state: StrategyState,
    posterior: Sequence[float],
    mi: int,
    cfg: EngineConfig,
    *,
    decision_id: str,
    templates: Mapping[Strategy, Template] = DEFAULT_EXPANSIONS,
) -> tuple[StrategyState, StrategySelectedPayload, WindowClosed | None]:
    """One strategy decision at month ``mi``. ``posterior`` is in ``ARCHS`` order."""
    top = max(range(len(ARCHS)), key=lambda i: (posterior[i], -i))  # ties -> declaration order
    conf = min(1.0, max(0.0, float(posterior[top])))
    forced = state.forced if state.forced is not None and mi < state.forced[1] else None
    expired = state.forced is not None and forced is None  # force ran out: re-evaluate via table now
    reopen = state.reopen or expired
    window_open = (not reopen and state.commit_until_mi is not None and state.commit_from_mi is not None
                   and mi < state.commit_until_mi)
    if forced is not None:
        chosen, reason, is_forced = forced[0], SelectReason.FORCED, True
    elif window_open and state.strategy is not None:
        chosen, reason, is_forced = state.strategy, SelectReason.COMMITMENT_HOLD, state.window_forced
    elif conf >= cfg.belief_threshold:
        chosen, reason, is_forced = COUNTER_TABLE[ARCHS[top]], SelectReason.TABLE, False
    else:
        chosen, reason, is_forced = Strategy.BASELINE, SelectReason.BELOW_THRESHOLD, False
    if reason not in (SelectReason.COMMITMENT_HOLD, SelectReason.FORCED) and chosen != Strategy.BASELINE \
            and not _floor_ok(chosen, cfg, templates):
        chosen, reason = Strategy.BASELINE, SelectReason.SAFETY_FLOOR
    if reason == SelectReason.COMMITMENT_HOLD:
        key = state.opened_key or ReasonKey.BASELINE
    elif reason == SelectReason.FORCED:
        key = ReasonKey.OPERATOR_FORCED
    elif reason == SelectReason.SAFETY_FLOOR:
        key = ReasonKey.SAFETY_FLOOR
    else:
        key = templates[chosen].reason_key if chosen in templates else ReasonKey.BASELINE
    new, closed = replace(state, last_mi=mi, forced=forced), None
    # Open a window only on reopen, strategy change, or expiry (not every forced tick).
    if reason == SelectReason.FORCED:
        open_new = reopen or not window_open or state.strategy != chosen
    else:
        open_new = reason != SelectReason.COMMITMENT_HOLD
    if open_new:
        if state.commit_from_mi is not None and state.commit_until_mi is not None and state.strategy is not None:
            closed = WindowClosed(state.strategy, state.commit_from_mi, mi, state.window_forced, state.opened_by)
        # UNVERIFIED (A-21): 24-month window length is a HYP; M0-8 checks effects appear in that span.
        until = forced[1] if forced is not None else mi + cfg.commitment_months
        new = replace(new, commit_from_mi=mi, commit_until_mi=until, opened_by=decision_id, opened_key=key,
                      window_forced=is_forced, reopen=False)
    new = replace(new, strategy=chosen)
    if new.commit_from_mi is None or new.commit_until_mi is None:
        raise ValueError("strategy window unset after select (inconsistent StrategyState)")
    payload = StrategySelectedPayload(
        strategy=chosen, previous=state.strategy,
        switched=state.strategy is not None and state.strategy != chosen, reason=reason,
        top_archetype=ARCHS[top], confidence=conf,
        commit_from=date_of(new.commit_from_mi), commit_until=date_of(new.commit_until_mi), reason_key=key,
    )
    return new, payload, closed
