"""Tests for the `sae m0` kit: analyzers, tailer, CLI, and the never-touch-the-game guarantees."""

from __future__ import annotations

import ast
import io
import json
import shlex
import sys
from pathlib import Path

import pytest

from sae.cli import main as sae_main
from sae.contracts.c2_ini import parse_ini
from sae.m0 import evidence, results
from sae.m0.tail import Arrival, analyze, run_tail

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "parser" / "fixtures" / "turtle_small"

GAME_LOG = """\
[00:00:01][game.cpp:1]: Game started
[00:00:02][effect.cpp:9]: SAE|v1|HELLO|2200.01.01|mod=0.1.0|schema=1|period=1|quiet=0|mp=0
[00:00:03][effect.cpp:9]: SAE|v1|HB|2200.02.01|m=1|period=1|quiet=0
[00:00:03][effect.cpp:9]: SAE|v1|SNAP|2200.02.01|sid=3|seq=0|fleet=850.5|navcap=40|name=Tzynn Empire
[00:00:03][effect.cpp:9]: SAE|v1|PLYR|2200.02.01|sid=1|fleet=900|name=United Nations of Earth
[00:00:05][effect.cpp:9]: SAE|v1|HB|2200.03.01|m=2|period=1|quiet=0
[00:00:07][effect.cpp:9]: SAE|v1|ACK|2200.03.01|sid=3|seq=1|status=APPLIED|code=OK|reason=0
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def user_dir(tmp_path: Path) -> Path:
    """A fake Stellaris user folder with logs, a mod folder and a save."""
    user = tmp_path / "Stellaris"
    _write(user / "logs" / "game.log", GAME_LOG)
    _write(user / "logs" / "error.log", "[00:00:01] some vanilla error\n")
    _write(user / "mod" / "other.mod", 'name="other"\n')
    (user / "save games" / "empire").mkdir(parents=True)
    (user / "save games" / "empire" / "autosave_2200.02.01.sav").write_bytes((FIXTURE / "save.sav").read_bytes())
    return user


def _m0(user: Path, results_dir: Path, *args: str) -> int:
    return sae_main(["m0", "--user-dir", str(user), "--results", str(results_dir), *args])


# --- analyzers ---------------------------------------------------------------------------------------------------


def test_log_channel_pass(user_dir: Path) -> None:
    v = evidence.check_log_channel(user_dir / "logs" / "game.log")
    assert v.status == "pass"
    assert v.evidence["hello"] == 1 and v.evidence["hb"] == 2
    assert v.evidence["dates_zero_padded"] is True
    assert v.evidence["prefix_sample"] == [f"[00:00:0{i}][effect.cpp:9]: " for i in (2, 3, 5)]
    assert v.evidence["unresolved_names"] == 0 and v.evidence["mp_values"] == [0]


def test_log_channel_duplicate_hb_and_malformed_fail(tmp_path: Path) -> None:
    log = _write(tmp_path / "game.log", GAME_LOG + "SAE|v1|HB|2200.03.01|m=3|period=1|quiet=0\nSAE|v1|HB|bad\n")
    v = evidence.check_log_channel(log)
    assert v.status == "fail"
    assert v.evidence["duplicate_hb_dates"] == ["2200.03.01"]
    assert len(v.evidence["bad_lines"]) == 1  # type: ignore[arg-type]


def test_log_channel_missing_file_inconclusive(tmp_path: Path) -> None:
    assert evidence.check_log_channel(tmp_path / "nope.log").status == "inconclusive"


def test_unpadded_dates_and_unresolved_names_recorded(tmp_path: Path) -> None:
    log = _write(tmp_path / "game.log", "SAE|v1|HELLO|2200.1.1|mod=0.1.0|schema=1|period=1|quiet=0\n"
                 "SAE|v1|HB|2200.2.1|m=1|period=1|quiet=0\nSAE|v1|SNAP|2200.2.1|sid=3|seq=0|name=[This.GetName]\n")
    v = evidence.check_log_channel(log)
    assert v.evidence["dates_zero_padded"] is False
    assert v.evidence["unresolved_names"] == 1


def test_utf8_errors_counted(tmp_path: Path) -> None:
    log = tmp_path / "game.log"
    log.write_bytes(GAME_LOG.encode() + b"SAE|v1|SNAP|2200.03.01|sid=4|seq=0|name=Bad \xff Name\n")
    assert evidence.check_log_channel(log).evidence["utf8_decode_errors"] == 1


def test_snapshots(user_dir: Path) -> None:
    v = evidence.check_snapshots(user_dir / "logs" / "game.log")
    assert v.status == "pass"
    assert v.evidence["managed_sids"] == [3] and v.evidence["player_sid"] == 1 and v.evidence["latest_m"] == 2


def test_snapshots_without_fleet_fail(tmp_path: Path) -> None:
    log = _write(tmp_path / "game.log", "SAE|v1|SNAP|2200.02.01|sid=3|seq=0|name=X\n")
    assert evidence.check_snapshots(log).status == "fail"


def test_ack(user_dir: Path) -> None:
    log = user_dir / "logs" / "game.log"
    assert evidence.check_ack(log, 3, 1).status == "pass"
    assert evidence.check_ack(log, 3, 2).status == "fail"


def test_ack_rejected_fails(tmp_path: Path) -> None:
    log = _write(tmp_path / "game.log", "SAE|v1|ACK|2200.03.01|sid=3|seq=1|status=REJECTED|code=NOT_MANAGED\n")
    v = evidence.check_ack(log, 3, 1)
    assert v.status == "fail" and v.evidence["acks"] == ["REJECTED/NOT_MANAGED on 2200.03.01"]


def test_error_log(user_dir: Path) -> None:
    err = user_dir / "logs" / "error.log"
    assert evidence.check_error_log(err).status == "pass"
    _write(err, "[x] Error in common/ai_budget/sae_budgets.txt line 3\n")
    v = evidence.check_error_log(err)
    assert v.status == "fail" and v.evidence["sae_error_lines"] == 1
    assert evidence.check_error_log(user_dir / "missing.log").status == "inconclusive"


def test_starnet(tmp_path: Path) -> None:
    sn = tmp_path / "starnet"
    _write(sn / "descriptor.mod", 'supported_version="v4.0.*"\n')
    _write(sn / "common" / "x.txt", "a = 1\n")
    assert evidence.check_starnet(sn).status == "fail"
    _write(sn / "common" / "y.txt", "check_variable = { which = str_var_ai_ship_data_factor_corvette }\n")
    v = evidence.check_starnet(sn)
    assert v.status == "pass" and v.evidence["supported_version"] == "v4.0.*"
    assert evidence.check_starnet(tmp_path / "none").status == "inconclusive"


def _fake_parser(tmp_path: Path, payload: dict[str, object], code: int = 0) -> Path:
    # A /bin/sh wrapper, not a python shebang: shebangs split on spaces and the repo path may contain one.
    script = tmp_path / "fake_sae_parse.py"
    script.write_text(f"import sys\nsys.stdout.write({json.dumps(json.dumps(payload))})\nsys.exit({code})\n")
    exe = tmp_path / "fake-sae-parse"
    exe.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n')
    exe.chmod(0o755)
    return exe


def _parse_result(ironman: bool | None = False) -> dict[str, object]:
    doc: dict[str, object] = json.loads((FIXTURE / "expected.json").read_text())
    doc["parser_version"] = "0.1.0"
    doc["save"] = {"path": "x.sav", "size_bytes": 10, "mtime_ns": 1}
    doc["cost"] = {"rss_peak_bytes": 2**20, "cpu_s": 0.1, "wall_s": 0.1}
    doc["meta"] = {**doc["meta"], "ironman": ironman}  # type: ignore[dict-item]
    return doc


@pytest.mark.skipif(sys.platform == "win32", reason="shebang fake binary")
def test_parse_pass_and_ironman(tmp_path: Path, user_dir: Path) -> None:
    save = next((user_dir / "save games" / "empire").iterdir())
    v = evidence.check_parse(_fake_parser(tmp_path, _parse_result()), save)
    assert v.status == "pass" and v.evidence["god"] is not None
    assert evidence.check_parse(_fake_parser(tmp_path, _parse_result()), save, want_ironman=True).status == "fail"
    iron = evidence.check_parse(_fake_parser(tmp_path, _parse_result(True)), save, want_ironman=True)
    assert iron.status == "pass"


@pytest.mark.skipif(sys.platform == "win32", reason="shebang fake binary")
def test_parse_error_and_garbage(tmp_path: Path, user_dir: Path) -> None:
    save = next((user_dir / "save games" / "empire").iterdir())
    err = {"schema_version": 1, "status": "error", "parser_version": "0.1.0", "exit_code": 4,
           "error": "format", "detail": "not a save"}
    v = evidence.check_parse(_fake_parser(tmp_path, err, 4), save)
    assert v.status == "fail" and isinstance(v.evidence.get("error"), dict)
    garbage = tmp_path / "garbage"
    garbage.write_text("#!/bin/sh\necho nope\n")
    garbage.chmod(0o755)
    g = evidence.check_parse(garbage, save)
    assert g.status == "fail" and g.notes[0].startswith("invalid C4 output")
    assert evidence.check_parse(None, save).status == "inconclusive"


# --- tailer ------------------------------------------------------------------------------------------------------


def test_analyze_detects_batching() -> None:
    steady = [Arrival(2.0 * i, i * 20, f"2200.{i + 1:02}.01") for i in range(6)]
    assert analyze(steady).status == "pass"
    batched = [Arrival(2.0 * i, (i // 2) * 40, f"2200.{i + 1:02}.01") for i in range(6)]
    assert analyze(batched).status == "fail"
    fast = [Arrival(0.2 * i, i // 2, f"2200.{i + 1:02}.01") for i in range(6)]
    assert analyze(fast).status == "inconclusive"
    assert analyze(steady[:2]).status == "inconclusive"


class _FakeTailer:
    def __init__(self, batches: list[list[str]]) -> None:
        self.batches = batches

    def poll(self) -> list[str]:
        return self.batches.pop(0) if self.batches else []


def test_run_tail_skips_history_and_stamps() -> None:
    hb = "SAE|v1|HB|2200.{:02}.01|m={}|period=1|quiet=0"
    # history spans two polls (> 1 MiB in real life), then the drained poll; all discarded
    batches = [["SAE|v1|HB|2199.11.01|m=0|period=1|quiet=0"], ["SAE|v1|HB|2199.12.01|m=0|period=1|quiet=0"], []]
    for i in range(1, 5):
        batches += [[hb.format(i, i)], [], ["noise", "SAE|v1|HB|broken"]]
    ticks = iter(float(t) for t in range(100))
    out = io.StringIO()
    v = run_tail(Path("unused"), out, 12.5, clock=lambda: next(ticks), sleep=lambda _s: None,
                 echo=lambda _s: None, tailer=_FakeTailer(batches))  # type: ignore[arg-type]
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["date"] for r in rows] == ["2200.01.01", "2200.02.01", "2200.03.01", "2200.04.01"]
    assert v.status == "pass" and v.evidence["s_per_month"] == 3.0


# --- CLI ---------------------------------------------------------------------------------------------------------


def test_help_and_list(capsys: pytest.CaptureFixture[str], tmp_path: Path, user_dir: Path) -> None:
    assert sae_main(["m0", "--help"]) == 0
    assert _m0(user_dir, tmp_path / "res", "list") == 0
    assert "M0-11" in capsys.readouterr().out


def test_steps_fill_paths(capsys: pytest.CaptureFixture[str], tmp_path: Path, user_dir: Path) -> None:
    assert _m0(user_dir, tmp_path / "res", "steps", "M0-6") == 0
    out = capsys.readouterr().out
    assert str(user_dir) in out and "run sae_in.ini" in out and "{" not in out
    assert _m0(user_dir, tmp_path / "res", "steps", "M0-99") == 2


def test_results_inside_user_dir_refused(capsys: pytest.CaptureFixture[str], user_dir: Path) -> None:
    assert _m0(user_dir, user_dir / "m0", "list") == 2
    assert _m0(user_dir, user_dir, "list") == 2
    assert "inside a game folder" in capsys.readouterr().err


def test_stage_m0_6_renders_valid_ini(tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    assert _m0(user_dir, res, "stage", "M0-6") == 2  # needs --sid/--m
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2", "--target", "3") == 2  # self-target
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2", "--target", "1") == 0
    ini = parse_ini((res / "stage" / "sae_in.ini").read_text(encoding="ascii"))
    cmd = ini.countries[0]
    assert (cmd.sid, cmd.seq, cmd.expires, cmd.econ, cmd.mil, cmd.mil_target) == (3, 1, 5, 2, 2, 1)
    assert not (user_dir / "sae_in.ini").exists()


def test_check_m0_6_flow(tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    assert _m0(user_dir, res, "check", "M0-6") == 2  # not staged yet
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2") == 0
    assert _m0(user_dir, res, "check", "M0-6") == 0
    entry = results.load(res, "M0-6")
    assert entry is not None and entry["auto"]["status"] == "pass"
    assert entry["auto"]["evidence"]["staged_sid"] == 3


def test_record_summary_go(tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    assert _m0(user_dir, res, "init", "--game-version", "4.4.4", "--checksum", "abcd") == 0
    assert _m0(user_dir, res, "check", "M0-3") == 0
    for cid in ("M0-6", "M0-11", "M0-8"):
        assert _m0(user_dir, res, "record", cid, "pass", "--value", "x=1") == 0
    assert _m0(user_dir, res, "record", "M0-3", "pass", "--value", "bad") == 2
    assert _m0(user_dir, res, "summary") == 0
    text = (res / "m0_results.md").read_text()
    assert "channels GO" in text and "knobs GO" in text and "4.4.4" in text


def test_go_verdicts() -> None:
    assert "PARTIAL" in results.go_verdict({"M0-3": "fail", "M0-11": "pass"})
    assert "knobs NO-GO" in results.go_verdict({"M0-8": "fail", "M0-7": "skip"})
    assert "UNDECIDED" in results.go_verdict({})


def test_manual_wins_over_auto(tmp_path: Path) -> None:
    results.save_auto(tmp_path, "M0-4", "pass", {}, [])
    results.save_manual(tmp_path, "M0-4", "fail", "paused HB took 9 s", {})
    entry = results.load(tmp_path, "M0-4")
    assert results.status_of(entry) == "fail" and entry is not None and "auto" in entry


# --- safety ------------------------------------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.skipif(sys.platform == "win32", reason="shebang fake binary")
def test_no_command_writes_to_the_game_folders(tmp_path: Path, user_dir: Path) -> None:
    before = _snapshot(user_dir)
    res = tmp_path / "res"
    exe = _fake_parser(tmp_path, _parse_result())
    save = str(next((user_dir / "save games" / "empire").iterdir()))
    sn = _write(tmp_path / "sn" / "a.txt", "x\n").parent
    for args in (
        ["list"], ["steps", "M0-2"], ["init", "--game-version", "4.4", "--checksum", "c"], ["stage", "M0-2"],
        ["stage", "M0-6", "--sid", "3", "--m", "1"], ["check", "M0-1", "--starnet-dir", str(sn)], ["check", "M0-2"],
        ["check", "M0-3"], ["check", "M0-5"], ["check", "M0-6"], ["--parser-exe", str(exe), "check", "M0-11",
        "--save", save], ["record", "M0-8", "fail"], ["summary"],
    ):
        if args[0].startswith("--"):
            sae_main(["m0", "--user-dir", str(user_dir), "--results", str(res), *args])
        else:
            _m0(user_dir, res, *args)
    assert _snapshot(user_dir) == before


def test_kit_spawns_only_the_parser() -> None:
    """Static guard: no process launch, shell, URL handler or game path write anywhere in sae.m0."""
    banned = {"Popen", "startfile", "system", "call", "check_call", "check_output", "execv", "spawnv", "webbrowser"}
    for path in (ROOT / "src" / "sae" / "m0").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert not names & banned, f"{path.name}: {names & banned}"
        runs = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "run"
                and isinstance(n.value, ast.Name) and n.value.id == "subprocess"]
        assert len(runs) == (1 if path.name == "evidence.py" else 0), path.name
        assert "steam://" not in path.read_text(encoding="utf-8")


class _InterruptingTailer(_FakeTailer):
    def poll(self) -> list[str]:
        if not self.batches:
            raise KeyboardInterrupt
        return super().poll()


def test_run_tail_ctrl_c_keeps_verdict() -> None:
    hb = "SAE|v1|HB|2200.{:02}.01|m={}|period=1|quiet=0"
    batches: list[list[str]] = [[]] + [[hb.format(i, i)] for i in range(1, 5)]
    ticks = iter(float(2 * t) for t in range(100))
    v = run_tail(Path("unused"), io.StringIO(), 1e9, clock=lambda: next(ticks), sleep=lambda _s: None,
                 echo=lambda _s: None, tailer=_InterruptingTailer(batches))  # type: ignore[arg-type]
    assert v.status == "pass" and v.evidence["hb"] == 4


def test_reload_is_a_new_session_not_a_duplicate(tmp_path: Path) -> None:
    replay = "SAE|v1|HB|2200.02.01|m=1|period=1|quiet=0\nSAE|v1|HB|2200.03.01|m=2|period=1|quiet=0\n"
    log = _write(tmp_path / "game.log", GAME_LOG + replay)
    v = evidence.check_log_channel(log)
    assert v.status == "pass" and v.evidence["duplicate_hb_dates"] == []


def test_partial_last_line_ignored(tmp_path: Path) -> None:
    log = tmp_path / "game.log"
    log.write_bytes(GAME_LOG.encode() + b"SAE|v1|HB|2200.04")
    v = evidence.check_log_channel(log)
    assert v.status == "pass" and v.evidence["bad_lines"] == []


def test_error_log_word_match(tmp_path: Path) -> None:
    err = _write(tmp_path / "error.log", "Unknown trigger in Caesar.txt\nPAUSE\n")
    assert evidence.check_error_log(err).status == "pass"
    _write(err, "Error: sae.0 event failed\n")
    assert evidence.check_error_log(err).status == "fail"


def test_restage_bumps_seq(tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2") == 0
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2") == 0
    assert parse_ini((res / "stage" / "sae_in.ini").read_text(encoding="ascii")).countries[0].seq == 2


def test_m0_7_never_run_counts_as_not_passed() -> None:
    assert "knobs NO-GO" in results.go_verdict({"M0-8": "fail"})


# --- reviewer round 1 regressions --------------------------------------------------------------------------------


def test_analyze_three_per_flush_is_batching() -> None:
    arrivals = [Arrival(6.0 * (i // 3), i // 3, f"2200.{i + 1:02}.01") for i in range(12)]
    v = analyze(arrivals)
    assert v.status == "fail" and v.evidence["batched_polls"] == 4


def test_skipped_month_and_unresolved_name_fail(tmp_path: Path) -> None:
    skipped = GAME_LOG.replace("|m=2|", "|m=3|")
    assert evidence.check_log_channel(_write(tmp_path / "a.log", skipped)).status == "fail"
    unresolved = GAME_LOG.replace("name=Tzynn Empire", "name=[This.GetName]")
    assert evidence.check_log_channel(_write(tmp_path / "b.log", unresolved)).status == "fail"
    fallback = evidence.check_log_channel(_write(tmp_path / "c.log", GAME_LOG.replace("name=Tzynn Empire", "name=?")))
    assert fallback.status == "pass" and fallback.evidence["fallback_names"] == 1


def test_corrupt_results_file_exits_1(capsys: pytest.CaptureFixture[str], tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    _write(res / "M0-1.json", "{bad")
    assert _m0(user_dir, res, "list") == 1
    assert "corrupt results file" in capsys.readouterr().err


def test_record_value_validation(capsys: pytest.CaptureFixture[str], tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    assert _m0(user_dir, res, "record", "M0-8", "pass", "--value", "a=1", "--value", "a=2") == 2
    assert _m0(user_dir, res, "record", "M0-8", "pass", "--value", "=x") == 2
    assert "given twice" in capsys.readouterr().err


def test_restage_keeps_m0_6_verdict(tmp_path: Path, user_dir: Path) -> None:
    res = tmp_path / "res"
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2") == 0
    assert _m0(user_dir, res, "check", "M0-6") == 0
    assert _m0(user_dir, res, "stage", "M0-6", "--sid", "3", "--m", "2") == 0
    assert results.status_of(results.load(res, "M0-6")) == "pass"


def test_results_inside_install_refused(tmp_path: Path, user_dir: Path) -> None:
    install = tmp_path / "GOG Games" / "Stellaris"
    _write(install / "stellaris.exe", "")
    assert _m0(user_dir, install / "m0", "list") == 2


def test_reload_of_day_one_save_is_not_a_duplicate(tmp_path: Path) -> None:
    again = "SAE|v1|HB|2200.03.01|m=2|period=1|quiet=0\n"
    v = evidence.check_log_channel(_write(tmp_path / "game.log", GAME_LOG + again))
    assert v.status == "pass"
    dup = "SAE|v1|HB|2200.03.01|m=3|period=1|quiet=0\n"
    assert evidence.check_log_channel(_write(tmp_path / "d.log", GAME_LOG + dup)).status == "fail"
