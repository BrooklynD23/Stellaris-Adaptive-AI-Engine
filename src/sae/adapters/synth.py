"""Synthetic adapter: a seeded world driven through :class:`ModSimulator`, with archetype saves (T2 U5).

Every month the simulator pulses (C1 lines -> assembler). Every 3 months a ``FixtureSpec`` describing
the player's archetype trajectory is rendered by ``sae-parse fixture`` and parsed, so T4's five features
(expansion rate, border fort ratio, fleet-power category, first-war month, tech category) move per
archetype. The player's border/fortification layout is built to match the parser's legal computation
(frontier = player systems with a hyperlane to an observer system; fortified = starbase with defenses).
All Stellaris-side assumptions behind the fixture are UNVERIFIED (A-19/A-30; M0-11 would run the real
parser on a real save and compare the legal view).
"""

from __future__ import annotations

import asyncio
import logging
import random
import shutil
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sae.adapters import health as H
from sae.adapters._pull import AckBox, AckPlant, handoff, parse_ack_model, write_ini_sync
from sae.adapters.assembler import Assembler
from sae.adapters.health import HealthFn
from sae.adapters.keys import synth_key
from sae.adapters.logtail import parse_lines
from sae.adapters.recording import RecordGate, write_label
from sae.adapters.simulator import ModSimulator, SimCountry
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c4_parse import FixtureSpec
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import (
    AdapterMode,
    EmitReceipt,
    GameClock,
    GodSink,
    IntentAck,
    Observation,
    SaveParser,
)
from sae.contracts.c8_config import RecordConfig
from sae.contracts.common import Archetype

_log = logging.getLogger("sae.adapters.synth")

RunFixture = Callable[[list[str]], Awaitable[int]]
"""Runs ``sae-parse fixture ...``; returns the exit code."""

SAVE_EVERY_MONTHS = 3
FIXTURE_TIMEOUT_S = 60
_N_AI = 4
_LATE_CONTACT_MONTH = 24  # AI 4 meets the player here
_AI_SYSTEMS = 6
_MAX_PLAYER_SYSTEMS = 60


@dataclass(frozen=True)
class _Profile:
    """Player trajectory parameters of one archetype."""

    start_systems: int
    expansion: float  # systems per month
    borders: int  # player systems adjacent to each observer
    fort_frac: float  # fraction of border systems with a fortified starbase
    fleet_ratio: float  # player fleet / typical AI fleet
    tech_ratio: float
    income: float
    war_month: int | None  # months into the archetype stint; war against AI 1


PROFILES: dict[Archetype, _Profile] = {
    Archetype.TURTLE: _Profile(6, 0.04, 4, 1.0, 1.0, 1.0, 30.0, None),
    Archetype.TECH_RUSH: _Profile(6, 0.10, 2, 0.0, 0.4, 2.2, 45.0, None),
    Archetype.EARLY_AGGRESSION: _Profile(5, 0.25, 2, 0.5, 2.5, 0.9, 30.0, 6),
    Archetype.BALANCED: _Profile(6, 0.15, 3, 0.34, 1.0, 1.0, 35.0, None),
}


def parse_schedule(text: str) -> list[tuple[Archetype, int]]:
    """``a[@year][,a@year...]`` -> sorted schedule. Raises ``ValueError`` on an unknown archetype or year."""
    out: list[tuple[Archetype, int]] = []
    for part in text.split(","):
        name, sep, year = part.strip().partition("@")
        try:
            arch = Archetype(name)
            start = int(year) if sep else 0
        except ValueError as exc:
            raise ValueError(f"bad archetype schedule item {part!r}") from exc
        if start < 0:
            raise ValueError(f"bad archetype schedule item {part!r}: negative year")
        out.append((arch, start))
    return sorted(out, key=lambda x: x[1])


def _month_date(start: str, i: int) -> str:
    y, m, _ = (int(x) for x in start.split("."))
    n = (y * 12 + m - 1) + i
    return f"{n // 12:04d}.{n % 12 + 1:02d}.01"


async def _subprocess_fixture(argv: list[str]) -> int:
    proc = await asyncio.to_thread(subprocess.run, argv, capture_output=True, check=False, timeout=FIXTURE_TIMEOUT_S)
    return proc.returncode


