"""Run-dir builders for the T9 tests: C5 models dumped to disk (bus.jsonl AND a non-empty bus.god.jsonl)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sae.contracts.c4_parse import LegalView
from sae.contracts.c5_bus import (
    BeliefUpdateEvent,
    BeliefUpdatePayload,
    ObservationEvent,
    ObservationGodEvent,
    PolicyRef,
    RunStartedEvent,
    RunStartedPayload,
)
from sae.contracts.c6_adapter import AdapterMode, GodObservation, Observation
from sae.contracts.common import Archetype

POLICY_SHA = "ab" * 32
UNIFORM = {a: 0.25 for a in Archetype}

# (date, systems, at_war, (fleet, tech, econ, fort, frontier), in_contact)
Month = tuple[str, int, bool, tuple[int, int, int, int, int], bool]


def T(year: int, month: int = 1, day: int = 1) -> datetime:
    """A UTC wall time."""
    return datetime(year, month, day, tzinfo=UTC)


def posterior(top: Archetype = Archetype.TURTLE, p: float = 0.7) -> dict[Archetype, float]:
    """A posterior summing to 1 with ``top`` at ``p``."""
    rest = (1 - p) / (len(Archetype) - 1)
    return {a: (p if a == top else rest) for a in Archetype}


def month(date: str, systems: int = 5, war: bool = False, cats: tuple[int, int, int, int, int] = (0, 0, 0, 1, 2),
          in_contact: bool = True) -> Month:
    """Shorthand for one month tuple."""
    return (date, systems, war, cats, in_contact)


def _view(systems: int, war: bool, cats: tuple[int, int, int, int, int], in_contact: bool) -> LegalView:
    if not in_contact:
        return LegalView(observer_sid=2, in_contact=False)
    fleet, tech, econ, fort, frontier = cats
    return LegalView(
        observer_sid=2, in_contact=True, systems_owned=systems, frontier_systems=frontier,
        fortified_border_starbases=fort, fleet_power_cat=fleet, tech_cat=tech, economy_cat=econ,
        player_war_count=1 if war else 0,
    )  # fmt: skip


def make_run(
    runs_dir: Path,
    run_id: str,
    mode: str,
    t_wall: datetime,
    folder_key: str | None,
    months: list[Month],
    belief: dict[Archetype, float] | None = None,
    *,
    keys: list[str | None] | None = None,
    beliefs: list[dict[Archetype, float] | None] | None = None,
) -> Path:
    """Write ``runs/<run_id>/bus.jsonl`` plus a non-empty ``bus.god.jsonl`` (so fog tests are never vacuous).

    ``keys`` overrides the per-month save_folder_key (default: ``folder_key`` for every month). A belief_update
    follows each observation when ``belief`` (all months) or ``beliefs`` (per month) gives one.
    """
    d = runs_dir / run_id
    d.mkdir(parents=True)
    lines: list[str] = []
    god: list[str] = []
    seq = 0
    lines.append(
        RunStartedEvent(
            run_id=run_id, seq=seq, t_wall=t_wall, t_mono_ns=0, game_date=None,
            payload=RunStartedPayload(
                engine_version="0.1.0", contracts={"C5": 1}, adapter_mode=AdapterMode(mode), preset="dev",
                config_sha256="0" * 64, platform="test", python="3.11",
                policy=PolicyRef(version="0.1.0", sha256=POLICY_SHA, source="builtin"),
            ),
        ).model_dump_json()
    )  # fmt: skip
    for i, (date, systems, war, cats, in_contact) in enumerate(months):
        seq += 1
        key = keys[i] if keys is not None else folder_key
        obs = Observation(
            game_date=date, m=None, session_epoch=0, source="log", player_sid=None, managed=(),
            player_legal=(_view(systems, war, cats, in_contact),), legal_as_of=date, save_folder_key=key,
            game_version="v4.4.4",
        )  # fmt: skip
        env = {"run_id": run_id, "t_wall": t_wall, "t_mono_ns": seq, "game_date": obs.game_date}
        lines.append(ObservationEvent(seq=seq, payload=obs, **env).model_dump_json())
        god.append(
            ObservationGodEvent(seq=len(god), payload=GodObservation(game_date=date, source="log"), **env)
            .model_dump_json()
        )
        b = beliefs[i] if beliefs is not None else belief
        if b is not None:
            seq += 1
            lines.append(
                BeliefUpdateEvent(
                    seq=seq, decision_id=f"d-{obs.game_date[:4]}.{obs.game_date[5:7]}-01",
                    payload=BeliefUpdatePayload(
                        prior=UNIFORM, loglik={a: 0.0 for a in Archetype}, posterior=b, entropy=1.0,
                        features={}, top_evidence=(),
                    ),
                    **env,
                ).model_dump_json()
            )  # fmt: skip
    (d / "bus.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "bus.god.jsonl").write_text(("\n".join(god) + "\n") if god else "\n", encoding="utf-8")
    return d


def run_id(n: int, mode: str = "live") -> str:
    """A valid RunId, distinct per ``n``."""
    return f"20261001-12{n // 60:02d}{n % 60:02d}-{mode}-{n:04x}"


def synth_key(n: int) -> str:
    """A valid synth FolderKey."""
    return f"f-synth-{n:08x}"


def live_key(n: int) -> str:
    """A valid live FolderKey."""
    return f"f-{n:016x}"
