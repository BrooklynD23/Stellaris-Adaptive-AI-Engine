"""Snapshot derivation tests: fixture files in a temporary repo and dev dir, no network, no game."""

from __future__ import annotations

import builtins
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from devdash import events, state

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def _ts(minutes_ago: float) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def _emit(
    dev: Path, kind: str, msg: str, *, ref: str | None = None, ago: float = 1, source: str = "t", pct: int | None = None
) -> None:
    ev = events.make_event(source, kind, msg, ref=ref, pct=pct, now=_ts(ago))
    events.append(ev, events.events_path(dev, "wsl"))


def _snap(repo: Path, dev: Path, **kw: Any) -> dict[str, Any]:
    return state.snapshot(repo, dev, now=NOW, **kw)


def _gate(snap: dict[str, Any], gid: str) -> dict[str, Any]:
    for group in snap["gates"]:
        for item in group["items"]:
            if item["id"] == gid:
                return dict(item)
    raise AssertionError(f"gate {gid} not in snapshot")


def _m0(repo: Path, gid: str, status: str = "pass") -> None:
    folder = repo / "m0_results"
    folder.mkdir(exist_ok=True)
    entry = {"manual": {"status": status, "values": {"workshop_id": "123"}, "at": "2026-10-29T10:00:00+00:00"}}
    (folder / f"{gid}.json").write_text(json.dumps(entry), encoding="utf-8")


def test_empty_repo_snapshot(tmp_path: Path, dev: Path) -> None:
    snap = _snap(tmp_path, dev)
    assert snap["enabled"] is False
    assert snap["owner"] is None and snap["ci"] is None
    assert snap["env"] == [] and snap["feed"] == [] and snap["running"] == [] and snap["prs"] == []
    assert snap["tasks"] == {"done": [], "in_progress": [], "pending": [], "blocked": []}
    kpis = {k["label"]: k["value"] for k in snap["kpis"]}
    assert [k["label"] for k in snap["kpis"]] == ["Done", "In progress", "Pending", "Blocked", "Gates passed", "CI"]
    assert kpis["Done"] == kpis["Pending"] == "0"
    assert kpis["Gates passed"] == "—" and kpis["CI"] == "—"
    assert snap["results"]["source"] is None and snap["results"]["budget_s"] == 3.0
    assert snap["results"]["parse"] == [] and snap["results"]["belief"] == []
    assert all(g["passed"] == 0 for g in snap["gates"])
    json.dumps(snap)


