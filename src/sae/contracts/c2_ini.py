"""C2: the ``sae_in.ini`` console command file.

Grammar and semantics: ``contracts/C2_sae_in_ini.md``. :func:`render_ini` is the only code allowed
to produce the file text (the engine's compiler calls it); :func:`parse_ini` is the strict inverse
used by the Python mod simulator (T2) and the contract tests.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import Field, model_validator

from sae.contracts.common import CLAUSEWITZ_INT_MAX, Frozen, MonthIndex, Seq, ShipSize, Sid

FILE_NAME = "sae_in.ini"
TMP_SUFFIX = ".tmp"
MAX_LINES = 64
MAX_LINE_CHARS = 2048
KEEP: Final = -1
SCHEMA_VERSION: Final = 1

_TARGET_MAX = CLAUSEWITZ_INT_MAX
#: Reserved country-line slots (C2 v1.1): always -1 until fleet bias returns after M0-7 (A-22).
SHIP_SLOTS: Final = tuple(f"sae_p_ship_{size.value}" for size in ShipSize)


class CountryCommand(Frozen):
    """One managed country's complete pending knob set for one batch.

    ``-1`` (``KEEP``) means "leave this knob as currently applied".
    """

    sid: Sid
    seq: Seq
    expires: MonthIndex = Field(description="Mod rejects with STALE/EXPIRED once its HB counter m > expires.")
    reason: int = Field(default=0, ge=0, le=6, description="ReasonKey code; 0 = none.")
    econ: Literal[-1, 1, 2, 3, 4] = KEEP
    mil: Literal[-1, 1, 2, 3] = KEEP
    mil_target: int = Field(default=KEEP, ge=-1, le=_TARGET_MAX)
    dip: Literal[-1, 1, 2, 3, 4] = KEEP
    dip_target: int = Field(default=KEEP, ge=-1, le=_TARGET_MAX)

    @model_validator(mode="after")
    def _check(self) -> CountryCommand:
        if (self.mil == KEEP) != (self.mil_target == KEEP):
            raise ValueError("mil and mil_target must both be set or both be -1")
        if (self.dip == KEEP) != (self.dip_target == KEEP):
            raise ValueError("dip and dip_target must both be set or both be -1")
        for target in (self.mil_target, self.dip_target):
            if target == 0:
                raise ValueError("target sid 0 is invalid")
            if target == self.sid:
                raise ValueError("a country cannot target itself")
        return self


class KnobCommand(Frozen):
    """In-game cost knobs (C7 section 6), set on the console scope country."""

    emit_period: int | None = Field(default=None, ge=1, le=12)
    quiet: Literal[0, 1] | None = None

    @model_validator(mode="after")
    def _check(self) -> KnobCommand:
        if self.emit_period is None and self.quiet is None:
            raise ValueError("a knob line must set at least one knob")
        return self


class IniFile(Frozen):
    """The whole ``sae_in.ini``: an optional knob line, then country lines in ascending ``sid``."""

    knobs: KnobCommand | None = None
    countries: tuple[CountryCommand, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> IniFile:
        if self.knobs is None and not self.countries:
            raise ValueError("an ini file must contain at least one line")
        sids = [c.sid for c in self.countries]
        if sids != sorted(set(sids)):
            raise ValueError("country lines must have unique sids in ascending order")
        if len({c.seq for c in self.countries}) > 1:
            raise ValueError("all country lines in one file share one batch seq")
        if len(self.countries) + (1 if self.knobs else 0) > MAX_LINES:
            raise ValueError(f"more than {MAX_LINES} lines")
        return self


def _var(name: str, value: int) -> str:
    return f"set_variable = {{ which = {name} value = {value} }}"


def _country_vars(cmd: CountryCommand) -> list[tuple[str, int]]:
    pairs = [
        ("sae_p_schema", SCHEMA_VERSION),
        ("sae_p_expires", cmd.expires),
        ("sae_p_reason", cmd.reason),
        ("sae_p_econ", cmd.econ),
        ("sae_p_mil", cmd.mil),
        ("sae_p_mil_target", cmd.mil_target),
        ("sae_p_dip", cmd.dip),
        ("sae_p_dip_target", cmd.dip_target),
    ]
    pairs += [(name, KEEP) for name in SHIP_SLOTS]
    pairs.append(("sae_seq_pending", cmd.seq))
    return pairs


def render_country_line(cmd: CountryCommand) -> str:
    """Render one country line. ``sae_seq_pending`` is always the last effect (commit marker)."""
    body = " ".join(_var(n, v) for n, v in _country_vars(cmd))
    limit = f"limit = {{ has_country_flag = sae_managed check_variable = {{ which = sae_sid value = {cmd.sid} }} }}"
    return f"effect every_country = {{ {limit} {body} }}"


def render_knob_line(knobs: KnobCommand) -> str:
    """Render the knob line (period first, then quiet; absent knobs are omitted)."""
    parts = []
    if knobs.emit_period is not None:
        parts.append(_var("sae_emit_period", knobs.emit_period))
    if knobs.quiet is not None:
        parts.append(_var("sae_quiet", knobs.quiet))
    return "effect " + " ".join(parts)


def render_ini(ini: IniFile) -> str:
    """Render the full file: ASCII, LF line endings, trailing LF, no comments, no blank lines."""
    lines = [render_knob_line(ini.knobs)] if ini.knobs else []
    lines += [render_country_line(c) for c in ini.countries]
    for line in lines:
        if len(line) > MAX_LINE_CHARS:
            raise ValueError(f"line exceeds {MAX_LINE_CHARS} chars")
    return "\n".join(lines) + "\n"


class IniError(ValueError):
    """``sae_in.ini`` text does not match the C2 templates exactly."""


_N = r"(0|-?[1-9][0-9]*)"
_VAR_RE = re.compile(r"set_variable = \{ which = (sae_[a-z_]+) value = " + _N + r" \}")
_COUNTRY_RE = re.compile(
    r"^effect every_country = \{ limit = \{ has_country_flag = sae_managed "
    r"check_variable = \{ which = sae_sid value = ([1-9][0-9]*) \} \} (.*) \}$"
)
_KNOB_RE = re.compile(r"^effect (set_variable = .*)$")
_COUNTRY_ORDER = [n for n, _ in _country_vars(CountryCommand(sid=1, seq=1, expires=0))]


def _vars(body: str) -> list[tuple[str, int]]:
    found = [(m.group(1), int(m.group(2))) for m in _VAR_RE.finditer(body)]
    if " ".join(_var(n, v) for n, v in found) != body:
        raise IniError(f"unexpected text in effect body: {body[:80]!r}")
    return found


def _parse_country(sid: int, body: str) -> CountryCommand:
    pairs = _vars(body)
    if [n for n, _ in pairs] != _COUNTRY_ORDER:
        raise IniError("country line variables are missing or out of order")
    values = dict(pairs)
    if values["sae_p_schema"] != SCHEMA_VERSION:
        raise IniError(f"sae_p_schema {values['sae_p_schema']} != {SCHEMA_VERSION}")
    if any(values[name] != KEEP for name in SHIP_SLOTS):
        raise IniError("sae_p_ship_<size> slots are reserved and must be -1 (C2 v1.1)")
    return CountryCommand.model_validate(
        {
            "sid": sid,
            "seq": values["sae_seq_pending"],
            "expires": values["sae_p_expires"],
            "reason": values["sae_p_reason"],
            "econ": values["sae_p_econ"],
            "mil": values["sae_p_mil"],
            "mil_target": values["sae_p_mil_target"],
            "dip": values["sae_p_dip"],
            "dip_target": values["sae_p_dip_target"],
        }
    )


def _parse_knobs(body: str) -> KnobCommand:
    pairs = _vars(body)
    names = [n for n, _ in pairs]
    if names not in (["sae_emit_period", "sae_quiet"], ["sae_emit_period"], ["sae_quiet"]):
        raise IniError(f"knob line variables wrong or out of order: {names}")
    values = dict(pairs)
    return KnobCommand.model_validate({"emit_period": values.get("sae_emit_period"), "quiet": values.get("sae_quiet")})


def parse_ini(text: str) -> IniFile:
    """Strictly parse ``sae_in.ini`` text produced by :func:`render_ini`.

    Accepts LF or CRLF. Raises :class:`IniError` (or pydantic ``ValidationError``, a ``ValueError``)
    on any deviation: unknown line, wrong variable order, out-of-range value, non-ASCII text.
    """
    if not text.isascii():
        raise IniError("file must be ASCII")
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        raise IniError("file must end with a line feed")
    lines = text.split("\n")
    lines.pop()
    knobs: KnobCommand | None = None
    countries: list[CountryCommand] = []
    for index, line in enumerate(lines):
        if match := _COUNTRY_RE.match(line):
            countries.append(_parse_country(int(match.group(1)), match.group(2)))
        elif (match := _KNOB_RE.match(line)) and index == 0:
            knobs = _parse_knobs(match.group(1))
        else:
            raise IniError(f"line {index + 1} matches no C2 template: {line[:80]!r}")
    return IniFile(knobs=knobs, countries=tuple(countries))
