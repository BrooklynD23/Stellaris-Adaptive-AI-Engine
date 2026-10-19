"""C9 application: turn an inbox item into an engine call, a result and a ``control`` draft (v2 rule 10)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sae.contracts.c5_bus import ControlPayload
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.c9_control import ControlResult, Pause, Resume
from sae.core import engine
from sae.core.state import EngineState, EventDraft
from sae.daemon.ports import InboxItem

log = logging.getLogger(__name__)

__all__ = ["ControlOutcome", "apply_item"]


@dataclass(frozen=True)
class ControlOutcome:
    """Result of applying one inbox item. ``suspend`` is the value for ``governor.suspend`` (None = leave)."""

    state: EngineState
    result: ControlResult
    drafts: tuple[EventDraft, ...]
    suspend: bool | None


def apply_item(
    state: EngineState, item: InboxItem, run_id: str, gov_state: GameState, level: HealthLevel,
    game_date: str | None,
) -> ControlOutcome:
    """Apply ``item`` between two engine calls.

    Items with a ready-made error result (bad file: no command; ``expired``: command present, C9 v1.1 section
    3.1) are completed with it and logged as a ``control`` bus event; the engine is never called for them.
    """
    cmd = item.command
    if cmd is None or item.error is not None:
        err = item.error
        if err is None:
            err = ControlResult(command_id="unknown", accepted=False, error="invalid",
                                detail="inbox item has neither command nor error", applied_at=None, status=None)
        log.warning("control item %s rejected: %s", item.handle, err.error)
        draft = EventDraft(type="control", game_date=game_date,
                           payload=ControlPayload(command=cmd, result=err))
        return ControlOutcome(state, err, (draft,), None)
    c = cmd.command
    # Report level S as soon as pause is applied; the governor reflects it on its next poll.
    shown = HealthLevel.S if isinstance(c, Pause) or (state.suspended and not isinstance(c, Resume)) else level
    new, result, outs = engine.on_control(state, cmd, run_id, gov_state, shown, game_date)
    suspend = True if isinstance(c, Pause) else False if isinstance(c, Resume) else None
    log.info("control %s applied: %s", cmd.command_id, c.cmd)
    return ControlOutcome(new, result, outs.drafts, suspend)