class SynthAdapter:
    """GameAdapter over a simulated world. Deterministic for a given seed. See the module docstring."""

    def __init__(
        self,
        schedule: Sequence[tuple[Archetype, int]],
        seed: int,
        years: int,
        parser: SaveParser,
        god_sink: GodSink,
        *,
        health: HealthFn | None = None,
        parser_exe: str,
        scratch_dir: Path,
        ack_model: str = "echo",
        run_fixture: RunFixture = _subprocess_fixture,
        start: str = "2200.01.01",
        record_dir: Path | None = None,
        record: RecordConfig | None = None,
        run_id: str | None = None,
    ) -> None:
        if not schedule or years < 1:
            raise ValueError("synth needs a non-empty schedule and years >= 1")
        spec = parse_ack_model(ack_model)
        if spec.kind == "from_recording":
            raise ValueError("synth has no recording: ack model from_recording is not supported")
        self._schedule = sorted(schedule, key=lambda x: x[1])
        self._years = years
        self._parser = parser
        self._health = H.safe_health(health if health is not None else H.log_health)
        health = self._health
        self._exe = parser_exe
        self._scratch = scratch_dir
        self._run_fixture = run_fixture
        self._start = start
        rng = random.Random(seed)
        self._ai_fleet = [9000.0 + rng.randrange(0, 3000) for _ in range(_N_AI)]
        self._ai_tech = [40 + rng.randrange(0, 20) for _ in range(_N_AI)]
        self._player = SimCountry(0, "Terran Federation", is_ai=False, contact_with_human=False)
        self._ais = [
            SimCountry(i + 1, f"Synth Empire {i + 1}", contact_with_human=i + 1 < _N_AI) for i in range(_N_AI)
        ]
        self._sim = ModSimulator([self._player, *self._ais])
        self._plant = AckPlant(self._sim, spec, seed)
        self._gate = H.new_gate()
        self._asm = Assembler(
            god_sink, health, self._gate, fixed_key=None if run_id is None else synth_key(run_id)
        )
        self._box = AckBox()
        self._closed = False
        self._record_dir = record_dir  # LiveAdapter record format; synchronous writes keep runs deterministic
        self._rec_size = 0
        self._rec_pulses = 0
        self._rec_failed = False
        self._rec_gate = RecordGate(record or RecordConfig(), health)
        self._rec_started = False
        self.specs: list[FixtureSpec] = []  # every spec built, for tests and debugging

    @property
    def mode(self) -> AdapterMode:
        """Always SYNTH."""
        return AdapterMode.SYNTH

    def clock(self) -> GameClock:
        """Current game time as last simulated."""
        return self._asm.clock()

    # -- iterators ----------------------------------------------------------------------------

    async def acks(self) -> AsyncIterator[IntentAck]:
        """Acks as the simulator produces them; ends after the observations end or :meth:`close`."""
        async for ack in self._box.drain():
            yield ack

    async def observations(self) -> AsyncIterator[Observation]:
        """One log observation per simulated month plus one save observation every 3 months."""
        try:
            for i in range(self._years * 12):
                if self._closed:
                    return
                if i == _LATE_CONTACT_MONTH:
                    self._sim.set_contact(self._ais[-1].country_id, True)
                self._advance_world(i)
                self._plant.tick()
                date = _month_date(self._start, i)
                clk = self._asm.clock()
                key = (clk.session_epoch, date[:7])
                pushed = False
                out: list[Observation] = []
                for rec in parse_lines(self._record_pulse(self._sim.pulse(date)), self._health, key, self._gate):
                    for item in self._asm.on_record(rec):
                        if isinstance(item, IntentAck):
                            self._box.push(item)
                            pushed = True
                        else:
                            out.append(item)
                out.extend(self._asm.flush())
                if pushed:
                    await asyncio.sleep(0)  # acks of this pulse are available before its observation
                for obs in out:
                    yield obs
                    await handoff()
                if (i + 1) % SAVE_EVERY_MONTHS == 0:
                    for obs in await self._save_observations(i, date):
                        yield obs
                        await handoff()
        finally:
            self._box.finish()

    # -- world ------------------------------------------------------------------------------------

    def _profile(self, i: int) -> tuple[_Profile, int]:
        """Profile in force at month ``i`` and the month the stint started."""
        year = i // 12
        active = max((s for s in self._schedule if s[1] <= year), key=lambda s: s[1], default=self._schedule[0])
        return PROFILES[active[0]], active[1] * 12

    def _advance_world(self, i: int) -> None:
        """Move fleets and wars of the simulated countries to month ``i``."""
        prof, since = self._profile(i)
        for k, ai in enumerate(self._ais):
            ai.fleet = round(self._ai_fleet[k] * (1 + 0.01 * i), 1)
            ai.navcap = round(ai.fleet / 80, 1)
            ai.navcap_used = round(ai.navcap * 0.8, 1)
        avg = sum(a.fleet for a in self._ais) / len(self._ais)
        self._player.fleet = round(avg * prof.fleet_ratio, 1)
        at_war = prof.war_month is not None and i - since >= prof.war_month
        self._player.wars = 1 if at_war else 0
        self._ais[0].wars = 1 if at_war else 0

    def _systems(self, i: int) -> int:
        prof, _ = self._profile(i)
        return min(_MAX_PLAYER_SYSTEMS, int(prof.start_systems + prof.expansion * i))

    def build_spec(self, i: int, date: str) -> FixtureSpec:
        """The fixture describing the world at month ``i`` (strict JSON types)."""
        prof, since = self._profile(i)
        n_sys = self._systems(i)
        contacts = [(0, a.country_id) for a in self._ais if a.contact_with_human]
        borders = min(prof.borders, n_sys)
        forts = round(prof.fort_frac * borders)
        lanes = {(j, j + 1) for j in range(1, n_sys)}
        for a in self._ais:
            base = 100 * a.country_id
            lanes |= {(base + j, base + j + 1) for j in range(1, _AI_SYSTEMS)}
            if a.contact_with_human:
                lanes |= {(j, base + j) for j in range(1, borders + 1)}
        countries: list[dict[str, Any]] = [
            {
                "country_id": 0,
                "name": self._player.name,
                "sid": self._player.sid,
                "is_player": True,
                "fleet_power": float(self._player.fleet),
                "tech_researched": int(self._player_tech(i)),
                "alloys_income": float(prof.income),
                "systems": list(range(1, n_sys + 1)),
            }
        ]
        for k, a in enumerate(self._ais):
            countries.append(
                {
                    "country_id": a.country_id,
                    "name": a.name,
                    "sid": a.sid,
                    "managed": a.managed,
                    "fleet_power": float(a.fleet),
                    "naval_cap_used": float(a.navcap_used),
                    "naval_cap": float(a.navcap),
                    "energy": 400.0,
                    "minerals": 1000.0,
                    "alloys": 100.0,
                    "alloys_income": 15.0,
                    "tech_researched": self._ai_tech[k] + i // 2,
                    "applied_seq": a.seq_applied,
                    "pending_seq": int(a.seq_pending or 0),
                    "applied_econ": a.econ,
                    "applied_mil": a.mil,
                    "systems": [100 * a.country_id + j for j in range(1, _AI_SYSTEMS + 1)],
                }
            )
        at_war = prof.war_month is not None and i - since >= prof.war_month
        raw = {
            "name": f"synth_{self._profile_name(i)}_{i:04d}",
            "date": date,
            "countries": countries,
            "hyperlanes": sorted(lanes),
            "contacts": contacts,
            "starbases": [
                {"system": j, "owner": 0, "defense_modules": 3, "platforms": 2} for j in range(1, forts + 1)
            ],
            "wars": [{"attackers": [0], "defenders": [self._ais[0].country_id]}] if at_war else [],
        }
        return FixtureSpec.model_validate(raw)

    def _player_tech(self, i: int) -> float:
        prof, _ = self._profile(i)
        return prof.tech_ratio * (sum(self._ai_tech) / _N_AI + i // 2)

    def _profile_name(self, i: int) -> str:
        year = i // 12
        return max((s for s in self._schedule if s[1] <= year), key=lambda s: s[1], default=self._schedule[0])[0].value

    async def _save_observations(self, i: int, date: str) -> list[Observation]:
        spec = self.build_spec(i, date)
        self.specs.append(spec)
        d = self._scratch / "synth"
        out_path = d / f"{spec.name}.sav"
        try:
            d.mkdir(parents=True, exist_ok=True)
            spec_path = d / f"{spec.name}.json"
            spec_path.write_text(spec.model_dump_json(), encoding="utf-8")
            code = await self._run_fixture([self._exe, "fixture", "--spec", str(spec_path), "--out", str(out_path)])
            if code != 0:
                self._health(HealthKind.PARSE_FAILED, f"fixture exit {code} for {spec.name}")
                return []
            self._record_save(out_path, date)
            result = await self._parser.parse(str(out_path))
        except Exception as exc:
            _log.exception("synth save failed")
            self._health(HealthKind.PARSE_FAILED, H.truncate(f"{spec.name}: {type(exc).__name__}: {exc}"))
            return []
        finally:
            for p in (d / f"{spec.name}.json", out_path):
                p.unlink(missing_ok=True)
        if result is None:
            return []
        return self._asm.on_parse(result, self._asm.clock().session_epoch)

    # -- recording --------------------------------------------------------------------------------

    def _record_fail(self, exc: Exception) -> None:
        if not self._rec_failed:
            self._rec_failed = True
            _log.error("record failed: %s", exc)
            self._health(HealthKind.INVALID_EVENT, H.truncate(f"synth record failed: {exc}"))

    def _label_schedule(self) -> list[tuple[str, str]]:
        """``(from_date, archetype)`` per distinct start year (first entry wins, as in :meth:`_profile`)."""
        seen: dict[int, str] = {}
        for arch, year in self._schedule:
            seen.setdefault(year, arch.value)
        # The simulator plays schedule[0] from month 0, so the first segment starts at the run start.
        items = sorted(seen.items())
        return [(_month_date(self._start, 0 if i == 0 else y * 12), a) for i, (y, a) in enumerate(items)]

    def _record_start(self, record_dir: Path) -> None:
        """Once: create the dir, the ``GOD_VIEW_INSIDE`` marker if due, and ``label.json`` (proposal 022)."""
        self._rec_started = True
        self._rec_gate.start(record_dir)
        write_label(record_dir, self._label_schedule(), note=f"sae synth --record; years={self._years}")

    def _record_pulse(self, lines: list[str]) -> list[str]:
        """Append one pulse's lines to ``game.log`` and its ``.ts`` stamp; returns ``lines`` unchanged."""
        self._rec_pulses += 1
        if self._record_dir is None or self._rec_failed:
            return lines
        try:
            if not self._rec_started:
                self._record_start(self._record_dir)
            data = "".join(f"{ln}\n" for ln in self._rec_gate.lines(lines)).encode("utf-8")
            self._rec_gate.note_log(len(data))
            with (self._record_dir / "game.log").open("ab") as f:
                f.write(data)
            self._rec_size += len(data)
            with (self._record_dir / "game.log.ts").open("a", encoding="utf-8") as f:
                f.write(f"{self._rec_size} {self._rec_pulses * 1_000_000_000}\n")
        except (OSError, ValueError) as exc:
            self._record_fail(exc)
        return lines

    def _record_save(self, path: Path, date: str) -> None:
        if self._record_dir is None or self._rec_failed:
            return
        try:
            if not self._rec_gate.allow_save(path.stat().st_size):
                return
            dest_dir = self._record_dir / "saves"
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest_dir / f"{self._rec_size:012d}-autosave_{date}.sav")
        except OSError as exc:
            self._record_fail(exc)

    # -- emit / close -----------------------------------------------------------------------------

    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        """Write ``scratch/sae_in.ini``; the ack model decides when the simulator runs it. Never raises."""
        if self._closed:
            return EmitReceipt(seq=batch.seq, ok=False, error="closed")
        if self._asm.mp_blocked:
            return EmitReceipt(seq=batch.seq, ok=False, error="multiplayer")
        if self._asm.emit_blocked:
            return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail="schema_skew")
        try:
            receipt, text = write_ini_sync(batch, self._scratch / "sae_in.ini", self._health)
            if text is not None:
                self._plant.submit(text)
            return receipt
        except Exception as exc:
            _log.exception("synth emit failed")
            return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail=str(exc)[:200])

    async def close(self) -> None:
        """End both iterators. Idempotent."""
        self._closed = True
        self._box.finish()
