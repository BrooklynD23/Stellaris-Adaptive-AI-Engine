"""Game-test pipeline (task 9): plan, preflight, input copies, stages, owner waits, launch, resume, Ctrl-C.

Every test uses tmp dirs, a fake ``runner`` that records argv, and a fake ``sleep``. Nothing touches the real
``/mnt`` drives, the game, or the user's mod folder.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.sax.saxutils import quoteattr

import pipeline
import pytest
from devdash import events as ev
from gates import Gate, load_gates

from sae.m0 import results as m0_results

NOW = datetime(2024, 1, 1, tzinfo=UTC)
NO_TASKS = "INFO: No tasks are running which match the specified criteria.\r\n"
SAVES = "SAE_GAME_SAVES_DIR"


def row(gid: str, stage: str = "m0", **kw: Any) -> dict[str, Any]:
    """One complete registry row with harmless defaults."""
    base: dict[str, Any] = {
        "id": gid, "stage": stage, "title": f"title {gid}", "runner": {"kind": "none"}, "inputs": [],
        "owner_play": False, "owner_instruction": f"play for {gid}", "judged": False, "question": "",
        "evidence": [], "assumptions": [], "hardware": "one_pc", "windows_only": False, "pending_source": None,
        "priority": "core", "milestone_later": False, "deferred_id": None,
    }
    base.update(kw)
    if base["judged"] and not base["question"]:
        base["question"] = "did it work?"
    return base


def m0_runner(auto: bool = True, args: list[str] | None = None, pre: list[list[str]] | None = None) -> dict[str, Any]:
    return {"kind": "m0", "auto": auto, "pre": pre or [], "args": args or []}


def pytest_gate(gid: str, select: str, **kw: Any) -> dict[str, Any]:
    return row(gid, "x3", runner={"kind": "pytest", "select": select}, deferred_id=gid, inputs=[SAVES], **kw)


def junit_xml(cases: list[tuple[str, str, dict[str, str], str]]) -> str:
    """``(name, deferred_id, extra properties, outcome)`` -> junit text; outcome is pass, fail or ``skip:<msg>``."""
    out = ["<testsuites><testsuite>"]
    for name, gid, props, outcome in cases:
        items = {"deferred_id": gid, **props}
        body = "".join(f"<property name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in items.items())
        extra = ""
        if outcome == "fail":
            extra = '<failure message="over budget"/>'
        elif outcome.startswith("skip:"):
            extra = f"<skipped message={quoteattr(outcome[5:])}/>"
        head = f'<testcase classname="tests.game.t" name="{name}">'
        out.append(f"{head}<properties>{body}</properties>{extra}</testcase>")
    out.append("</testsuite></testsuites>")
    return "".join(out)


class Fake:
    """Scripted runner: records ``(argv, kwargs)`` and imitates the few programs the pipeline calls."""

    def __init__(self, repo: Path, *, reg: str | None = None, tasklist: str = NO_TASKS, uv_rc: int = 0) -> None:
        self.repo, self.reg, self.tasklist, self.uv_rc = repo, reg, tasklist, uv_rc
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.junit: str | None = None
        self.deferred_rc = 0
        self.auto_status = "pass"
        self.check_rc = 0
        self.stage_rc = 0
        self.write_mod = True  # False: ``sae m0 stage`` exits 0 but leaves no sae_bridge.mod
        self.tasklist_after: str | None = None  # returned from the second tasklist call on
        self._tasklist_calls = 0

    def argvs(self) -> list[list[str]]:
        return [a for a, _ in self.calls]

    def deferred_calls(self) -> list[list[str]]:
        return [a for a in self.argvs() if any(x.endswith("run_deferred.py") for x in a) and "--out" in a]

    def __call__(self, argv: list[str], **kw: Any) -> Any:
        self.calls.append((list(argv), kw))
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        head = argv[0]
        if head == "reg.exe":
            if self.reg is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            return SimpleNamespace(returncode=0, stdout=self.reg, stderr="")
        if head == "tasklist.exe":
            self._tasklist_calls += 1
            late = self.tasklist_after is not None and self._tasklist_calls > 1
            return SimpleNamespace(returncode=0, stdout=self.tasklist_after if late else self.tasklist, stderr="")
        if head == "cmd.exe" and argv[2:4] == ["where", "uv"]:
            return SimpleNamespace(returncode=self.uv_rc, stdout="", stderr="")
        if "sae" in argv and "m0" in argv:
            tail = argv[argv.index("m0") + 1 :]
            if "check" in tail:
                cid = tail[tail.index("check") + 1]
                m0_results.save_auto(self.repo / "m0_results", cid, self.auto_status, {}, [])
                return SimpleNamespace(returncode=self.check_rc, stdout="", stderr="")
            if "stage" in tail:
                if self.stage_rc != 0:
                    return SimpleNamespace(returncode=self.stage_rc, stdout="", stderr="boom")
                stage = self.repo / "m0_results" / "stage"
                stage.mkdir(parents=True, exist_ok=True)
                if self.write_mod:
                        (stage / "sae_bridge.mod").write_text('name="x"\npath="mod/sae_bridge"\n', encoding="utf-8")
            return ok
        if any(x.endswith("run_deferred.py") for x in argv) and "--out" in argv:
            out = Path(argv[argv.index("--out") + 1])
            if self.junit is not None:
                (out / "private").mkdir(parents=True, exist_ok=True)
                (out / "private" / "junit.xml").write_text(self.junit, encoding="utf-8")
            return SimpleNamespace(returncode=self.deferred_rc, stdout="", stderr="")
        return ok


class World:
    """A fake ``/mnt`` tree, repo and registry under ``tmp_path``."""

    def __init__(self, tmp: Path, dev: Path) -> None:
        self.tmp, self.dev = tmp, dev
        self.roots = (tmp / "mnt" / "c", tmp / "mnt" / "d", tmp / "mnt" / "e")
        self.user = self.roots[0] / "Users" / "Bob" / "OneDrive" / "Documents" / "Paradox Interactive" / "Stellaris"
        self.install = self.roots[1] / "Steam" / "steamapps" / "common" / "Stellaris"
        self.repo = tmp / "repo"
        self.registry = tmp / "gates.json"
        self.out = tmp / "out"
        self.sleeps: list[float] = []
        self.install.mkdir(parents=True)
        campaign = self.user / "save games" / "camp_1"
        campaign.mkdir(parents=True)
        for i in range(5):
            sav = campaign / f"autosave_{i}.sav"
            sav.write_bytes(b"SAV%d" % i)
            os.utime(sav, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
        (self.user / "logs").mkdir()
        (self.user / "logs" / "game.log").write_text("SAE|v1|x\n", encoding="utf-8")
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "mod" / "sae_bridge" / "common").mkdir(parents=True)
        (self.repo / "mod" / "sae_bridge" / "descriptor.mod").write_text("d1", encoding="utf-8")
        (self.repo / "mod" / "sae_bridge" / "common" / "a.txt").write_text("a", encoding="utf-8")
        for skipped in ("tools", "tests", "__pycache__"):
            (self.repo / "mod" / "sae_bridge" / skipped).mkdir()
            (self.repo / "mod" / "sae_bridge" / skipped / "x.txt").write_text("x", encoding="utf-8")
        (self.repo / "mod" / "sae_bridge" / ".hidden").write_text("h", encoding="utf-8")
        ev.enable(dev)

    def set_registry(self, rows: list[dict[str, Any]]) -> None:
        self.registry.write_text(json.dumps(rows), encoding="utf-8")

    def marker_file(self, ids: list[str]) -> None:
        lines = ["import pytest", ""] + [f'@pytest.mark.game(id="{i}")\ndef test_{n}() -> None: ...\n'
                                          for n, i in enumerate(ids)]
        (self.repo / "tests" / "test_markers.py").write_text("\n".join(lines), encoding="utf-8")

    def run(self, fake: Fake, *argv: str, sleep: Callable[[float], None] | None = None, **kw: Any) -> int:
        def default_sleep(seconds: float) -> None:
            if seconds != pipeline.SETTLE_GAP_S:  # the save-settle gap is not a poll tick
                self.sleeps.append(seconds)

        def custom_sleep(seconds: float) -> None:  # hides settle gaps from the caller's sleep
            if seconds != pipeline.SETTLE_GAP_S and sleep is not None:
                sleep(seconds)

        args = ["--out", str(self.out), *argv] if "--out" not in argv else list(argv)
        return pipeline.main(
            args, runner=fake, sleep=custom_sleep if sleep else default_sleep, now=lambda: NOW, registry=self.registry,
            roots=self.roots, repo=self.repo, environ=kw.pop("environ", {}),
            isatty=kw.pop("isatty", lambda: True), **kw,
        )

    def events(self) -> list[dict[str, Any]]:
        return ev.read_events([ev.events_path(self.dev)])[0]

    def kinds(self, ref: str) -> list[str]:
        return [e["kind"] for e in self.events() if e.get("ref") == ref]

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.out / "state.json").read_text(encoding="utf-8"))
        return data


@pytest.fixture
def world(tmp_path: Path, dev: Path) -> World:
    return World(tmp_path, dev)


@pytest.fixture
def fake(world: World) -> Fake:
    return Fake(world.repo)


def say(world: World, kind: str, ref: str) -> None:
    """What the dashboard's Mark done does: append an event from another writer."""
    ev.emit("dashboard", kind, f"{ref} {kind}", ref=ref, dev=world.dev)


