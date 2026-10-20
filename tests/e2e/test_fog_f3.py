"""F3 past-campaign fog check (player-profile plan section 3): god buses of past campaigns never move the profile.

Non-vacuity: god buses must exist and a legal-view edit must move the profile (the control).
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from typing import Any

import pytest

from e2e._cli import run_sae
from e2e._synth import synth

ARCHS = (("turtle", 1), ("tech_rush", 2), ("early_aggression", 3))


def _profile(base: Path, *argv: str) -> str:
    sets = ['profile.mode="strict"', "paths.sae_dir=" + json.dumps(str(base / "sae"))]
    r = run_sae(["profile", *argv], base, sets=sets)
    assert r.code == 0, r.stderr[-800:]
    return r.stdout


def _pbytes(base: Path) -> bytes:
    return (base / "sae" / "profile" / "default" / "profile.json").read_bytes()


def _update(base: Path) -> None:
    _profile(base, "update", "--include-synth", "--force")


def _runs(base: Path) -> list[Path]:
    return sorted(p for p in (base / "runs").iterdir() if p.is_dir())


def _god_lines(run: Path) -> list[str]:
    f = run / "bus.god.jsonl"
    return [ln for ln in f.read_text("utf-8").splitlines() if ln.strip()] if f.is_file() else []


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, parser_exe: Path) -> Path:
    base = tmp_path_factory.mktemp("f3")
    for arch, seed in ARCHS:
        r = synth(base, parser_exe, arch, 3, seed)
        assert r.code == 0, r.stderr[-500:]
    _update(base)
    doc = json.loads(_profile(base, "show", "--json"))
    ids = [c["id"] for c in doc["campaigns"]]
    assert len(ids) == 3
    for cid in ids:
        _profile(base, "close", cid)
    _update(base)
    prof = json.loads(_pbytes(base))
    assert len(prof["source_campaigns"]) == 3
    assert any(f["n_eff"] > 0 for f in prof["features"].values()), "vacuous: no feature has n_eff > 0"
    index = base / "sae" / "campaigns.jsonl"
    before, n = _pbytes(base), len(index.read_text("utf-8").splitlines())
    _update(base)
    assert len(index.read_text("utf-8").splitlines()) == n
    assert _pbytes(base) == before
    return base


@pytest.fixture
def work(built: Path, tmp_path: Path) -> Path:
    """A per-test copy so test order never matters."""
    dst = tmp_path / "w"
    shutil.copytree(built, dst)
    return dst


def _randomise(x: Any, rng: random.Random) -> Any:
    if isinstance(x, bool):
        return x
    if isinstance(x, int):
        return rng.randint(0, 10_000)
    if isinstance(x, float):
        return rng.uniform(0.0, 10_000.0)
    if isinstance(x, list):
        return [_randomise(v, rng) for v in x]
    if isinstance(x, dict):
        return {k: _randomise(v, rng) for k, v in x.items()}
    return x


def test_f3_random_god_buses(work: Path) -> None:
    runs = _runs(work)
    gods = [r for r in runs if any('"observation_god"' in ln for ln in _god_lines(r))]
    assert gods, "vacuous: no run has an observation_god line"
    rng = random.Random(1234)
    before = _pbytes(work)
    for run in gods:
        f = run / "bus.god.jsonl"
        old = f.read_text("utf-8")
        out = []
        for ln in _god_lines(run):
            ev = json.loads(ln)
            ev["payload"] = _randomise(ev.get("payload"), rng)
            out.append(json.dumps(ev, sort_keys=True))
        f.write_text("\n".join(out) + "\n", "utf-8")
        assert f.read_text("utf-8") != old
    _update(work)
    assert _pbytes(work) == before


def test_f3_deleted_god_buses(work: Path) -> None:
    gods = [r / "bus.god.jsonl" for r in _runs(work) if (r / "bus.god.jsonl").is_file()]
    assert gods, "vacuous: no god bus exists"
    before = _pbytes(work)
    for g in gods:
        g.unlink()
    _update(work)
    assert _pbytes(work) == before


def test_f3_control_legal_change_moves_profile(work: Path) -> None:
    before = _pbytes(work)
    edited = False
    for run in _runs(work):
        f = run / "bus.jsonl"
        lines = f.read_text("utf-8").splitlines()
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            ev = json.loads(ln)
            if ev.get("type") != "observation":
                continue
            for v in ev["payload"].get("player_legal", []):
                if v.get("in_contact") is True and v.get("systems_owned") is not None:
                    v["systems_owned"] = v["systems_owned"] + 500
                    assert v["in_contact"] is True
                    lines[i] = json.dumps(ev, sort_keys=True)
                    edited = True
                    break
            if edited:
                break
        if edited:
            f.write_text("\n".join(lines) + "\n", "utf-8")
            break
    assert edited, "vacuous: no in-contact observation with systems_owned"
    _update(work)
    assert _pbytes(work) != before
