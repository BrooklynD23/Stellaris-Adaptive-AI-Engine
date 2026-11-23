"""Gate index: junit and m0_results folding, newest-wins merge, atomic index file, run_deferred hook."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from devdash import gateindex
from devdash.gateindex import GateResult
from hypothesis import given
from hypothesis import strategies as st

REPO = Path(__file__).resolve().parents[3]
JUNIT = """<testsuites><testsuite>
<testcase classname="t.g" name="a"><properties><property name="deferred_id" value="X3-G1"/>
<property name="assumption" value="A-20"/></properties></testcase>
<testcase classname="t.g" name="b1"><properties><property name="deferred_id" value="X3-G2"/></properties></testcase>
<testcase classname="t.g" name="b2"><properties><property name="deferred_id" value="X3-G2"/></properties>
<failure message="boom">trace</failure></testcase>
<testcase classname="t.g" name="c"><properties><property name="deferred_id" value="X3-G4"/></properties>
<skipped message="game input missing: SAE_GAME_LOG"/></testcase>
<testcase classname="t.g" name="d"><skipped message="no id"/></testcase>
</testsuite></testsuites>"""
NOW = "2026-10-29T10:00:00+00:00"


def _junit(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


def _res(status: str, at: str, run: str = "r") -> GateResult:
    return GateResult(status=status, evidence={}, source="junit", run=run, at=at)


def _m0(tmp_path: Path) -> Path:
    folder = tmp_path / "m0_results"
    folder.mkdir()
    (folder / "M0-5.json").write_text(json.dumps({
        "check": "M0-5",
        "auto": {"status": "fail", "evidence": {"x": 1}, "notes": [], "at": "2026-10-01T00:00:00+00:00"},
        "manual": {"status": "pass", "note": "ok", "values": {"build": "4.0"}, "at": "2026-10-02T00:00:00+00:00"},
    }), encoding="utf-8")
    (folder / "M0-3.json").write_text(json.dumps({
        "check": "M0-3", "auto": {"status": "fail", "evidence": {"n": 3}, "notes": [], "at": "2026-10-03T00:00:00Z"},
    }), encoding="utf-8")
    (folder / "env.json").write_text('{"game_version": "1"}', encoding="utf-8")
    (folder / "M0-6.stage.json").write_text('{"at": "x"}', encoding="utf-8")
    return folder


def test_junit_statuses(tmp_path: Path) -> None:
    got = gateindex.from_junit(_junit(tmp_path), "run1", NOW)
    assert set(got) == {"X3-G1", "X3-G2", "X3-G4"}
    assert got["X3-G1"]["status"] == "pass" and got["X3-G1"]["evidence"] == {"assumption": "A-20"}
    assert got["X3-G2"]["status"] == "fail" and got["X3-G2"]["evidence"]["message"] == "boom"
    assert got["X3-G4"]["status"] == "skip" and "SAE_GAME_LOG" in got["X3-G4"]["evidence"]["message"]
    assert got["X3-G1"]["run"] == "run1" and got["X3-G1"]["at"] == NOW and got["X3-G1"]["source"] == "junit"


def test_junit_message_capped(tmp_path: Path) -> None:
    path = tmp_path / "j.xml"
    path.write_text(
        '<testsuite><testcase name="a"><properties><property name="deferred_id" value="G"/></properties>'
        f'<skipped message="{"y" * 900}"/></testcase></testsuite>', encoding="utf-8")
    assert len(gateindex.from_junit(path, "r", NOW)["G"]["evidence"]["message"]) == 500


def test_junit_scrub_home(tmp_path: Path) -> None:
    home = str(Path.home())
    path = tmp_path / "j.xml"
    path.write_text(
        '<testsuite><testcase name="a"><properties><property name="deferred_id" value="G"/></properties>'
        f'<failure message="bad {home}/secret/save.sav"/></testcase></testsuite>', encoding="utf-8")
    plain = gateindex.from_junit(path, "r", NOW)
    assert home in plain["G"]["evidence"]["message"]
    got = gateindex.from_junit(path, home + "/out", NOW, scrub=gateindex.default_scrub)
    assert "<HOME>/secret/save.sav" in got["G"]["evidence"]["message"] and home not in json.dumps(got)


def test_m0_scrub_home(tmp_path: Path) -> None:
    folder = _m0(tmp_path)
    (folder / "M0-2.json").write_text(
        json.dumps({"manual": {"status": "pass", "note": f"{Path.home()}/x", "values": {}}}), encoding="utf-8"
    )
    assert "<HOME>/x" in gateindex.from_m0(folder, scrub=gateindex.default_scrub)["M0-2"]["evidence"]["note"]


def test_m0_statuses(tmp_path: Path) -> None:
    got = gateindex.from_m0(_m0(tmp_path))
    assert set(got) == {"M0-5", "M0-3"}
    assert got["M0-5"]["status"] == "pass" and got["M0-5"]["at"] == "2026-10-02T00:00:00+00:00"
    assert got["M0-5"]["evidence"] == {"build": "4.0", "note": "ok"} and got["M0-5"]["source"] == "m0"
    assert got["M0-3"]["status"] == "fail" and got["M0-3"]["evidence"] == {"n": "3"}


def test_m0_missing_or_corrupt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gateindex.from_m0(tmp_path / "nope") == {}
    folder = _m0(tmp_path)
    (folder / "M0-9.json").write_text("{not json", encoding="utf-8")
    (folder / "M0-8.json").write_text('{"manual": "pass"}', encoding="utf-8")
    (folder / "M0-7.json").write_text('{"auto": {"evidence": {}}}', encoding="utf-8")
    (folder / "M0-4.json").write_text('{"auto": {"status": "weird", "at": 5}}', encoding="utf-8")
    got = gateindex.from_m0(folder)
    assert not {"M0-9", "M0-8", "M0-7"} & set(got)
    assert got["M0-4"]["status"] == "pending" and got["M0-4"]["at"] == ""
    assert capsys.readouterr().err.count("gateindex: skipping") == 3


def test_merge_newest_keeps_newer() -> None:
    old = {"G": _res("fail", "2026-10-01T00:00:00Z", "old")}
    new = {"G": _res("pass", "2026-10-02T00:00:00Z", "new"), "H": _res("pass", NOW)}
    merged = gateindex.merge_newest(old, new)
    assert merged["G"]["run"] == "new" and "H" in merged
    assert gateindex.merge_newest(new, old)["G"]["run"] == "new"


def test_merge_compares_datetimes_not_strings() -> None:
    # "…+02:00" at 09:00 local is 07:00Z, older than 08:00Z although it sorts later as a string.
    a = _res("pass", "2026-10-01T08:00:00+00:00", "a")
    b = _res("fail", "2026-10-01T09:00:00+02:00", "b")
    assert gateindex.merge_newest({"G": a}, {"G": b})["G"]["run"] == "a"


def test_newer_skip_keeps_older_pass() -> None:
    old = {"G": _res("pass", "2026-10-01T00:00:00Z", "old"), "F": _res("fail", "2026-10-01T00:00:00Z", "oldf")}
    new = {"G": _res("skip", "2026-10-09T00:00:00Z"), "F": _res("skip", "2026-10-09T00:00:00Z")}
    merged = gateindex.merge_newest(old, new)
    assert merged["G"]["run"] == "old" and merged["F"]["run"] == "oldf"
    # and a real result replaces a skip even when the skip is the newer one
    assert gateindex.merge_newest(new, old)["G"]["status"] == "pass"


@given(st.sampled_from(["pass", "fail", "skip"]), st.sampled_from(["pass", "fail", "skip"]),
       st.integers(1, 28), st.integers(1, 28))
def test_merge_never_loses_real_result(s_old: str, s_new: str, d_old: int, d_new: int) -> None:
    old = {"G": _res(s_old, f"2026-10-{d_old:02d}T00:00:00Z", "old")}
    new = {"G": _res(s_new, f"2026-10-{d_new:02d}T00:00:00Z", "new")}
    for merged in (gateindex.merge_newest(old, new), gateindex.merge_newest(new, old)):
        if "pass" in (s_old, s_new) or "fail" in (s_old, s_new):
            assert merged["G"]["status"] != "skip"


def test_update_index_persists(tmp_path: Path) -> None:
    dev = tmp_path / "dev"
    gateindex.update_index(dev, {"G": _res("pass", "2026-10-01T00:00:00Z")})
    gateindex.update_index(dev, {"G": _res("skip", "2026-10-02T00:00:00Z"), "H": _res("fail", NOW)})
    data = json.loads((dev / "gates.json").read_text(encoding="utf-8"))
    assert data["G"]["status"] == "pass" and data["H"]["status"] == "fail"
    assert gateindex.load_index(dev) == data
    assert [p.name for p in dev.iterdir()] == ["gates.json"]


def test_load_index_tolerates_garbage(tmp_path: Path) -> None:
    (tmp_path / "gates.json").write_text("[1,2", encoding="utf-8")
    assert gateindex.load_index(tmp_path) == {}
    (tmp_path / "gates.json").write_text('{"a": 1, "b": {"status": "pass", "evidence": {}, "source": "s", '
                                         '"run": "r", "at": "t"}}', encoding="utf-8")
    assert list(gateindex.load_index(tmp_path)) == ["b"]


def _load_rd() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_deferred_idx", REPO / "tools" / "game" / "run_deferred.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Runner:
    def __call__(self, cmd: list[str], **kw: Any) -> Any:
        if "--junitxml" in cmd:
            Path(cmd[cmd.index("--junitxml") + 1]).write_text(JUNIT, encoding="utf-8")
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="")


def test_run_deferred_updates_index(tmp_path: Path, dev: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    dev.mkdir(parents=True)
    (dev / "enabled").write_text("", encoding="utf-8")
    assert _load_rd().main(["--out", str(out)], runner=_Runner()) == 1
    index = json.loads((dev / "gates.json").read_text(encoding="utf-8"))
    assert index["X3-G1"]["status"] == "pass" and index["X3-G1"]["run"] == str(out)
    assert index["X3-G2"]["status"] == "fail"
    assert capsys.readouterr().err == ""


def test_run_deferred_index_failure_harmless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "enabled").write_text("", encoding="utf-8")
    (dev / "gates.json").mkdir()  # a directory where the index file belongs, so the atomic replace fails
    monkeypatch.setenv("SAE_DEVDASH_DIR", str(dev))
    assert _load_rd().main(["--out", str(tmp_path / "out")], runner=_Runner()) == 1
    err = capsys.readouterr().err
    assert err.count("run_deferred: gate index not updated:") == 1


def test_default_scrub_covers_windows_profile() -> None:
    from devdash.gateindex import default_scrub

    out = default_scrub("/mnt/c/Users/Some One/Documents and C:\\Users\\x\\y")
    assert "Some One" not in out and "Users\\x" not in out
    assert out.count("<WINHOME>") == 2