def test_branch_from_head(tmp_path: Path, dev: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feat/x\n", encoding="utf-8")
    assert _snap(tmp_path, dev)["branch"] == "feat/x"
    (tmp_path / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    assert _snap(tmp_path, dev)["branch"] == "0123456"


def test_running_and_stale(tmp_path: Path, dev: Path) -> None:
    _emit(dev, "start", "fresh job", ref="job:a", ago=5, pct=10)
    _emit(dev, "progress", "step two", ref="job:a", ago=2, pct=40)
    _emit(dev, "start", "old job", ref="job:b", ago=45)
    snap = _snap(tmp_path, dev)
    by_key = {r["key"]: r for r in snap["running"]}
    assert by_key["job:a"]["title"] == "fresh job" and by_key["job:a"]["step"] == "step two"
    assert by_key["job:a"]["pct"] == 40 and by_key["job:a"]["stale"] is False
    assert by_key["job:b"]["stale"] is True
    _emit(dev, "done", "finished", ref="job:a", ago=1)
    assert [r["key"] for r in _snap(tmp_path, dev)["running"]] == ["job:b"]


def test_running_key_falls_back_to_source(tmp_path: Path, dev: Path) -> None:
    _emit(dev, "start", "no ref", source="tool", ago=3)
    assert _snap(tmp_path, dev)["running"][0]["key"] == "tool"


def test_agent_start_closed_by_stop(tmp_path: Path, dev: Path) -> None:
    _emit(dev, "start", "first", ref="agent:sae-scout", ago=10)
    _emit(dev, "start", "second", ref="agent:sae-scout", ago=8)
    _emit(dev, "done", "stopped", ref="agent:sae-scout", ago=5)
    running = _snap(tmp_path, dev)["running"]
    assert len(running) == 1 and running[0]["title"] == "second"


def test_gate_status_precedence(tmp_path: Path, dev: Path) -> None:
    # todo baseline; M0-1 untouched
    assert _gate(_snap(tmp_path, dev), "M0-1")["status"] == "todo"
    # result passes
    _m0(tmp_path, "M0-5", "pass")
    _m0(tmp_path, "M0-6", "fail")
    snap = _snap(tmp_path, dev)
    assert _gate(snap, "M0-5")["status"] == "pass" and _gate(snap, "M0-6")["status"] == "fail"
    # run beats result; stale run does not
    _emit(dev, "start", "go", ref="M0-5", ago=2)
    _emit(dev, "start", "go", ref="M0-1", ago=40)
    snap = _snap(tmp_path, dev)
    assert _gate(snap, "M0-5")["status"] == "run"
    assert _gate(snap, "M0-1")["status"] == "todo"
    # start then owner -> owner
    _emit(dev, "owner", "play now", ref="M0-5", ago=1)
    assert _gate(_snap(tmp_path, dev), "M0-5")["status"] == "owner"
    # static rules
    snap = _snap(tmp_path, dev)
    assert _gate(snap, "R-1")["status"] == "hardware"
    assert _gate(snap, "X4-G0")["status"] == "later"
    assert _gate(snap, "X2-G1")["status"] == "waiting"
    assert _gate(snap, "X3-G4")["status"] == "todo"
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "env.json").write_text(json.dumps({"windows_uv": False}), encoding="utf-8")
    assert _gate(_snap(tmp_path, dev), "X3-G4")["status"] == "setup"
    (dev / "env.json").write_text(json.dumps({"windows_uv": True}), encoding="utf-8")
    assert _gate(_snap(tmp_path, dev), "X3-G4")["status"] == "todo"


def test_junit_index_and_pending(tmp_path: Path, dev: Path) -> None:
    dev.mkdir(parents=True, exist_ok=True)
    idx = {
        "X3-G1": {
            "status": "pass",
            "evidence": {"a": "b"},
            "source": "junit",
            "run": "r",
            "at": "2026-10-29T09:00:00+00:00",
        },
        "X3-G2": {"status": "pending", "evidence": {}, "source": "junit", "run": "r", "at": ""},
        "X3-G3": {"status": "skip", "evidence": {}, "source": "junit", "run": "r", "at": ""},
    }
    (dev / "gates.json").write_text(json.dumps(idx), encoding="utf-8")
    snap = _snap(tmp_path, dev)
    assert _gate(snap, "X3-G1")["status"] == "pass" and _gate(snap, "X3-G1")["when"] == "2026-10-29T09:00:00+00:00"
    assert _gate(snap, "X3-G2")["status"] == "todo"
    assert _gate(snap, "X3-G3")["status"] == "skip"
    x3 = next(g for g in snap["gates"] if g["stage"] == "x3")
    assert x3["passed"] == 1 and x3["total"] == 12


def test_manual_m0_record_shows(tmp_path: Path, dev: Path) -> None:
    _m0(tmp_path, "M0-5", "pass")
    snap = _snap(tmp_path, dev)
    item = _gate(snap, "M0-5")
    assert item["status"] == "pass" and item["unverified"]
    kpis = {k["label"]: k["value"] for k in snap["kpis"]}
    assert kpis["Gates passed"].startswith("1/")


def test_m0_cache_sees_rewrite(tmp_path: Path, dev: Path) -> None:
    _m0(tmp_path, "M0-5", "pass")
    assert _gate(_snap(tmp_path, dev), "M0-5")["status"] == "pass"
    _m0(tmp_path, "M0-5", "fail")
    folder = tmp_path / "m0_results" / "M0-5.json"
    folder.write_text(
        folder.read_text(encoding="utf-8").replace('"values"', '"note": "again", "values"'), encoding="utf-8"
    )
    assert _gate(_snap(tmp_path, dev), "M0-5")["status"] == "fail"


def test_owner_banner_clears_on_done(tmp_path: Path, dev: Path) -> None:
    _emit(dev, "owner", "Play 10 minutes", ref="pipeline:play", ago=4)
    owner = _snap(tmp_path, dev)["owner"]
    assert owner is not None and owner["ref"] == "pipeline:play" and owner["msg"] == "Play 10 minutes"
    assert owner["judged"] is False and owner["since"].startswith("2026-10-29")
    _emit(dev, "done", "marked", ref="pipeline:play", ago=1)
    assert _snap(tmp_path, dev)["owner"] is None


def test_owner_carries_gate_question(tmp_path: Path, dev: Path) -> None:
    from gates import load_gates

    judged = next(g for g in load_gates() if g.judged)
    _emit(dev, "owner", "judge it", ref=judged.id, ago=1)
    owner = _snap(tmp_path, dev)["owner"]
    assert owner is not None and owner["judged"] is True and owner["question"] == judged.question
    assert owner["evidence"] == [dict(e) for e in judged.evidence]


def test_feed_newest_first_capped(tmp_path: Path, dev: Path) -> None:
    for i in range(230):
        ev = events.make_event("t", "info", f"m{i}", now=NOW - timedelta(seconds=300 - i))
        events.append(ev, events.events_path(dev, "wsl"))
    snap = _snap(tmp_path, dev)
    assert len(snap["feed"]) == 200
    assert snap["feed"][0]["msg"] == "m229" and snap["feed"][-1]["msg"] == "m30"
    assert set(snap["feed"][0]) == {"ts", "source", "kind", "ref", "msg"}


def test_events_param_used(tmp_path: Path, dev: Path) -> None:
    evs = [events.make_event("t", "info", "given", now=_ts(1))]
    snap = _snap(tmp_path, dev, events=evs)
    assert [f["msg"] for f in snap["feed"]] == ["given"]


def test_invalid_lines_counted_and_enabled(tmp_path: Path, dev: Path) -> None:
    dev.mkdir(parents=True)
    (dev / "events.jsonl").write_bytes(b"garbage\n")
    events.enable(dev)
    snap = _snap(tmp_path, dev)
    assert snap["invalid_lines"] == 1 and snap["enabled"] is True


def test_tasks_and_kpis(tmp_path: Path, dev: Path) -> None:
    path = tmp_path / "docs" / "dev" / "tasks.json"
    path.parent.mkdir(parents=True)
    rows = [
        {"id": f"T{i}", "title": f"t{i}", "status": s} for i, s in enumerate(["done", "done", "pending", "blocked"])
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    snap = _snap(tmp_path, dev)
    assert [t["id"] for t in snap["tasks"]["done"]] == ["T0", "T1"]
    kpis = {k["label"]: k["value"] for k in snap["kpis"]}
    assert kpis["Done"] == "2" and kpis["Blocked"] == "1" and kpis["In progress"] == "0"


def test_results_from_pipeline_run(tmp_path: Path, dev: Path) -> None:
    run = dev / "pipeline" / "20261029"
    run.mkdir(parents=True)
    shutil.copy(FIXTURES / "results.json", run / "results.json")
    bus = run / "run1"
    shutil.copytree(FIXTURES / "run1", bus)
    res = _snap(tmp_path, dev)["results"]
    assert res["source"] == "20261029" and res["budget_s"] == 3.0
    assert res["parse"] == [
        {"label": "save-a", "wall_s": 1.25, "rss_bytes": 1000},
        {"label": "save-b", "wall_s": 2.5, "rss_bytes": 2000},
    ]
    assert res["impact"] == [{"label": "overhead", "value": "1.2%", "pct": 12}]
    assert res["belief"] == [
        {"date": "2200.01.01", "p": {"rush": 0.7, "turtle": 0.3}},
        {"date": "2200.02.01", "p": {"rush": 0.2, "turtle": 0.8}},
    ]
    assert res["commitments"] == [
        {"date": "2200.01.01", "strategy": "hold", "reason": "floor"},
        {"date": "2200.02.01", "strategy": "expand", "reason": "safe"},
    ]


def test_results_without_bus(tmp_path: Path, dev: Path) -> None:
    run = dev / "pipeline" / "r"
    run.mkdir(parents=True)
    shutil.copy(FIXTURES / "results.json", run / "results.json")
    res = _snap(tmp_path, dev)["results"]
    assert res["parse"] and res["belief"] == [] and res["commitments"] == []


def test_never_opens_god_bus(tmp_path: Path, dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = dev / "pipeline" / "r"
    run.mkdir(parents=True)
    shutil.copy(FIXTURES / "results.json", run / "results.json")
    shutil.copytree(FIXTURES / "run1", run / "run1")
    (run / "run1" / "bus.god.jsonl").write_text("secret\n", encoding="utf-8")
    real_open = builtins.open
    real_path_open = Path.open

    def guard(path: object) -> None:
        if str(path).endswith("bus.god.jsonl"):
            raise AssertionError("opened god bus")

    def fake_open(file: Any, *a: Any, **k: Any) -> Any:
        guard(file)
        return real_open(file, *a, **k)

    def fake_path_open(self: Path, *a: Any, **k: Any) -> Any:
        guard(self)
        return real_path_open(self, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(Path, "open", fake_path_open)
    assert _snap(tmp_path, dev)["results"]["belief"]


def test_gh_failure_is_empty(tmp_path: Path, dev: Path) -> None:
    snap = _snap(tmp_path, dev, gh=lambda args: None)
    assert snap["prs"] == [] and snap["ci"] is None
    snap = _snap(tmp_path, dev, gh=lambda args: "not json")
    assert snap["prs"] == [] and snap["ci"] is None

    def boom(args: list[str]) -> str | None:
        raise OSError("gh missing")

    assert _snap(tmp_path, dev, gh=boom)["prs"] == []


def test_gh_data(tmp_path: Path, dev: Path) -> None:
    def gh(args: list[str]) -> str | None:
        if args[0] == "pr":
            return json.dumps(
                [
                    {
                        "number": 4,
                        "title": "x",
                        "isDraft": True,
                        "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
                    }
                ]
            )
        return json.dumps([{"status": "completed", "conclusion": "success", "url": "https://example.test/run/1"}])

    snap = _snap(tmp_path, dev, gh=gh)
    assert snap["prs"] == [{"number": 4, "title": "x", "isDraft": True, "checks": "pass"}]
    assert snap["ci"] == {"status": "success", "url": "https://example.test/run/1"}
    assert {k["label"]: k["value"] for k in snap["kpis"]}["CI"] == "success"


def test_env_list_from_env_json(tmp_path: Path, dev: Path) -> None:
    dev.mkdir(parents=True)
    raw = {"install_dir": "/x/Stellaris", "windows_uv": False, "game_running": False, "mod_stale": True, "parser": None}
    (dev / "env.json").write_text(json.dumps(raw), encoding="utf-8")
    env = {e["k"]: e for e in _snap(tmp_path, dev)["env"]}
    assert env["windows_uv"]["tone"] in {"warn", "bad"} and env["parser"]["tone"] == "muted"
    assert env["mod_stale"]["tone"] == "warn" and env["install_dir"]["v"] == "/x/Stellaris"
    assert all(e["tone"] in {"ok", "info", "warn", "bad", "muted"} for e in env.values())


# --- fix round 1 ---------------------------------------------------------------------------------------------------

_DUMMY = {
    "user_dir": "/u",
    "save": "/s.sav",
    "starnet_dir": "/sn",
    "parser_exe": "/p",
    "ironman_save": "/i.sav",
    "r1_csv": "/r1.csv",
    "run_dir": "/run",
}


def _cmds() -> dict[str, str]:
    return {
        i["id"]: i["cmd"]
        for g in _snap(Path("/nonexistent-repo"), Path("/nonexistent-dev"))["gates"]
        for i in g["items"]
    }


def test_cmd_per_runner_kind() -> None:
    cmds = _cmds()
    assert cmds["X3-G0"] == "uv run python3 tools/game/run_deferred.py --list"
    assert cmds["X3-G1"] == (
        "uv run python3 tools/game/run_deferred.py --out .sae-dev/pipeline/<run> -- -k test_parse_real_saves"
    )
    assert cmds["M0-1"] == "uv run sae m0 --user-dir {user_dir} check M0-1 --starnet-dir {starnet_dir}"
    assert cmds["M0-2"].startswith("uv run sae m0 --user-dir {user_dir} stage M0-2 && ")
    assert cmds["M0-7"] == "uv run sae m0 steps M0-7 then uv run sae m0 record M0-7 pass|fail --value k=v"
    assert cmds["M0-4"].startswith("uv run sae m0 --user-dir {user_dir} tail --seconds 300 && uv run sae m0 steps M0-4")
    assert cmds["R-1"] == "uv run sae bench overhead {r1_csv}"
    assert cmds["R-2"] == "see bench/README.md"
    assert cmds["X4-G0"] == "manual"
    assert "pipeline.py" not in " ".join(cmds.values())


def test_cmds_parse_with_real_clis() -> None:
    import re

    from sae.bench import cli as bench_cli
    from sae.m0 import cli as m0_cli

    m0_parser = m0_cli._parser()
    bench_parser = bench_cli._build()
    m0_seen = bench_seen = 0
    for cmd in _cmds().values():
        for seg in re.split(r" && | then ", cmd):
            if seg.startswith("uv run sae m0 "):
                text = seg.removeprefix("uv run sae m0 ").replace("pass|fail", "pass").format(**_DUMMY)
                argv = text.split()
                if argv[0] != "--user-dir":
                    argv = ["--user-dir", "/x", *argv]
                m0_parser.parse_args(argv)
                m0_seen += 1
            elif seg.startswith("uv run sae bench "):
                bench_parser.parse_args(seg.removeprefix("uv run sae bench ").format(**_DUMMY).split())
                bench_seen += 1
    assert m0_seen >= 20 and bench_seen == 2


def _run_with_results(tmp_path: Path, dev: Path, results: dict[str, Any]) -> Path:
    run = dev / "pipeline" / "r"
    run.mkdir(parents=True)
    (run / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return run


def test_run_dir_outside_is_rejected(tmp_path: Path, dev: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    shutil.copytree(FIXTURES / "run1", outside)
    for bad in (str(outside), "../../../../" + outside.name, "../../.."):
        _run_with_results(tmp_path, dev, {"run_dir": bad}) if not (dev / "pipeline" / "r").exists() else (
            (dev / "pipeline" / "r" / "results.json").write_text(json.dumps({"run_dir": bad}), encoding="utf-8")
        )
        res = _snap(tmp_path, dev)["results"]
        assert res["belief"] == [] and res["commitments"] == [], bad


def test_bus_symlink_rejected(tmp_path: Path, dev: Path) -> None:
    run = _run_with_results(tmp_path, dev, {"run_dir": "run1"})
    (run / "run1").mkdir()
    (run / "run1" / "bus.god.jsonl").write_text((FIXTURES / "run1" / "bus.jsonl").read_text(encoding="utf-8"))
    (run / "run1" / "bus.jsonl").symlink_to(run / "run1" / "bus.god.jsonl")
    assert _snap(tmp_path, dev)["results"]["belief"] == []


def test_run_dir_inside_repo_allowed(tmp_path: Path, dev: Path) -> None:
    shutil.copytree(FIXTURES / "run1", tmp_path / "runs" / "one")
    _run_with_results(tmp_path, dev, {"run_dir": str(tmp_path / "runs" / "one")})
    assert len(_snap(tmp_path, dev)["results"]["belief"]) == 2


def test_home_path_scrubbed(tmp_path: Path, dev: Path) -> None:
    home = str(Path.home())
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "env.json").write_text(json.dumps({"install_dir": f"{home}/Steam", "parser": home}), encoding="utf-8")
    run = _run_with_results(
        tmp_path,
        dev,
        {
            "parse": [{"label": f"{home}/save", "wall_s": 1.0}],
            "impact": [{"label": "x", "value": f"{home}/v", "pct": 5}],
        },
    )
    assert run.exists()
    snap = _snap(tmp_path, dev)
    text = json.dumps(snap)
    assert home not in text and "<HOME>" in text


def test_bad_registry_gives_no_gates(
    tmp_path: Path, dev: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> list[Any]:
        raise ValueError("broken registry")

    state._CACHE.clear()
    monkeypatch.setattr(state, "load_gates", boom)
    snap = _snap(tmp_path, dev)
    state._CACHE.clear()
    assert snap["gates"] == []
    assert "broken registry" in capsys.readouterr().err


def test_registry_loaded_once_per_signature(tmp_path: Path, dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = state.load_gates

    def counting() -> Any:
        calls.append(1)
        return real()

    state._CACHE.clear()
    monkeypatch.setattr(state, "load_gates", counting)
    _snap(tmp_path, dev)
    _emit(dev, "owner", "x", ref="M0-5")
    _snap(tmp_path, dev)
    state._CACHE.clear()
    assert len(calls) == 1


def test_nonfinite_numbers_dropped(tmp_path: Path, dev: Path) -> None:
    run = dev / "pipeline" / "r"
    run.mkdir(parents=True)
    (run / "results.json").write_text(
        '{"budget_s": NaN, "parse": [{"label": "a", "wall_s": Infinity}, {"label": "b", "wall_s": 1.5}],'
        ' "impact": [{"label": "i", "value": "v", "pct": NaN}], "run_dir": "bus"}',
        encoding="utf-8",
    )
    (run / "bus").mkdir()
    (run / "bus" / "bus.jsonl").write_text(
        '{"seq":1,"game_date":"d","type":"belief_update","decision_id":"x","payload":{"posterior":{"a":NaN}}}\n'
        '{"seq":2,"game_date":"d","type":"belief_update","decision_id":"x","payload":{"posterior":{"a":0.5}}}\n',
        encoding="utf-8",
    )
    res = _snap(tmp_path, dev)["results"]
    assert res["budget_s"] == 3.0 and res["parse"] == [{"label": "b", "wall_s": 1.5}] and res["impact"] == []
    assert res["belief"] == [{"date": "d", "p": {"a": 0.5}}]


def test_owner_event_refreshes_running_key(tmp_path: Path, dev: Path) -> None:
    _emit(dev, "start", "job", ref="M0-5", ago=45)
    _emit(dev, "owner", "play", ref="M0-5", ago=2)
    item = _snap(tmp_path, dev)["running"][0]
    assert item["stale"] is False


def test_cached_results_are_copies(tmp_path: Path, dev: Path) -> None:
    path = tmp_path / "docs" / "dev" / "tasks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"id": "T", "title": "t", "status": "done"}]), encoding="utf-8")
    first = _snap(tmp_path, dev)
    first["tasks"]["done"].clear()
    assert len(_snap(tmp_path, dev)["tasks"]["done"]) == 1


def test_same_size_same_mtime_rewrite_seen(tmp_path: Path, dev: Path) -> None:
    _m0(tmp_path, "M0-5", "pass")
    path = tmp_path / "m0_results" / "M0-5.json"
    stamp = path.stat().st_mtime_ns
    assert _gate(_snap(tmp_path, dev), "M0-5")["status"] == "pass"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(path.read_text(encoding="utf-8").replace("pass", "fail"), encoding="utf-8")
    assert tmp.stat().st_size == path.stat().st_size
    os.utime(tmp, ns=(stamp, stamp))
    os.replace(tmp, path)
    assert path.stat().st_mtime_ns == stamp
    assert _gate(_snap(tmp_path, dev), "M0-5")["status"] == "fail"


def test_symlinked_run_dir_outside_rejected(tmp_path: Path, dev: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-linked"
    shutil.copytree(FIXTURES / "run1", outside)
    run = _run_with_results(tmp_path, dev, {"run_dir": "link"})
    (run / "link").symlink_to(outside, target_is_directory=True)
    assert _snap(tmp_path, dev)["results"]["belief"] == []


def test_bus_values_scrubbed(tmp_path: Path, dev: Path) -> None:
    home = str(Path.home())
    run = _run_with_results(tmp_path, dev, {"run_dir": "b"})
    (run / "b").mkdir()
    row = {
        "seq": 1,
        "game_date": f"{home}/d",
        "type": "belief_update",
        "decision_id": "x",
        "payload": {"posterior": {f"{home}/a": 0.5}},
    }
    (run / "b" / "bus.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    res = _snap(tmp_path, dev)["results"]
    assert res["belief"] and home not in json.dumps(res) and "<HOME>" in json.dumps(res)


def test_results_parse_rows_carry_rss_platform_and_source(tmp_path: Path, dev: Path) -> None:
    run = dev / "pipeline" / "20261029-010101"
    run.mkdir(parents=True)
    rows = [
        {"save": "x", "label": "autosave_1.sav", "wall_s": 1.5, "rss_bytes": 123456789,
         "platform": "linux+wsl", "source": "wsl-copy"},
        {"label": "old.sav", "wall_s": 2.0},
        {"label": "bad.sav", "wall_s": 2.0, "rss_bytes": "lots", "platform": 7},
    ]
    (run / "results.json").write_text(json.dumps({"budget_s": 3.0, "parse": rows}), encoding="utf-8")
    parse = _snap(tmp_path, dev)["results"]["parse"]
    assert parse[0] == {"label": "autosave_1.sav", "wall_s": 1.5, "rss_bytes": 123456789,
                        "platform": "linux+wsl", "source": "wsl-copy"}
    assert parse[1] == {"label": "old.sav", "wall_s": 2.0}
    assert parse[2] == {"label": "bad.sav", "wall_s": 2.0}


def test_windows_home_is_scrubbed_in_env_rows_and_results(tmp_path: Path, dev: Path) -> None:
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "env.json").write_text(
        json.dumps({"user_dir": "/mnt/c/Users/Bob Smith/OneDrive/Stellaris", "install": "D:\\Users\\bob\\x"}),
        encoding="utf-8",
    )
    run = dev / "pipeline" / "20261029-010101"
    run.mkdir(parents=True)
    (run / "results.json").write_text(
        json.dumps({"parse": [{"label": "C:/Users/Bob/x.sav", "wall_s": 1.0}]}), encoding="utf-8"
    )
    snap = _snap(tmp_path, dev)
    text = json.dumps(snap["env"]) + json.dumps(snap["results"])
    assert "<WINHOME>" in text
    assert "Bob" not in text and "bob" not in text