def gates_of(world: World) -> list[Gate]:
    return load_gates(world.registry)


# --- plan / dry run ---------------------------------------------------------------------------------------------


def test_plan_order_and_backlog_dropped(world: World) -> None:
    world.set_registry([
        row("M0-1", priority="later"), row("M0-2", priority="core"), row("M0-3", priority="critical"),
        row("M0-4", priority="stretch"), row("M0-5", priority="backlog"), row("M0-6", priority="critical"),
        row("R-1", "r"), row("X3-G1", "x3"), row("X2-G1", "x2"),
    ])
    gs = gates_of(world)
    ids = [g.id for g in pipeline.plan(gs, set(pipeline.ALL_STAGES), None)]
    assert ids == ["X3-G1", "M0-3", "M0-6", "M0-2", "M0-4", "M0-1", "X2-G1", "R-1"]
    assert [g.id for g in pipeline.plan(gs, {"m0"}, "M0-2")] == ["M0-2", "M0-4", "M0-1"]
    with pytest.raises(ValueError, match="not in the plan"):
        pipeline.plan(gs, {"m0"}, "M0-5")


def test_dry_run_prints_plan_runs_nothing(
    world: World, fake: Fake, capsys: pytest.CaptureFixture[str], dev: Path
) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True), pytest_gate("X3-G1", "test_a")])
    assert world.run(fake, "--dry-run", "--launch") == 0
    text = capsys.readouterr().out
    assert "preflight:" in text and "plan:" in text and "M0-3" in text and "X3-G1" in text
    assert str(world.install) in text
    names = [a[0] for a in fake.argvs()]
    assert set(names) <= {"reg.exe", "tasklist.exe", "cmd.exe"}
    assert not any(a[:3] == ["cmd.exe", "/c", "start"] for a in fake.argvs())
    assert not world.out.exists() and not (world.user / "mod").exists()
    assert (dev / "env.json").is_file()


# --- skip reasons -----------------------------------------------------------------------------------------------


def test_skip_reasons(world: World) -> None:
    pf = {"windows_uv": False}
    rows = [
        row("R-1", "r", hardware="second_machine"), row("X4-G0", "x4", milestone_later=True),
        row("X2-G1", "x2", pending_source="PR #4"), pytest_gate("X3-G4", "test_d", windows_only=True),
        row("R-2", "r"), row("R-3", "r", judged=True), row("M0-1", runner=m0_runner()),
    ]
    world.set_registry(rows)
    by = {g.id: g for g in gates_of(world)}
    assert pipeline.skip_reason(by["R-1"], pf, set()) == "needs other hardware"
    assert pipeline.skip_reason(by["X4-G0"], pf, set()) == "later milestone"
    assert pipeline.skip_reason(by["X2-G1"], pf, set()) == "waiting on PR #4"
    assert pipeline.skip_reason(by["X2-G1"], pf, {"X2-G1"}) == "manual procedure: see bench/README.md"
    reason = pipeline.skip_reason(by["X3-G4"], pf, set())
    assert reason is not None and reason.startswith("setup needed: Windows uv") and "winget install" in reason
    assert pipeline.skip_reason(by["X3-G4"], {"windows_uv": True}, set()) == (
        "setup needed: Windows-side runner (not built)"
    )
    assert pipeline.skip_reason(by["R-2"], pf, set()) == "manual procedure: see bench/README.md"
    assert pipeline.skip_reason(by["R-3"], pf, set()) is None
    assert pipeline.skip_reason(by["M0-1"], pf, set()) is None


def test_skipped_gates_get_start_then_skip(world: World, fake: Fake) -> None:
    world.set_registry([row("R-1", "r", hardware="second_machine"), row("X2-G1", "x2", pending_source="PR #4")])
    assert world.run(fake) == 0
    assert world.kinds("R-1") == ["start", "skip"] and world.kinds("X2-G1") == ["start", "skip"]
    msgs = {e["ref"]: e["msg"] for e in world.events() if e["kind"] == "skip"}
    assert msgs == {"R-1": "needs other hardware", "X2-G1": "waiting on PR #4"}


# --- X3 stage ---------------------------------------------------------------------------------------------------


def test_x3_stage_single_pytest_run(world: World, fake: Fake) -> None:
    world.set_registry([
        row("X3-G0", "x3", runner={"kind": "collect"}), pytest_gate("X3-G1", "test_a"),
        pytest_gate("X3-G2", "test_b"), pytest_gate("X3-G3", "test_c"), pytest_gate("X3-G4", "test_d"),
    ])
    world.marker_file(["X3-G1", "X3-G2", "X3-G3", "X3-G4"])
    fake.junit = junit_xml([
        ("test_a", "X3-G1", {}, "pass"), ("test_b", "X3-G2", {}, "fail"),
        ("test_d", "X3-G4", {}, "skip:game input missing: SAE_GAME_LOG"),
    ])
    assert world.run(fake, "--stages", "x3") == 1
    calls = fake.deferred_calls()
    assert len(calls) == 1
    argv = calls[0]
    assert argv[-2:] == ["-k", "test_a or test_b or test_c or test_d"]
    env = next(kw["env"] for a, kw in fake.calls if a is calls[0])
    saves = Path(env[SAVES])
    assert saves.is_relative_to(world.out / "private" / "inputs") and len(list(saves.glob("*.sav"))) == 5
    assert env["SAE_GAME_LOG"].endswith("game.log") and env["SAE_GAME_MAX_SAVES"] == "13"
    final = {e["ref"]: (e["kind"], e["msg"]) for e in world.events() if e["kind"] in {"pass", "fail", "skip"}}
    assert final["X3-G1"][0] == "pass" and final["X3-G2"] == ("fail", "over budget")
    assert final["X3-G3"] == ("skip", "not collected")
    assert final["X3-G4"] == ("skip", "game input missing: SAE_GAME_LOG")
    assert final["X3-G0"][0] == "pass"
    for gid in ("X3-G1", "X3-G2", "X3-G3", "X3-G0"):
        assert world.kinds(gid)[0] == "start" and len(world.kinds(gid)) == 2
    assert world.state()["gates"]["X3-G1"] == "pass"


def test_x3_collect_fails_without_marker(world: World, fake: Fake) -> None:
    world.set_registry([row("X3-G0", "x3", runner={"kind": "collect"}), pytest_gate("X3-G1", "test_a")])
    world.marker_file([])
    fake.junit = junit_xml([("test_a", "X3-G1", {}, "pass")])
    world.run(fake, "--stages", "x3")
    msg = next(e["msg"] for e in world.events() if e.get("ref") == "X3-G0" and e["kind"] == "fail")
    assert "X3-G1" in msg


