"""WU-3: salted save_folder_key, game_version, recording hygiene, label.json (C6 v1.3, proposals 020/022)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter

from sae.adapters import make_adapter
from sae.adapters.assembler import Assembler
from sae.adapters.health import new_gate
from sae.adapters.keys import SaltError, folder_key, load_salt, synth_key
from sae.adapters.live import LiveAdapter
from sae.adapters.recording import GOD_VIEW_INSIDE, RecordGate
from sae.adapters.replay import ReplayAdapter
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c8_config import RecordConfig, SaeConfig
from sae.contracts.c10_profile import CampaignLabel
from sae.contracts.common import Archetype, FolderKey

from ._asm_helpers import Sink, hb, hello, plyr, result
from .test_live import Mono, Parser, _append, _paths, _result
from .test_synth import drive
from .test_synth_record import _rec

SALT = bytes(range(32))
KEY = TypeAdapter(FolderKey)


def _asm(salt_fn: object = None, fixed: str | None = None) -> tuple[Assembler, list[tuple[HealthKind, str | None]]]:
    ev: list[tuple[HealthKind, str | None]] = []
    a = Assembler(Sink(), lambda k, d: ev.append((k, d)), new_gate(), salt_fn=salt_fn, fixed_key=fixed)  # type: ignore[arg-type]
    return a, ev


# -- keys ---------------------------------------------------------------------------------------


def test_folder_key_matches_contract_formula() -> None:
    want = "f-" + hmac.new(SALT, b"united_nations", hashlib.sha256).hexdigest()[:16]
    assert folder_key(SALT, "united_nations") == want
    assert synth_key("r1") == "f-synth-" + hashlib.sha256(b"r1").hexdigest()[:8]


@given(st.text(max_size=40), st.binary(min_size=32, max_size=32))
def test_keys_always_valid_and_never_contain_name(name: str, salt: bytes) -> None:
    k = folder_key(salt, name)
    assert KEY.validate_python(k) == k
    assert KEY.validate_python(synth_key(name)) == synth_key(name)
    assert len(name) < 8 or name not in k


def test_load_salt_creates_once_and_never_rewrites(tmp_path: Path) -> None:
    d = tmp_path / "a" / "b"
    first = load_salt(d)
    assert len(first) == 32 and (d / "salt").read_bytes() == first
    assert load_salt(d) == first


def test_load_salt_loser_of_race_rereads_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open

    def racing_open(path: object, flags: int, mode: int = 0o777) -> int:
        if str(path).endswith("salt") and flags & os.O_EXCL:
            Path(str(path)).write_bytes(SALT)  # the winner creates it first
            raise FileExistsError(str(path))
        return real_open(path, flags, mode)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", racing_open)
    assert load_salt(tmp_path) == SALT
    assert not list(tmp_path.glob("salt.tmp-*"))


def test_load_salt_existing_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "salt").write_bytes(SALT)
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("must not create"))
    assert load_salt(tmp_path) == SALT


def test_recording_leaves_non_sae_plyr_lines_untouched() -> None:
    gate = RecordGate(RecordConfig(), lambda k, d: None)
    line = "[game] mentions PLYR " + "x" * 300
    assert gate.lines([line]) == [line]


@pytest.mark.parametrize("size", [0, 31, 33])
def test_bad_salt_size_is_error_and_untouched(tmp_path: Path, size: int) -> None:
    (tmp_path / "salt").write_bytes(b"x" * size)
    with pytest.raises(SaltError):
        load_salt(tmp_path)
    assert (tmp_path / "salt").read_bytes() == b"x" * size


def test_unwritable_sae_dir_is_salt_error(tmp_path: Path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    with pytest.raises(SaltError):
        load_salt(blocker / "sub")


# -- assembler ----------------------------------------------------------------------------------


def test_save_observation_carries_salted_key_and_game_version() -> None:
    a, ev = _asm(salt_fn=lambda: SALT)
    a.on_record(hello("2231.04.01"))
    assert a.flush() == []
    obs = a.on_parse(result("2231.04.01"), a.clock().session_epoch, "united_nations_of_earth")
    assert len(obs) == 1
    assert obs[0].save_folder_key == folder_key(SALT, "united_nations_of_earth")
    assert obs[0].game_version == "v"
    assert "united_nations_of_earth" not in obs[0].model_dump_json()
    a.on_record(hb("2231.05.01", 5))
    log = a.flush()
    assert log[0].save_folder_key == obs[0].save_folder_key and log[0].game_version == "v"  # carried forward
    assert not ev


def test_key_none_before_first_parse_and_cleared_on_reload() -> None:
    a, _ = _asm(salt_fn=lambda: SALT)
    a.on_record(hb("2231.04.01", 4))
    first = a.flush()[0]
    assert first.save_folder_key is None and first.game_version is None
    a.on_parse(result("2231.04.01"), a.clock().session_epoch, "c1")
    a.on_record(hello("2231.04.01"))  # reload: epoch bump drops save-derived state
    a.on_record(hb("2231.04.01", 4))
    assert a.flush()[0].save_folder_key is None


def test_bad_salt_reports_invalid_event_once_and_key_stays_none() -> None:
    def boom() -> bytes:
        raise SaltError("salt must be 32 bytes, found 5")

    a, ev = _asm(salt_fn=boom)
    a.on_record(hello("2231.04.01"))
    for _ in range(3):
        out = a.on_parse(result("2231.04.01"), a.clock().session_epoch, "c1")
        assert out[0].save_folder_key is None
    assert [k for k, _ in ev] == [HealthKind.INVALID_EVENT]


def test_salt_read_once() -> None:
    calls: list[int] = []
    a, _ = _asm(salt_fn=lambda: calls.append(1) or SALT)  # type: ignore[func-returns-value]
    a.on_record(hello("2231.04.01"))
    for f in ("c1", "c2"):
        a.on_parse(result("2231.04.01"), a.clock().session_epoch, f)
    assert len(calls) == 1


def test_no_salt_source_means_no_key() -> None:
    a, ev = _asm()
    a.on_record(hello("2231.04.01"))
    assert a.on_parse(result("2231.04.01"), a.clock().session_epoch, "c1")[0].save_folder_key is None
    assert not ev


def test_fixed_key_for_replay_and_synth() -> None:
    a, _ = _asm(fixed=synth_key("run-1"))
    a.on_record(hello("2231.04.01"))
    assert a.on_parse(result("2231.04.01"), a.clock().session_epoch)[0].save_folder_key == synth_key("run-1")


def test_long_game_version_is_clipped_not_dropped() -> None:
    a, ev = _asm()
    a.on_record(hello("2231.04.01"))
    r = result("2231.04.01")
    r = r.model_copy(update={"meta": r.meta.model_copy(update={"game_version": "v" * 100})})
    assert a.on_parse(r, a.clock().session_epoch)[0].game_version == "v" * 64
    assert not ev


# -- live adapter ----------------------------------------------------------------------------------


def _live(
    tmp: Path, *, record: RecordConfig | None = None, rec: Path | None = None
) -> tuple[LiveAdapter, list[tuple[HealthKind, str | None]]]:
    ev: list[tuple[HealthKind, str | None]] = []
    mono = Mono()
    a = LiveAdapter(
        _paths(tmp), Parser(_result("2231.01.01")), Sink(), health=lambda k, d: ev.append((k, d)),
        sleep=mono.sleep, monotonic=mono, record_dir=rec, record=record, sae_dir=tmp / "state",
    )
    return a, ev


def test_live_key_from_save_parent_folder(tmp_path: Path) -> None:
    async def go() -> None:
        a, ev = _live(tmp_path)
        save = tmp_path / "saves" / "c1" / "autosave_2231.01.01.sav"
        save.write_bytes(b"SAV")
        it = a.observations()
        _append(tmp_path, hb("2231.01.01", 1))
        obs = [await asyncio.wait_for(it.__anext__(), 2.0) for _ in range(2)]
        saved = next(o for o in obs if o.source == "save")
        assert saved.save_folder_key == folder_key((tmp_path / "state" / "salt").read_bytes(), "c1")
        assert saved.game_version == _result().meta.game_version
        assert not ev
        await a.close()

    asyncio.run(go())


def test_make_adapter_passes_sae_dir_and_record(tmp_path: Path) -> None:
    cfg = SaeConfig.model_validate(
        {"adapter": "live", "paths": {"sae_dir": str(tmp_path / "s"), "user_dir": str(tmp_path)}}
    )
    a = make_adapter(cfg, Parser(_result()), Sink())
    assert a.mode is AdapterMode.LIVE and isinstance(a, LiveAdapter)
    assert a._rec_gate._cfg == cfg.record


# -- recording hygiene ---------------------------------------------------------------------------


def _gate(**kw: object) -> tuple[RecordGate, list[tuple[HealthKind, str | None]]]:
    ev: list[tuple[HealthKind, str | None]] = []
    return RecordGate(RecordConfig(**kw), lambda k, d: ev.append((k, d))), ev  # type: ignore[arg-type]


def test_gate_saves_none_and_every_n() -> None:
    g, _ = _gate(saves="none")
    assert [g.allow_save(1) for _ in range(3)] == [False] * 3
    g, _ = _gate(saves="every_n", save_every=3)
    assert [g.allow_save(1) for _ in range(7)] == [False, False, True, False, False, True, False]
    g, _ = _gate()
    assert all(g.allow_save(1) for _ in range(5))


def test_gate_cap_stops_saves_forever_with_one_health() -> None:
    g, ev = _gate(max_bytes=10)
    g.note_log(4)
    assert g.allow_save(6) is True  # 10 <= 10
    assert g.allow_save(1) is False
    g.note_log(0)
    assert g.allow_save(0) is False  # stays capped
    assert ev == [(HealthKind.RECORD_CAPPED, "max_bytes 10 reached; bytes_used 10")]


@given(st.lists(st.integers(0, 50), max_size=30), st.integers(0, 200))
def test_gate_never_exceeds_cap(sizes: list[int], cap: int) -> None:
    g, _ = _gate(max_bytes=cap)
    assert sum(s for s in sizes if g.allow_save(s)) <= cap


def test_gate_redacts_plyr_only() -> None:
    g, _ = _gate()
    out = g.lines(["SAE|v1|PLYR|2231.01.01|1|fleet=5", "SAE|v1|HB|2231.01.01|1|1|0"])
    assert out == ["SAE|v1|PLYR|2231.01.01", "SAE|v1|HB|2231.01.01|1|1|0"]
    raw = RecordGate(RecordConfig.model_construct(redact_plyr=False), lambda k, d: None)
    line = "SAE|v1|PLYR|2231.01.01|1|fleet=5"
    assert raw.lines([line]) == [line]


def test_gate_start_writes_marker_only_when_raw(tmp_path: Path) -> None:
    g, _ = _gate()
    g.start(tmp_path / "a")
    assert not (tmp_path / "a" / GOD_VIEW_INSIDE).exists()
    raw = RecordGate(RecordConfig.model_construct(redact_plyr=False), lambda k, d: None)
    raw.start(tmp_path / "b")
    assert (tmp_path / "b" / GOD_VIEW_INSIDE).read_bytes() == b""


def test_live_recording_is_redacted_and_save_policy_applies(tmp_path: Path) -> None:
    async def go() -> None:
        rec = tmp_path / "rec"
        a, ev = _live(tmp_path, record=RecordConfig(max_bytes=5), rec=rec)
        _append(tmp_path, hb("2231.01.01", 1), plyr("2231.01.01", fleet=1234.5))
        a._poll_once()
        text = (rec / "game.log").read_text(encoding="utf-8")
        assert "fleet" not in text and "SAE|v1|PLYR|2231.01.01|sid=1\n" in text
        assert not (rec / GOD_VIEW_INSIDE).exists()
        s1 = tmp_path / "saves" / "c1" / "autosave_a.sav"
        s1.write_bytes(b"SAVE")
        await a._record_save(s1)  # log already exceeds max_bytes=5, so capped
        assert list((rec / "saves").iterdir()) == []
        await a._record_save(s1)
        assert [k for k, _ in ev] == [HealthKind.RECORD_CAPPED]
        await a.close()

    asyncio.run(go())


def test_live_saves_none_logs_only(tmp_path: Path) -> None:
    async def go() -> None:
        rec = tmp_path / "rec"
        a, ev = _live(tmp_path, record=RecordConfig(saves="none"), rec=rec)
        s1 = tmp_path / "saves" / "c1" / "autosave_a.sav"
        s1.write_bytes(b"SAVE")
        await a._record_save(s1)
        assert list((rec / "saves").iterdir()) == [] and not ev
        await a.close()

    asyncio.run(go())


# -- synth --------------------------------------------------------------------------------------


def test_synth_record_writes_label_json(tmp_path: Path) -> None:
    from sae.adapters.synth import SynthAdapter

    rec = tmp_path / "rec"
    a = SynthAdapter(
        [(Archetype.TURTLE, 0), (Archetype.EARLY_AGGRESSION, 1), (Archetype.BALANCED, 1)], 1, 2, Parser(_result()),
        Sink(), parser_exe="sae-parse", scratch_dir=tmp_path / "s", run_fixture=_ok_fixture, record_dir=rec,
    )
    asyncio.run(drive(a))
    label = CampaignLabel.model_validate_json((rec / "label.json").read_text(encoding="utf-8"))
    assert label.source == "synth_schedule" and label.campaign_id is None
    assert [(s.from_date, s.archetype) for s in label.segments] == [
        ("2200.01.01", Archetype.TURTLE), ("2201.01.01", Archetype.EARLY_AGGRESSION),
    ]


async def _ok_fixture(argv: list[str]) -> int:
    Path(argv[argv.index("--out") + 1]).write_bytes(b"SAVE")
    return 0


def test_synth_key_is_per_run_and_in_save_observations(tmp_path: Path) -> None:
    from sae.adapters.synth import SynthAdapter

    keys = []
    for rid in ("20260101-000000-synth-aaaa", "20260101-000001-synth-bbbb"):
        a = SynthAdapter(
            [(Archetype.TURTLE, 0)], 1, 1, Parser(_result("2200.01.01")), Sink(), parser_exe="sae-parse",
            scratch_dir=tmp_path / rid, run_fixture=_ok_fixture, run_id=rid,
        )
        obs, _ = asyncio.run(drive(a))
        saved = [o for o in obs if o.source == "save"]
        assert saved and {o.save_folder_key for o in saved} == {synth_key(rid)}
        keys.append(synth_key(rid))
    assert keys[0] != keys[1]


def test_synth_record_honours_hygiene_and_capped_recording_replays(tmp_path: Path) -> None:
    a, ev, rec = _rec(tmp_path, record=RecordConfig(max_bytes=400_000))
    asyncio.run(drive(a))
    n_log = (rec / "game.log").stat().st_size
    assert not ev and len(list((rec / "saves").iterdir())) == 4

    cap = 2000  # log bytes count too: the first save (log ~1.5 kB) fits, the second (~3 kB) does not
    rec2 = tmp_path / "rec2"
    from sae.adapters.synth import SynthAdapter

    ev2: list[tuple[HealthKind, str | None]] = []
    b = SynthAdapter(
        [(Archetype.TURTLE, 0)], 1, 1, Parser(_result("2200.01.01")), Sink(), health=lambda k, d: ev2.append((k, d)),
        parser_exe="sae-parse", scratch_dir=tmp_path / "s2", run_fixture=_ok_fixture, record_dir=rec2,
        record=RecordConfig(max_bytes=cap),
    )
    asyncio.run(drive(b))
    assert len(list((rec2 / "saves").iterdir())) == 1
    assert [k for k, _ in ev2] == [HealthKind.RECORD_CAPPED]
    assert (rec2 / "game.log").stat().st_size == n_log  # the log kept recording

    r = ReplayAdapter(rec2, Parser(_result("2200.01.01")), Sink(), scratch_dir=tmp_path / "s3")
    obs, _ = asyncio.run(drive(r, emit=False))
    assert len([o for o in obs if o.source == "log"]) == 12
    assert len([o for o in obs if o.source == "save"]) == 1


def test_synth_every_n_keeps_valid_replay_session(tmp_path: Path) -> None:
    a, _, rec = _rec(tmp_path, record=RecordConfig(saves="every_n", save_every=2))
    asyncio.run(drive(a))
    names = sorted(p.name for p in (rec / "saves").iterdir())
    assert len(names) == 2
    r = ReplayAdapter(rec, Parser(_result("2200.01.01")), Sink(), scratch_dir=tmp_path / "s3")
    obs, _ = asyncio.run(drive(r, emit=False))
    assert len([o for o in obs if o.source == "save"]) == 2


def test_replay_key_from_run_id(tmp_path: Path) -> None:
    a, _, rec = _rec(tmp_path)
    asyncio.run(drive(a))
    r = ReplayAdapter(
        rec, Parser(_result("2200.01.01")), Sink(), scratch_dir=tmp_path / "s3", run_id="20260101-000000-replay-abcd"
    )
    obs, _ = asyncio.run(drive(r, emit=False))
    assert {o.save_folder_key for o in obs if o.source == "save"} == {synth_key("20260101-000000-replay-abcd")}
