"""sae telemetry CLI: T0, missing extra, usage/config codes, default .rrd path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import sae.telemetry.cli as cli_mod
import sae.telemetry.stream as stream_mod
from sae.telemetry.cli import main as _main

from .test_telemetry_mapping import doc_lines


@pytest.fixture(autouse=True)
def _no_rss_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pytest process (duckdb, rerun, hypothesis loaded) is far above the 150 MiB telemetry cap."""
    monkeypatch.setattr(stream_mod, "MEM_LIMIT", 1 << 40)
    # limit_process_memory sets env vars and prctl/mallopt: keep them out of the pytest process.
    monkeypatch.setattr(cli_mod, "limit_process_memory", lambda: None)


RUN = "20261019-031500-synth-0a1b"


def main(command: str, argv: list[str]) -> int:
    """cli.main with a recording exit_hard (the real one is os._exit)."""
    codes: list[int] = []
    rc = _main(command, argv, exit_hard=codes.append)
    assert codes in ([], [rc])
    return rc


def setup_run(tmp_path: Path) -> list[str]:
    d = tmp_path / RUN
    d.mkdir()
    (d / "bus.jsonl").write_text("".join(ln + "\n" for ln in doc_lines()), encoding="utf-8")
    return ["--run", RUN, "--runs-dir", str(tmp_path)]


def test_t0_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = setup_run(tmp_path)
    assert main("telemetry", [*args, "--set", "telemetry.tier=T0"]) == 0
    assert "tier T0: bus only, nothing to stream" in capsys.readouterr().out


def test_missing_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    args = setup_run(tmp_path)
    monkeypatch.setitem(sys.modules, "rerun", None)
    assert main("telemetry", args) == 1
    assert "uv sync --extra telemetry" in capsys.readouterr().err


def test_usage_and_config_errors(tmp_path: Path) -> None:
    args = setup_run(tmp_path)
    assert main("telemetry", []) == 2
    assert main("telemetry", ["--run", "bad", "--runs-dir", str(tmp_path)]) == 2
    assert main("telemetry", [*args, "--rrd", "a", "--rerun-url", "b"]) == 2
    assert main("telemetry", [*args, "--set", "telemetry.tier=T9"]) == 3
    assert main("telemetry", [*args, "--set", "oops"]) == 3


def test_default_rrd_path(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    args = setup_run(tmp_path)
    assert main("telemetry", args) == 0
    assert (tmp_path / RUN / "telemetry.rrd").stat().st_size > 0


def test_explicit_rrd(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    args = setup_run(tmp_path)
    out = tmp_path / "x.rrd"
    assert main("telemetry", [*args, "--rrd", str(out)]) == 0
    assert out.stat().st_size > 0


def test_no_bus(tmp_path: Path) -> None:
    assert main("telemetry", ["--run", RUN, "--runs-dir", str(tmp_path)]) == 1


def test_set_conflict_is_config_error(tmp_path: Path) -> None:
    args = setup_run(tmp_path)
    assert main("telemetry", [*args, "--set", "a=1", "--set", "a.b=2"]) == 3


def test_bad_targets_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("rerun")
    args = setup_run(tmp_path)
    assert main("telemetry", [*args, "--rerun-url", "not a url"]) == 1
    assert "cannot open not a url" in capsys.readouterr().err
    bad = str(tmp_path / "nodir" / "x.rrd")
    assert main("telemetry", [*args, "--rrd", bad]) == 1
    assert f"cannot open {bad}" in capsys.readouterr().err


def test_enabled_false_note(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("rerun")
    args = setup_run(tmp_path)
    assert main("telemetry", args) == 0
    assert "telemetry.enabled is false" in capsys.readouterr().err


def test_hard_exit_called_once_with_code(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    args = setup_run(tmp_path)
    codes: list[int] = []
    assert _main("telemetry", args, exit_hard=codes.append) == 0
    assert codes == [0]


def test_stream_error_not_mislabelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("rerun")
    import sae.telemetry.cli as cli

    def boom(*a: object, **k: object) -> int:
        raise RuntimeError("grpc down")

    monkeypatch.setattr(cli, "run_stream", boom)
    assert main("telemetry", setup_run(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "stream failed: grpc down" in err and "cannot open" not in err


def test_empty_current_grace_is_live(tmp_path: Path) -> None:
    from sae.control.runlive import is_run_live

    cur = tmp_path / "CURRENT"
    cur.write_text("")
    assert is_run_live(tmp_path, RUN)
    old = cur.stat().st_mtime
    assert not is_run_live(tmp_path, RUN, now=lambda: old + 6)
    cur.write_text(f"{RUN} 4242")
    assert is_run_live(tmp_path, RUN, pid_exists=lambda p: p == 4242)
    assert not is_run_live(tmp_path, "other", pid_exists=lambda p: True)
    assert not is_run_live(tmp_path / "none", RUN)


def test_keyboard_interrupt_exits_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("rerun")
    import sae.telemetry.cli as cli

    def stop(*a: object, **k: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_stream", stop)
    codes: list[int] = []
    assert _main("telemetry", setup_run(tmp_path), exit_hard=codes.append) == 130
    assert codes == [130]


def test_whitespace_current_is_not_empty(tmp_path: Path) -> None:
    from sae.control.runlive import is_run_live

    (tmp_path / "CURRENT").write_text("  \n")
    assert not is_run_live(tmp_path, RUN)
