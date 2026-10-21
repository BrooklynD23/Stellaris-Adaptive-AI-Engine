"""Python mod simulator: reproduces ``mod/sae_bridge/SEMANTICS.md`` §§1-8 (C1 lines, C2 staging).

Deterministic, no I/O. All Stellaris behaviour modelled here is UNVERIFIED until M0 (see
``mod/sae_bridge/UNVERIFIED.md``); M0 would replay these cases against the real mod's ``game.log``.
Countries are iterated by ascending in-game country id for sid assignment and by ascending ``sid``
everywhere else (SEMANTICS preamble).
"""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from sae.contracts.c1_log import AckRecord, HeartbeatRecord, HelloRecord, LogRecord, format_log_line
from sae.contracts.common import AckCode, AckStatus, ShipSize

log = logging.getLogger(__name__)

MOD_VERSION = "0.1.0"
MAX_MANAGED = 16  # @sae_max_managed
_SEQ_MAX = 1_000_000
_ECON = ("balanced", "military", "research", "expansion")
_MIL = ("defensive", "deterrent", "offensive")
_DIP = ("friendly", "neutral", "rival", "hostile")
_SIZES = tuple(s.value for s in ShipSize)

Value = int | float


def _num(text: str) -> Value:
    """Console number: the game stages fractional values as given (R12); integers stay int."""
    return float(text) if "." in text else int(text)
IntStyle = Literal["plain", "clausewitz"]

_COUNTRY_LAX = re.compile(
    r"^effect every_country = \{ limit = \{ has_country_flag = sae_managed "
    r"check_variable = \{ which = sae_sid value = (\d+) \} \} (.*) \}$"
)
_KNOB_LAX = re.compile(r"^effect (set_variable = .*)$")
_SETVAR = re.compile(r"set_variable = \{ which = (sae_[a-z_]+) value = (-?\d+(?:\.\d+)?) \}")


@dataclass
class SimCountry:
    """One country: engine-side facts plus the §1 per-country script state (valid once it has a sid)."""

    country_id: int
    name: str
    is_ai: bool = True
    alive: bool = True
    contact_with_human: bool = False
    fleet: float = 0.0
    navcap_used: float = 0.0
    navcap: float = 0.0
    wars: int = 0
    sid: int | None = None
    seq_applied: int = 0
    econ: int = 0
    mil: int = 0
    dip: int = 0
    mil_target: int = 0
    dip_target: int = 0
    ship: dict[str, int] = field(default_factory=lambda: {s: 50 for s in _SIZES})
    reason: int = 0
    managed: bool = False
    flags: set[str] = field(default_factory=set)
    modifiers: set[tuple[str, int]] = field(default_factory=set)
    staged: dict[str, Value] = field(default_factory=dict)  # sae_p_*; absent = unset (reads as 0, A-41)
    seq_pending: Value | None = None  # None = unset
    knob_period: Value | None = None  # sae_emit_period on the human
    knob_quiet: Value | None = None  # sae_quiet on the human
    popup_flag: bool = False  # sae_t_popup (per pulse)


@dataclass
class _Anchor:
    initialised: bool = False
    m: int = 0
    sid_next: int = 0
    period: int = 1
    quiet: int = 0
    mp: int = 0
    managed: int = 0


@dataclass
class SimState:
    """Deep-copied simulator state for save/reload."""

    countries: dict[int, SimCountry]
    anchor: _Anchor
    multiplayer: bool


