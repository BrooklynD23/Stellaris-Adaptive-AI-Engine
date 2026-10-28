"""run_deferred collector: junit summary, redaction, argv, index, exit codes (game-free)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
JUNIT = """<testsuites><testsuite>
<testcase classname="tests.game.test_saves" name="test_a">
<properties><property name="deferred_id" value="X3-G1"/></properties></testcase>
<testcase classname="tests.game.test_saves" name="test_b">
<properties><property name="deferred_id" value="X3-G2"/></properties>
<failure message="boom at {log} and {home}/x | pipe">trace</failure></testcase>
<testcase classname="tests.game.test_log" name="test_c"><skipped message="game input missing: SAE_GAME_LOG"/></testcase>
<testcase classname="tests.game.test_log" name="test_d"><error message="{long}"/></testcase>
</testsuite></testsuites>"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_deferred", REPO / "tools" / "game" / "run_deferred.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Runner:
    def __init__(self, junit: str | None, code: int = 0) -> None:
        self.junit, self.code, self.calls = junit, code, []  # type: ignore[var-annotated]

    def __call__(self, cmd: list[str], **kw: Any) -> Any:
        self.calls.append((cmd, kw))
        if "--junitxml" in cmd:
            if self.junit is not None:
                Path(cmd[cmd.index("--junitxml") + 1]).write_text(self.junit, encoding="utf-8")
            return SimpleNamespace(returncode=self.code, stdout="")
        return SimpleNamespace(returncode=0, stdout="sae-parse 9.9\n")


@pytest.fixture
def mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    m = _load()
    monkeypatch.setattr(m, "REPO", tmp_path / "repo")
    for k in list(__import__("os").environ):
        if k.startswith("SAE_GAME_") or k == "SAE_PARSER_EXE":
            monkeypatch.delenv(k)
    return m


def _junit(tmp_path: Path) -> str:
    return JUNIT.format(log="/secret/dir/game.log", home=Path.home(), long="x" * 500)


def test_summary_redaction_and_argv(mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAE_GAME_LOG", "/secret/dir/game.log")
    monkeypatch.setenv("SAE_GAME_EXPECT_ACK", "1")
    monkeypatch.setenv("SAE_GAME_MAX_SAVES", "24")
    monkeypatch.setenv("SAE_PARSER_EXE", "/opt/sae-parse")
    out = tmp_path / "out"
    r = Runner(_junit(tmp_path), code=1)
    assert mod.main(["--out", str(out), "--", "-k", "foo"], runner=r) == 1
    cmd = r.calls[0][0]
    assert cmd[:4] == [sys.executable, "-m", "pytest", "-m"] and "game" in cmd
    assert cmd[cmd.index("--basetemp") + 1] == str(out / "private" / "tmp")
    assert cmd[-2:] == ["-k", "foo"] and "-rs" in cmd and "no:cacheprovider" in cmd
    assert (out / "private" / "tmp").is_dir()
    s = (out / "summary.md").read_text(encoding="utf-8")
    assert "pass 1 / fail 1 / skip 1 / error 1" in s
    assert "X3-G1" in s and "X3-G2" in s and "| skip |" in s
    assert "/secret/dir/game.log" not in s and "<SAE_GAME_LOG>" in s
    assert str(Path.home()) not in s and "<HOME>" in s
    assert "x" * 201 not in s and "\\|" in s
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["sae_game_vars_set"] == ["SAE_GAME_EXPECT_ACK", "SAE_GAME_LOG", "SAE_GAME_MAX_SAVES"]
    assert inputs["sae_parse_version"] == "sae-parse 9.9"
    assert inputs["platform"] == sys.platform
    assert "secret" not in json.dumps(inputs) and "/opt" not in json.dumps(inputs)


def test_short_flags_not_redacted(mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAE_GAME_EXPECT_ACK", "1")
    monkeypatch.setenv("SAE_GAME_MAX_SAVES", "24")
    out = tmp_path / "out"
    mod.main(["--out", str(out)], runner=Runner(_junit(tmp_path)))
    s = (out / "summary.md").read_text(encoding="utf-8")
    assert "X3-G1" in s and "pass 1 / fail 1 / skip 1 / error 1" in s
    assert "<SAE_GAME_EXPECT_ACK>" not in s and "<SAE_GAME_MAX_SAVES>" not in s


def test_missing_junit_passes_code_through(mod: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert mod.main(["--out", str(out)], runner=Runner(None, code=4)) == 4
    assert "junit file missing" in (out / "summary.md").read_text(encoding="utf-8")
    assert json.loads((out / "inputs.json").read_text(encoding="utf-8"))["sae_parse_version"] is None


def test_list_uses_glob_index(mod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "repo" / "docs" / "handoff" / "deferred"
    d.mkdir(parents=True)
    x2 = "# T\n## X2-M0-3 thing\n### X2-A.1 sub\nnot a heading\n#### lower-case\n"
    (d / "X2.md").write_text(x2, encoding="utf-8")
    (d / "X4.md").write_text("## X4-L2\n", encoding="utf-8")
    assert mod.main(["--list"]) == 0
    o = capsys.readouterr().out
    assert "X2.md: X2-M0-3, X2-A.1" in o and "X4.md: X4-L2" in o


def test_list_empty_and_out_required(mod: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert mod.main(["--list"]) == 0
    assert "no docs/handoff" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        mod.main([])
    assert e.value.code == 2


def test_parser_version_unavailable(mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAE_PARSER_EXE", "/no/such")

    def runner(cmd: list[str], **kw: Any) -> Any:
        if "--junitxml" in cmd:
            return SimpleNamespace(returncode=0, stdout="")
        raise FileNotFoundError

    out = tmp_path / "o"
    assert mod.main(["--out", str(out)], runner=runner) == 0
    assert "unavailable" in (out / "inputs.json").read_text(encoding="utf-8")


def test_windows_path_forms_redacted(mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = r"C:\Users\u\game.log"
    home = r"C:\Users\u"
    monkeypatch.setenv("SAE_GAME_LOG", log)
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: home))
    msg = f"a {log} b {log.replace(chr(92), '/')} c {log.replace(chr(92), chr(92) * 2)} d {home.replace(chr(92), '/')}"
    case = f'<testcase classname="t" name="n"><failure message="{msg}"/></testcase>'
    xml = f"<testsuites><testsuite>{case}</testsuite></testsuites>"
    out = tmp_path / "out"
    mod.main(["--out", str(out)], runner=Runner(xml, code=1))
    s = (out / "summary.md").read_text(encoding="utf-8")
    assert "Users" not in s and "game.log" not in s
