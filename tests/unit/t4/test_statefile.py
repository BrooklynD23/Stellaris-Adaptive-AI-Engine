"""state.json: atomic write, round-trip, clear errors."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sae.contracts.common import Archetype
from sae.core.state import EngineState
from sae.daemon.statefile import StateFileError, load_state, save_state

from .fake_synth import Sim


def test_round_trip_and_no_tmp_left(tmp_path: Path) -> None:
    state, _ = Sim(Archetype.TURTLE, 1).run(30)
    p = tmp_path / "state.json"
    save_state(p, state)
    assert load_state(p) == state
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]
    save_state(p, EngineState())  # overwrite is atomic and replaces
    assert load_state(p) == EngineState()


def test_missing(tmp_path: Path) -> None:
    with pytest.raises(StateFileError, match="missing"):
        load_state(tmp_path / "nope.json")


def test_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateFileError, match="corrupt"):
        load_state(p)
    p.write_text("[1]", encoding="utf-8")
    with pytest.raises(StateFileError, match="not an object"):
        load_state(p)
    p.write_text(json.dumps({"state_version": 1, "next_intent_id": -5}), encoding="utf-8")
    with pytest.raises(StateFileError, match="invalid fields"):
        load_state(p)


def test_version_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"state_version": 2}), encoding="utf-8")
    with pytest.raises(StateFileError, match="version mismatch"):
        load_state(p)


@given(st.text(max_size=200))
def test_arbitrary_text_only_raises_state_file_error(tmp_path_factory: pytest.TempPathFactory, text: str) -> None:
    p = tmp_path_factory.mktemp("s") / "state.json"
    p.write_text(text, encoding="utf-8")
    try:
        load_state(p)
    except StateFileError:
        pass


def test_fsync_before_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_sync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_sync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    save_state(tmp_path / "state.json", EngineState())
    assert calls == ["fsync", "replace"]


def test_default_runs_dir() -> None:
    from sae.daemon.runs_dir import default_runs_dir

    home = Path.home()
    assert default_runs_dir({"LOCALAPPDATA": "L"}, "win32") == Path("L") / "sae" / "runs"
    assert default_runs_dir({}, "win32") == home / "AppData" / "Local" / "sae" / "runs"
    assert default_runs_dir({"XDG_STATE_HOME": "X"}, "linux") == Path("X") / "sae" / "runs"
    assert default_runs_dir({}, "linux") == home / ".local" / "state" / "sae" / "runs"
    assert default_runs_dir({"LOCALAPPDATA": "L"}, "linux") == home / ".local" / "state" / "sae" / "runs"


def test_old_state_without_new_fields_loads(tmp_path: Path) -> None:
    data = json.loads(EngineState().model_dump_json())
    data.pop("decision_gen")
    data["lifecycle"].pop("invalid_seen")
    p = tmp_path / "state.json"
    p.write_text(json.dumps(data))
    s = load_state(p)
    assert s.decision_gen == 0 and s.lifecycle.invalid_seen == 0