class ModSimulator:
    """Simulates the ``sae_bridge`` mod; every method returns the log lines the mod would print."""

    def __init__(
        self, countries: Iterable[SimCountry], *, multiplayer: bool = False, int_style: IntStyle = "plain"
    ) -> None:
        self._countries: dict[int, SimCountry] = {c.country_id: c for c in countries}
        self._anchor = _Anchor()
        self._multiplayer = multiplayer
        self._int_style = int_style
        self.flavour_events: list[tuple[str, int, int, int]] = []

    # ---- inspection -------------------------------------------------------------------------
    @property
    def m(self) -> int:
        """Mod month counter."""
        return self._anchor.m

    @property
    def period(self) -> int:
        """Effective ``sae_g_period``."""
        return self._anchor.period

    @property
    def quiet(self) -> int:
        """Effective ``sae_g_quiet``."""
        return self._anchor.quiet

    @property
    def mp(self) -> int:
        """``sae_g_mp`` as of the last check."""
        return self._anchor.mp

    @property
    def sid_next(self) -> int:
        """Last sid handed out."""
        return self._anchor.sid_next

    @property
    def initialised(self) -> bool:
        """Whether the anchor is initialised (``sae_g_m`` set)."""
        return self._anchor.initialised

    def country(self, sid: int) -> SimCountry:
        """A copy of the state of the country with this sae sid. Raises KeyError if none."""
        for c in self._countries.values():
            if c.sid == sid:
                return copy.deepcopy(c)
        raise KeyError(sid)

    def human(self) -> SimCountry | None:
        """A copy of the first human country (ascending id), or None in observer mode."""
        h = self._first_human()
        return copy.deepcopy(h) if h else None

    # ---- environment control ----------------------------------------------------------------
    def _by_id(self, country_id: int) -> SimCountry:
        return self._countries[country_id]

    def set_contact(self, country_id: int, contact: bool) -> None:
        """Set communications with a human."""
        self._by_id(country_id).contact_with_human = contact

    def set_alive(self, country_id: int, alive: bool) -> None:
        """Mark a country alive or dead."""
        self._by_id(country_id).alive = alive

    def set_player(self, country_id: int, is_ai: bool) -> None:
        """Hot-join or ``play``: a human takes over (``is_ai=False``) or releases a country."""
        self._by_id(country_id).is_ai = is_ai

    def set_multiplayer(self, multiplayer: bool) -> None:
        """Set the engine-side ``is_multiplayer`` value (read at the next pulse)."""
        self._multiplayer = multiplayer

    def add_country(self, country: SimCountry) -> None:
        """Add a country mid-game; it gets a sid at the next pulse (§4 step 3)."""
        self._countries[country.country_id] = country

    # ---- save reload ------------------------------------------------------------------------
    def snapshot(self) -> SimState:
        """Deep copy of all state."""
        return SimState(copy.deepcopy(self._countries), copy.deepcopy(self._anchor), self._multiplayer)

    def restore(self, state: SimState) -> None:
        """Reload a save: ``m``, ``applied_seq`` and staged variables revert to the snapshot."""
        self._countries = copy.deepcopy(state.countries)
        self._anchor = copy.deepcopy(state.anchor)
        self._multiplayer = state.multiplayer

    # ---- helpers ----------------------------------------------------------------------------
    def _first_human(self) -> SimCountry | None:
        for cid in sorted(self._countries):
            c = self._countries[cid]
            if c.alive and not c.is_ai:
                return c
        return None

    def _by_sid(self) -> list[SimCountry]:
        return sorted((c for c in self._countries.values() if c.sid is not None), key=lambda c: c.sid or 0)

    @staticmethod
    def _line(rec: LogRecord) -> str:
        return format_log_line(rec)

    def _hello(self, date: str) -> str:
        a = self._anchor
        return self._line(
            HelloRecord.model_validate(
                {
                    "date": date,
                    "mod": MOD_VERSION,
                    "schema": 1,
                    "period": a.period,
                    "quiet": a.quiet,
                    "mp": a.mp,
                }
            )
        )

    def _hello_steps(self, date: str) -> str:
        # §3 steps 2-3 (step 1, anchor init, done by the caller)
        self._anchor.mp = 1 if self._multiplayer else 0
        return self._hello(date)

    # ---- §3 / §4 ----------------------------------------------------------------------------
    def game_start(self, date: str) -> list[str]:
        """``sae.3``: initialise the anchor if needed and print HELLO (§3)."""
        self._anchor.initialised = True
        return [self._hello_steps(date)]

    def pulse(self, date: str) -> list[str]:
        """``sae.0``: one monthly pulse (§4 steps 0-7)."""
        a = self._anchor
        out: list[str] = []
        if not a.initialised:  # step 0 (R6)
            a.initialised = True
            out.append(self._hello_steps(date))
        a.m += 1  # step 1
        self._read_knobs()  # step 2
        self._assign_sids()  # step 3
        self._flag_managed()  # step 4
        out.append(  # step 5
            self._line(HeartbeatRecord(date=date, m=a.m, period=a.period, quiet=a.quiet))
        )
        for c in self._countries.values():
            c.popup_flag = False
        for c in self._by_sid():  # step 6
            if c.alive and c.seq_pending is not None:
                ack = self._apply(c, date)
                if ack is not None:
                    out.append(ack)
        if a.quiet == 0:  # step 7
            for c in self._by_sid():
                if c.alive and (a.m + (c.sid or 0)) % a.period == 0:
                    out.extend(self._emit(c, date))
        return out

    def _read_knobs(self) -> None:
        a = self._anchor
        a.mp = 1 if self._multiplayer else 0
        human = self._first_human()
        if a.mp or human is None:
            return
        p, q = human.knob_period, human.knob_quiet
        # SEMANTICS R5: out-of-range or non-integer values are ignored silently
        if p is not None and p == int(p) and 1 <= p <= 12:
            a.period = int(p)
        if q is not None and q in (0, 1):
            a.quiet = int(q)

    def _assign_sids(self) -> None:
        a = self._anchor
        for cid in sorted(self._countries):
            c = self._countries[cid]
            if c.alive and c.sid is None:
                a.sid_next += 1
                c.sid = a.sid_next
                c.seq_applied = 0
                c.econ = c.mil = c.dip = c.mil_target = c.dip_target = c.reason = 0
                c.ship = {s: 50 for s in _SIZES}

    def _flag_managed(self) -> None:
        a = self._anchor
        for c in self._by_sid():
            if c.alive and c.is_ai and not c.managed and c.contact_with_human and a.managed < MAX_MANAGED:
                c.managed = True
                a.managed += 1

    # ---- §7 ---------------------------------------------------------------------------------
    def _v(self, value: float) -> str:
        """Print a script variable: plain (``17``) or Clausewitz style (``17.000``, A-06)."""
        if self._int_style == "clausewitz":
            return f"{value:.3f}"
        return str(int(value)) if value == int(value) else f"{value:.3f}".rstrip("0")

    # Literal templates of sae_emit_snap / sae_emit_plyr in sae_effects.txt (key order per SEMANTICS §7;
    # R9: no en/mi/al/al_inc).
    def _snap(self, c: SimCountry, date: str) -> str:
        v = self._v
        return (
            f"SAE|v1|SNAP|{date}|sid={v(c.sid or 0)}|seq={v(c.seq_applied)}|fleet={v(c.fleet)}"
            f"|navcap_used={v(c.navcap_used)}|navcap={v(c.navcap)}|wars={v(c.wars)}|econ={v(c.econ)}"
            f"|mil={v(c.mil)}|contact={v(1 if c.contact_with_human else 0)}|name={c.name or '?'}"
        )

    def _plyr(self, c: SimCountry, date: str) -> str:
        v = self._v
        return (
            f"SAE|v1|PLYR|{date}|sid={v(c.sid or 0)}|fleet={v(c.fleet)}|navcap_used={v(c.navcap_used)}"
            f"|navcap={v(c.navcap)}|wars={v(c.wars)}|name={c.name or '?'}"
        )

    def _emit(self, c: SimCountry, date: str) -> list[str]:
        if c.sid is None:
            return []
        if not c.is_ai:
            return [self._plyr(c, date)]
        return [self._snap(c, date)] if c.managed else []

    def console_event_sae1(self, sid: int, date: str) -> list[str]:
        """Console hook ``sae.1``: emit this country only, ignoring stagger and quiet (§2)."""
        return self._emit(self._by_sid_one(sid), date)

    def console_event_sae2(self, sid: int, date: str) -> list[str]:
        """Console hook ``sae.2``: §6 apply for this country only; does not clear popup flags (§2)."""
        c = self._by_sid_one(sid)
        if c.seq_pending is None:
            return []
        ack = self._apply(c, date)
        return [ack] if ack is not None else []

    def _by_sid_one(self, sid: int) -> SimCountry:
        for c in self._countries.values():
            if c.sid == sid:
                return c
        raise KeyError(sid)

    # ---- console `run` ----------------------------------------------------------------------
    def run_ini(self, text: str) -> None:
        """Console ``run``: stage knob and country lines.

        A line with a C2 shape is staged leniently, in any variable order and possibly partial (the game
        does not range-check, A-41; unset reads as 0). Lines of no template are skipped (console error).
        """
        for line in text.replace("\r\n", "\n").split("\n"):
            if not line:
                continue
            if m := _COUNTRY_LAX.match(line):
                self.stage_raw(int(m.group(1)), {n: _num(v) for n, v in _SETVAR.findall(m.group(2))})
            elif (m := _KNOB_LAX.match(line)) and _SETVAR.sub("", m.group(1)).strip() == "":
                self._set_knobs({n: _num(v) for n, v in _SETVAR.findall(m.group(1))})
            else:
                log.warning("sim: console error, line skipped: %r", line[:80])

    def _set_knobs(self, values: dict[str, Value]) -> None:
        human = self._first_human()  # observer mode: no console scope, no effect
        if human is None:
            return
        if "sae_emit_period" in values:
            human.knob_period = values["sae_emit_period"]
        if "sae_quiet" in values:
            human.knob_quiet = values["sae_quiet"]

    def stage_raw(self, sid: int, values: dict[str, Value]) -> None:
        """Stage ``sae_p_*`` / ``sae_seq_pending`` on every managed country with this sid (A-10).

        Not range-checked: lets tests build RANGE cases. Variables not given stay as they were
        (unset reads as 0, A-41).
        """
        for c in self._countries.values():
            if c.alive and c.managed and c.sid == sid:
                for name, value in values.items():
                    if name == "sae_seq_pending":
                        c.seq_pending = value
                    else:
                        c.staged[name] = value

    # ---- §6 ---------------------------------------------------------------------------------
    def _ack(self, c: SimCountry, date: str, p: Value, status: AckStatus, code: AckCode, **kw: int) -> str:
        assert c.sid is not None
        return self._line(AckRecord(date=date, sid=c.sid, seq=int(p), status=status, code=code, **kw))

    def _close(self, c: SimCountry, p: Value, line: str) -> str:
        c.seq_applied = int(p)
        return line

    @staticmethod
    def _range_ok(c: SimCountry, g: dict[str, Value]) -> bool:
        def pair(kind: str, codes: range) -> bool:
            k, t = g[f"sae_p_{kind}"], g[f"sae_p_{kind}_target"]
            if k == -1 and t == -1:
                return True
            return k in codes and 1 <= t <= _SEQ_MAX and t != c.sid

        # SEMANTICS §6 step 8, R12: integers only, no rounding
        if any(v != int(v) for v in g.values()):
            return False
        return (
            0 <= g["sae_p_expires"] <= _SEQ_MAX
            and g["sae_p_reason"] in range(0, 7)
            and g["sae_p_econ"] in (-1, 1, 2, 3, 4)
            and pair("mil", range(1, 4))
            and pair("dip", range(1, 5))
            and all(g[f"sae_p_ship_{s}"] == -1 or 0 <= g[f"sae_p_ship_{s}"] <= 100 for s in _SIZES)
        )

    def _apply(self, c: SimCountry, date: str) -> str | None:
        a = self._anchor
        assert c.seq_pending is not None
        p, applied = c.seq_pending, c.seq_applied
        if p < 1 or p > _SEQ_MAX or p != int(p):  # rule 1 (R2, R12: fractional P resets silently)
            c.seq_pending = applied
            return None
        if p == applied:  # rule 2
            return None
        if p < applied:  # rule 3
            c.seq_pending = applied
            return self._ack(c, date, p, AckStatus.DUPLICATE, AckCode.SEQ_OLD, applied=applied)
        if not c.managed or not c.is_ai:  # rule 4 (R3)
            return self._close(c, p, self._ack(c, date, p, AckStatus.REJECTED, AckCode.NOT_MANAGED))
        if a.mp == 1:  # rule 5
            return self._close(c, p, self._ack(c, date, p, AckStatus.REJECTED, AckCode.MULTIPLAYER))
        names = ["sae_p_schema", "sae_p_expires", "sae_p_reason", "sae_p_econ", "sae_p_mil", "sae_p_mil_target"]
        names += ["sae_p_dip", "sae_p_dip_target", *(f"sae_p_ship_{s}" for s in _SIZES)]
        g: dict[str, Value] = {n: c.staged.get(n, 0) for n in names}  # unset reads as 0 (A-41)
        if g["sae_p_schema"] != 1:  # rule 6
            return self._close(c, p, self._ack(c, date, p, AckStatus.SCHEMA_MISMATCH, AckCode.SCHEMA))
        if a.m > g["sae_p_expires"]:  # rule 7 (m == expires still applies)
            return self._close(c, p, self._ack(c, date, p, AckStatus.STALE, AckCode.EXPIRED))
        if not self._range_ok(c, g):  # rule 8 (R1: self-target is RANGE)
            return self._close(c, p, self._ack(c, date, p, AckStatus.REJECTED, AckCode.RANGE))
        living = {x.sid for x in self._countries.values() if x.alive and x.sid is not None}
        for t in (g["sae_p_mil_target"], g["sae_p_dip_target"]):  # rule 9
            if t != -1 and t not in living:
                return self._close(c, p, self._ack(c, date, p, AckStatus.REJECTED, AckCode.BAD_TARGET))
        self._apply_values(c, g, date)  # rule 10
        reason = int(g["sae_p_reason"])
        return self._close(c, p, self._ack(c, date, p, AckStatus.APPLIED, AckCode.OK, reason=reason))  # rule 11

    def _set_family(self, c: SimCountry, kind: str, names: tuple[str, ...], code: int, target: int | None) -> None:
        c.flags -= {f"sae_{kind}_{n}" for n in names}
        name = names[code - 1]
        c.flags.add(f"sae_{kind}_{name}")
        if target is not None:
            prefix = "sae_posture_" if kind == "mil" else "sae_stance_"
            old = c.mil_target if kind == "mil" else c.dip_target
            if old != 0:  # remove all modifiers toward the previous target (A-23)
                c.modifiers -= {(prefix + n, old) for n in names}
            c.modifiers.add((prefix + name, target))

    def _apply_values(self, c: SimCountry, g: dict[str, Value], date: str) -> None:
        if g["sae_p_econ"] != -1:
            self._set_family(c, "econ", _ECON, int(g["sae_p_econ"]), None)
            c.econ = int(g["sae_p_econ"])
        if g["sae_p_mil"] != -1:
            self._set_family(c, "mil", _MIL, int(g["sae_p_mil"]), int(g["sae_p_mil_target"]))
            c.mil, c.mil_target = int(g["sae_p_mil"]), int(g["sae_p_mil_target"])
        if g["sae_p_dip"] != -1:
            self._set_family(c, "dip", _DIP, int(g["sae_p_dip"]), int(g["sae_p_dip_target"]))
            c.dip, c.dip_target = int(g["sae_p_dip"]), int(g["sae_p_dip_target"])
        for s in _SIZES:
            if g[f"sae_p_ship_{s}"] != -1:
                c.ship[s] = int(g[f"sae_p_ship_{s}"])
        reason = int(g["sae_p_reason"])
        if reason > 0 and reason != c.reason:  # reason 0 leaves sae_reason unchanged
            c.reason = reason
            for h in self._countries.values():
                if h.alive and not h.is_ai and h.sid is not None and c.contact_with_human and not h.popup_flag:
                    h.popup_flag = True  # R10: one popup per human per pulse
                    assert c.sid is not None
                    self.flavour_events.append((date, h.sid, c.sid, reason))
