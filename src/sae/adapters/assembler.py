"""Turns C1 log records and parse results into fog-legal observations (T2 U2).

Synchronous; the only side channels are the god sink and the health callback. God-view data
(PLYR, ``ParseResult.god``) leaves through :meth:`Assembler._route_god` and nowhere else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from sae.adapters import health as H
from sae.adapters.acks import to_intent_ack
from sae.adapters.health import HealthFn, HealthGate
from sae.adapters.keys import SaltError, folder_key
from sae.contracts.c1_log import (
    AckRecord,
    HeartbeatRecord,
    HelloRecord,
    LogRecord,
    PlayerRecord,
    SnapRecord,
)
from sae.contracts.c4_parse import CountryInfo, GodView, LegalView, ParseResult, SelfState
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import (
    GameClock,
    GameEvent,
    GodObservation,
    GodSink,
    IntentAck,
    ManagedState,
    Observation,
)

_log = logging.getLogger("sae.adapters.assembler")
_PLYR_KEYS = ("fleet", "navcap_used", "navcap", "en", "mi", "al", "al_inc", "wars")


def _months_between(a: str, b: str) -> int:
    """Whole months from date ``a`` to date ``b`` (``YYYY.MM.DD``); negative if b is earlier."""
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return (yb - ya) * 12 + (mb - ma)


@dataclass
class _Pending:
    m: int
    date: str
    epoch: int
    events: tuple[GameEvent, ...]


class Assembler:
    """Stateful record-to-observation assembler. Never raises."""

    def __init__(
        self,
        god_sink: GodSink,
        health: HealthFn,
        gate: HealthGate,
        *,
        salt_fn: Callable[[], bytes] | None = None,
        fixed_key: str | None = None,
    ) -> None:
        """``salt_fn`` (live/release; may raise :class:`SaltError`) or ``fixed_key`` (replay/synth) enables
        ``save_folder_key``; with neither it stays None."""
        self._salt_fn = salt_fn
        self._fixed_key = fixed_key
        self._salt: bytes | None = None
        self._salt_failed = False
        self._folder_key: str | None = None
        self._game_version: str | None = None
        self._god = god_sink
        self._health = health
        self._gate = gate
        self._epoch = 0
        self._hello_seen = False
        self._bumped = False
        self._events: list[GameEvent] = []
        self._last_hb_date: str | None = None
        self._date: str | None = None
        self._m: int | None = None
        self._last_obs_date: str | None = None
        self._pending: _Pending | None = None
        self._snaps: dict[int, SnapRecord] = {}
        self._save: ParseResult | None = None
        self._plyr_sid: int | None = None
        self._blocked = False
        self._mp = False
        self._knobs: tuple[int, int] | None = None

    @property
    def last_knobs(self) -> tuple[int, int] | None:
        """Latest ``(period, quiet)`` from a HELLO or HB, else None."""
        return self._knobs

    @property
    def emit_blocked(self) -> bool:
        """True after a HELLO with an unsupported intent schema."""
        return self._blocked

    @property
    def mp_blocked(self) -> bool:
        """True while the latest HELLO said ``mp=1`` (C6 section 1.6 write guard)."""
        return self._mp

    def clock(self) -> GameClock:
        """Current game time as last observed."""
        return GameClock(game_date=self._date, m=self._m, session_epoch=self._epoch)

    # -- record input -------------------------------------------------------------------------

    def on_record(self, rec: LogRecord) -> list[IntentAck | Observation]:
        """Process one record; returns acks and any observation it completes, in delivery order."""
        out: list[IntentAck | Observation] = []
        if self._pending is not None and rec.date > self._pending.date:
            out.extend(self.flush())
        if isinstance(rec, HelloRecord):
            out.extend(self.flush())
            self._on_hello(rec)
        elif isinstance(rec, HeartbeatRecord):
            out.extend(self.flush())
            self._on_hb(rec)
        elif isinstance(rec, SnapRecord):
            self._snaps[rec.sid] = rec
            self._touch(rec.date)
        elif isinstance(rec, PlayerRecord):
            self._on_plyr(rec)
        elif isinstance(rec, AckRecord):
            ack = to_intent_ack(rec, self._health)
            if ack is not None:
                out.append(ack)
        return out

    def _touch(self, date: str) -> None:
        if self._date is None or date > self._date:
            self._date = date

    def _bump(self, date: str) -> None:
        if self._bumped:
            return
        self._bumped = True
        self._epoch += 1
        self._snaps.clear()
        self._save = None
        self._folder_key = None
        self._game_version = None
        self._last_obs_date = None
        self._date = date
        H.reset_session(self._gate)

    def _on_hello(self, rec: HelloRecord) -> None:
        self._bump(rec.date)
        self._snaps.clear()  # even when the bump guard held: a HELLO means a (re)load
        self._save = None
        self._hello_seen = True
        self._last_hb_date = None
        self._events.append(GameEvent.HELLO)
        self._touch(rec.date)
        self._mp = rec.mp == 1
        self._knobs = (rec.period, rec.quiet)
        if rec.schema_ != 1:
            self._blocked = True
            H.version_skew(self._health, self._gate, f"mod schema {rec.schema_} != 1")
        else:
            self._blocked = False

    def _on_hb(self, rec: HeartbeatRecord) -> None:
        if self._last_hb_date is not None and rec.date < self._last_hb_date:
            self._events.append(GameEvent.DATE_REGRESSED)
            self._bump(rec.date)
        self._last_hb_date = rec.date
        self._touch(rec.date)
        self._m = rec.m
        self._knobs = (rec.period, rec.quiet)
        self._pending = _Pending(rec.m, rec.date, self._epoch, tuple(self._events))
        self._events = []
        self._bumped = False

    def _on_plyr(self, rec: PlayerRecord) -> None:
        self._plyr_sid = rec.sid
        self._touch(rec.date)
        plyr = {k: float(v) for k in _PLYR_KEYS if (v := getattr(rec, k)) is not None}
        self._route_god(rec.date, "log", plyr=plyr)

    # -- god boundary ---------------------------------------------------------------------------

    def _route_god(
        self, date: str, source: str, *, god: GodView | None = None, plyr: dict[str, float] | None = None
    ) -> None:
        """The only place god-view data leaves the assembler (to the god sink)."""
        try:
            obs = GodObservation(
                game_date=date,
                source="save" if source == "save" else "log",
                player=god,
                plyr=plyr or {},
            )
            self._god.write_god(obs)
        except Exception as exc:
            _log.error("god sink failed: %s", exc)

    # -- observations -----------------------------------------------------------------------------

    def flush(self) -> list[Observation]:
        """Emit the pending log observation, if any."""
        p, self._pending = self._pending, None
        if p is None:
            return []
        date = self._mono_date(p.date)
        obs = self._build("log", date, p.m, p.epoch, p.events)
        return [] if obs is None else [obs]

    def _key_for(self, folder: str | None) -> str | None:
        """C6 section 2 ``save_folder_key``; the salt is read once, a bad salt reports ``invalid_event`` once."""
        if self._fixed_key is not None:
            return self._fixed_key
        if folder is None or self._salt_fn is None or self._salt_failed:
            return None
        if self._salt is None:
            try:
                self._salt = self._salt_fn()
            except SaltError as exc:
                self._salt_failed = True
                self._invalid(f"salt: {exc}")
                return None
        return folder_key(self._salt, folder)

    def on_parse(self, result: ParseResult, epoch: int, folder: str | None = None) -> list[Observation]:
        """Route god data and emit the save observation; a result from an older epoch is dropped.

        ``folder`` is the parsed save's parent folder name (live/release); it is hashed, never stored.

        Does not flush the pending log observation: callers apply results after a poll's records
        and then call :meth:`flush`.
        """
        if self._date is not None and _months_between(self._date, result.meta.date) > 1:
            self._health(HealthKind.SAVE_SKIPPED, "save_ahead")  # foreign/baseline save from the future
            return []
        if epoch != self._epoch:
            _log.info("dropping stale parse result epoch=%d current=%d", epoch, self._epoch)
            return []
        out: list[Observation] = []
        if result.god is not None:
            self._route_god(result.meta.date, "save", god=result.god)
        self._save = result
        self._folder_key = self._key_for(folder) or self._folder_key
        self._game_version = result.meta.game_version[:64]
        events = (GameEvent.SAVE_PARSED, *self._events)
        self._events = []
        self._bumped = False
        date = self._mono_date(max(self._date or "", result.meta.date))
        obs = self._build("save", date, self._m, self._epoch, events)
        if obs is not None:
            out.append(obs)
        return out

    def _mono_date(self, date: str) -> str:
        if self._last_obs_date is not None and date < self._last_obs_date:
            return self._last_obs_date
        self._last_obs_date = date
        return date

    def _player_sid(self) -> int | None:
        if self._save is not None:
            for c in self._save.countries:
                if c.is_player and c.sid is not None:
                    return c.sid
        return self._plyr_sid

    def _build(
        self, source: str, date: str, m: int | None, epoch: int, events: tuple[GameEvent, ...]
    ) -> Observation | None:
        save = self._save
        legal: tuple[LegalView, ...] = save.legal if save else ()
        try:
            return Observation(
                game_date=date,
                m=m,
                session_epoch=epoch,
                source="save" if source == "save" else "log",
                player_sid=self._player_sid(),
                managed=self._managed(),
                player_legal=legal,
                legal_as_of=save.meta.date if save else None,
                events=events,
                save_folder_key=self._folder_key,
                game_version=self._game_version,
            )
        except ValidationError as exc:
            self._invalid(f"observation: {exc.errors(include_url=False)[0]['msg']}")
            return None

    def _invalid(self, detail: str) -> None:
        self._health(HealthKind.INVALID_EVENT, H.truncate(detail))

    def _managed(self) -> tuple[ManagedState, ...]:
        save = self._save
        infos: dict[int, CountryInfo] = {}
        if save is not None:
            infos = {c.sid: c for c in save.countries if c.managed and c.sid is not None}
        out: list[ManagedState] = []
        for sid in sorted(set(infos) | set(self._snaps)):
            try:
                out.append(self._merge(sid, infos.get(sid)))
            except ValidationError as exc:
                self._invalid(f"managed sid={sid}: {exc.errors(include_url=False)[0]['msg']}")
        return tuple(out)

    def _merge(self, sid: int, info: CountryInfo | None) -> ManagedState:
        """C6 section 2 merge of the latest SNAP and the latest save for one sid."""
        save = self._save
        ss: SelfState | None = save.self_state.get(str(sid)) if save else None
        snap = self._snaps.get(sid)
        use_snap = snap is not None and (save is None or snap.date >= save.meta.date)
        real = info.name if info is not None else None
        name = snap.name if snap is not None and (snap.name != "?" or real is None) else (real or str(sid))
        pending = ss.pending_seq if ss is not None else None
        sysown = ss.systems_owned if ss is not None else None
        if use_snap and snap is not None:
            return ManagedState(
                sid=sid, name=name, applied_seq=snap.seq, pending_seq=pending, applied_econ=snap.econ,
                applied_mil=snap.mil, fleet_power=snap.fleet, naval_cap_used=snap.navcap_used,
                naval_cap=snap.navcap, energy=snap.en, minerals=snap.mi, alloys=snap.al,
                alloys_income=snap.al_inc, systems_owned=sysown, wars=snap.wars, snap_date=snap.date,
            )
        if ss is None:
            return ManagedState(sid=sid, name=name, applied_seq=0)
        return ManagedState(
            sid=sid, name=name, applied_seq=ss.applied_seq, pending_seq=ss.pending_seq,
            applied_econ=ss.applied_econ, applied_mil=ss.applied_mil, fleet_power=ss.fleet_power,
            naval_cap_used=ss.naval_cap_used, naval_cap=ss.naval_cap, energy=ss.energy,
            minerals=ss.minerals, alloys=ss.alloys, alloys_income=ss.alloys_income,
            systems_owned=ss.systems_owned, wars=ss.wars,
        )
