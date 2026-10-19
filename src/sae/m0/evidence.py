"""Read-only evidence analyzers for the automatic M0 checks.

Every function here only reads files the maintainer produced (game.log, error.log, a save, a Workshop
folder) and runs ``sae-parse`` on a save. Nothing is written to the game's folders. The behaviours these
analyzers look for are UNVERIFIED (A-02..A-42) until a real game produces the evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from sae.contracts.c1_log import (
    PREFIX,
    AckRecord,
    HeartbeatRecord,
    HelloRecord,
    LogLineError,
    LogRecord,
    PlayerRecord,
    SnapRecord,
    parse_log_line,
)
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseResult

Status = Literal["pass", "fail", "inconclusive"]

HOOK = "str_var_ai_ship_data_factor_"
PARSE_BUDGET_S = 3.0
_PADDED_DATE = re.compile(r"^\d+\.\d{2}\.\d{2}$")
_SAMPLE = 5
#: "sae" as a word or name prefix (sae_bridge, sae.0, sae_effects.txt), not inside other words.
_SAE_WORD = re.compile(r"\bsae(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    """Outcome of one automatic check: status, evidence for the results file, human-readable notes."""

    status: Status
    evidence: dict[str, object] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogScan:
    """Every ``SAE|`` line of a game.log, parsed with the C1 parser."""

    records: tuple[LogRecord, ...]
    raw_dates: tuple[str, ...]
    prefixes: tuple[str, ...]
    bad_lines: tuple[str, ...]
    decode_errors: int


def scan_game_log(path: Path) -> LogScan:
    """Parse every ``SAE|`` line in ``path`` (A-04 prefix, A-05 date token and A-16 UTF-8 are recorded).

    Streams the file line by line. A final line without a newline is skipped: the game may still be writing it.
    """
    records: list[LogRecord] = []
    raw_dates: list[str] = []
    prefixes: list[str] = []
    bad: list[str] = []
    decode_errors = 0
    with path.open("rb") as fh:
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                decode_errors += 1
                line = raw.decode("utf-8", errors="replace")
            start = line.find(PREFIX)
            if start < 0:
                continue
            prefixes.append(line[:start])
            try:
                record = parse_log_line(line)
            except LogLineError as exc:
                bad.append(f"{exc.code}: {line[:200].rstrip()}")
                continue
            if record is not None:
                records.append(record)
                raw_dates.append(line[start:].split("|", 4)[3])
    return LogScan(tuple(records), tuple(raw_dates), tuple(prefixes), tuple(bad), decode_errors)


def duplicate_heartbeats(records: tuple[LogRecord, ...]) -> list[str]:
    """HB dates printed twice within one session (A-40).

    A HELLO or a heartbeat date that does not move forward (a reload of an earlier save) starts a new session,
    so months replayed after a reload are not duplicates.
    """
    dups: list[str] = []
    last: tuple[str, int] | None = None
    for r in records:
        if isinstance(r, HelloRecord):
            last = None
        elif isinstance(r, HeartbeatRecord):
            date = str(r.date)
            # same date with m not moving forward = a reload of a save taken that day: new session
            if last is not None and date == last[0] and r.m > last[1]:
                dups.append(date)
            last = (date, r.m)
    return dups


def skipped_months(records: tuple[LogRecord, ...]) -> list[str]:
    """HB dates whose mod counter ``m`` jumped by more than 1 within a session (a month without HB, A-40)."""
    gaps: list[str] = []
    last_m: int | None = None
    for r in records:
        if isinstance(r, HelloRecord):
            last_m = None
        elif isinstance(r, HeartbeatRecord):
            if last_m is not None and r.m > last_m + 1:
                gaps.append(str(r.date))
            last_m = r.m
    return gaps


def _of(scan: LogScan, kind: type[LogRecord]) -> list[LogRecord]:
    return [r for r in scan.records if isinstance(r, kind)]


def check_starnet(starnet_dir: Path) -> Verdict:
    """M0-1: grep a StarNet Workshop folder for the ship-size hook (A-22)."""
    if not starnet_dir.is_dir():
        return Verdict("inconclusive", notes=(f"not a folder: {starnet_dir}",))
    hits = sorted(
        str(p.relative_to(starnet_dir))
        for p in starnet_dir.rglob("*.txt")
        if HOOK in p.read_text(encoding="utf-8", errors="replace")
    )
    version = None
    descriptor = starnet_dir / "descriptor.mod"
    if descriptor.is_file():
        found = re.search(r'supported_version\s*=\s*"([^"]+)"', descriptor.read_text(errors="replace"))
        version = found.group(1) if found else None
    evidence: dict[str, object] = {"files_with_hook": hits[:20], "hook_files": len(hits), "supported_version": version}
    return Verdict("pass" if hits else "fail", evidence, (f"{len(hits)} file(s) contain {HOOK}",))


def check_error_log(error_log: Path) -> Verdict:
    """M0-2: zero ``error.log`` lines mentioning sae (A-01)."""
    if not error_log.is_file():
        return Verdict("inconclusive", notes=(f"missing: {error_log} (launch the game once first)",))
    lines = [ln for ln in error_log.read_text(encoding="utf-8", errors="replace").splitlines() if _SAE_WORD.search(ln)]
    evidence: dict[str, object] = {"sae_error_lines": len(lines), "sample": lines[:_SAMPLE]}
    return Verdict("pass" if not lines else "fail", evidence, (f"{len(lines)} error.log line(s) mention sae",))


def check_log_channel(game_log: Path) -> Verdict:
    """M0-3: HELLO and HB lines reach game.log, one HB per month, names resolved (A-02..A-42)."""
    if not game_log.is_file():
        return Verdict("inconclusive", notes=(f"missing: {game_log}",))
    scan = scan_game_log(game_log)
    hb_count = len(_of(scan, HeartbeatRecord))
    dups = duplicate_heartbeats(scan.records)
    skipped = skipped_months(scan.records)
    names = [r.name for r in scan.records if isinstance(r, SnapRecord | PlayerRecord)]
    hellos = _of(scan, HelloRecord)
    evidence: dict[str, object] = {
        "sae_lines": len(scan.records),
        "hello": len(hellos),
        "hb": hb_count,
        "duplicate_hb_dates": dups[:_SAMPLE],
        "skipped_hb_months": skipped[:_SAMPLE],
        "bad_lines": list(scan.bad_lines[:_SAMPLE]),
        "prefix_sample": sorted(set(scan.prefixes))[:3],
        "raw_date_sample": list(scan.raw_dates[:3]),
        "dates_zero_padded": all(_PADDED_DATE.match(d) for d in scan.raw_dates) if scan.raw_dates else None,
        "unresolved_names": sum(1 for n in names if "[" in n),
        "fallback_names": sum(1 for n in names if n == "?"),
        "utf8_decode_errors": scan.decode_errors,
        "mp_values": sorted({r.mp for r in hellos if isinstance(r, HelloRecord) and r.mp is not None}),
    }
    ok = (bool(hellos) and hb_count > 0 and not dups and not skipped and not scan.bad_lines
          and evidence["unresolved_names"] == 0)
    notes = (f"{len(hellos)} HELLO, {hb_count} HB, {len(scan.bad_lines)} malformed SAE line(s)",)
    return Verdict("pass" if ok else "fail", evidence, notes)


def check_snapshots(game_log: Path) -> Verdict:
    """M0-5: SNAP lines carry numeric stats and sids (A-06, A-31, A-38); prints values for M0-6 staging."""
    if not game_log.is_file():
        return Verdict("inconclusive", notes=(f"missing: {game_log}",))
    scan = scan_game_log(game_log)
    snaps = [r for r in scan.records if isinstance(r, SnapRecord)]
    players = [r for r in scan.records if isinstance(r, PlayerRecord)]
    hbs = [r for r in scan.records if isinstance(r, HeartbeatRecord)]
    with_fleet = [s for s in snaps if s.fleet is not None]
    evidence: dict[str, object] = {
        "snap": len(snaps),
        "snap_with_fleet": len(with_fleet),
        "managed_sids": sorted({s.sid for s in snaps})[:20],
        "player_sid": players[-1].sid if players else None,
        "latest_m": hbs[-1].m if hbs else None,
        "sample": [f"sid={s.sid} fleet={s.fleet} name={s.name}" for s in with_fleet[-3:]],
    }
    notes = (
        f"{len(snaps)} SNAP ({len(with_fleet)} with fleet power); player sid {evidence['player_sid']}; "
        f"latest HB m={evidence['latest_m']}",
    )
    return Verdict("pass" if with_fleet else "fail", evidence, notes)


def check_ack(game_log: Path, sid: int, seq: int) -> Verdict:
    """M0-6: an ACK for the staged (sid, seq) says APPLIED/OK (A-08..A-12)."""
    if not game_log.is_file():
        return Verdict("inconclusive", notes=(f"missing: {game_log}",))
    acks = [r for r in scan_game_log(game_log).records if isinstance(r, AckRecord) and r.sid == sid and r.seq == seq]
    if not acks:
        return Verdict("fail", {"sid": sid, "seq": seq, "acks": []}, (f"no ACK for sid={sid} seq={seq} yet",))
    last = acks[-1]
    evidence: dict[str, object] = {
        "sid": sid, "seq": seq, "acks": [f"{a.status.value}/{a.code.value} on {a.date}" for a in acks],
    }
    applied = last.status.value == "APPLIED" and last.code.value == "OK"
    return Verdict("pass" if applied else "fail", evidence, (f"ACK {last.status.value}/{last.code.value}",))


def run_parser(parser_exe: Path, save: Path, timeout_s: float = 60.0) -> tuple[int, str, float]:
    """Run ``sae-parse parse --save`` once (read-only on the save); return exit code, stdout and wall seconds."""
    start = time.perf_counter()
    proc = subprocess.run(  # fixed argv, no shell
        [str(parser_exe), "parse", "--save", str(save)],
        capture_output=True, text=True, timeout=timeout_s, check=False,
    )
    return proc.returncode, proc.stdout, time.perf_counter() - start


def check_parse(parser_exe: Path | None, save: Path, *, want_ironman: bool = False) -> Verdict:
    """M0-11 / M0-14: time one parse, validate it as C4 output, show fields to compare with the UI."""
    if parser_exe is None or not parser_exe.is_file():
        return Verdict("inconclusive", notes=("sae-parse not found: build it (cargo build --release -j 1)",))
    if not save.is_file():
        return Verdict("inconclusive", notes=(f"missing save: {save}",))
    try:
        _, _, cold = run_parser(parser_exe, save)  # first run warms the disk cache; the second is judged
        code, out, wall = run_parser(parser_exe, save)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Verdict("fail", {"error": str(exc)[:300]}, (f"sae-parse did not finish: {exc}"[:300],))
    try:
        doc = PARSE_OUTPUT_ADAPTER.validate_python(json.loads(out))
    except (json.JSONDecodeError, ValidationError) as exc:
        return Verdict("fail", {"exit_code": code, "wall_s": round(wall, 3)}, (f"invalid C4 output: {exc}"[:300],))
    evidence: dict[str, object] = {
        "exit_code": code, "wall_s": round(wall, 3), "cold_wall_s": round(cold, 3), "size_bytes": save.stat().st_size,
    }
    if not isinstance(doc, ParseResult):
        evidence["error"] = doc.model_dump(mode="json")
        return Verdict("fail", evidence, (f"sae-parse exit {code}",))
    evidence.update(
        {
            "rss_peak_mb": round(doc.cost.rss_peak_bytes / 2**20, 1),
            "game_version": doc.meta.game_version,
            "date": str(doc.meta.date),
            "ironman": doc.meta.ironman,
            "god": doc.god.model_dump(mode="json") if doc.god else None,
            "countries_with_sid": sum(1 for c in doc.countries if c.sid is not None),
            "managed": sum(1 for c in doc.countries if c.managed),
        }
    )
    ok = code == 0 and wall <= PARSE_BUDGET_S and (doc.meta.ironman is True or not want_ironman)
    notes = (f"parsed in {wall:.2f} s (budget {PARSE_BUDGET_S} s); player: {evidence['god']}",)
    return Verdict("pass" if ok else "fail", evidence, notes)