def test_x3_save_locked_skips_gates_needing_saves(world: World, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    world.set_registry([pytest_gate("X3-G1", "test_a")])

    def locked(src: Any, dst: Any) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(pipeline.shutil, "copy2", locked)
    world.run(fake, "--stages", "x3")
    assert fake.deferred_calls() == []
    skip = next(e for e in world.events() if e.get("ref") == "X3-G1" and e["kind"] == "skip")
    assert skip["msg"] == "save locked"


def test_results_json_from_parse_costs(world: World, fake: Fake) -> None:
    costs = json.dumps([{"save": "autosave_1.sav", "wall_s": 1.5, "rss_bytes": 100},
                        {"save": "autosave_2.sav", "wall_s": 4.0, "rss_bytes": 200}])
    world.set_registry([pytest_gate("X3-G2", "test_b"), pytest_gate("X3-G9", "test_c")])
    fake.junit = junit_xml([
        ("test_b", "X3-G2", {"parse_costs": costs}, "fail"),
        ("test_c", "X3-G9", {"run_dir": str(world.out / "run1")}, "pass"),
    ])
    world.run(fake, "--stages", "x3")
    data = json.loads((world.out / "results.json").read_text(encoding="utf-8"))
    plat = pipeline.platform_label()
    src = "wsl-copy" if "wsl" in plat else "copy"
    assert data["parse"] == [
        {"label": "autosave_1.sav", "wall_s": 1.5, "rss_bytes": 100, "platform": plat, "source": src},
        {"label": "autosave_2.sav", "wall_s": 4.0, "rss_bytes": 200, "platform": plat, "source": src},
    ]
    assert data["budget_s"] == 3.0 and data["impact"] == [] and data["run_dir"] == str(world.out / "run1")


# --- inputs -----------------------------------------------------------------------------------------------------


def test_copy_inputs_retries_locked(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    real = shutil.copy2
    failures = {"left": 3}

    def flaky(src: Any, dst: Any) -> Any:
        if Path(src).name == "game.log" and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError("OneDrive has it")
        return real(src, dst)

    monkeypatch.setattr(pipeline.shutil, "copy2", flaky)
    sleeps: list[float] = []
    pf = {"user_dir": str(world.user), "parser": None}
    env = pipeline.copy_inputs(pf, world.out, 2, sleep=sleeps.append)
    assert [x for x in sleeps if x != pipeline.SETTLE_GAP_S] == [30, 30, 30]
    assert Path(env["SAE_GAME_LOG"]).is_file()
    names = sorted(p.name for p in Path(env[SAVES]).glob("*.sav"))
    assert names == ["autosave_3.sav", "autosave_4.sav"]  # the newest two
    assert "SAE_PARSER_EXE" not in env and env["SAE_GAME_MAX_SAVES"] == "2"


def test_copy_inputs_leaves_out_a_file_locked_ten_times(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    real = shutil.copy2

    def locked_log(src: Any, dst: Any) -> Any:
        if Path(src).name == "game.log":
            raise OSError(16, "busy")  # EBUSY
        return real(src, dst)

    monkeypatch.setattr(pipeline.shutil, "copy2", locked_log)
    sleeps: list[float] = []
    pf = {"user_dir": str(world.user), "parser": "/x/sae-parse"}
    env = pipeline.copy_inputs(pf, world.out, 13, sleep=sleeps.append)
    assert "SAE_GAME_LOG" not in env and env["SAE_PARSER_EXE"] == "/x/sae-parse"
    locks = [x for x in sleeps if x != pipeline.SETTLE_GAP_S]
    assert len(locks) == 9 and set(locks) == {30}
    assert any("still locked after 10 tries" in e["msg"] for e in world.events() if e["kind"] == "info")


# --- M0 gates and owner waits -----------------------------------------------------------------------------------


def test_m0_order_owner_pre_check_verdict(world: World, fake: Fake) -> None:
    world.set_registry([row(
        "M0-11", runner=m0_runner(args=["--save", "{save}"], pre=[["stage", "M0-2"]]), owner_play=True,
        judged=True, inputs=[SAVES], priority="critical",
    )])
    m0_results.save_manual(world.repo / "m0_results", "M0-11", "fail", "old", {})
    stale = json.loads((world.repo / "m0_results" / "M0-11.json").read_text(encoding="utf-8"))
    stale["manual"]["at"] = "2000-01-01T00:00:00+00:00"
    (world.repo / "m0_results" / "M0-11.json").write_text(json.dumps(stale), encoding="utf-8")
    script = iter([lambda: say(world, "done", "M0-11"),
                   lambda: m0_results.save_manual(world.repo / "m0_results", "M0-11", "pass", "ok", {})])
    seen: list[str] = []

    def sleep(_: float) -> None:
        seen.append("checked" if any("check" in a for a in fake.argvs()) else "waiting")
        next(script)()

    assert world.run(fake, "--stages", "m0", sleep=sleep) == 0
    assert seen == ["waiting", "checked"]
    assert world.kinds("M0-11") == ["start", "owner", "done", "owner", "pass"]
    user = str(world.user)
    pre, check = [a for a in fake.argvs() if "m0" in a and ("stage" in a or "check" in a)]
    assert pre[:6] == ["uv", "run", "sae", "m0", "--user-dir", user] and pre[-2:] == ["stage", "M0-2"]
    assert check[:6] == ["uv", "run", "sae", "m0", "--user-dir", user]
    i = check.index("--save")
    assert check[i - 2 : i] == ["check", "M0-11"]
    saved = Path(check[i + 1])
    assert saved.parent == world.out / "private" / "inputs" / "saves" and saved.name == "autosave_4.sav"
    assert world.state()["gates"]["M0-11"] == "pass"


def test_owner_wait_resumes_on_done_event(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True, priority="critical")])
    say(world, "done", "M0-3")  # a stale Mark done from an earlier run must not resume this wait
    ticks: list[int] = []
    checks_at_sleep: list[bool] = []

    def sleep(_: float) -> None:
        ticks.append(1)
        checks_at_sleep.append(any("check" in a for a in fake.argvs()))
        if len(ticks) == 3:
            say(world, "done", "M0-3")

    assert world.run(fake, "--stages", "m0", sleep=sleep) == 0
    assert len(ticks) == 3
    owner = next(e for e in world.events() if e["kind"] == "owner")
    assert owner["msg"] == "play for M0-3"
    assert checks_at_sleep == [False, False, False]  # no check argv before the wait resolved
    assert any("check" in a for a in fake.argvs())  # ...and it ran afterwards
    assert world.kinds("M0-3")[-1] == "pass"


def test_owner_wait_without_dashboard_uses_enter(world: World, fake: Fake, dev: Path) -> None:
    ev.disable(dev)
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    pressed: list[int] = []
    assert world.run(fake, "--stages", "m0", input_fn=lambda: pressed.append(1) or "") == 0
    assert pressed == [1]


def test_owner_step_without_dashboard_or_tty_skips(world: World, fake: Fake, dev: Path) -> None:
    ev.disable(dev)
    ev.enable(dev)  # events recorded below need the flag; the check is on the flag at wait time
    flag = dev / "enabled"
    flag.unlink()
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])

    def no_input() -> str:
        raise AssertionError("input() must not be called without a terminal")

    assert world.run(fake, "--stages", "m0", isatty=lambda: False, input_fn=no_input) == 0
    assert not any("check" in a for a in fake.argvs())
    assert world.state()["gates"]["M0-3"] == "skip"


def test_enter_fallback_eof_closes_owner_state(world: World, fake: Fake, dev: Path) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    (dev / "enabled").unlink()

    def eof() -> str:
        raise EOFError

    assert world.run(fake, "--stages", "m0", input_fn=eof) == 0
    assert world.state()["gates"]["M0-3"] == "skip"
    assert not any("check" in a for a in fake.argvs())


def test_enter_fallback_ctrl_c(world: World, fake: Fake, dev: Path) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    (dev / "enabled").unlink()

    def interrupt() -> str:
        raise KeyboardInterrupt

    assert world.run(fake, "--stages", "m0", input_fn=interrupt) == 130
    assert world.state()["gates"]["M0-3"] == "interrupted"


