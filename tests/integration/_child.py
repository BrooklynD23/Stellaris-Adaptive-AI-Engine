"""Child process body for the CI-4 tests: synth, import wiring, resolve factories, dump facts as JSON.

Run as ``python _child.py <years> <runs_dir> <parser_exe>``.
"""

from __future__ import annotations

import importlib
import json
import sys


def main(years: str, runs_dir: str, exe: str) -> int:
    """Run the synth chain in-process and print a JSON fact sheet."""
    import sae.cli

    code = sae.cli.main(
        ["synth", "--years", years, "--seed", "2", "--runs-dir", runs_dir,
         "--set", f"paths.parser_exe={json.dumps(exe)}"]
    )
    wiring = importlib.import_module("sae.daemon.wiring")
    # Resolve (never call) every C8 section 5.1 factory used by wiring.
    for mod, attr in (
        ("sae.bus", "open_sink"), ("sae.governor", "make_governor"), ("sae.platform", "make_sensors"),
        ("sae.platform", "make_platform"), ("sae.control", "open_inbox"),
        ("sae.adapters.paths", "resolve_parser_exe"), ("sae.adapters", "make_adapter"),
    ):
        assert callable(wiring._factory(mod, attr, "ci4"))
    maps = ""
    if sys.platform.startswith("linux"):
        with open("/proc/self/maps", encoding="utf-8") as fh:
            maps = fh.read()
    if sys.platform == "win32":
        import psutil

        peak = int(psutil.Process().memory_info().peak_wset)  # type: ignore[attr-defined,unused-ignore]
    elif sys.platform.startswith("linux"):
        # VmHWM belongs to this process image. ru_maxrss would carry the forking parent's peak across exec
        # (Linux keeps signal->maxrss over execve), which made a full pytest run report the pytest RSS.
        with open("/proc/self/status", encoding="utf-8") as fh:
            hwm = next(line for line in fh if line.startswith("VmHWM:"))
        peak = int(hwm.split()[1]) * 1024
    else:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS
    print("FACTS" + json.dumps({
        "code": code,
        "peak": peak,
        "maps": maps,
        "modules": sorted(sys.modules),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
