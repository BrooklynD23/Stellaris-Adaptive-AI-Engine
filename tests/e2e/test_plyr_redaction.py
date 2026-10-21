"""Proposal 012: malformed PLYR lines never leak their bytes into the fog-legal bus."""

from __future__ import annotations

import json
from pathlib import Path

from e2e import _bus
from e2e._synth import record, replay

SENTINEL = "9876501234"
GOOD = f"SAE|v1|PLYR|2200.05.01|sid=1|fleet={SENTINEL}|navcap_used=1|navcap=2|wars=0|name=Human"
MALFORMED = (
    f"SAE|v1|PLYR|fleet={SENTINEL}|garbage",
    f"SAE|v1|PLYRfleet={SENTINEL}",
    f"SAE|v1| PLYR|fleet={SENTINEL}",
)


def test_malformed_plyr_is_redacted_and_good_one_reaches_god_bus(tmp_path: Path, parser_exe: Path) -> None:
    session = record(tmp_path / "s", parser_exe, "turtle", 1, 1)
    extra = "".join(f"{ln}\n" for ln in (GOOD, *MALFORMED))
    with (session / "game.log").open("ab") as f:  # appended after the last save offset: saves stay aligned
        f.write(extra.encode())
    size = (session / "game.log").stat().st_size
    with (session / "game.log.ts").open("a", encoding="utf-8") as f:
        f.write(f"{size} 99000000000\n")
    assert SENTINEL in (session / "game.log").read_text(encoding="utf-8")  # the leak was really offered
    r = replay(session, tmp_path / "r", parser_exe)
    assert r.code == 0, r.stderr[-500:]
    assert r.run_dir is not None
    events = _bus.load(r.run_dir)
    assert events
    for e in events:
        assert SENTINEL not in json.dumps(e["payload"]), e["type"]
    errors = [e for e in events if e["type"] == "health" and e["payload"]["kind"] == "log_parse_error"]
    assert len(errors) >= 1, "malformed lines were not reported at all (vacuous)"
    for e in errors:  # only the redacted prefix may remain
        assert "fleet=" not in (e["payload"].get("detail") or "")
    god = _bus.load(r.run_dir, god=True)
    assert any(
        e["type"] == "observation_god" and e["payload"]["plyr"].get("fleet") == float(SENTINEL) for e in god
    ), "well-formed PLYR value missing from bus.god.jsonl (vacuous)"
