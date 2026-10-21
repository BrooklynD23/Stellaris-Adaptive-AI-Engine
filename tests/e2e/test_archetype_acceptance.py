"""T4 acceptance on real synth: each archetype reaches its C3 section 6 counter by year 30 in >= 3 of 4 seeds."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e import _bus
from e2e._cli import CliResult, run_parallel
from e2e._synth import COUNTER, synth

ARCHETYPES = tuple(COUNTER)
NEEDED = 3


@pytest.fixture(scope="module")
def seeds(full: bool) -> tuple[int, ...]:
    return (1, 2, 3, 4) if full else (1,)


@pytest.fixture(scope="module")
def finals(
    tmp_path_factory: pytest.TempPathFactory, parser_exe: Path, seeds: tuple[int, ...]
) -> dict[str, list[str]]:
    base = tmp_path_factory.mktemp("accept")
    keys = [(a, s) for a in ARCHETYPES for s in seeds]
    results: list[CliResult] = run_parallel(
        [lambda k=k: synth(base / f"{k[0]}-{k[1]}", parser_exe, k[0], 30, k[1]) for k in keys]  # type: ignore[misc]
    )
    out: dict[str, list[str]] = {a: [] for a in ARCHETYPES}
    for (arch, _), r in zip(keys, results, strict=True):
        assert r.code == 0 and r.run_dir is not None, r.stderr[-500:]
        events = _bus.load(r.run_dir)
        assert len([e for e in events if e["type"] == "strategy_selected"]) >= 360  # non-vacuity: 30 years
        out[arch].append(_bus.final_strategy(events))
    return out


@pytest.mark.parametrize("arch", ARCHETYPES)
def test_archetype_reaches_counter(finals: dict[str, list[str]], arch: str, full: bool) -> None:
    hits = finals[arch].count(COUNTER[arch])
    print(f"acceptance {arch}: finals={finals[arch]} expect={COUNTER[arch]} hits={hits}/{len(finals[arch])}")
    if full:
        assert hits >= NEEDED, finals[arch]
    else:
        assert len(finals[arch]) == 1  # default mode: one seed, report only
