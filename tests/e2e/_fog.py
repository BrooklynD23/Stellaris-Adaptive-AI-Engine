"""Fog-perturbation harness (plan section 3, F0/F1): replay a recording with god data randomised.

The daemon runs in-process so a wrapper can be put on the ``SaveParser`` seam (``ReplayAdapter(parser=...)``).
F1's PLYR leg is live: full seeded PLYR lines are inserted before the first saves (``with_plyr_lines``), so they
reach the run's god bus. The other perturbation is ``PerturbingParser`` (god view of every parse).
"""

from __future__ import annotations

import asyncio
import random
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from e2e import _bus
from sae.adapters.assembler import Assembler
from sae.adapters.replay import ReplayAdapter
from sae.contracts.c4_parse import GodView, ParseResult
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, GodSink, Observation, SaveParser
from sae.contracts.c8_config import SaeConfig, new_run_id, resolve_config

#: Bus event types the engine decides from; they must not move when god data does (plan section 3, F1).
DECISION_TYPES = frozenset(
    {"observation", "belief_update", "strategy_selected", "intent_emitted", "intent_rejected", "outcome"}
)


def _plyr_line(rng: random.Random, date: str) -> bytes:
    return (
        f"SAE|v1|PLYR|{date}|sid=1|fleet={rng.randrange(10**7)}|navcap_used={rng.randrange(10**4)}"
        f"|navcap={rng.randrange(10**4)}|en={rng.randrange(10**5)}|mi={rng.randrange(10**5)}"
        f"|al={rng.randrange(10**5)}|al_inc={rng.randrange(10**3)}|wars={rng.randrange(9)}|name=Human\n"
    ).encode()


def with_plyr_lines(session: Path, dest: Path, seed: int) -> Path:
    """Copy a recording to ``dest`` with a full seeded PLYR line inserted before each of its first 3 saves.

    Each line sits at the save's byte offset, dated like the last log line before it (a save with no such line is
    skipped), so it is replayed before that save is parsed. Later ``game.log.ts`` offsets and save names shift by
    the inserted bytes; each insertion also gets a stamp carrying the ns of the poll that follows it. Two seeds
    give two different players over the same visible game.
    """
    shutil.copytree(session, dest)
    log, stamps, saves = dest / "game.log", dest / "game.log.ts", dest / "saves"
    rng = random.Random(seed)
    data = log.read_bytes()
    ts = [(int(o), int(n)) for o, n in (ln.split() for ln in stamps.read_text(encoding="utf-8").splitlines())]
    found = sorted((int(p.name.split("-", 1)[0]), p) for p in saves.glob("*.sav"))
    inserts: list[tuple[int, bytes]] = []
    for off, _ in found[:3]:
        before = data[:off].decode("utf-8", errors="replace").splitlines()
        dates = [ln.split("|")[3] for ln in before if ln.startswith("SAE|v1|") and ln.count("|") >= 3]
        if dates:
            inserts.append((off, _plyr_line(rng, dates[-1])))

    def shift(o: int) -> int:
        return o + sum(len(b) for at, b in inserts if o >= at)

    out, prev = bytearray(), 0
    for at, line in inserts:
        out += data[prev:at] + line
        prev = at
    log.write_bytes(bytes(out) + data[prev:])
    extra = [(at, next((n for o, n in ts if o >= at), ts[-1][1])) for at, _ in inserts]
    merged = sorted([(shift(o), n) for o, n in ts] + extra, key=lambda t: (t[0], t[1]))
    stamps.write_text("".join(f"{o} {n}\n" for o, n in merged), encoding="utf-8")
    for off, path in sorted(found, reverse=True):
        path.rename(saves / f"{shift(off):012d}-{path.name.split('-', 1)[1]}")
    return dest


def random_god(rng: random.Random) -> GodView:
    """A seeded random but valid ``GodView``."""
    return GodView(
        fleet_power=rng.uniform(0, 1e6),
        systems_owned=rng.randrange(200),
        tech_researched=rng.randrange(200),
        alloys_income=rng.uniform(-50, 500),
        fortified_starbases=rng.randrange(50),
    )


class PerturbingParser:
    """``SaveParser`` wrapper that replaces ``ParseResult.god`` by seeded random values."""

    def __init__(self, inner: SaveParser, seed: int) -> None:
        self._inner = inner
        self._rng = random.Random(seed)

    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        """Parse through the inner parser, then randomise the god view (when one exists)."""
        res = await self._inner.parse(save_path, sections=sections)
        if res is None or res.god is None:
            return res
        return res.model_copy(update={"god": random_god(self._rng)})


class LeakyAssembler(Assembler):
    """F0 mutant: copies ``god.fleet_power`` into every ``LegalView.fleet_power_cat`` it yields (test-only)."""

    def on_parse(self, result: ParseResult, epoch: int, folder: str | None = None) -> list[Observation]:
        """Leak the god value into the legal view, then behave like the real assembler."""
        if result.god is not None:
            cat = int(result.god.fleet_power) % 5 - 2
            legal = tuple(
                v.model_copy(update={"fleet_power_cat": cat}) if v.in_contact else v for v in result.legal
            )
            result = result.model_copy(update={"legal": legal})
        return super().on_parse(result, epoch, folder)


def run_replay(
    session: Path, base: Path, exe: Path, *, perturb_seed: int | None = None, leaky: bool = False
) -> Path:
    """Replay ``session`` in-process (``--speed max``, ``from_recording`` acks); returns the run directory.

    ``perturb_seed`` wraps the governed parser in :class:`PerturbingParser`; ``leaky`` swaps in the F0 mutant.
    """
    from sae import daemon  # imported here: the harness is the only test code that runs the daemon in-process

    runs = base / "runs"
    cfg: SaeConfig = resolve_config(
        {}, {}, {"adapter": "replay", "paths": {"runs_dir": str(runs), "parser_exe": str(exe)}}
    )
    run_id = new_run_id(AdapterMode.REPLAY, datetime.now(UTC))
    scratch = runs / run_id / "scratch"

    def factory(parser: SaveParser, god: GodSink, *, health: Any = None) -> GameAdapter:
        wrapped: SaveParser = parser if perturb_seed is None else PerturbingParser(parser, perturb_seed)
        adapter = ReplayAdapter(
            session, wrapped, god, health=health, scratch_dir=scratch, speed="max",
            ack_model="from_recording", seed=cfg.engine.seed,
        )
        if leaky:
            # private-attr seam into T2; fail loudly if T2 renames/retypes it
            assert isinstance(adapter._asm, Assembler), "ReplayAdapter._asm moved; update F0 seam"
            adapter._asm = LeakyAssembler(god, adapter._health, adapter._gate)
        return adapter

    code = asyncio.run(daemon.run(cfg, factory, run_id))
    assert code == 0, f"daemon exit {code}"
    return runs / run_id


def decision_stream(run_dir: Path) -> list[str]:
    """Canonical lines of the decision event types of a run (volatile fields dropped), in bus order."""
    return [_bus.canonical(e) for e in _bus.load(run_dir) if e["type"] in DECISION_TYPES]