def test_resume_skips_passed(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-1", runner=m0_runner()), row("M0-3", runner=m0_runner())])
    assert world.run(fake, "--stages", "m0") == 0
    first = [a for a in fake.argvs() if "check" in a]
    assert len(first) == 2
    fake.calls.clear()
    assert world.run(fake, "--stages", "m0") == 0
    assert [a for a in fake.argvs() if "check" in a] == []
    fake.calls.clear()
    assert world.run(fake, "--stages", "m0", "--rerun") == 0
    assert len([a for a in fake.argvs() if "check" in a]) == 2


def test_failed_gate_is_rerun_on_resume(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-1", runner=m0_runner())])
    fake.auto_status = "fail"
    assert world.run(fake, "--stages", "m0") == 1
    fake.auto_status = "pass"
    assert world.run(fake, "--stages", "m0") == 0
    assert world.state()["gates"]["M0-1"] == "pass"


def test_ctrl_c_emits_interrupted(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])

    def sleep(_: float) -> None:
        raise KeyboardInterrupt

    assert world.run(fake, "--stages", "m0", sleep=sleep) == 130
    events = [e for e in world.events() if e.get("ref") == "M0-3"]
    assert [e["kind"] for e in events] == ["start", "owner", "skip", "done"]
    assert events[2]["msg"] == "interrupted"
    assert world.state()["gates"]["M0-3"] == "interrupted"


def test_pipeline_does_not_touch_priority_or_kill(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-3", runner=m0_runner())])
    world.run(fake, "--stages", "m0")
    for argv in fake.argvs():
        assert argv[0] in {"reg.exe", "tasklist.exe", "cmd.exe", "uv"}
        text = " ".join(argv).lower()
        assert not any(bad in text for bad in ("taskkill", "wmic", "/low", "/high", "/affinity", "priority"))


# --- launch -----------------------------------------------------------------------------------------------------


def test_launch_uses_m0_stage_and_steam(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-2", runner=m0_runner(pre=[["stage", "M0-2"]]), owner_play=True, priority="critical")])
    sleeps = {"n": 0}

    def sleep(_: float) -> None:
        sleeps["n"] += 1
        say(world, "done", "M0-2")

    assert world.run(fake, "--stages", "m0", "--launch", sleep=sleep) == 0
    argvs = fake.argvs()
    user = str(world.user)
    assert ["uv", "run", "sae", "m0", "--user-dir", user, "stage", "M0-2"] in argvs
    start = ["cmd.exe", "/c", "start", "", "steam://rungameid/281990"]
    assert argvs.count(start) == 1
    assert argvs.index(start) > argvs.index(["uv", "run", "sae", "m0", "--user-dir", user, "stage", "M0-2"])
    text = " ".join(" ".join(a) for a in argvs).lower()
    assert not any(bad in text for bad in ("taskkill", "wmic", "start /low", "start /high", "/affinity"))
    staged = world.user / "mod" / "sae_bridge"
    assert (staged / "descriptor.mod").read_text(encoding="utf-8") == "d1"
    assert (staged / "common" / "a.txt").is_file()
    assert not (staged / "tools").exists() and not (staged / "tests").exists()
    assert not (staged / "__pycache__").exists() and not (staged / ".hidden").exists()
    assert 'path="mod/sae_bridge"' in (world.user / "mod" / "sae_bridge.mod").read_text(encoding="utf-8")


def test_no_launch_flag_copies_and_starts_nothing(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    world.run(fake, "--stages", "m0", sleep=lambda _: say(world, "done", "M0-3"))
    assert not any(a[:3] == ["cmd.exe", "/c", "start"] for a in fake.argvs())
    assert not (world.user / "mod").exists()


def test_launch_skipped_when_game_running(world: World) -> None:
    fake = Fake(world.repo, tasklist="stellaris.exe   4242 Console   1   1,000,000 K\r\n")
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    world.run(fake, "--stages", "m0", "--launch", sleep=lambda _: say(world, "done", "M0-3"))
    assert not any(a[:3] == ["cmd.exe", "/c", "start"] for a in fake.argvs())
    assert not (world.user / "mod").exists()


# --- preflight --------------------------------------------------------------------------------------------------


def test_preflight_finds_install(world: World, dev: Path) -> None:
    reg = "\r\nHKEY_CURRENT_USER\\Software\\Valve\\Steam\r\n    SteamPath    REG_SZ    D:/Steam\r\n"
    fake = Fake(world.repo, reg=reg)
    (world.install.parent.parent.parent / "steamapps").mkdir(exist_ok=True)
    pf = pipeline.preflight(fake, roots=world.roots, repo=world.repo, environ={})
    assert pf["install_dir"] == str(world.install)
    assert pf["user_dir"] == str(world.user) and pf["onedrive"] is True
    assert pf["parser"] is None and pf["game_running"] is False and pf["windows_uv"] is True
    assert pf["mod_staged"] is False and pf["mod_stale"] is False
    env = json.loads((dev / "env.json").read_text(encoding="utf-8"))
    assert env["windows_uv"] is True and env["install_dir"] == str(world.install)
    assert {"k", "v", "tone"} <= set(env["env"][0])


def test_preflight_reads_libraryfolders_and_falls_back(world: World) -> None:
    other = world.roots[2] / "Lib" / "steamapps" / "common" / "Stellaris"
    other.mkdir(parents=True)
    shutil.rmtree(world.install)
    vdf = world.roots[1] / "Steam" / "steamapps" / "libraryfolders.vdf"
    vdf.parent.mkdir(parents=True, exist_ok=True)
    vdf.write_text('"libraryfolders"\n{\n "0"\n {\n  "path"\t\t"E:\\\\Lib"\n }\n}\n', encoding="utf-8")
    via_reg = Fake(world.repo, reg="    SteamPath    REG_SZ    d:/steam/../Steam\r\n")
    pf = pipeline.preflight(via_reg, roots=world.roots, repo=world.repo, environ={})
    assert pf["install_dir"] == str(other)
    no_reg = Fake(world.repo, reg=None)  # reg.exe fails: known roots are tried
    pf = pipeline.preflight(no_reg, roots=world.roots, repo=world.repo, environ={})
    assert pf["install_dir"] == str(other)
    shutil.rmtree(other)
    assert pipeline.preflight(no_reg, roots=world.roots, repo=world.repo, environ={})["install_dir"] is None


def test_preflight_fallback_known_root_and_parser(world: World) -> None:
    fake = Fake(world.repo, reg=None, uv_rc=1)
    pf = pipeline.preflight(fake, roots=world.roots, repo=world.repo, environ={"SAE_PARSER_EXE": "/p/sae-parse"})
    assert pf["install_dir"] == str(world.install) and pf["parser"] == "/p/sae-parse"
    assert pf["windows_uv"] is False


def test_preflight_mod_stale(world: World) -> None:
    staged = world.user / "mod"
    shutil.copytree(world.repo / "mod" / "sae_bridge", staged / "sae_bridge")
    (staged / "sae_bridge.mod").write_text("x", encoding="utf-8")
    fake = Fake(world.repo)
    pf = pipeline.preflight(fake, roots=world.roots, repo=world.repo, environ={})
    assert pf["mod_staged"] is True and pf["mod_stale"] is False
    (staged / "sae_bridge" / "descriptor.mod").write_text("changed", encoding="utf-8")
    assert pipeline.preflight(fake, roots=world.roots, repo=world.repo, environ={})["mod_stale"] is True


def test_preflight_no_user_dir(world: World) -> None:
    empty = world.tmp / "nothing"
    pf = pipeline.preflight(Fake(world.repo), roots=(empty / "c", empty / "d"), repo=world.repo, environ={})
    assert pf["user_dir"] is None and pf["install_dir"] is None and pf["onedrive"] is False


def test_tasklist_no_match_is_not_running(world: World) -> None:
    assert pipeline.game_running(Fake(world.repo, tasklist=NO_TASKS)) is False
    assert pipeline.game_running(Fake(world.repo, tasklist="Stellaris.exe  1 Console  1  2 K")) is True

    def boom(argv: list[str], **kw: Any) -> Any:
        raise FileNotFoundError("tasklist.exe")

    assert pipeline.game_running(boom) is False


# --- watchers ---------------------------------------------------------------------------------------------------


def test_watcher_emits_autosave_and_game_events(world: World) -> None:
    fake = Fake(world.repo)
    watcher = pipeline.Watcher(world.user, fake, world.dev, running=False)
    watcher.tick()
    assert [e for e in world.events() if e["source"] == "stellaris"] == []
    newest = world.user / "save games" / "camp_1" / "autosave_9.sav"
    newest.write_bytes(b"new")
    os.utime(newest, (1_800_000_000, 1_800_000_000))
    watcher.tick()
    fake.tasklist = "stellaris.exe 1 Console 1 1 K"
    for _ in range(pipeline.GAME_CHECK_EVERY):
        watcher.tick()
    msgs = [e["msg"] for e in world.events() if e["source"] == "stellaris"]
    assert msgs == ["autosave written: autosave_9.sav", "game started"]
    tasklist_calls = [a for a in fake.argvs() if a[0] == "tasklist.exe"]
    assert len(tasklist_calls) == 1  # only every sixth tick asks the process list
    fake.tasklist = NO_TASKS
    for _ in range(pipeline.GAME_CHECK_EVERY):
        watcher.tick()
    assert [e["msg"] for e in world.events() if e["source"] == "stellaris"][-1] == "game exited"


# --- bench / ironman / usage --------------------------------------------------------------------------------------


def test_ironman_save_from_env_or_new_file(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-14", runner=m0_runner(args=["--save", "{ironman_save}"]))])
    iron = world.tmp / "iron.sav"
    iron.write_bytes(b"i")
    world.run(fake, "--stages", "m0", environ={"SAE_GAME_IRONMAN_SAVE": str(iron)})
    check = next(a for a in fake.argvs() if "check" in a)
    assert check[-1] == str(iron)
    fake.calls.clear()
    shutil.rmtree(world.out)
    world.run(fake, "--stages", "m0")  # none newer than the step start: skip with the missing input
    assert not any("check" in a for a in fake.argvs())
    skip = [e for e in world.events() if e.get("ref") == "M0-14" and e["kind"] == "skip"][-1]
    assert skip["msg"] == "input missing: ironman_save"


def test_bench_gate_runs_sae_bench(world: World, fake: Fake) -> None:
    world.set_registry([row("R-6", "r", runner={"kind": "bench", "args": ["bus", "{run_dir}"]})])
    world.run(fake, "--stages", "r")
    assert [e["msg"] for e in world.events() if e["kind"] == "skip"] == ["input missing: run_dir"]
    world.out.joinpath("results.json").write_text(
        json.dumps({"parse": [], "budget_s": 3.0, "impact": [], "run_dir": "/some/run"}), encoding="utf-8"
    )
    (world.out / "state.json").unlink()
    world.run(fake, "--stages", "r")
    assert ["uv", "run", "sae", "bench", "bus", "/some/run"] in fake.argvs()


def test_bad_stage_is_usage_error(world: World, fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    world.set_registry([])
    assert world.run(fake, "--stages", "x9") == 2
    assert "unknown stage" in capsys.readouterr().err


def test_from_without_out_resumes_newest_run(world: World, fake: Fake, dev: Path) -> None:
    world.set_registry([row("M0-1", runner=m0_runner()), row("M0-3", runner=m0_runner())])
    older, newer = dev / "pipeline" / "20240101-000000", dev / "pipeline" / "20240102-000000"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    rc = pipeline.main(
        ["--stages", "m0", "--from", "M0-3"], runner=fake, sleep=lambda _: None, now=lambda: NOW,
        registry=world.registry, roots=world.roots, repo=world.repo, environ={},
    )
    assert rc == 0 and (newer / "state.json").is_file() and not (older / "state.json").exists()
    assert list((newer / "state.json").parent.glob("state.json"))
    assert json.loads((newer / "state.json").read_text(encoding="utf-8"))["gates"] == {"M0-3": "pass"}


def test_true_case_restores_on_disk_spelling(world: World) -> None:
    wrong = world.roots[1] / "steam" / "STEAMAPPS" / "common" / "Stellaris"
    assert pipeline._true_case(wrong, world.roots) == world.install
    assert pipeline._true_case(world.tmp / "elsewhere", world.roots) == world.tmp / "elsewhere"


# --- fix round 1 ------------------------------------------------------------------------------------------------


def test_windows_only_gate_never_runs_under_wsl(world: World) -> None:
    world.set_registry([pytest_gate("X3-G4", "test_d", windows_only=True)])
    for uv_rc, want in ((1, "setup needed: Windows uv"), (0, "setup needed: Windows-side runner (not built)")):
        fake = Fake(world.repo, uv_rc=uv_rc)
        fake.junit = junit_xml([("test_d", "X3-G4", {}, "pass")])
        shutil.rmtree(world.out, ignore_errors=True)
        world.run(fake, "--stages", "x3")
        assert fake.deferred_calls() == []
        skip = [e for e in world.events() if e.get("ref") == "X3-G4" and e["kind"] == "skip"][-1]
        assert skip["msg"].startswith(want)


def test_stale_junit_is_not_used(world: World, fake: Fake) -> None:
    world.set_registry([pytest_gate("X3-G1", "test_a")])
    stale = world.out / "x3" / "private" / "junit.xml"
    stale.parent.mkdir(parents=True)
    stale.write_text(junit_xml([("test_a", "X3-G1", {}, "pass")]), encoding="utf-8")
    fake.junit = None  # run_deferred dies before writing a new junit
    fake.deferred_rc = 3
    assert world.run(fake, "--stages", "x3") == 1
    assert not stale.exists()
    final = [e for e in world.events() if e.get("ref") == "X3-G1" and e["kind"] in {"pass", "fail", "skip"}]
    assert [e["kind"] for e in final] == ["fail"] and "no junit" in final[0]["msg"]


def test_copy_inputs_prunes_to_selection_across_campaigns(world: World) -> None:
    other = world.user / "save games" / "camp_0"
    other.mkdir()
    old = other / "autosave_old.sav"
    old.write_bytes(b"o")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    saves = world.out / "private" / "inputs" / "saves"
    saves.mkdir(parents=True)
    (saves / "autosave_old.sav").write_bytes(b"stale from another campaign")
    (saves / "autosave_0.sav").write_bytes(b"too old for max_saves=2")
    env = pipeline.copy_inputs({"user_dir": str(world.user), "parser": None}, world.out, 2, sleep=lambda _: None)
    assert sorted(p.name for p in Path(env[SAVES]).glob("*.sav")) == ["autosave_3.sav", "autosave_4.sav"]


def test_copy_inputs_ignores_vanished_save(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path.stat

    def flaky(self: Path, *a: Any, **k: Any) -> Any:
        if self.name == "autosave_2.sav" and self.parent.name == "camp_1":
            raise FileNotFoundError(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "stat", flaky)
    env = pipeline.copy_inputs({"user_dir": str(world.user), "parser": None}, world.out, 13, sleep=lambda _: None)
    names = sorted(p.name for p in Path(env[SAVES]).glob("*.sav"))
    assert "autosave_2.sav" not in names and "autosave_4.sav" in names


def launch_world(world: World, fake: Fake) -> int:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    return world.run(fake, "--stages", "m0", "--launch", sleep=lambda _: say(world, "done", "M0-3"))


def started(fake: Fake) -> bool:
    return any(a[:3] == ["cmd.exe", "/c", "start"] for a in fake.argvs())


def test_launch_removes_extra_dest_files_and_flags_stale(world: World, fake: Fake) -> None:
    dest = world.user / "mod" / "sae_bridge"
    skip = shutil.ignore_patterns("tools", "tests", ".*", "__pycache__")
    shutil.copytree(world.repo / "mod" / "sae_bridge", dest, ignore=skip)
    (dest / "common" / "leftover.txt").write_text("old", encoding="utf-8")
    (dest / "gone").mkdir()
    (dest / "gone" / "old.txt").write_text("old", encoding="utf-8")
    (world.user / "mod" / "sae_bridge.mod").write_text("x", encoding="utf-8")
    pf = pipeline.preflight(Fake(world.repo), roots=world.roots, repo=world.repo, environ={})
    assert pf["mod_stale"] is True  # an extra file makes the copy stale
    launch_world(world, fake)
    assert not (dest / "common" / "leftover.txt").exists() and not (dest / "gone").exists()
    assert (dest / "common" / "a.txt").is_file() and started(fake)
    pf = pipeline.preflight(Fake(world.repo), roots=world.roots, repo=world.repo, environ={})
    assert pf["mod_stale"] is False


def test_launch_refused_when_game_starts_before_copy(world: World) -> None:
    fake = Fake(world.repo)
    fake.tasklist_after = "stellaris.exe 1 Console 1 1 K"
    launch_world(world, fake)
    assert not started(fake) and not (world.user / "mod").exists()
    assert not any("stage" in a for a in fake.argvs())


def test_launch_aborts_when_stage_fails(world: World, fake: Fake) -> None:
    fake.stage_rc = 1
    launch_world(world, fake)
    assert not started(fake) and not (world.user / "mod" / "sae_bridge").exists()


def test_launch_refuses_destination_outside_user_mod(world: World, fake: Fake) -> None:
    elsewhere = world.tmp / "elsewhere"
    elsewhere.mkdir()
    (world.user / "mod").symlink_to(elsewhere, target_is_directory=True)
    launch_world(world, fake)
    assert not started(fake) and list(elsewhere.iterdir()) == []
    inside_repo = world.repo / "somewhere"
    inside_repo.mkdir()
    assert pipeline._safe_dest(inside_repo / "mod" / "sae_bridge", inside_repo, world.repo) is False
    fresh_user = world.tmp / "fresh_user"
    (fresh_user / "mod").mkdir(parents=True)
    assert pipeline._safe_dest(fresh_user / "mod" / "sae_bridge", fresh_user, world.repo) is True


def test_mod_files_skip_symlinks(world: World) -> None:
    source = world.repo / "mod" / "sae_bridge"
    (source / "link.txt").symlink_to(source / "descriptor.mod")
    names = {p.name for p in pipeline._mod_files(source)}
    assert "link.txt" not in names and "descriptor.mod" in names


def test_ironman_only_computed_when_used(world: World, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Any, step_start: float) -> str:
        raise AssertionError("find_ironman must not run for a template without {ironman_save}")

    monkeypatch.setattr(pipeline._Run, "find_ironman", boom)
    world.set_registry([row("M0-11", runner=m0_runner(args=["--save", "{save}"]), inputs=[SAVES])])
    assert world.run(fake, "--stages", "m0") == 0


def test_failed_pre_step_fails_gate(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-2", runner=m0_runner(pre=[["stage", "M0-2"]]))])
    fake.stage_rc = 2
    assert world.run(fake, "--stages", "m0") == 1
    msg = next(e["msg"] for e in world.events() if e.get("ref") == "M0-2" and e["kind"] == "fail")
    assert msg == "pre step failed: sae m0 stage"
    assert not any("check" in a for a in fake.argvs())


# --- fix round 2: confinement and silent verdict wait -------------------------------------------------------------


def _mods(world: World) -> Path:
    mods = world.user / "mod"
    mods.mkdir(exist_ok=True)
    return mods


def test_launch_refuses_dest_symlinked_to_mod_dir(world: World, fake: Fake) -> None:
    mods = _mods(world)
    (mods / "other_mod").mkdir()
    (mods / "other_mod" / "f.txt").write_text("keep", encoding="utf-8")
    (mods / "sae_bridge").symlink_to(mods, target_is_directory=True)
    launch_world(world, fake)
    assert not started(fake)
    assert (mods / "other_mod" / "f.txt").read_text(encoding="utf-8") == "keep"
    assert not any("stage" in a for a in fake.argvs())


def test_launch_refuses_dest_symlinked_to_another_mod(world: World, fake: Fake) -> None:
    mods = _mods(world)
    other = mods / "other_mod"
    other.mkdir()
    (other / "f.txt").write_text("keep", encoding="utf-8")
    (mods / "sae_bridge").symlink_to(other, target_is_directory=True)
    launch_world(world, fake)
    assert not started(fake)
    assert sorted(p.name for p in other.iterdir()) == ["f.txt"]


def test_launch_unlinks_symlinked_subdir_inside_dest(world: World, fake: Fake) -> None:
    outside = world.tmp / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("keep", encoding="utf-8")
    dest = _mods(world) / "sae_bridge"
    dest.mkdir()
    (dest / "common").symlink_to(outside, target_is_directory=True)  # a wanted folder replaced by a link
    (dest / "evil").symlink_to(outside, target_is_directory=True)
    launch_world(world, fake)
    assert started(fake)
    assert not (dest / "common").is_symlink() and (dest / "common" / "a.txt").is_file()
    assert not (dest / "evil").exists()
    assert sorted(p.name for p in outside.iterdir()) == ["x.txt"]
    assert (outside / "x.txt").read_text(encoding="utf-8") == "keep"


def test_launch_replaces_symlinked_dest_file(world: World, fake: Fake) -> None:
    outside = world.tmp / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    dest = _mods(world) / "sae_bridge"
    dest.mkdir()
    (dest / "descriptor.mod").symlink_to(outside)
    launch_world(world, fake)
    assert started(fake)
    assert not (dest / "descriptor.mod").is_symlink()
    assert (dest / "descriptor.mod").read_text(encoding="utf-8") == "d1"
    assert outside.read_text(encoding="utf-8") == "keep"


def test_launch_refuses_symlinked_mod_file(world: World, fake: Fake) -> None:
    outside = world.tmp / "target.mod"
    outside.write_text("keep", encoding="utf-8")
    mods = _mods(world)
    (mods / "sae_bridge.mod").symlink_to(outside)
    launch_world(world, fake)
    assert not started(fake) and outside.read_text(encoding="utf-8") == "keep"
    assert not (mods / "sae_bridge").exists()  # nothing was copied


def test_verdict_instruction_reaches_stdout_with_dashboard_off(
    world: World, fake: Fake, dev: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (dev / "enabled").unlink()
    world.set_registry([row("M0-1", runner=m0_runner(), judged=True)])

    def sleep(_: float) -> None:
        m0_results.save_manual(world.repo / "m0_results", "M0-1", "pass", "ok", {})

    assert world.run(fake, "--stages", "m0", sleep=sleep) == 0
    out = capsys.readouterr().out
    assert "uv run sae m0 record M0-1 pass|fail --value KEY=VALUE" in out


# --- fix round 3 ------------------------------------------------------------------------------------------------


def test_launch_replaces_hardlinked_dest_file(world: World, fake: Fake) -> None:
    outside = world.tmp / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    dest = _mods(world) / "sae_bridge"
    (dest / "common").mkdir(parents=True)
    os.link(outside, dest / "common" / "a.txt")
    launch_world(world, fake)
    assert started(fake)
    assert outside.read_text(encoding="utf-8") == "keep"
    assert (dest / "common" / "a.txt").read_text(encoding="utf-8") == "a"
    assert not list(dest.rglob(".sae-tmp-*"))


def test_launch_replaces_hardlinked_mod_file(world: World, fake: Fake) -> None:
    outside = world.tmp / "target.mod"
    outside.write_text("keep", encoding="utf-8")
    mods = _mods(world)
    os.link(outside, mods / "sae_bridge.mod")
    launch_world(world, fake)
    assert started(fake) and outside.read_text(encoding="utf-8") == "keep"
    assert 'path="mod/sae_bridge"' in (mods / "sae_bridge.mod").read_text(encoding="utf-8")


def test_fifo_in_mod_trees_does_not_hang(world: World) -> None:
    source = world.repo / "mod" / "sae_bridge"
    os.mkfifo(source / "pipe_in_source")
    staged = _mods(world) / "sae_bridge"
    skip = shutil.ignore_patterns("pipe_in_source", "tools", "tests", ".*", "__pycache__")
    shutil.copytree(source, staged, ignore=skip)
    os.mkfifo(staged / "pipe_in_dest")
    (world.user / "mod" / "sae_bridge.mod").write_text("x", encoding="utf-8")
    assert "pipe_in_source" not in {p.name for p in pipeline._mod_files(source)}
    pf = pipeline.preflight(Fake(world.repo), roots=world.roots, repo=world.repo, environ={})
    assert pf["mod_staged"] is True and pf["mod_stale"] is False


def test_launch_refuses_wrong_type_destinations(world: World, fake: Fake) -> None:
    mods = _mods(world)
    (mods / "sae_bridge").write_text("i am a file", encoding="utf-8")  # should be a folder
    launch_world(world, fake)
    assert not started(fake) and (mods / "sae_bridge").read_text(encoding="utf-8") == "i am a file"
    (mods / "sae_bridge").unlink()
    (mods / "sae_bridge.mod").mkdir()  # should be a regular file
    fake.calls.clear()
    shutil.rmtree(world.out)
    launch_world(world, fake)
    assert not started(fake) and not (mods / "sae_bridge").exists()
    assert pipeline._safe_dest(mods / "sae_bridge.mod", world.user, world.repo, file=True) is False


def test_launch_aborts_when_mod_file_write_fails(world: World, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    real = pipeline._write_atomic

    def fail_on_mod(src: Path, target: Path) -> None:
        if target.name == "sae_bridge.mod":
            raise PermissionError("locked")
        real(src, target)

    monkeypatch.setattr(pipeline, "_write_atomic", fail_on_mod)
    launch_world(world, fake)
    assert not started(fake)


def test_judged_only_site_prints_instruction(
    world: World, fake: Fake, dev: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (dev / "enabled").unlink()
    world.set_registry([row("R-3", "r", judged=True)])

    def sleep(_: float) -> None:
        m0_results.save_manual(world.repo / "m0_results", "R-3", "pass", "ok", {})

    assert world.run(fake, "--stages", "r", sleep=sleep) == 0
    assert "uv run sae m0 record R-3 pass|fail --value KEY=VALUE" in capsys.readouterr().out


# --- game stand-down (final-fix B) ------------------------------------------------------------------------------

RUNNING = "stellaris.exe   4242 Console   1   1,000,000 K\r\n"


def x3_world(world: World, fake: Fake) -> None:
    world.set_registry([pytest_gate("X3-G1", "test_a")])
    fake.junit = junit_xml([("test_a", "X3-G1", {}, "pass")])


def test_parse_stage_waits_for_the_game_to_close(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    ticks: list[int] = []

    def sleep(_: float) -> None:
        ticks.append(1)
        assert not fake.deferred_calls() and not (world.out / "private").exists()  # nothing ran or was copied yet
        if len(ticks) == 2:
            say(world, "done", "game-close")
            fake.tasklist = ""  # the owner closed the game before clicking

    assert world.run(fake, "--stages", "x3", sleep=sleep) == 0
    owner = next(e for e in world.events() if e["kind"] == "owner")
    assert owner["ref"] == "game-close"
    assert owner["msg"] == "close Stellaris to run X3 parse gates, or rerun with --while-game"
    assert len(fake.deferred_calls()) == 1 and world.state()["gates"]["X3-G1"] == "pass"


def test_parse_stage_while_game_flag_skips_the_wait(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    assert world.run(fake, "--stages", "x3", "--while-game") == 0
    assert not any(e["kind"] == "owner" for e in world.events())
    assert len(fake.deferred_calls()) == 1
    assert world.sleeps == []


def test_parse_stage_game_closed_does_not_ask(world: World, fake: Fake) -> None:
    x3_world(world, fake)
    assert world.run(fake, "--stages", "x3") == 0
    assert not any(e["kind"] == "owner" for e in world.events())


def test_parse_stage_game_running_without_dashboard_or_tty_skips(world: World, dev: Path) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    (dev / "enabled").unlink()

    def no_input() -> str:
        raise AssertionError("input() must not be called without a terminal")

    assert world.run(fake, "--stages", "x3", isatty=lambda: False, input_fn=no_input) == 0
    assert world.state()["gates"]["X3-G1"] == "skip"
    assert not fake.deferred_calls() and not (world.out / "private").exists()


def test_parse_stage_game_running_enter_fallback(world: World, dev: Path) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    (dev / "enabled").unlink()
    pressed: list[int] = []
    assert world.run(fake, "--stages", "x3", input_fn=lambda: pressed.append(1) or "") == 0
    assert pressed == [1] and len(fake.deferred_calls()) == 1


def test_game_stand_down_never_touches_the_process(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    world.run(fake, "--stages", "x3", "--while-game")
    world.run(fake, "--stages", "x3", sleep=lambda _: say(world, "done", "game-close"), **{"environ": {}})
    text = " ".join(" ".join(a) for a in fake.argvs()).lower()
    assert not any(bad in text for bad in ("taskkill", "wmic", "/affinity", "renice", "setpriority"))


# --- platform and source labels --------------------------------------------------------------------------------


def test_platform_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline.sys, "platform", "linux")
    wsl = tmp_path / "version"
    wsl.write_text("Linux version 6.6.114.1-microsoft-standard-WSL2", encoding="utf-8")
    assert pipeline.platform_label(wsl) == "linux+wsl"
    wsl.write_text("Linux version 6.8 generic", encoding="utf-8")
    assert pipeline.platform_label(wsl) == "linux"
    assert pipeline.platform_label(tmp_path / "missing") == "linux"


def test_parse_rows_say_wsl_copy_when_run_under_wsl(world: World, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "platform_label", lambda *a: "linux+wsl")
    costs = json.dumps([{"save": "a.sav", "wall_s": 1.0, "rss_bytes": 5}])
    world.set_registry([pytest_gate("X3-G2", "test_b")])
    fake.junit = junit_xml([("test_b", "X3-G2", {"parse_costs": costs}, "pass")])
    world.run(fake, "--stages", "x3")
    data = json.loads((world.out / "results.json").read_text(encoding="utf-8"))
    assert data["parse"][0]["platform"] == "linux+wsl" and data["parse"][0]["source"] == "wsl-copy"


# --- run retention ---------------------------------------------------------------------------------------------


def make_run(dev: Path, name: str) -> Path:
    run = dev / "pipeline" / name
    for sub in ("private/inputs/saves", "x3/private/basetemp"):
        (run / sub).mkdir(parents=True)
        (run / sub / "raw.sav").write_bytes(b"raw")
    (run / "state.json").write_text("{}", encoding="utf-8")
    os.utime(run / "state.json", (1_700_000_000, 1_700_000_000))  # long finished, not in flight
    return run


def test_prune_runs_keeps_newest_n_and_only_private(dev: Path) -> None:
    runs = [make_run(dev, f"2026010{i}-000000") for i in range(1, 7)]
    removed = pipeline.prune_runs(dev, 3)
    assert len(removed) == 6  # private and x3/private of the three oldest
    for old in runs[:3]:
        assert not (old / "private").exists() and not (old / "x3" / "private").exists()
        assert (old / "state.json").is_file() and (old / "x3").is_dir()
    for new in runs[3:]:
        assert (new / "private" / "inputs" / "saves" / "raw.sav").is_file()


def test_prune_runs_always_keeps_the_current_run(dev: Path) -> None:
    runs = [make_run(dev, f"2026010{i}-000000") for i in range(1, 5)]
    pipeline.prune_runs(dev, 1, current=runs[0])
    assert (runs[0] / "private").is_dir() and (runs[3] / "private").is_dir()
    assert not (runs[1] / "private").exists() and not (runs[2] / "private").exists()


def test_prune_runs_never_follows_symlinks_or_leaves_the_pipeline_folder(dev: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "private").mkdir(parents=True)
    (outside / "private" / "keep.txt").write_text("k", encoding="utf-8")
    base = dev / "pipeline"
    base.mkdir(parents=True)
    (base / "20260101-000000").symlink_to(outside, target_is_directory=True)  # a run folder that is a symlink
    linked = make_run(dev, "20260102-000000")
    shutil.rmtree(linked / "private")
    (linked / "private").symlink_to(outside / "private", target_is_directory=True)  # a private that is a symlink
    stray = base / "notes"  # not a run the pipeline created
    (stray / "private").mkdir(parents=True)
    for i in range(3, 7):
        make_run(dev, f"2026010{i}-000000")
    pipeline.prune_runs(dev, 3)
    assert (outside / "private" / "keep.txt").is_file()
    assert (linked / "private").is_symlink()
    assert (stray / "private").is_dir()
    assert not (base / "20260103-000000" / "private").exists()


def test_main_prunes_older_runs_and_validates_flag(world: World, fake: Fake, dev: Path) -> None:
    old = [make_run(dev, f"2025010{i}-000000") for i in range(1, 6)]
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=False)])
    current = str(dev / "pipeline" / "20260101-000000")
    assert world.run(fake, "--stages", "m0", "--keep-runs", "2", "--out", current) == 0
    assert not (old[0] / "private").exists() and not (old[2] / "private").exists()
    assert not (old[3] / "private").exists()  # the current run (newest name) plus one older run are kept
    assert (old[4] / "private").is_dir()
    assert world.run(fake, "--stages", "m0", "--keep-runs", "0") == 2


# --- settle ----------------------------------------------------------------------------------------------------


def test_copy_waits_until_a_save_is_stable(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    clock = {"t": 1_700_000_400.0 + 5.0}  # 5 s after the newest save was written
    os.utime(newest, (1_700_000_400, 1_700_000_400))
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    pf = {"user_dir": str(world.user), "parser": None}
    env = pipeline.copy_inputs(pf, world.out, 1, sleep=sleep, clock=lambda: clock["t"])
    assert sleeps == [11.0, pipeline.SETTLE_GAP_S]  # 15 - 5 + 1, then the second stat
    assert [p.name for p in Path(env[SAVES]).glob("*.sav")] == ["autosave_4.sav"]


def test_copy_leaves_out_a_save_that_keeps_changing(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    os.utime(newest, (1_700_000_400, 1_700_000_400))
    pf = {"user_dir": str(world.user), "parser": None}
    env = pipeline.copy_inputs(pf, world.out, 1, sleep=lambda _: None, clock=lambda: 1_700_000_401.0)
    assert SAVES not in env
    infos = [e["msg"] for e in world.events() if e["kind"] == "info"]
    assert any("autosave_4.sav was still changing" in m for m in infos)


# --- Windows home scrubbing and the launch check ---------------------------------------------------------------


def test_dry_run_scrubs_windows_profile_paths(world: World, fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    world.set_registry([row("M0-3", runner=m0_runner(), owner_play=True)])
    assert world.run(fake, "--dry-run") == 0
    text = capsys.readouterr().out
    assert "<WINHOME>" in text and "Bob" not in text


def test_env_json_display_rows_scrub_windows_profile(world: World, fake: Fake, dev: Path, tmp_path: Path) -> None:
    from devdash import state

    pipeline.preflight(fake, roots=world.roots, repo=world.repo, environ={})
    snap = state.snapshot(world.repo, dev, now=NOW)
    shown = json.dumps(snap["env"])
    assert "<WINHOME>" in shown and "Bob" not in shown


def test_launch_aborts_when_staged_mod_file_is_missing(world: World, fake: Fake) -> None:
    fake.write_mod = False
    assert launch_world(world, fake) == 0
    assert not started(fake)
    assert not (world.user / "mod" / "sae_bridge.mod").exists()
    infos = [e["msg"] for e in world.events() if e["kind"] == "info"]
    assert any("sae_bridge.mod is missing" in m for m in infos)


def test_prune_runs_resolves_the_current_run(dev: Path) -> None:
    runs = [make_run(dev, f"2026010{i}-000000") for i in range(1, 5)]
    pipeline.prune_runs(dev, 1, current=runs[0] / "x3" / "..")
    assert (runs[0] / "private").is_dir()
    older = make_run(dev, "20251231-000000")
    link = dev / "pipeline" / "latest"
    link.symlink_to(older, target_is_directory=True)
    pipeline.prune_runs(dev, 1, current=link)
    assert (older / "private").is_dir()


def test_prune_runs_ignores_a_symlinked_pipeline_folder(dev: Path, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    (real / "20260101-000000" / "private").mkdir(parents=True)
    (real / "20260102-000000" / "private").mkdir(parents=True)
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "pipeline").symlink_to(real, target_is_directory=True)
    assert pipeline.prune_runs(dev, 1) == []
    assert (real / "20260101-000000" / "private").is_dir()


def test_prune_runs_keeps_a_run_in_flight(dev: Path) -> None:
    runs = [make_run(dev, f"2026010{i}-000000") for i in range(1, 5)]
    os.utime(runs[0] / "state.json", (1_700_000_000 + 3000, 1_700_000_000 + 3000))  # written 50 min before "now"
    assert [p for p in pipeline.prune_runs(dev, 1, now=1_700_000_000 + 6000) if p.parent == runs[0]] == []
    assert (runs[0] / "private").is_dir() and not (runs[1] / "private").exists()
    pipeline.prune_runs(dev, 1, now=1_700_000_000 + 3000 + 3601)
    assert not (runs[0] / "private").exists()


def test_future_mtime_is_not_trusted_but_unchanged_after_a_wait_settles(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    os.utime(newest, (1_700_000_500, 1_700_000_500))
    sleeps: list[float] = []
    pf = {"user_dir": str(world.user), "parser": None}
    env = pipeline.copy_inputs(pf, world.out, 1, sleep=sleeps.append, clock=lambda: 1_700_000_000.0)
    assert sleeps == [pipeline.SETTLE_S]  # slept once, then saw size and mtime unchanged
    assert [p.name for p in Path(env[SAVES]).glob("*.sav")] == ["autosave_4.sav"]


def test_future_mtime_that_changes_is_left_out(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    os.utime(newest, (1_700_000_500, 1_700_000_500))
    stamp = {"t": 1_700_000_500}

    def sleep(_: float) -> None:
        stamp["t"] += 1
        os.utime(newest, (stamp["t"], stamp["t"]))  # the game keeps writing

    pf = {"user_dir": str(world.user), "parser": None}
    env = pipeline.copy_inputs(pf, world.out, 1, sleep=sleep, clock=lambda: 1_700_000_000.0)
    assert SAVES not in env


def test_settle_checks_once_more_after_the_last_sleep(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    os.utime(newest, (1_700_000_400, 1_700_000_400))
    clock = {"t": 1_700_000_401.0}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == pipeline.SETTLE_TRIES:
            clock["t"] += 100  # old enough only after the final sleep

    ok = pipeline._settled(newest, lambda: clock["t"], sleep, lambda _: None)
    assert ok and sleeps == [15.0] * pipeline.SETTLE_TRIES + [pipeline.SETTLE_GAP_S]


def test_stand_down_ends_when_the_game_closes_without_a_click(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    ticks: list[int] = []

    def sleep(_: float) -> None:
        ticks.append(1)
        if len(ticks) == 2:
            fake.tasklist = ""  # closed, but the next check is GAME_CHECK_EVERY polls in

    assert world.run(fake, "--stages", "x3", sleep=sleep) == 0
    assert len(ticks) == pipeline.GAME_CHECK_EVERY - 1  # no faster than the game-check interval
    assert len(fake.deferred_calls()) == 1 and world.state()["gates"]["X3-G1"] == "pass"
    closes = [e for e in world.events() if e.get("ref") == "game-close"]
    assert [e["kind"] for e in closes] == ["owner", "done"] and closes[-1]["msg"] == "Stellaris closed"


def test_stand_down_skips_when_mark_done_but_the_game_still_runs(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    world.run(fake, "--stages", "x3", sleep=lambda _: say(world, "done", "game-close"))
    assert not fake.deferred_calls() and not (world.out / "private").exists()
    assert world.state()["gates"]["X3-G1"] == "skip"
    assert any("Stellaris is running" in e["msg"] for e in world.events() if e["kind"] == "info")


def test_stand_down_never_taskkills_while_waiting_for_close(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    n = {"t": 0}

    def sleep(_: float) -> None:
        n["t"] += 1
        if n["t"] == 3 * pipeline.GAME_CHECK_EVERY:
            fake.tasklist = ""

    world.run(fake, "--stages", "x3", sleep=sleep)
    assert not any(a[0].startswith("taskkill") for a in fake.argvs())


def test_old_looking_save_that_changes_between_two_stats_is_left_out(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    stamp = time.time() + 500  # the newest save
    os.utime(newest, (stamp, stamp))  # the clock below says it is 100 s old
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        newest.write_bytes(newest.read_bytes() + b"x")  # the game is mid-write (WSL clock ahead of Windows)
        os.utime(newest, (stamp, stamp))

    pf = {"user_dir": str(world.user), "parser": None}
    env = pipeline.copy_inputs(pf, world.out, 1, sleep=sleep, clock=lambda: stamp + 100)
    assert SAVES not in env and sleeps[0] == pipeline.SETTLE_GAP_S


def test_old_save_needs_a_second_equal_stat(world: World) -> None:
    newest = world.user / "save games" / "camp_1" / "autosave_4.sav"
    os.utime(newest, (1_700_000_000, 1_700_000_000))
    sleeps: list[float] = []
    assert pipeline._settled(newest, lambda: 1_700_000_100.0, sleeps.append, lambda _: None)
    assert sleeps == [pipeline.SETTLE_GAP_S]


def test_ironman_copy_waits_for_the_save_to_settle(world: World, fake: Fake) -> None:
    world.set_registry([row("M0-14", runner=m0_runner(args=["--save", "{ironman_save}"]))])
    iron = world.user / "save games" / "camp_1" / "ironman.sav"
    future = time.time() + 1000
    iron.write_bytes(b"i")
    os.utime(iron, (future, future))
    seen: list[float] = []

    def sleep(seconds: float) -> None:
        seen.append(seconds)
        iron.write_bytes(iron.read_bytes() + b"i")  # still being written
        os.utime(iron, (future, future))

    world.run(fake, "--stages", "m0", sleep=sleep)
    assert not any("check" in a for a in fake.argvs()) and pipeline.SETTLE_S in seen
    assert not (world.out / "private" / "inputs" / "ironman" / "ironman.sav").exists()


def test_game_is_rechecked_at_every_stand_down(world: World) -> None:
    fake = Fake(world.repo, tasklist=RUNNING)
    x3_world(world, fake)
    run_calls = {"n": 0}

    def sleep(_: float) -> None:
        run_calls["n"] += 1
        say(world, "done", "game-close")

    world.run(fake, "--stages", "x3", sleep=sleep)
    first = len([a for a in fake.argvs() if a[0] == "tasklist.exe"])
    assert first >= 2  # preflight plus the stand-down; nothing is cached for later gates


def test_newest_campaign_prefers_autosave_folders(tmp_path: Path) -> None:
    """A newer manual repro save in its own folder must not displace the campaign with autosaves."""
    root = tmp_path / "save games"
    camp, repro = root / "campaign", root / "repro"
    camp.mkdir(parents=True)
    repro.mkdir()
    (camp / "autosave_2300.01.01.sav").write_bytes(b"x")
    (repro / "repro.sav").write_bytes(b"x")
    os.utime(camp / "autosave_2300.01.01.sav", (1000, 1000))
    os.utime(repro / "repro.sav", (2000, 2000))
    assert pipeline.newest_campaign(tmp_path) == repro
    assert pipeline.newest_campaign(tmp_path, prefer_autosaves=True) == camp
    (camp / "autosave_2300.01.01.sav").unlink()
    (camp / "manual.sav").write_bytes(b"x")
    os.utime(camp / "manual.sav", (500, 500))
    assert pipeline.newest_campaign(tmp_path, prefer_autosaves=True) == repro
