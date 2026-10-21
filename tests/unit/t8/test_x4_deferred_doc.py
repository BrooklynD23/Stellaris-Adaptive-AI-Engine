"""Guards docs/handoff/deferred/X4.md against stale X4-E text and missing env vars."""
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs/handoff/deferred/X4.md"
GATE = Path(__file__).resolve().parents[1] / "t7/test_gate_game.py"


def test_no_stale_pending_text() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert GATE.exists()
    for stale in ("pending X4-E", "PENDING X4-E", "not in the tree yet"):
        assert stale not in text


def test_runs_dir_documented_and_skip_reason_prefix() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "SAE_GAME_RUNS_DIR" in GATE.read_text(encoding="utf-8")
    assert "SAE_GAME_RUNS_DIR" in text
    assert 'starts with "set SAE_GAME_SAE_DIR (X4-G"' in text
