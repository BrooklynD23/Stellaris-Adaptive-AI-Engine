"""Review fixes: synth label limits, atomic salt, capped bytes_used, no folder name in health or logs."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest

from sae.adapters import keys
from sae.adapters.keys import SaltError, load_salt
from sae.adapters.synth import SynthAdapter, _month_date
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c10_profile import CampaignLabel
from sae.contracts.common import Archetype

from ._asm_helpers import Sink
from .test_live import _mk
from .test_synth import NullParser
from .test_synth_record import _ok


def _synth(tmp_path: Path, schedule: list[tuple[Archetype, int]], years: int) -> tuple[SynthAdapter, list, Path]:
    rec = tmp_path / "rec"
    ev: list[tuple[HealthKind, str | None]] = []
    a = SynthAdapter(
        schedule, 1, years, NullParser(), Sink(),
        health=lambda k, d: ev.append((k, d)), parser_exe="sae-parse", scratch_dir=tmp_path / "scratch",
        run_fixture=_ok, record_dir=rec,
    )
    return a, ev, rec


def test_seventeen_segments_is_one_invalid_event_not_a_crash(tmp_path: Path) -> None:
    a, ev, _ = _synth(tmp_path, [(Archetype.TURTLE, y) for y in range(17)], 17)
    assert a._record_pulse(["SAE|v1|HB|x"]) == ["SAE|v1|HB|x"]
    a._record_pulse(["SAE|v1|HB|y"])
    assert [k for k, _ in ev] == [HealthKind.INVALID_EVENT]


def test_label_first_segment_starts_at_run_start(tmp_path: Path) -> None:
    a, _, rec = _synth(tmp_path, [(Archetype.TURTLE, 3), (Archetype.TECH_RUSH, 5)], 6)
    a._record_pulse(["SAE|v1|HB|x"])
    label = CampaignLabel.model_validate_json((rec / "label.json").read_text(encoding="utf-8"))
    assert label.segments[0].from_date == _month_date(a._start, 0)
    assert label.segments[1].from_date == _month_date(a._start, 60)
    assert [s.archetype for s in label.segments] == [Archetype.TURTLE, Archetype.TECH_RUSH]


def test_salt_empty_file_is_retried_then_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(keys.time, "sleep", sleeps.append)
    (tmp_path / "salt").write_bytes(b"")
    with pytest.raises(SaltError):
        load_salt(tmp_path)
    assert len(sleeps) == 3
    assert not [p for p in tmp_path.iterdir() if p.name != "salt"]


def test_salt_partial_write_by_winner_is_completed_on_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = b"k" * 32
    (tmp_path / "salt").write_bytes(b"")

    def fill(_: float) -> None:
        (tmp_path / "salt").write_bytes(good)

    monkeypatch.setattr(keys.time, "sleep", fill)
    assert load_salt(tmp_path) == good


def test_salt_no_temp_file_left_and_perm_error_is_salt_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(load_salt(tmp_path)) == 32
    assert sorted(p.name for p in tmp_path.iterdir()) == ["salt"]

    def boom(*_: object) -> None:
        raise PermissionError("nope")

    monkeypatch.setattr(os, "open", boom)
    with pytest.raises(SaltError):
        load_salt(tmp_path / "other")
    assert not list((tmp_path / "other").glob("salt*"))


def test_live_copy_failure_hides_folder_name(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    a, _, ev, _ = _mk(tmp_path, record_dir=tmp_path / "rec")
    secret = tmp_path / "saves" / "SecretCampaign"
    secret.mkdir()
    sav = secret / "autosave.sav"
    sav.write_bytes(b"SAVE")

    def boom(*_: object, **__: object) -> None:
        raise OSError(f"cannot copy {sav}")

    import shutil
    orig = shutil.copy2
    shutil.copy2 = boom  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.ERROR):
            asyncio.run(a._record_save(sav))
    finally:
        shutil.copy2 = orig
    assert "SecretCampaign" not in caplog.text and "autosave.sav" in caplog.text
    assert all("SecretCampaign" not in (d or "") for _, d in ev)
