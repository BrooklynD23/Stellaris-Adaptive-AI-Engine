"""Peak RSS regression: the real ``sae telemetry`` process on a ~30k-record bus stays well under the 150 MiB cap.

On this ~3 MiB / ~20k-record bus the old path (per-record ``rr.log``, 8 MiB tail batches) peaked at 162 MiB;
columnar ``send_columns`` plus 256 KiB tail batches peak at 122 MiB (WSL2, 9p venv) / 128 MiB (ext4 venv). The
bound fails if that growth comes back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .test_telemetry_mapping import doc_lines

PEAK_BOUND_MIB = 135  # cap is 150 (C8 section 2); measured 122-128 after the fix, 162 before
REPEATS = 400  # doc bus x 400: ~3 MiB, ~20k records at T2
RUN = "20261019-031500-synth-0a1b"


def _big_bus(run_dir: Path) -> None:
    events = [json.loads(ln) for ln in doc_lines()]
    rows = []
    for i in range(REPEATS * len(events)):
        ev = events[i % len(events)]
        rows.append(json.dumps({**ev, "seq": i}, separators=(",", ":")) + "\n")
    run_dir.mkdir()
    (run_dir / "bus.jsonl").write_text("".join(rows), encoding="utf-8")


# Runs cli.main in a fresh interpreter and reads VmHWM itself: ru_maxrss from wait4 is polluted by the forking
# pytest process's high-water mark, so it cannot be used here.
_CHILD = """
import os, sys
from sae.telemetry.cli import main
rc = main("telemetry", sys.argv[1:], exit_hard=lambda code: None)
hwm = next(ln for ln in open("/proc/self/status") if ln.startswith("VmHWM:"))
print(int(hwm.split()[1]) >> 10, flush=True)
os._exit(rc)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="VmHWM is Linux-specific")
def test_telemetry_peak_rss_bounded(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    _big_bus(tmp_path / RUN)
    argv = ["--run", RUN, "--rrd", str(tmp_path / "t.rrd"), "--runs-dir", str(tmp_path),
            "--set", 'telemetry.tier="T2"']
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAE_")}
    p = subprocess.run([sys.executable, "-c", _CHILD, *argv], env=env, cwd=tmp_path, capture_output=True,
                       text=True, timeout=300, check=False)
    assert p.returncode == 0, p.stderr[-500:]
    peak_mib = int(p.stdout.split()[-1])
    assert peak_mib <= PEAK_BOUND_MIB, f"telemetry peak RSS {peak_mib} MiB > {PEAK_BOUND_MIB} MiB"
