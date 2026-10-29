"""Tests for the ``dd.py`` command line: ``emit``, ``enable`` and ``disable``, run through ``devdash.cli.main``."""

from __future__ import annotations

import shutil
import statistics
import subprocess
import time
from pathlib import Path

import pytest
from devdash import REPO, cli, events

_DD = REPO / "tools" / "devdash" / "dd.py"


def test_cli_emit(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--kind", "start", "--ref", "X3-G5", "hello"]) == 0
    got, invalid = events.read_events([events.events_path(dev)])
    assert invalid == 0
    assert len(got) == 1
    assert got[0]["ref"] == "X3-G5"
    assert got[0]["kind"] == "start"
    assert got[0]["source"] == "orchestrator"
    assert got[0]["msg"] == "hello"
    assert "pct" not in got[0]


def test_cli_emit_source_and_pct(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--source", "ci", "--kind", "progress", "--pct", "100", "half done"]) == 0
    got, _ = events.read_events([events.events_path(dev)])
    assert got[0]["source"] == "ci"
    assert got[0]["pct"] == 100


def test_cli_emit_rejects_pct_out_of_range(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--pct", "101", "hello"]) == 2
    assert not events.events_path(dev).exists()


def test_cli_emit_rejects_non_integer_pct(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--pct", "half", "hello"]) == 2
    assert not events.events_path(dev).exists()


def test_cli_emit_rejects_unknown_kind(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--kind", "bogus", "hello"]) == 2
    assert not events.events_path(dev).exists()


def test_cli_emit_rejects_blank_source(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--source", "  ", "hello"]) == 2
    assert not events.events_path(dev).exists()


def test_cli_emit_message_may_start_with_a_dash(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert cli.main(["emit", "--", "-x leading dash"]) == 0
    got, _ = events.read_events([events.events_path(dev)])
    assert got[0]["msg"] == "-x leading dash"


def test_cli_emit_when_disabled_is_a_quiet_no_op(dev: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["emit", "hello"]) == 0
    assert not events.events_path(dev).exists()
    assert "not enabled" in capsys.readouterr().err


def test_cli_emit_write_failure_exits_one(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    events.events_path(dev).mkdir()  # a directory where the log file should be
    assert cli.main(["emit", "hello"]) == 1


def test_cli_enable_and_disable_roundtrip(dev: Path) -> None:
    assert cli.main(["enable"]) == 0
    assert (dev / "enabled").is_file()
    assert cli.main(["disable"]) == 0
    assert not (dev / "enabled").exists()


def test_cli_without_a_command_is_a_usage_error() -> None:
    assert cli.main([]) == 2


def test_cli_help_exits_zero() -> None:
    assert cli.main(["--help"]) == 0


_PYTHON3 = shutil.which("python3")


def _run_dd(dev: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``dd.py`` under the system ``python3 -I`` from outside the repo, as a hook would."""
    assert _PYTHON3 is not None
    return subprocess.run(
        [_PYTHON3, "-I", str(_DD), *args],
        capture_output=True,
        text=True,
        cwd=dev.parent,
        check=False,
        env={"SAE_DEVDASH_DIR": str(dev), "PATH": ""},
    )


def _median_seconds(dev: Path, *args: str, runs: int = 5) -> float:
    """Median wall time of ``runs`` invocations of ``dd.py``."""
    times: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        done = _run_dd(dev, *args)
        times.append(time.perf_counter() - start)
        assert done.returncode == 0, done.stderr
    return statistics.median(times)


@pytest.mark.skipif(_PYTHON3 is None, reason="no system python3")
def test_dd_py_runs_under_bare_python3(dev: Path) -> None:
    """The hook path: the system interpreter with no venv, run from another directory."""
    enable = _run_dd(dev, "enable")
    assert enable.returncode == 0, enable.stderr
    emit = _run_dd(dev, "emit", "--kind", "done", "--ref", "T1", "ok")
    assert emit.returncode == 0, emit.stderr
    got, invalid = events.read_events([events.events_path(dev)])
    assert invalid == 0
    assert [event["ref"] for event in got] == ["T1"]


@pytest.mark.skipif(_PYTHON3 is None, reason="no system python3")
def test_disabled_noop_is_fast(dev: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A disabled hook call must stay inside the 100 ms hook budget (limit 150 ms, median of 5)."""
    # Disabled and bare-interpreter runs are interleaved so load hits both alike. On a saturated machine even a
    # bare interpreter exceeds 150 ms, so a bounded overhead (150 ms) over the bare run is accepted too.
    assert _PYTHON3 is not None
    disabled_times: list[float] = []
    bare_times: list[float] = []
    for _ in range(5):
        start = time.perf_counter()
        subprocess.run([_PYTHON3, "-I", "-c", "pass"], capture_output=True, check=False, env={"PATH": ""})
        bare_times.append(time.perf_counter() - start)
        start = time.perf_counter()
        assert _run_dd(dev, "emit", "x").returncode == 0
        disabled_times.append(time.perf_counter() - start)
    disabled = statistics.median(disabled_times)
    overhead = disabled - statistics.median(bare_times)
    assert not dev.exists()
    assert disabled < 0.15 or overhead < 0.15, f"disabled emit median {disabled:.3f}s (overhead {overhead:.3f}s)"
    assert _run_dd(dev, "enable").returncode == 0
    enabled = _median_seconds(dev, "emit", "x")  # measured and printed only
    with capsys.disabled():
        print(f"\ndevdash emit medians: disabled={disabled:.3f}s enabled={enabled:.3f}s")


@pytest.mark.skipif(_PYTHON3 is None, reason="no system python3")
@pytest.mark.parametrize("args", [("emit", "x"), ("hook", "pre-tool")])
def test_disabled_path_never_imports_devdash(dev: Path, args: tuple[str, ...]) -> None:
    """Deterministic guard for the hook budget: a disabled emit/hook must not import the ``devdash`` package."""
    assert _PYTHON3 is not None
    done = subprocess.run(
        [_PYTHON3, "-I", "-X", "importtime", str(_DD), *args],
        input="",
        capture_output=True,
        text=True,
        cwd=dev.parent,
        check=False,
        env={"SAE_DEVDASH_DIR": str(dev), "PATH": ""},
    )
    assert done.returncode == 0, done.stderr
    assert "devdash" not in done.stderr
