"""C1: ``SAE|v1|`` log lines written by the sae_bridge mod into ``game.log``.

Grammar and semantics: ``contracts/C1_log_lines.md``. This module is the reference parser and
formatter; the Python mod simulator (T2) formats with :func:`format_log_line` and the LiveAdapter
parses with :func:`parse_log_line`.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from sae.contracts.common import (
    MOD_ACK_STATUSES,
    AckCode,
    AckStatus,
    Frozen,
    GameDate,
    MonthIndex,
    Seq,
    SeqOrZero,
    Sid,
)

PREFIX = "SAE|v"  # one char safer than "SAE|" against game prefixes
VERSION = "v1"
MAX_LINE_CHARS = 4096
MAX_NAME_CHARS = 256
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_INT_RE = re.compile(r"^-?[0-9]+(\.0+)?$")
_FLOAT_RE = re.compile(r"^-?[0-9]{1,15}(\.[0-9]{1,15})?$")


class LogLineError(ValueError):
    """A line carried the ``SAE|`` marker but violated C1. ``code`` is a stable error id."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class _Record(Frozen):
    """Fields common to every record. ``extra`` is a dict, so records are not hashable."""

    date: GameDate
    extra: dict[str, str] = Field(default_factory=dict, description="Unknown keys, kept verbatim.")

    @model_validator(mode="after")
    def _check_text(self) -> _Record:
        known = _KNOWN.get(str(getattr(self, "type", "")), frozenset())
        for key, value in self.extra.items():
            if not _KEY_RE.match(key) or key in known or key in {"date", "type"}:
                raise ValueError(f"extra key {key!r} is not a free key=value key")
            if not value.isascii() or any(c in value for c in "|\r\n"):
                raise ValueError(f"extra value for {key!r} must be ASCII without | CR LF")
        name = getattr(self, "name", None)
        if isinstance(name, str) and any(c in name for c in "\r\n"):
            raise ValueError("name must not contain CR or LF")
        return self


class HelloRecord(_Record):
    """Once per game start or save load: mod identity and current knob values."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    type: Literal["HELLO"] = "HELLO"
    mod: str = Field(min_length=1, max_length=32, description="sae_bridge mod version, e.g. 0.1.0")
    schema_: int = Field(alias="schema", ge=1, description="Intent schema version the mod accepts (C3).")
    period: int = Field(ge=1, le=12, description="Current sae_emit_period.")
    quiet: Literal[0, 1] = Field(description="Current sae_quiet.")
    mp: Literal[0, 1] | None = Field(default=None, description="1 if multiplayer (UNVERIFIED A-13).")


class HeartbeatRecord(_Record):
    """Once per game month, never suppressed by ``sae_quiet`` (signoff change 1)."""

    type: Literal["HB"] = "HB"
    m: MonthIndex = Field(description="Mod month counter; used for C2 expiry.")
    period: int = Field(ge=1, le=12)
    quiet: Literal[0, 1]


class _CountryStats(_Record):
    """Numeric stats shared by SNAP and PLYR. All optional: a missing key means not emitted."""

    sid: Sid
    fleet: float | None = Field(default=None, ge=0, description="Fleet power.")
    navcap_used: float | None = Field(default=None, ge=0)
    navcap: float | None = Field(default=None, ge=0)
    en: float | None = Field(default=None, description="Energy stockpile.")
    mi: float | None = Field(default=None, description="Minerals stockpile.")
    al: float | None = Field(default=None, description="Alloys stockpile.")
    al_inc: float | None = Field(default=None, description="Monthly alloys income.")
    wars: int | None = Field(default=None, ge=0, description="Wars this country is in.")
    name: str = Field(min_length=1, max_length=256, description="[This.GetName]; always the LAST field.")


class SnapRecord(_CountryStats):
    """One managed AI country's own state; staggered by ``sae_emit_period`` slots."""

    type: Literal["SNAP"] = "SNAP"
    seq: SeqOrZero = Field(description="sae_seq_applied: last batch applied to this country (0 = none).")
    econ: int | None = Field(default=None, ge=0, le=4, description="Applied EconPlan (0 = never set).")
    mil: int | None = Field(default=None, ge=0, le=3, description="Applied Posture (0 = never set).")
    contact: Literal[0, 1] | None = Field(default=None, description="1 if in contact with the player.")


class PlayerRecord(_CountryStats):
    """The player's true state. GOD-VIEW: routed only to ``observation_god`` (C6 section 4).

    ``name`` is optional for PLYR only (C1 section 3, C6 section 3): the recorder's redacted stub
    ``SAE|v1|PLYR|<date>|sid=<n>`` parses as a stat-less, name-less record so replay keeps the human's sid.
    """

    type: Literal["PLYR"] = "PLYR"
    name: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=256, description="[This.GetName]; absent only in a recorded stub."
    )


class AckRecord(_Record):
    """The mod's answer to one (country, seq) batch from ``sae_in.ini``."""

    type: Literal["ACK"] = "ACK"
    sid: Sid
    seq: Seq
    status: AckStatus
    code: AckCode
    reason: int | None = Field(default=None, ge=0, le=6, description="sae_p_reason code applied (ReasonKey).")
    applied: SeqOrZero | None = Field(default=None, description="sae_seq_applied; sent with DUPLICATE.")


LogRecord = Annotated[
    HelloRecord | HeartbeatRecord | SnapRecord | PlayerRecord | AckRecord, Field(discriminator="type")
]
_RECORD_ADAPTER: TypeAdapter[LogRecord] = TypeAdapter(LogRecord)

