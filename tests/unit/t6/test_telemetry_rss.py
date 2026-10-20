"""Peak memory regression: the real ``sae telemetry`` process on a ~3 MiB / ~20k-record bus stays under the cap.

The cap (C8 section 2, maintainer decision 2026-10-20, proposal 026) is 150 MiB of PRIVATE memory (Linux RssAnon).
The child samples RssAnon every 5 ms and prints its peak private memory, peak RSS (VmHWM) and the final anon/file
split on every run, so CI output calibrates the bound. Old path (per-record ``rr.log``, 8 MiB polls): 162 MiB
peak RSS on WSL2; new path: ~70 MiB private / ~122-128 MiB RSS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import sae.telemetry.stream as stream_mod

from .test_telemetry_mapping import doc_lines

PEAK_BOUND_MIB = stream_mod.MEM_LIMIT >> 20  # the contract cap; tighten only after 3 green ubuntu CI runs
REPEATS = 400  # doc bus x 400: ~3 MiB, ~20k records at T2
RUN = "20261019-031500-synth-0a1b"

# cli.main in a fresh interpreter. ru_maxrss from wait4 is polluted by the forking pytest process's high-water mark.
_CHILD = """
import json, os, sys, threading, time
from sae.telemetry.stream import _status_kib
from sae.telemetry.cli import main
peak = [0]
done = threading.Event()
def sample():
    while not done.is_set():
        peak[0] = max(peak[0], _status_kib().get("RssAnon", 0))
        time.sleep(0.005)
t = threading.Thread(target=sample, daemon=True)
t.start()
rc = main("telemetry", sys.argv[1:], exit_hard=lambda code: None)
done.set()
t.join()
kib = _status_kib()
hwm = next(int(ln.split()[1]) for ln in open("/proc/self/status") if ln.startswith("VmHWM:"))
print(json.dumps({"peak_private_mib": max(peak[0], kib["RssAnon"]) >> 10, "peak_rss_mib": hwm >> 10,
                  "anon_mib": kib["RssAnon"] >> 10, "file_mib": kib["RssFile"] >> 10}), flush=True)
os._exit(rc)
"""


def _big_bus(run_dir: Path) -> None:
    events = [json.loads(ln) for ln in doc_lines()]
    rows = []
    for i in range(REPEATS * len(events)):
        ev = events[i % len(events)]
        rows.append(json.dumps({**ev, "seq": i}, separators=(",", ":")) + "\n")
    run_dir.mkdir()
    (run_dir / "bus.jsonl").write_text("".join(rows), encoding="utf-8")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RssAnon/VmHWM are Linux-specific")
def test_telemetry_peak_private_memory_bounded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("rerun")
    _big_bus(tmp_path / RUN)
    argv = ["--run", RUN, "--rrd", str(tmp_path / "t.rrd"), "--runs-dir", str(tmp_path),
            "--set", 'telemetry.tier="T2"']
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAE_")}
    p = subprocess.run([sys.executable, "-c", _CHILD, *argv], env=env, cwd=tmp_path, capture_output=True,
                       text=True, timeout=300, check=False)
    assert p.returncode == 0, p.stderr[-500:]
    got = json.loads(p.stdout.splitlines()[-1])
    with capsys.disabled():  # printed on every run, so CI logs calibrate the bound
        print(f"\ntelemetry memory (MiB): {got}")
    assert got["peak_private_mib"] <= PEAK_BOUND_MIB, f"telemetry peak private memory over the cap: {got}"
