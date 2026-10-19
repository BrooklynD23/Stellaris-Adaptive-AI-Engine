"""CI-5: parser memory-cap behaviour. (a) every fixture parses with cost; (b) a pathological save breaches a cap."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from e2e import _bus
from e2e._synth import record, replay
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseResult

FIXTURES = sorted((Path(__file__).resolve().parents[2] / "parser" / "fixtures").glob("*/save.sav"))
PATHOLOGICAL = Path(__file__).resolve().parents[2] / "parser" / "fixtures" / "pathological_2x" / "save.sav"
CAP = 128 * 1024 * 1024
# UNVERIFIED (A-18 class): RLIMIT_AS abort exit code and the systemd-scope kill code are Linux/WSL behaviour; M0-11
# would repeat this on the target machine. Windows memory-cap evidence is bench R-5.
_DETAIL_OK = ("timeout", "exit=134", "exit=None", "exit=-")


@pytest.mark.parametrize("save", FIXTURES, ids=lambda p: p.parent.name)
def test_every_fixture_parses_with_cost(parser_exe: Path, save: Path) -> None:
    assert len(FIXTURES) >= 9
    p = subprocess.run([str(parser_exe), "parse", "--save", str(save)], capture_output=True, text=True,
                       timeout=120, check=False)
    assert p.returncode == 0, p.stderr[-300:]
    result = PARSE_OUTPUT_ADAPTER.validate_json(p.stdout)
    assert isinstance(result, ParseResult)
    assert result.cost.wall_s >= 0 and result.cost.cpu_s >= 0 and result.cost.rss_peak_bytes > 0


@pytest.mark.skipif(sys.platform == "win32", reason="R-5 covers Windows")
def test_pathological_save_breaches_budget_and_daemon_survives(tmp_path: Path, parser_exe: Path) -> None:
    session = record(tmp_path / "s", parser_exe, "turtle", 2, 1)
    # the 3rd-quarter point of game.log: well inside the recording, so later normal saves follow it
    offset = (session / "game.log").stat().st_size // 2
    shutil.copy(PATHOLOGICAL, session / "saves" / f"{offset:012d}-pathological.sav")
    sets = [f"governor.worker_mem_cap_bytes={CAP}", f"governor.worker_mem_cap_closed_bytes={CAP}"]
    r = replay(session, tmp_path / "r", parser_exe, sets=sets)
    assert r.code == 0, r.stderr[-500:]
    assert r.run_dir is not None
    events = _bus.load(r.run_dir)
    breaches = [e for e in events if e["type"] == "health" and e["payload"]["kind"] == "budget_breach"]
    assert len(breaches) == 1, [b["payload"] for b in breaches]
    detail = breaches[0]["payload"]["detail"]
    print(f"CI-5 budget_breach detail: {detail!r}")
    assert any(detail.startswith(d) for d in _DETAIL_OK), detail
    at = breaches[0]["seq"]
    assert [e for e in events if e["type"] == "observation" and e["seq"] > at], "no observations after the breach"
    later = [e for e in events if e["type"] == "observation" and e["payload"]["source"] == "save" and e["seq"] > at]
    assert later, "a later normal save was not parsed"
    assert events[-1]["type"] == "health" and events[-1]["payload"]["kind"] == "daemon_exit"

    # control: the same session without the cap parses the pathological save, so the cap caused the kill
    c = replay(session, tmp_path / "c", parser_exe)
    assert c.code == 0, c.stderr[-500:]
    assert c.run_dir is not None
    uncapped = _bus.load(c.run_dir)
    assert not [e for e in uncapped if e["type"] == "health" and e["payload"]["kind"] == "budget_breach"]
    # the fixture is dated 2231.04.01, ahead of the recording, so a completed parse shows as save_skipped/save_ahead
    skipped = [e for e in uncapped if e["type"] == "health" and e["payload"]["kind"] == "save_skipped"]
    assert [e["payload"]["detail"] for e in skipped] == ["save_ahead"], "the pathological save was not parsed uncapped"