#: Allowed (status, code) pairs in a mod ACK line (C1 section 3 table).
_ACK_PAIRS: dict[AckStatus, frozenset[AckCode]] = {
    AckStatus.APPLIED: frozenset({AckCode.OK}),
    AckStatus.REJECTED: frozenset({AckCode.RANGE, AckCode.NOT_MANAGED, AckCode.BAD_TARGET, AckCode.MULTIPLAYER}),
    AckStatus.STALE: frozenset({AckCode.EXPIRED}),
    AckStatus.DUPLICATE: frozenset({AckCode.SEQ_OLD}),
    AckStatus.SCHEMA_MISMATCH: frozenset({AckCode.SCHEMA}),
}
assert set(_ACK_PAIRS) == MOD_ACK_STATUSES

_INT_KEYS = frozenset(
    {"schema", "period", "quiet", "mp", "m", "sid", "seq", "wars", "econ", "mil", "contact", "reason", "applied"}
)
_FLOAT_KEYS = frozenset({"fleet", "navcap_used", "navcap", "en", "mi", "al", "al_inc"})
_STR_KEYS = frozenset({"mod", "status", "code", "name"})
_KNOWN: dict[str, frozenset[str]] = {
    "HELLO": frozenset({"mod", "schema", "period", "quiet", "mp"}),
    "HB": frozenset({"m", "period", "quiet"}),
    "SNAP": frozenset({"sid", "fleet", "navcap_used", "navcap", "en", "mi", "al", "al_inc", "wars", "name"})
    | {"seq", "econ", "mil", "contact"},
    "PLYR": frozenset({"sid", "fleet", "navcap_used", "navcap", "en", "mi", "al", "al_inc", "wars", "name"}),
    "ACK": frozenset({"sid", "seq", "status", "code", "reason", "applied"}),
}


def _convert(key: str, raw: str) -> object:
    if key in _INT_KEYS:
        if not _INT_RE.match(raw):
            raise LogLineError("bad_value", f"{key}={raw!r} is not an integer")
        return int(raw.split(".")[0])
    if key in _FLOAT_KEYS:
        if not _FLOAT_RE.match(raw):
            raise LogLineError("bad_value", f"{key}={raw!r} is not a decimal number")
        return float(raw)
    return raw


def _split_fields(rest: str) -> dict[str, str]:
    """Split ``k=v|k=v|...``; a ``name=`` field swallows the remainder of the line."""
    fields: dict[str, str] = {}
    while rest:
        if rest.startswith("name="):
            fields["name"] = rest[len("name=") :][:MAX_NAME_CHARS]  # truncate, never reject (C1 rule 7)
            break
        part, _, rest = rest.partition("|")
        key, sep, value = part.partition("=")
        if not sep or not _KEY_RE.match(key):
            raise LogLineError("bad_field", f"field {part!r} is not key=value")
        if key in fields:
            raise LogLineError("dup_key", f"key {key!r} repeated")
        fields[key] = value
    return fields


def parse_log_line(raw: str) -> LogRecord | None:
    """Parse one ``game.log`` line.

    Returns ``None`` when the line has no ``SAE|`` marker (ordinary game noise). Text before the
    first ``SAE|`` (the game's own timestamp prefix, UNVERIFIED A-04) is ignored.

    Raises:
        LogLineError: the line has the marker but breaks C1 (codes: ``too_long``, ``version_skew``,
            ``bad_header``, ``bad_field``, ``dup_key``, ``bad_value``, ``invalid``).
    """
    line = raw.rstrip("\r\n")
    start = line.find(PREFIX)
    if start < 0:
        return None
    line = line[start:]
    if len(line) > MAX_LINE_CHARS:
        raise LogLineError("too_long", f"{len(line)} chars > {MAX_LINE_CHARS}")
    head = line.split("|", 2)
    if len(head) > 1 and head[1] != VERSION:
        raise LogLineError("version_skew", f"version {head[1]!r} != {VERSION}")
    parts = line.split("|", 4)
    if len(parts) < 4:
        raise LogLineError("bad_header", "expected SAE|<ver>|<TYPE>|<date>")
    _, version, rtype, date = parts[:4]
    if version != VERSION:
        raise LogLineError("version_skew", f"version {version!r} != {VERSION}")
    if rtype not in _KNOWN:
        raise LogLineError("bad_header", f"unknown record type {rtype!r}")
    fields = _split_fields(parts[4]) if len(parts) == 5 else {}
    data: dict[str, object] = {"type": rtype, "date": date}
    extra: dict[str, str] = {}
    for key, value in fields.items():
        if key in _KNOWN[rtype]:
            data[key] = _convert(key, value)
        else:
            extra[key] = value
    data["extra"] = extra
    try:
        record = _RECORD_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise LogLineError("invalid", exc.errors(include_url=False)[0]["msg"]) from exc
    if isinstance(record, AckRecord) and record.code not in _ACK_PAIRS.get(record.status, frozenset()):
        raise LogLineError("invalid", f"status/code {record.status.value}/{record.code.value} not allowed (C1 table)")
    return record


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    if hasattr(value, "value") and not isinstance(value, int):
        return str(value.value)
    return str(int(value)) if isinstance(value, int) else str(value)


def format_log_line(record: LogRecord) -> str:
    """Render a record exactly as the mod must print it (no trailing newline, ``name`` last)."""
    dumped = record.model_dump(by_alias=True, exclude_none=True, exclude={"type", "date", "extra"})
    name = dumped.pop("name", None)
    items = [f"{k}={_fmt(getattr(record, 'schema_' if k == 'schema' else k))}" for k in dumped]
    items += [f"{k}={v}" for k, v in record.extra.items()]
    if name is not None:
        items.append(f"name={name}")
    return "|".join(["SAE", VERSION, record.type, record.date, *items])
