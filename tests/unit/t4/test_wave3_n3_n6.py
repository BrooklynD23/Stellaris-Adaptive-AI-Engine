"""Wave 3 / proposal 017: adapter.close() (neutral ini, T2) before the sink on every exit; Ironman observe only."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sae.contracts.c5_bus import HealthKind, HealthPayload, IntentRejectedPayload, RejectRule
from sae.contracts.c6_adapter import AdapterMode, EmitReceipt
from sae.contracts.c7_governor import GameState, GovernorDecision, HealthLevel
from sae.core import lifecycle
from sae.core.engine import initial_state, on_emitted, on_observation
from sae.core.state import EngineCtx, EventDraft
from sae.daemon.loop import is_ironman

from .fake_synth import Sim
from .fakes import FakeGovernor, FakeSink, decision
from .test_hardening import CTX, ECFG, M0, TURTLE, obs, view
from .test_loop import EchoAdapter, go


class ClosingAdapter(EchoAdapter):
    """Like T2's LiveAdapter: close() neutralises sae_in.ini and reports ini_neutralised through the bus."""

    def __init__(self, months: int, sink: FakeSink, *, boom_at: int | None = None) -> None:
        super().__init__(months, delay=0.0, boom_at=boom_at)
        self.sink = sink
        self.close_calls = 0
        self.sink_closed_at_close: bool | None = None

    async def close(self) -> None:
        self.close_calls += 1
        self.sink_closed_at_close = self.sink.closed
        self.sink.emit(EventDraft(type="health", game_date=None, payload=HealthPayload(
            kind=HealthKind.INI_NEUTRALISED, component="adapter", state=GameState.PLAYING, level=HealthLevel.H0,
            detail="close")))
        await super().close()


def _closing(months: int, tmp_path: Path, **kw: Any) -> tuple[int, ClosingAdapter, FakeSink, list[int]]:
    sink = FakeSink()
    ad = ClosingAdapter(months, sink, boom_at=kw.pop("boom_at", None))
    reexec: list[int] = []
    hooks = kw.pop("hooks", None)
    if hooks:
        ad.hooks = hooks
    code, _, _, _ = go(ad, tmp_path, sink=sink, reexec=lambda: reexec.append(1), **kw)
    return code, ad, sink, reexec


def _neutralised_on_bus(sink: FakeSink) -> bool:
    return any(d.type == "health" and d.payload.kind is HealthKind.INI_NEUTRALISED  # type: ignore[attr-defined]
               for d in sink.drafts)


def test_adapter_closed_before_sink_on_clean_exit(tmp_path: Path) -> None:
    code, ad, sink, _ = _closing(4, tmp_path)
    assert code == 0 and ad.close_calls == 1 and ad.sink_closed_at_close is False and sink.closed
    assert _neutralised_on_bus(sink)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGTERM path")
def test_adapter_closed_before_sink_on_sigterm(tmp_path: Path) -> None:
    async def term() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.01)

    code, ad, sink, _ = _closing(10_000, tmp_path, hooks={3: term})
    assert code == 0 and ad.close_calls == 1 and ad.sink_closed_at_close is False and _neutralised_on_bus(sink)


def test_adapter_closed_before_sink_on_error(tmp_path: Path) -> None:
    code, ad, sink, _ = _closing(30, tmp_path, boom_at=4)
    assert code == 1 and ad.close_calls == 1 and ad.sink_closed_at_close is False and _neutralised_on_bus(sink)


def test_adapter_closed_before_sink_on_reexec(tmp_path: Path) -> None:
    exit_now: GovernorDecision = decision(daemon_should_exit=True)
    code, ad, _, reexec = _closing(10_000, tmp_path, gov=FakeGovernor(exit_now))
    assert code == 0 and reexec == [1] and ad.close_calls == 1 and ad.sink_closed_at_close is False


def test_ironman_receipt_is_untracked_and_audited() -> None:
    """Proposal 017 / C6 v1.2 section 1.10: error="ironman" -> no ACK tracking, no ini_write_failed, audit."""
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, out = on_observation(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1), CTX)
    assert out.batch is not None
    s, em = on_emitted(s, out.batch, EmitReceipt(seq=out.batch.seq, ok=False, error="ironman"))
    types = [d.type for d in em.drafts]
    assert types[0] == "intent_emitted" and "health" not in types
    rej = [d.payload for d in em.drafts if d.type == "intent_rejected"]
    assert len(rej) == len(out.batch.intents)
    assert all(isinstance(p, IntentRejectedPayload) and p.rule is RejectRule.MODE and "ironman" in p.detail
               for p in rej)
    assert lifecycle.in_flight_batches(s.lifecycle) == 0 and not s.lifecycle.resync_needed
    for k in range(1, 40):  # beliefs keep updating; nothing LOST, no ack_alarm, no ini_write_failed
        s, o = on_observation(s, obs(M0 + k, 100 + k, views=vs, asof=M0 + k), CTX)
        if o.batch is not None:
            s, _ = on_emitted(s, o.batch, EmitReceipt(seq=o.batch.seq, ok=False, error="ironman"))
        assert not any(d.type == "intent_ack" for d in o.drafts)
        assert not any(d.type == "health" and d.payload.kind in (HealthKind.ACK_ALARM,  # type: ignore[attr-defined]
                                                                 HealthKind.INI_WRITE_FAILED) for d in o.drafts)
    assert any(d.type == "belief_update" for d in o.drafts)


# -- N6 ----------------------------------------------------------------------------------------------------
class IronmanAdapter(EchoAdapter):
    ironman_detected = True


def test_ironman_adapter_observes_only(tmp_path: Path) -> None:
    ad = IronmanAdapter(30, delay=0.0)
    code, sink, _, _ = go(ad, tmp_path)
    assert code == 0 and ad.batches == []
    types = [d.type for d in sink.drafts]
    assert "belief_update" in types and "intent_emitted" not in types
    rej = [d.payload for d in sink.drafts if d.type == "intent_rejected"]
    assert rej and all(isinstance(p, IntentRejectedPayload) and p.rule is RejectRule.MODE
                       and p.detail.startswith("ironman") for p in rej)
    assert not any(d.type == "health" and d.payload.kind is HealthKind.ACK_ALARM  # type: ignore[attr-defined]
                   for d in sink.drafts)


def test_ironman_mid_run_never_raises_ack_alarm_for_in_flight_batches() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, out = on_observation(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1), CTX)
    assert out.batch is not None
    s, _ = on_emitted(s, out.batch, EmitReceipt(seq=out.batch.seq, ok=True))  # in flight, never acked
    iron = EngineCtx(mode=AdapterMode.LIVE, cfg=ECFG, observe_only=True)
    for k in range(1, 40):
        s, o = on_observation(s, obs(M0 + k, 100 + k, views=vs, asof=M0 - 1), iron)
        assert o.batch is None
        assert not any(d.type == "health" and d.payload.kind is HealthKind.ACK_ALARM  # type: ignore[attr-defined]
                       for d in o.drafts)


def test_is_ironman_accepts_property_or_event() -> None:
    plain = EchoAdapter(1)
    assert not is_ironman(plain, None)  # type: ignore[arg-type]
    assert is_ironman(IronmanAdapter(1), None)  # type: ignore[arg-type]
    ev = SimpleNamespace(events=("save_parsed", "ironman"))
    assert is_ironman(plain, ev)  # type: ignore[arg-type]
    assert Sim  # fake synth stays importable for the module's helpers
