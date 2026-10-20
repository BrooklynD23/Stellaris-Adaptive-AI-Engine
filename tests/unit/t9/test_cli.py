"""``sae profile`` end to end through ``cli.main`` (paths are always under tmp_path)."""

from __future__ import annotations

import builtins
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c10_profile import CampaignEvent, CampaignLabel
from sae.profile import cli, store
from sae.profile.index import new_campaign_id

from ._fixtures import T, live_key, make_run, month, posterior, run_id, synth_key

KEY = "default"
# Real helpers captured before the autouse fixture stubs them.
REAL_GAME_RUNNING = cli._game_running
REAL_LOWER_PRIORITY = cli._lower_priority
REAL_ASK_SPLIT = cli._ask_split


class Env:
    """A tmp sae_dir/runs_dir pair and a runner for ``cli.main``."""

    def __init__(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.sae = root / "sae"
        self.runs = root / "runs"
        self.runs.mkdir(parents=True)
        self.capsys = capsys
        self.mode = "strict"

    @property
    def index(self) -> Path:
        return self.sae / "campaigns.jsonl"

    @property
    def pdir(self) -> Path:
        return self.sae / "profile" / KEY

    def flags(self) -> list[str]:
        return [
            "--set", f"paths.sae_dir={self.sae}", "--set", f"profile.mode={self.mode}", "--runs-dir", str(self.runs),
        ]  # fmt: skip

    def run(self, sub: str, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = cli.main("profile", [sub, *self.flags(), *args])
        out = self.capsys.readouterr()
        return code, out.out, out.err

    def rows(self) -> list[CampaignEvent]:
        return store.read_index(self.index)[0]

    def ids(self) -> list[str]:
        return [r.campaign_id for r in self.rows() if r.event == "seen"]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(__import__("os").environ):
        if name.startswith("SAE_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(cli, "_game_running", lambda: False)
    monkeypatch.setattr(cli, "_lower_priority", lambda: None)
    clock = {"t": datetime(2026, 10, 20, 12, tzinfo=UTC)}

    def tick() -> datetime:
        clock["t"] += timedelta(seconds=1)
        return clock["t"]

    monkeypatch.setattr(store, "now", tick)


@pytest.fixture
def env(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Env:
    return Env(tmp_path, capsys)


def add_synth(env: Env, n: int, months: int = 3) -> str:
    ms = [month(f"22{n:02d}.{m:02d}.01", 5 + m) for m in range(1, months + 1)]
    make_run(env.runs, run_id(n, "synth"), "synth", T(2026, 10, n), synth_key(n), ms, posterior())
    return new_campaign_id(synth_key(n), run_id(n, "synth"))


def three_closed(env: Env) -> list[str]:
    ids = [add_synth(env, n) for n in (1, 2, 3)]
    assert env.run("update", "--include-synth")[0] == 0
    assert env.ids() == ids
    for cid in ids:
        assert env.run("close", cid)[0] == 0
    return ids


# --- update --------------------------------------------------------------------------------------------------


def test_update_mode_off_is_2(env: Env) -> None:
    env.mode = "off"
    code, _, err = env.run("update")
    assert code == 2 and "profile.mode is off" in err


def test_update_game_running_1_and_force(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_game_running", lambda: True)
    code, _, err = env.run("update")
    assert code == 1 and "Stellaris is running; close it or pass --force" in err
    assert env.run("update", "--force")[0] == 0


def test_update_empty_index_writes_no_profile(env: Env) -> None:
    code, out, _ = env.run("update")
    assert code == 0 and "no profile" in out and not env.pdir.exists()


def test_update_live_run_end_to_end(env: Env) -> None:
    make_run(env.runs, run_id(1), "live", T(2026, 10, 1), live_key(1), [month("2200.01.01"), month("2201.06.01")])
    code, out, _ = env.run("update")
    assert code == 0 and "1 new row(s)" in out
    assert env.run("update")[0] == 0 and len(env.rows()) == 1
    doc = json.loads((env.pdir / "profile.json").read_text())
    assert doc["source_campaigns"] == []  # active, not closed: stays out of the profile


def test_synth_multi_campaign_profile_and_idempotence(env: Env) -> None:
    ids = three_closed(env)
    assert env.run("update", "--include-synth")[0] == 0
    prof = env.pdir / "profile.json"
    doc = json.loads(prof.read_text())
    assert doc["source_campaigns"] == ids and doc["mode"] == "strict"
    assert datetime.fromisoformat(doc["built_at"]) == env.rows()[-1].at
    before, n_rows = prof.read_bytes(), len(env.rows())
    code, out, _ = env.run("update", "--include-synth")
    assert code == 0 and "0 new row(s)" in out
    assert prof.read_bytes() == before and len(env.rows()) == n_rows
    for cid in ids:
        assert (env.pdir / "summaries" / f"{cid}.json").exists()


def test_update_without_include_synth_ignores_synth(env: Env) -> None:
    add_synth(env, 1)
    assert env.run("update")[0] == 0 and env.rows() == []


def test_update_reports_pending_split_on_stderr(env: Env) -> None:
    make_run(env.runs, run_id(1), "live", T(2026, 10, 1), live_key(1), [month("2200.01.01"), month("2260.01.01")])
    make_run(env.runs, run_id(2), "live", T(2026, 10, 2), live_key(1), [month("2201.01.01")])
    code, _, err = env.run("update")
    assert code == 0 and run_id(2) in err and "pending" in err
    assert len(env.rows()) == 1


def test_corrupt_index_is_1(env: Env) -> None:
    env.sae.mkdir()
    row = CampaignEvent(seq=5, at=datetime(2026, 1, 1, tzinfo=UTC), campaign_id="c-" + "0" * 16, event="closed")
    env.index.write_text(row.model_dump_json(exclude_none=True) + "\n")
    add_synth(env, 1)
    for sub in ("update", "show", "close", "exclude", "delete", "label"):
        extra = {
            "update": ["--include-synth"], "show": [], "delete": ["--yes"],
            "label": ["c-" + "0" * 16, "--segment", "2200.01.01=turtle"],
        }.get(sub, ["c-" + "0" * 16])
        code, _, err = env.run(sub, *extra)
        assert code == 1 and "campaigns.jsonl corrupted; rename it and re-run update" in err, sub


def test_update_corrupt_after_load_gap_appended_race(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    add_synth(env, 1)
    real = store.read_index
    calls = {"n": 0}

    def flaky(path: Path) -> Any:
        calls["n"] += 1
        events, invalid, gap = real(path)
        return events, invalid, gap or calls["n"] >= 3

    monkeypatch.setattr(store, "read_index", flaky)
    code, _, err = env.run("update", "--include-synth")
    assert code == 1 and "corrupted" in err


def test_index_lock_present_is_1(env: Env) -> None:
    add_synth(env, 1)
    env.sae.mkdir()
    (env.sae / store.LOCK_FILE).write_text("held")
    code, _, err = env.run("update", "--include-synth")
    assert code == 1 and "index locked: campaigns.jsonl.lock" in err
    assert (env.sae / store.LOCK_FILE).read_text() == "held" and not env.index.exists()


@pytest.mark.parametrize(
    "bad", [["--set", "profile.mode=bogus"], ["--set", "noequals"], ["--preset", "dev", "--set", "x.y=1"]]
)
def test_bad_config_is_3(env: Env, bad: list[str]) -> None:
    code = cli.main("profile", ["show", *bad])
    assert code == 3 and "invalid config" in env.capsys.readouterr().err


def test_bad_config_file_is_3(env: Env, tmp_path: Path) -> None:
    code = cli.main("profile", ["show", "--config", str(tmp_path / "missing.toml")])
    assert code == 3
    toml = tmp_path / "c.toml"
    toml.write_text('[profile]\nmode = "strict"\n[paths]\nsae_dir = "' + str(env.sae).replace("\\", "/") + '"\n')
    assert cli.main("profile", ["show", "--config", str(toml)]) == 0


def test_usage_errors_exit_2(env: Env) -> None:
    assert cli.main("profile", []) == 2
    assert cli.main("profile", ["bogus"]) == 2
    assert cli.main("profile", ["label", "--set", "x=1"]) == 2  # --segment required
    assert cli.main("profile", ["show", "--help"]) == 0


# --- show / close / exclude / delete -------------------------------------------------------------------------


def test_show_empty_and_with_profile(env: Env) -> None:
    code, out, _ = env.run("show")
    assert code == 0 and "no profile yet" in out and str(env.sae / "salt") in out
    three_closed(env)
    assert env.run("update", "--include-synth")[0] == 0
    code, out, _ = env.run("show")
    assert "eligible=False" in out and "index as of" in out and "expansion_rate_early:" in out and "alpha:" in out


def test_show_json_shape_and_salt_bytes_never_printed(env: Env) -> None:
    env.sae.mkdir(parents=True)
    (env.sae / "salt").write_bytes(b"SECRETSALTBYTES-0123456789abcdef")
    ids = three_closed(env)
    env.run("update", "--include-synth")
    code, out, _ = env.run("show", "--json")
    doc = json.loads(out)
    assert code == 0 and set(doc) == {"campaigns", "profile", "salt"}
    assert [c["id"] for c in doc["campaigns"]] == ids
    assert set(doc["campaigns"][0]) == {"id", "status", "split", "start_date", "last_date"}
    assert doc["profile"]["source_campaigns"] == ids and doc["salt"] == str(env.sae / "salt")
    _, text, err = env.run("show")
    assert "SECRETSALT" not in out + text + err
    # no profile -> null
    for p in env.pdir.glob("profile.json"):
        p.unlink()
    assert json.loads(env.run("show", "--json")[1])["profile"] is None


def test_close_and_exclude_and_unknown(env: Env) -> None:
    cid = add_synth(env, 1)
    env.run("update", "--include-synth")
    assert env.run("close", "c-" + "f" * 16)[0] == 2
    assert env.run("exclude", "c-" + "f" * 16)[0] == 2
    assert env.run("close", cid)[0] == 0 and env.rows()[-1].event == "closed"
    code, out, _ = env.run("close", cid)
    assert code == 0 and "already closed" in out and len(env.rows()) == 2
    assert env.run("exclude", cid, "--reason", "mod list changed")[0] == 0
    assert env.rows()[-1].event == "excluded" and env.rows()[-1].reason == "mod list changed"
    assert env.run("exclude", cid)[0] == 0 and len(env.rows()) == 3  # already excluded: no new row
    assert env.run("exclude", cid, "--reason", "x" * 300)[0] == 0  # excluded already: reason unused


def test_exclude_reason_too_long_is_2(env: Env) -> None:
    cid = add_synth(env, 1)
    env.run("update", "--include-synth")
    code, _, err = env.run("exclude", cid, "--reason", "x" * 300)
    assert code == 2 and "invalid reason" in err


def test_privacy_exclude_removes_files_even_with_mode_off(env: Env) -> None:
    ids = three_closed(env)
    env.run("update", "--include-synth")
    assert (env.pdir / "profile.json").exists()
    env.mode = "off"
    assert env.run("exclude", ids[0])[0] == 0
    assert not (env.pdir / "profile.json").exists() and not (env.pdir / "summaries" / f"{ids[0]}.json").exists()
    assert (env.pdir / "summaries" / f"{ids[1]}.json").exists()
    env.mode = "strict"
    assert env.run("update", "--include-synth")[0] == 0
    doc = json.loads((env.pdir / "profile.json").read_text())
    assert doc["source_campaigns"] == ids[1:]
    assert not (env.pdir / "summaries" / f"{ids[0]}.json").exists()


def test_privacy_delete_one_removes_files_even_with_mode_off(env: Env) -> None:
    ids = three_closed(env)
    env.run("update", "--include-synth")
    assert env.run("label", ids[0], "--segment", "2201.01.01=turtle")[0] == 0
    assert (env.pdir / "labels" / f"{ids[0]}.json").exists()
    env.mode = "off"
    assert env.run("delete", "--campaign", ids[0], "--yes")[0] == 0
    assert not (env.pdir / "profile.json").exists()
    assert not (env.pdir / "summaries" / f"{ids[0]}.json").exists()
    assert not (env.pdir / "labels" / f"{ids[0]}.json").exists()
    assert env.run("delete", "--campaign", ids[0], "--yes")[0] == 2  # deleted is terminal
    env.mode = "strict"
    env.run("update", "--include-synth")
    doc = json.loads((env.pdir / "profile.json").read_text())
    assert doc["source_campaigns"] == ids[1:]
    assert len([r for r in env.rows() if r.event == "seen"]) == 3  # the deleted run is never re-indexed


def test_delete_all_requires_confirmation(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = three_closed(env)
    env.run("update", "--include-synth")
    assert env.run("delete")[0] == 2 and (env.pdir / "profile.json").exists()  # no TTY, no --yes
    assert env.run("delete", "--campaign", "c-" + "f" * 16, "--yes")[0] == 2

    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(builtins, "input", lambda _p="": "nope")
    assert env.run("delete")[0] == 2 and env.pdir.exists()
    monkeypatch.setattr(builtins, "input", lambda _p="": (_ for _ in ()).throw(EOFError()))
    assert env.run("delete")[0] == 2
    monkeypatch.setattr(builtins, "input", lambda _p="": "DELETE")
    assert env.run("delete")[0] == 0
    assert not env.pdir.exists()
    assert [r.campaign_id for r in env.rows() if r.event == "deleted"] == ids
    assert env.run("delete", "--yes")[0] == 0 and len(env.rows()) == 3 + 3 + 3  # seen + closed + deleted, nothing more


def test_delete_with_lock_is_1_and_keeps_files(env: Env) -> None:
    three_closed(env)
    env.run("update", "--include-synth")
    (env.sae / store.LOCK_FILE).write_text("held")
    assert env.run("delete", "--yes")[0] == 1
    assert (env.pdir / "profile.json").exists()


# --- label ---------------------------------------------------------------------------------------------------


def test_label_file_row_and_sha(env: Env) -> None:
    cid = add_synth(env, 1)
    env.run("update", "--include-synth")
    code, _, _ = env.run("label", cid, "--segment", "2200.01.01=turtle", "--segment", "2210.01.01=early_aggression",
                         "--note", "n")
    assert code == 0
    path = env.pdir / "labels" / f"{cid}.json"
    label = CampaignLabel.model_validate_json(path.read_bytes())
    assert label.campaign_id == cid and label.source == "human" and label.blind and label.note == "n"
    assert [s.archetype.value for s in label.segments] == ["turtle", "early_aggression"]
    row = env.rows()[-1]
    assert row.event == "labelled" and row.label_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_label_not_blind_and_recording(env: Env, tmp_path: Path) -> None:
    rec = tmp_path / "rec"
    rec.mkdir()
    assert env.run("label", "--recording", str(rec), "--segment", "2200.01.01=balanced", "--not-blind")[0] == 0
    label = CampaignLabel.model_validate_json((rec / "label.json").read_bytes())
    assert label.campaign_id is None and not label.blind and env.rows() == []
    assert env.run("label", "--recording", str(tmp_path / "nope"), "--segment", "2200.01.01=balanced")[0] == 2


def test_label_no_overwrite(env: Env, tmp_path: Path) -> None:
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "label.json").write_bytes(b"ground truth")
    code, _, err = env.run("label", "--recording", str(rec), "--segment", "2200.01.01=balanced")
    assert code == 2 and f"label exists: {rec / 'label.json'}" in err
    assert (rec / "label.json").read_bytes() == b"ground truth"
    cid = add_synth(env, 1)
    env.run("update", "--include-synth")
    assert env.run("label", cid, "--segment", "2200.01.01=turtle")[0] == 0
    path = env.pdir / "labels" / f"{cid}.json"
    before, n = path.read_bytes(), len(env.rows())
    assert env.run("label", cid, "--segment", "2200.01.01=balanced")[0] == 2
    assert path.read_bytes() == before and len(env.rows()) == n


@pytest.mark.parametrize(
    "args",
    [
        ["--segment", "2200.01.01=turtle"],  # neither id nor --recording
        ["c-" + "f" * 16, "--segment", "2200.01.01=turtle"],  # unknown id
        ["{cid}", "--segment", "2200.01.01"],  # no '='
        ["{cid}", "--segment", "2200.01.01=wizard"],  # bad archetype
        ["{cid}", "--segment", "2210.01.01=turtle", "--segment", "2200.01.01=balanced"],  # not increasing
        ["{cid}", "--segment", "2200.01.01=turtle", "--note", "x" * 300],
    ],
)
def test_label_invalid_is_2(env: Env, args: list[str]) -> None:
    cid = add_synth(env, 1)
    env.run("update", "--include-synth")
    code, _, err = env.run("label", *[a.replace("{cid}", cid) for a in args])
    assert code == 2 and err
    assert not (env.pdir / "labels").exists()


def test_label_both_id_and_recording_is_2(env: Env, tmp_path: Path) -> None:
    assert env.run("label", "c-" + "0" * 16, "--recording", str(tmp_path), "--segment", "2200.01.01=turtle")[0] == 2


def test_label_lock_rolls_back_file(env: Env) -> None:
    cid = add_synth(env, 1)
    env.run("update", "--include-synth")
    (env.sae / store.LOCK_FILE).write_text("held")
    code, _, err = env.run("label", cid, "--segment", "2200.01.01=turtle")
    assert code == 1 and "index locked" in err and not (env.pdir / "labels" / f"{cid}.json").exists()


# --- helpers -------------------------------------------------------------------------------------------------


def test_game_running_helper(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    fresh = cli

    class Sample:
        game_running = True

    class Sensors:
        def sample(self) -> Sample:
            return Sample()

    monkeypatch.setattr(fresh, "make_sensors", lambda: Sensors())
    assert REAL_GAME_RUNNING() is True

    def boom() -> Any:
        raise RuntimeError("no ram probe")

    monkeypatch.setattr(fresh, "make_sensors", boom)
    with caplog.at_level("WARNING"):
        assert REAL_GAME_RUNNING() is False
    assert "probe failed" in caplog.text


def test_lower_priority_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = cli
    calls: list[int] = []
    monkeypatch.setattr(fresh.os, "nice", lambda n: calls.append(n) or 0)
    monkeypatch.setattr(fresh.sys, "platform", "linux")
    REAL_LOWER_PRIORITY()
    assert calls == [19]

    def deny(n: int) -> int:
        raise PermissionError("no")

    monkeypatch.setattr(fresh.os, "nice", deny)
    REAL_LOWER_PRIORITY()  # must not raise

    class Proc:
        def nice(self, value: int) -> None:
            calls.append(value)

    monkeypatch.setattr(fresh.sys, "platform", "win32")  # mock-based: cannot run for real on this host
    monkeypatch.setattr(fresh.psutil, "Process", lambda: Proc(), raising=True)
    monkeypatch.setattr(fresh.psutil, "IDLE_PRIORITY_CLASS", 64, raising=False)
    REAL_LOWER_PRIORITY()
    assert calls[-1] == 64


def test_ask_split_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert REAL_ASK_SPLIT("c-x") is None  # pytest stdin is not a TTY

    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    for reply, want in [("n", "new"), ("R", "reload"), ("", None), ("zzz", None)]:
        monkeypatch.setattr(builtins, "input", lambda _p="", r=reply: r)
        assert REAL_ASK_SPLIT("c-x") == want

    def eof(_p: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)
    assert REAL_ASK_SPLIT("c-x") is None


def test_delete_all_rmtree_failure_exits_1(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    three_closed(env)
    env.run("update", "--include-synth")

    def boom(path: Any) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(cli.shutil, "rmtree", boom)
    assert env.run("delete", "--yes")[0] == 1
