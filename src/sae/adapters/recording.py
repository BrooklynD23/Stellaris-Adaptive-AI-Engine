"""Recording hygiene for ``--record`` (C6 section 3 v1.3, proposal 022): save policy, size cap, PLYR redaction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sae.adapters import health as H
from sae.adapters.health import HealthFn
from sae.adapters.logtail import _redact
from sae.contracts.c1_log import LogLineError, PlayerRecord, parse_log_line
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c8_config import RecordConfig
from sae.contracts.c10_profile import CampaignLabel, LabelSegment
from sae.contracts.common import Archetype

GOD_VIEW_INSIDE = "GOD_VIEW_INSIDE"
LABEL_NAME = "label.json"


def _plyr_form(line: str) -> str:
    """C6 section 3: a PLYR line that passes C1 becomes the stub ``SAE|v1|PLYR|<date>|sid=<n>``; else the bus form."""
    start = line.find("SAE|")
    try:
        rec = parse_log_line(line[start:])
    except LogLineError:
        return _redact(line)
    if isinstance(rec, PlayerRecord):
        return f"SAE|v1|PLYR|{rec.date}|sid={rec.sid}"
    return _redact(line)


class RecordGate:
    """Per-recording-dir policy state. Nothing is ever deleted; a cap only stops save copies."""

    def __init__(self, cfg: RecordConfig, health: HealthFn) -> None:
        self._cfg = cfg
        self._health = health
        self._used = 0
        self._eligible = 0
        self._capped = False

    def lines(self, lines: list[str]) -> list[str]:
        """The lines to record: PLYR lines reduced by the bus redaction when ``redact_plyr`` (default)."""
        if not self._cfg.redact_plyr:
            return lines
        return [_plyr_form(ln) if "SAE|" in ln and "PLYR" in ln[ln.find("SAE|") :] else ln for ln in lines]

    def note_log(self, nbytes: int) -> None:
        """Count log bytes against ``max_bytes`` (the log itself keeps recording after the cap)."""
        self._used += nbytes

    def allow_save(self, size: int) -> bool:
        """True if this eligible save should be copied; False for ``none``, skipped ``every_n``, or capped."""
        if self._cfg.saves == "none":
            return False
        self._eligible += 1
        if self._cfg.saves == "every_n" and self._eligible % self._cfg.save_every != 0:
            return False
        if self._capped or self._used + size > self._cfg.max_bytes:
            if not self._capped:
                self._capped = True
                detail = f"max_bytes {self._cfg.max_bytes} reached; bytes_used {self._used}"
                self._health(HealthKind.RECORD_CAPPED, H.truncate(detail))
            return False
        self._used += size
        return True

    def start(self, record_dir: Path) -> None:
        """Create the dir; with ``redact_plyr = false`` also write the empty ``GOD_VIEW_INSIDE`` marker."""
        record_dir.mkdir(parents=True, exist_ok=True)
        if not self._cfg.redact_plyr:
            (record_dir / GOD_VIEW_INSIDE).touch()


def write_label(record_dir: Path, schedule: list[tuple[str, str]], note: str | None = None) -> None:
    """Write ``label.json`` (C10 ``CampaignLabel``, source ``synth_schedule``) from ``(from_date, archetype)`` pairs."""
    label = CampaignLabel(
        campaign_id=None,
        source="synth_schedule",
        segments=tuple(LabelSegment(from_date=d, archetype=Archetype(a)) for d, a in schedule),
        labelled_at=datetime.now(UTC),
        blind=False,
        note=note,
    )
    (record_dir / LABEL_NAME).write_text(label.model_dump_json(), encoding="utf-8")
