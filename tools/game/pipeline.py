"""Game-test pipeline: run the game-PC gates in order, report each step to the dev dashboard, pause for the owner.

Usage: ``uv run python3 tools/game/pipeline.py [--stages x3,x4,m0,x2,r] [--from ID] [--out DIR] [--rerun]
[--launch] [--dry-run] [--max-saves N] [--while-game] [--keep-runs N]``.

The pipeline reuses ``run_deferred.py`` (X3/X4 pytest stages), ``sae m0`` (M0 gates) and ``sae bench``; it never
reimplements their checks. Every gate emits ``start`` then exactly one of ``pass|fail|skip`` (``owner`` in between
while it waits for the maintainer to play or judge). Passed gates are recorded in ``<out>/state.json`` and skipped on
resume unless ``--rerun``.

Safety rules (settled decisions): the game's priority and affinity are never touched and the game is never killed.
Only ``cmd.exe``, ``reg.exe`` and ``tasklist.exe`` are called, none of them with a file path; every file operation
happens on the WSL side. Waiting for the maintainer is a cheap stat loop (no parsing while the game runs).
Stellaris behaviour is UNVERIFIED until M0 proves it; see the per-site comments.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import string
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
for _entry in (str(_REPO / "tools" / "devdash"), str(Path(__file__).resolve().parent)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import gates as gate_registry  # noqa: E402
from devdash import dev_dir  # noqa: E402
from devdash import events as ev  # noqa: E402
from devdash.gateindex import default_scrub, from_junit  # noqa: E402
from gates import Gate  # noqa: E402

from sae.m0 import results as m0_results  # noqa: E402

_LOG = logging.getLogger("sae.pipeline")

Runner = Callable[..., Any]
Sleep = Callable[[float], None]
Now = Callable[[], datetime]

DEFAULT_ROOTS: tuple[Path, ...] = (Path("/mnt/c"), Path("/mnt/d"), Path("/mnt/e"))
SOURCE = "pipeline"
STELLARIS_APP_ID = "281990"
ALL_STAGES = ("x3", "x4", "m0", "x2", "r")
POLL_S = 10
GAME_CHECK_EVERY = 6  # ticks of POLL_S: the game-process check runs every 60 s
LOCK_RETRY_S = 30
LOCK_TRIES = 10
BUDGET_S = 3.0
_PRIORITY_ORDER = {"critical": 0, "core": 1, "stretch": 2, "later": 3}
_MOD_EXCLUDE = frozenset({"tools", "tests", "__pycache__"})
_SAVE_SEGMENT = "save games"
_STELLARIS_REL = Path("Paradox Interactive") / "Stellaris"
_STEAM_SUBDIRS = ("Steam", "Program Files (x86)/Steam", "SteamLibrary")
_STOP_AFTER = 130
SETTLE_S = 15.0  # a save is copied only after its mtime is this old (UNVERIFIED: Stellaris write pattern; M0 would
SETTLE_GAP_S = 2.0  # two equal stats this far apart are required even for a save that looks old
SETTLE_TRIES = 4  # watch a save grow during an autosave and time how long the file keeps changing)
KEEP_RUNS = 3
ACTIVE_RUN_S = 3600.0  # a run whose state.json is newer than this is never pruned
_RUN_NAME = re.compile(r"\d{8}-\d{6}")
PROC_VERSION = Path("/proc/version")


# --- small helpers --------------------------------------------------------------------------------------------------


def _call(runner: Runner, argv: Sequence[str], timeout: float | None = 30) -> tuple[int, str]:
    """Run a tool and return ``(returncode, stdout)``; a missing binary or a timeout gives ``(127, "")``."""
    try:
        res = runner(list(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        _LOG.warning("%s failed: %s", argv[0], exc)
        return 127, ""
    return int(res.returncode), str(res.stdout or "")


def _drive_root(letter: str, roots: Sequence[Path]) -> Path:
    for root in roots:
        if root.name.lower() == letter.lower():
            return root
    return Path("/mnt") / letter.lower()


def _win_to_wsl(text: str, roots: Sequence[Path]) -> Path | None:
    """Convert ``X:/dir`` or ``X:\\\\dir`` to ``<root x>/dir``; ``None`` when ``text`` has no drive letter."""
    match = re.match(r"^([A-Za-z]):[\\/]*(.*)$", text.strip())
    if match is None:
        return None
    rest = [p for p in re.split(r"[\\/]+", match.group(2)) if p]
    return _drive_root(match.group(1), roots).joinpath(*rest)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _when(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


# --- preflight ------------------------------------------------------------------------------------------------------


def _steam_libraries(steam: Path, roots: Sequence[Path]) -> list[Path]:
    """The Steam folder itself plus every library listed in ``steamapps/libraryfolders.vdf`` (WSL paths)."""
    libs = [steam]
    try:
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libs
    for raw in re.findall(r'"path"\s+"([^"]+)"', text):
        lib = _win_to_wsl(raw.replace("\\\\", "\\"), roots)
        if lib is not None and lib not in libs:
            libs.append(lib)
    return libs


def _true_case(path: Path, roots: Sequence[Path]) -> Path:
    """Restore the on-disk spelling of the parts below a drive root (the registry may say ``d:/steam``)."""
    base = next((r for r in roots if path.is_relative_to(r)), None)
    if base is None:
        return path
    current = base
    for part in path.relative_to(base).parts:
        try:
            match = next((n for n in os.listdir(current) if n.lower() == part.lower()), part)
        except OSError:
            match = part
        current = current / match
    return current


def _find_install(
    runner: Runner, roots: Sequence[Path]
) -> tuple[Path | None, Path | None]:
    """Return ``(install_dir, library)``: registry SteamPath first, then the known Steam roots (no recursion)."""
    steams: list[Path] = []
    code, out = _call(runner, ["reg.exe", "query", r"HKCU\Software\Valve\Steam", "/v", "SteamPath"])
    match = re.search(r"SteamPath\s+REG_SZ\s+(.+)", out) if code == 0 else None
    if match is not None:
        converted = _win_to_wsl(match.group(1), roots)
        if converted is not None:
            steams.append(converted)
    for root in roots:
        steams.extend(root / sub for sub in _STEAM_SUBDIRS)
    for steam in steams:
        for lib in _steam_libraries(steam, roots):
            candidate = lib / "steamapps" / "common" / "Stellaris"
            if candidate.is_dir():
                return _true_case(candidate, roots), _true_case(lib, roots)
    return None, None


def _find_user_dir(roots: Sequence[Path]) -> Path | None:
    """``<C>/Users/*/OneDrive/Documents/Paradox Interactive/Stellaris``, then ``.../Documents/...``."""
    users = _drive_root("c", roots) / "Users"
    try:
        homes = sorted(p for p in users.iterdir() if p.is_dir())
    except OSError:
        return None
    for sub in (Path("OneDrive") / "Documents", Path("Documents")):
        for home in homes:
            candidate = home / sub / _STELLARIS_REL
            if candidate.is_dir():
                return candidate
    return None


def _find_starnet(lib: Path | None) -> Path | None:
    """The StarNet folder in the Workshop content of ``lib`` (a ``descriptor.mod`` naming StarNet), else None."""
    if lib is None:
        return None
    content = lib / "steamapps" / "workshop" / "content" / STELLARIS_APP_ID
    try:
        folders = sorted(p for p in content.iterdir() if p.is_dir())
    except OSError:
        return None
    for folder in folders:
        try:
            head = (folder / "descriptor.mod").read_text(encoding="utf-8", errors="replace")[:2048]
        except OSError:
            continue
        if "starnet" in head.lower():
            return folder
    return None


def _mod_files(root: Path) -> Iterator[Path]:
    """Files of ``root`` the pipeline stages: no ``tools``/``tests``/``__pycache__`` folders and no dotfiles."""
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _MOD_EXCLUDE and not d.startswith("."))
        for name in sorted(files):
            path = Path(base) / name
            if name.startswith("."):
                continue
            try:
                regular = stat.S_ISREG(path.lstat().st_mode)  # no symlinks, FIFOs or devices (they can hang a read)
            except OSError:
                continue
            if regular:
                yield path


def _hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in _mod_files(root):
        out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _mod_state(user_dir: Path | None, repo: Path) -> tuple[bool, bool]:
    """Return ``(mod_staged, mod_stale)``; stale means a repo mod file is missing from, or differs in, the copy."""
    if user_dir is None:
        return False, False
    staged_dir = user_dir / "mod" / "sae_bridge"
    staged = (user_dir / "mod" / "sae_bridge.mod").is_file() and staged_dir.is_dir()
    if not staged:
        return False, False
    try:
        want = _hashes(repo / "mod" / "sae_bridge")
        have = _hashes(staged_dir)
    except OSError:
        return True, True
    return True, set(have) != set(want) or any(have[name] != digest for name, digest in want.items())


def platform_label(proc_version: Path = PROC_VERSION) -> str:
    """``sys.platform``, plus ``+wsl`` when ``/proc/version`` mentions Microsoft (a WSL kernel)."""
    try:
        wsl = "microsoft" in proc_version.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        wsl = False
    return f"{sys.platform}+wsl" if wsl else sys.platform


def game_running(runner: Runner) -> bool:
    """True when ``tasklist.exe`` lists ``stellaris.exe`` (the exit code is 0 whether or not it matches)."""
    _, out = _call(runner, ["tasklist.exe", "/FI", "IMAGENAME eq stellaris.exe", "/NH"])
    return "stellaris.exe" in out.lower()


def _tone(key: str, value: Any) -> str:
    if value is None:
        return "muted"
    if isinstance(value, bool):
        bad_true = key in {"game_running", "mod_stale", "onedrive"}
        bad_false = key in {"windows_uv", "mod_staged"}
        if bad_true:
            return "warn" if value else "ok"
        if bad_false:
            return "ok" if value else "warn"
        return "ok" if value else "muted"
    return "info"


def preflight(
    runner: Runner,
    *,
    roots: Sequence[Path] = DEFAULT_ROOTS,
    repo: Path = _REPO,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Detect the environment, write ``<dev>/env.json`` and emit an ``info`` event.

    Keys: ``install_dir``, ``user_dir``, ``onedrive``, ``parser``, ``starnet_dir``, ``game_running``,
    ``windows_uv``, ``mod_staged``, ``mod_stale`` (paths are strings or None). ``env.json`` holds these plus the
    ready-made ``env`` rows the dashboard sidebar shows.
    """
    env = os.environ if environ is None else environ
    install, lib = _find_install(runner, roots)
    user = _find_user_dir(roots)
    parser_env = env.get("SAE_PARSER_EXE")
    default_parser = repo / "parser" / "target" / "release" / "sae-parse"
    parser = parser_env if parser_env else (str(default_parser) if default_parser.is_file() else None)
    starnet = _find_starnet(lib)
    staged, stale = _mod_state(user, repo)
    code, _ = _call(runner, ["cmd.exe", "/c", "where", "uv"])
    pf: dict[str, Any] = {
        "install_dir": str(install) if install else None,
        "user_dir": str(user) if user else None,
        "onedrive": bool(user and any(p.lower().startswith("onedrive") for p in user.parts)),
        "parser": parser,
        "starnet_dir": str(starnet) if starnet else None,
        "game_running": game_running(runner),
        "windows_uv": code == 0,
        "mod_staged": staged,
        "mod_stale": stale,
    }
    rows = [{"k": k, "v": "-" if v is None else str(v).lower() if isinstance(v, bool) else str(v), "tone": _tone(k, v)}
            for k, v in pf.items()]
    dev = dev_dir(repo)
    try:
        dev.mkdir(parents=True, exist_ok=True)
        (dev / "env.json").write_text(json.dumps({**pf, "env": rows}, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        _LOG.warning("env.json not written: %s", exc)
    ev.emit(
        SOURCE, "info",
        f"preflight: install={'ok' if install else 'missing'} user_dir={'ok' if user else 'missing'} "
        f"parser={'ok' if parser else 'missing'} game_running={pf['game_running']} windows_uv={pf['windows_uv']} "
        f"mod_staged={staged} mod_stale={stale}",
        dev=dev,
    )
    return pf


# --- planning -------------------------------------------------------------------------------------------------------


def markers(repo: Path = _REPO) -> set[str]:
    """Every ``id=`` of a ``pytest.mark.game(...)`` call under ``<repo>/tests`` (AST scan, nothing is imported)."""
    found: set[str] = set()
    for path in (repo / "tests").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "pytest.mark.game":
                found.update(
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                )
    return found


def plan(gs: Sequence[Gate], stages: set[str], start: str | None) -> list[Gate]:
    """Gates to run: registry order, M0 sorted critical -> core -> stretch -> later, ``backlog`` dropped.

    ``start`` drops every gate before it. Raises ``ValueError`` when ``start`` is not in the plan.
    """
    chosen = [g for g in gs if g.stage in stages and g.priority != "backlog"]
    ordered: list[Gate] = []
    for stage in ALL_STAGES:
        group = [g for g in chosen if g.stage == stage]
        if stage == "m0":
            group.sort(key=lambda g: _PRIORITY_ORDER.get(g.priority, len(_PRIORITY_ORDER)))
        ordered.extend(group)
    ordered.extend(g for g in chosen if g.stage not in ALL_STAGES)
    if start is not None:
        ids = [g.id for g in ordered]
        if start not in ids:
            raise ValueError(f"--from {start}: not in the plan ({', '.join(ids) or 'empty'})")
        ordered = ordered[ids.index(start):]
    return ordered


def skip_reason(gate: Gate, pf: Mapping[str, Any], found_markers: set[str]) -> str | None:
    """The reason a gate cannot run on this PC right now, or None. Checked in the brief's order."""
    if gate.hardware != "one_pc":
        return "needs other hardware"
    if gate.milestone_later:
        return "later milestone"
    if gate.pending_source and gate.id not in found_markers:
        return f"waiting on {gate.pending_source}"
    if gate.windows_only:
        if not pf.get("windows_uv"):
            return "setup needed: Windows uv (install: winget install astral-sh.uv)"
        return "setup needed: Windows-side runner (not built)"  # never run a Windows-only gate under WSL
    if gate.runner.get("kind") == "none" and not gate.judged:
        return "manual procedure: see bench/README.md"
    return None


# --- input copies ---------------------------------------------------------------------------------------------------


def _newest_sav(folder: Path, prefix: str = "") -> float:
    best = 0.0
    try:
        for entry in os.scandir(folder):
            if entry.name.endswith(".sav") and entry.name.startswith(prefix):
                with contextlib.suppress(OSError):
                    best = max(best, entry.stat().st_mtime)
    except OSError:
        return 0.0
    return best


def newest_campaign(user_dir: Path, *, prefer_autosaves: bool = False) -> Path | None:
    """The campaign folder under ``<user_dir>/save games/`` holding the newest ``.sav``.

    With ``prefer_autosaves`` the newest folder holding an ``autosave*.sav`` wins, and any ``.sav`` counts only when no
    folder has an autosave. A manual save in its own folder (a bug-repro save, say) would otherwise starve the
    autosave gates (X3-G5, X3-G9, X3-G10) of a campaign; found on the first real-save run, 2026-11-23.
    """
    root = user_dir / _SAVE_SEGMENT
    try:
        folders = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return None
    for prefix in ("autosave", "") if prefer_autosaves else ("",):
        scored = [(s, p) for p in folders if (s := _newest_sav(p, prefix)) > 0]
        if scored:
            return max(scored, key=lambda sp: sp[0])[1]
    return None


def _locked(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or exc.errno == errno.EBUSY


def _copy_retry(src: Path, dst: Path, sleep: Sleep, note: Callable[[str], None]) -> bool:
    """``shutil.copy2`` with the save-locked retry (OneDrive or the game holds the file): 30 s apart, 10 tries."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, LOCK_TRIES + 1):
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            if not _locked(exc):
                note(f"copy failed for {src.name}: {type(exc).__name__}")
                return False
            if attempt < LOCK_TRIES:
                sleep(LOCK_RETRY_S)
            continue
        return True
    note(f"{src.name} still locked after {LOCK_TRIES} tries; left out")
    return False


def _same(src: Path, dst: Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)


def _settled(src: Path, clock: Callable[[], float], sleep: Sleep, note: Callable[[str], None]) -> bool:
    """True once ``src`` is unchanged across two stats ``SETTLE_GAP_S`` apart and is at least ``SETTLE_S`` old.

    A torn copy of a save the game is still writing would be parsed and failed as a parser bug. Age alone is not
    trusted: the WSL clock can run ahead of Windows, so even a save that looks old needs two equal ``(size, mtime)``
    stats. A younger save is waited for first (``sleep``); one dated in the future sleeps ``SETTLE_S`` before the
    second stat. At most ``SETTLE_TRIES`` rounds.
    """
    for i in range(SETTLE_TRIES + 1):
        try:
            before = src.stat()
        except OSError:
            return False
        age = clock() - before.st_mtime
        young = 0 <= age < SETTLE_S
        if young and i == SETTLE_TRIES:
            break
        sleep(SETTLE_S - age + 1.0 if young else (SETTLE_S if age < 0 else SETTLE_GAP_S))
        try:
            after = src.stat()
        except OSError:
            return False
        if not young and (after.st_size, after.st_mtime) == (before.st_size, before.st_mtime):
            return True
    note(f"{src.name} was still changing; left out")
    return False


def copy_inputs(
    pf: Mapping[str, Any], out: Path, max_saves: int, *, sleep: Sleep, clock: Callable[[], float] = time.time
) -> dict[str, str]:
    """Copy the newest ``max_saves`` autosaves of the newest campaign, plus ``game.log``, into ``<out>/private/inputs``.

    A file that stays locked is left out and reported in an ``info`` event. Returns the env vars to set:
    ``SAE_GAME_SAVES_DIR`` (only when at least one save was copied), ``SAE_GAME_LOG`` (when the log was copied),
    ``SAE_GAME_MAX_SAVES`` and ``SAE_PARSER_EXE`` (when known). A save whose mtime is under ``SETTLE_S`` seconds old is
    waited for (``clock`` and ``sleep`` are injectable) and left out if it keeps changing. Nothing is parsed here.
    """
    dev = dev_dir()
    env: dict[str, str] = {"SAE_GAME_MAX_SAVES": str(max_saves)}
    if pf.get("parser"):
        env["SAE_PARSER_EXE"] = str(pf["parser"])
    user = Path(pf["user_dir"]) if pf.get("user_dir") else None

    def note(msg: str) -> None:
        ev.emit(SOURCE, "info", f"inputs: {msg}", dev=dev)

    if user is None:
        note("no Stellaris user folder; nothing copied")
        return env
    inputs = out / "private" / "inputs"
    campaign = newest_campaign(user, prefer_autosaves=True)
    if campaign is None:
        _prune(inputs / "saves", set())
        note("no saves found")
    else:
        stamped: list[tuple[float, str, Path]] = []
        for p in campaign.glob("*.sav"):
            try:
                stamped.append((p.stat().st_mtime, p.name, p))
            except OSError:
                continue  # vanished between listing and stat
        sav = [p for _, _, p in sorted(stamped, key=lambda s: (s[0], s[1]), reverse=True)]
        autos = [p for p in sav if p.name.startswith("autosave")] or sav
        chosen = autos[:max_saves]
        _prune(inputs / "saves", {p.name for p in chosen})
        copied = 0
        for src in chosen:
            dst = inputs / "saves" / src.name
            if _same(src, dst) or (_settled(src, clock, sleep, note) and _copy_retry(src, dst, sleep, note)):
                copied += 1
        if copied:
            env["SAE_GAME_SAVES_DIR"] = str(inputs / "saves")
        note(f"copied {copied} of {min(len(autos), max_saves)} saves")
    log = user / "logs" / "game.log"
    if log.is_file() and (_same(log, inputs / "game.log") or _copy_retry(log, inputs / "game.log", sleep, note)):
        env["SAE_GAME_LOG"] = str(inputs / "game.log")
    return env


def _prune(folder: Path, keep: set[str]) -> None:
    """Delete copied ``.sav`` files not in ``keep`` so the inputs match exactly the selected saves."""
    try:
        for stale in folder.glob("*.sav"):
            if stale.name not in keep:
                stale.unlink(missing_ok=True)
    except OSError as exc:
        _LOG.warning("could not prune %s: %s", folder, exc)


def _private_dirs(run: Path) -> list[Path]:
    """The ``private`` folder of a run and of each stage folder directly in it (real folders only)."""
    found: list[Path] = []
    for cand in [run / "private", *sorted(run.glob("*/private"))]:
        if cand.is_dir() and not cand.is_symlink() and not cand.parent.is_symlink():
            found.append(cand)
    return found


def _active(run: Path, now: float) -> bool:
    """True when the run's ``state.json`` was written within ``ACTIVE_RUN_S`` (another pipeline may be using it)."""
    try:
        return now - (run / "state.json").stat().st_mtime < ACTIVE_RUN_S
    except OSError:
        return False


def prune_runs(dev: Path, keep: int, current: Path | None = None, *, now: float | None = None) -> list[Path]:
    """Delete ``private/`` (raw saves, basetemp copies) of all but the newest ``keep`` pipeline runs.

    Only timestamp-named real folders directly inside ``<dev>/pipeline`` count as runs the pipeline created; the
    ``current`` run is always kept. Only ``private`` folders are removed (never the run folder, ``state.json`` or
    ``results.json``), and no symlink is followed. The ``current`` run is compared by resolved path (so ``<run>/x3/..``
    or a symlink to it still counts), and a run whose ``state.json`` changed within ``ACTIVE_RUN_S`` is treated as in
    flight and kept. Returns the folders removed.
    """
    base = dev / "pipeline"
    removed: list[Path] = []
    if base.is_symlink():
        return removed
    stamp = time.time() if now is None else now
    try:
        cur = current.resolve() if current is not None else None
        root = base.resolve(strict=True)
        runs = sorted(
            p for p in base.iterdir()
            if _RUN_NAME.fullmatch(p.name) and p.is_dir() and not p.is_symlink() and p.resolve().parent == root
        )
    except OSError:
        return removed
    kept = {p.name for p in runs[max(len(runs) - max(keep, 1), 0):]}
    for run in runs:
        if run.name in kept or run.resolve() == cur or _active(run, stamp):
            continue
        for private in _private_dirs(run):
            try:
                shutil.rmtree(private)
            except OSError as exc:
                _LOG.warning("could not remove %s: %s", private, exc)
                continue
            removed.append(private)
    return removed


def newest_copied_save(env: Mapping[str, str]) -> Path | None:
    """Newest ``.sav`` in the copied-saves folder, or None."""
    folder = env.get("SAE_GAME_SAVES_DIR")
    if not folder:
        return None
    try:
        return max(Path(folder).glob("*.sav"), key=lambda p: (p.stat().st_mtime, p.name), default=None)
    except OSError:
        return None


# --- watchers (spec 6.2) ----------------------------------------------------------------------------------------------


class Watcher:
    """Emits ``stellaris`` events while the pipeline waits: a new autosave, or the game starting or exiting.

    ``tick`` is called once per ``POLL_S`` and only stats files; the game-process check runs every
    ``GAME_CHECK_EVERY`` ticks (60 s). Nothing is parsed.
    """

    def __init__(self, user_dir: Path | None, runner: Runner, dev: Path, running: bool) -> None:
        self._user = user_dir
        self._runner = runner
        self._dev = dev
        self._running = running
        self._ticks = 0
        self._campaign: Path | None = newest_campaign(user_dir) if user_dir else None
        self._seen = self._newest()

    def _newest(self) -> tuple[str, int] | None:
        if self._campaign is None:
            return None
        best: tuple[str, int] | None = None
        try:
            for entry in os.scandir(self._campaign):
                if entry.name.endswith(".sav"):
                    stamp = entry.stat().st_mtime_ns
                    if best is None or stamp > best[1]:
                        best = (entry.name, stamp)
        except OSError:
            return None
        return best

    def tick(self) -> None:
        """One poll: stat the newest autosave; every sixth call also ask ``tasklist.exe`` about the game."""
        self._ticks += 1
        if self._user is not None and self._ticks % GAME_CHECK_EVERY == 0:
            self._campaign = newest_campaign(self._user) or self._campaign
        current = self._newest()
        if current is not None and current != self._seen:
            ev.emit("stellaris", "info", f"autosave written: {current[0]}", dev=self._dev)
        self._seen = current
        if self._ticks % GAME_CHECK_EVERY == 0:
            now_running = game_running(self._runner)
            if now_running != self._running:
                ev.emit("stellaris", "info", "game started" if now_running else "game exited", dev=self._dev)
                self._running = now_running


# --- pipeline run ---------------------------------------------------------------------------------------------------


@dataclass
class _Run:
    """Everything one pipeline run shares."""

    repo: Path
    dev: Path
    out: Path
    pf: dict[str, Any]
    runner: Runner
    sleep: Sleep
    now: Now
    max_saves: int
    launch: bool
    rerun: bool
    found: set[str]
    input_fn: Callable[[], str]
    isatty: Callable[[], bool]
    environ: Mapping[str, str]
    while_game: bool = False
    platform: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, str] | None = None
    launched: bool = False
    open_refs: dict[str, bool] = field(default_factory=dict)  # ref -> owner event open?
    owner_clicked: bool = False  # the last dashboard owner_wait ended on Mark done (not on the game closing)
    failed: bool = False
    missing: str = ""

    # -- events and state --

    def emit(self, kind: str, msg: str, ref: str | None = None, source: str = SOURCE) -> None:
        ev.emit(source, kind, msg, ref=ref, dev=self.dev)

    def save_state(self) -> None:
        self.state["updated"] = _iso(self.now())
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "state.json").write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (self.out / "results.json").write_text(json.dumps(self.results, indent=2) + "\n", encoding="utf-8")

    def begin(self, gate: Gate) -> None:
        self.open_refs[gate.id] = False
        self.emit("start", f"{gate.id} {gate.title}", gate.id)

    def finish(self, gate: Gate, status: str, msg: str) -> None:
        """Close a gate: exactly one ``pass|fail|skip`` event, then state."""
        self.open_refs.pop(gate.id, None)
        self.emit(status, msg, gate.id)
        self.state["gates"][gate.id] = status
        self.failed = self.failed or status == "fail"
        print(f"[{status}] {gate.id} {msg}", flush=True)
        self.save_state()

    # -- inputs --

    def ensure_inputs(self, *, refresh: bool = False) -> dict[str, str]:
        if self.inputs is None or refresh:
            self.inputs = copy_inputs(
                self.pf, self.out, self.max_saves, sleep=self.sleep, clock=lambda: self.now().timestamp()
            )
        return self.inputs

    # -- waiting --

    def watcher(self) -> Watcher:
        user = Path(self.pf["user_dir"]) if self.pf.get("user_dir") else None
        return Watcher(user, self.runner, self.dev, bool(self.pf.get("game_running")))

    def wait(self, until: Callable[[], bool]) -> None:
        """Poll ``until`` every ``POLL_S`` with no timeout; watchers run between polls."""
        watcher = self.watcher()
        while not until():
            self.sleep(POLL_S)
            watcher.tick()

    def wait_done(self, gate: Gate) -> bool:
        """Emit the ``owner`` event and wait for Mark done. False means the gate was finished as a skip."""
        if self.owner_wait(gate.id, gate.owner_instruction):
            return True
        self.finish(gate, "skip", "owner step needs the dashboard or a terminal")
        return False

    def owner_wait(self, ref: str, instruction: str, *, until_game_closed: bool = False) -> bool:
        """Emit an ``owner`` event for ``ref`` and wait for Mark done (dashboard) or Enter (terminal).

        False means the step cannot be waited for (no dashboard and no terminal, or the terminal input ended); the
        caller decides what that means. Ctrl-C propagates; ``main`` closes the open refs. With ``until_game_closed``
        the dashboard wait also ends when Stellaris is no longer running (checked every ``GAME_CHECK_EVERY`` polls,
        read-only), so closing the game needs no click.
        """
        if not ev.is_enabled(self.dev):
            if not self.isatty():
                return False
            self.emit("owner", instruction, ref)
            print(f"{ref}: {instruction}\nDashboard is off; press Enter when done.", flush=True)
            self.open_refs[ref] = True
            try:
                self.input_fn()
            except EOFError:
                self.emit("done", "no terminal input", ref)  # closes the owner state
                return False
            except KeyboardInterrupt:
                raise  # main() closes the gate (skip + done) and exits 130
            return True
        tail = ev.Tail(ev.events_path(self.dev, "wsl"))
        tail.poll()  # baseline: older done events for this ref must not resume us
        self.emit("owner", instruction, ref)
        self.open_refs[ref] = True
        hit = {"done": False}
        polls = [0]

        def done() -> bool:
            fresh, _ = tail.poll()
            hit["done"] = hit["done"] or any(e["kind"] == "done" and e.get("ref") == ref for e in fresh)
            if hit["done"]:
                return True
            polls[0] += 1
            return until_game_closed and polls[0] % GAME_CHECK_EVERY == 0 and not game_running(self.runner)

        self.wait(done)
        self.owner_clicked = hit["done"]
        return True

    def stand_down(self, what: str) -> bool:
        """Do not compete with the game: when Stellaris runs, ask the owner to close it before ``what``.

        True means go ahead (game not running, ``--while-game``, or the owner confirmed). False means the owner could
        not be asked (no dashboard and no terminal), so the caller must skip the work. Only reads the process list;
        the game's process, priority and affinity are never touched. UNVERIFIED: that a parse in the WSL2 VM slows the
        game; M0 would time a parse with and without the game running.
        """
        if self.while_game or not game_running(self.runner):
            return True
        ref = "game-close"
        dashboard = ev.is_enabled(self.dev)
        ok = self.owner_wait(ref, f"close Stellaris to run {what}, or rerun with --while-game", until_game_closed=True)
        self.open_refs.pop(ref, None)
        if ok and dashboard and game_running(self.runner):
            ok = False  # Mark done with the game still open: do not compete with it
        elif ok and dashboard and not self.owner_clicked:
            self.emit("done", "Stellaris closed", ref)  # closes the owner state; nobody clicked
        elif ok and not dashboard:
            self.emit("done", "owner pressed Enter", ref)
        if not ok:
            self.emit("info", f"{what}: skipped, Stellaris is running (close it or pass --while-game)")
        return ok

    def manual_since(self, gate_id: str, start: datetime) -> bool:
        floor = start.replace(microsecond=0)
        try:
            entry = m0_results.load(self.repo / "m0_results", gate_id)
        except (ValueError, OSError):
            return False
        manual = (entry or {}).get("manual")
        at = _when(manual.get("at")) if isinstance(manual, dict) else None
        return at is not None and at >= floor

    # -- M0 / R gates --

    def m0_argv(self, *tail: str) -> list[str]:
        argv = ["uv", "run", "sae", "m0", "--user-dir", str(self.pf["user_dir"])]
        if self.pf.get("parser"):
            argv += ["--parser-exe", str(self.pf["parser"])]
        return [*argv, *tail]

    def fill(self, template: str, step_start: float) -> str | None:
        """Fill a ``{placeholder}`` template; None (name left in ``self.missing``) when an input is unavailable.

        Each value is computed only if the template uses it (the ironman copy touches the game's save folder).
        """
        names = sorted({f for _, f, _, _ in string.Formatter().parse(template) if f})
        values: dict[str, str] = {}
        for name in names:
            value = self.value(name, step_start)
            if not value:
                self.missing = name
                return None
            values[name] = value
        return template.format(**values)

    def value(self, name: str, step_start: float) -> str | None:
        """One placeholder value, or None when the input is not available."""
        if name == "save":
            save = newest_copied_save(self.ensure_inputs())
            return str(save) if save else None
        if name == "ironman_save":
            return self.environ.get("SAE_GAME_IRONMAN_SAVE") or self.find_ironman(step_start)
        if name == "run_dir":
            run_dir = self.results.get("run_dir")
            return str(run_dir) if run_dir else None
        simple: dict[str, Any] = {
            "user_dir": self.pf.get("user_dir"), "starnet_dir": self.pf.get("starnet_dir"),
            "parser_exe": self.pf.get("parser"), "r1_csv": self.environ.get("SAE_GAME_R1_CSV"),
        }
        got = simple.get(name)
        return str(got) if got else None

    def find_ironman(self, step_start: float) -> str | None:
        """R13: the newest ``.sav`` in any campaign folder, written after the step started; copied out of the game."""
        user = self.pf.get("user_dir")
        if not user:
            return None
        best: Path | None = None
        best_t = step_start
        try:
            for campaign in (Path(user) / _SAVE_SEGMENT).iterdir():
                for sav in campaign.glob("*.sav"):
                    stamp = sav.stat().st_mtime
                    if stamp > best_t:
                        best, best_t = sav, stamp
        except OSError:
            return None
        if best is None:
            return None
        dst = self.out / "private" / "inputs" / "ironman" / best.name

        def note(msg: str) -> None:
            self.emit("info", f"inputs: {msg}")

        if _same(best, dst):
            return str(dst)
        if not _settled(best, lambda: self.now().timestamp(), self.sleep, note):
            return None
        return str(dst) if _copy_retry(best, dst, self.sleep, note) else None

    def run_m0_gate(self, gate: Gate) -> None:
        start = self.now()
        step_start = time.time()
        runner_cfg = gate.runner
        if gate.owner_play:
            self.maybe_launch()
            if not self.wait_done(gate):
                return
        if not self.pf.get("user_dir"):
            self.finish(gate, "skip", "setup needed: Stellaris user folder not found")
            return
        for pre in runner_cfg.get("pre", []):
            res = self.runner(self.m0_argv(*pre), cwd=self.repo, capture_output=True, text=True)
            if int(res.returncode) != 0:
                self.finish(gate, "fail", f"pre step failed: sae m0 {pre[0]}")
                return
        if runner_cfg["auto"]:
            # an owner-play gate needs the game running, so only the other M0 gates stand down for it
            if not gate.owner_play and not self.stand_down(f"{gate.id} (copies saves)"):
                self.finish(gate, "skip", "Stellaris is running; close it or rerun with --while-game")
                return
            self.ensure_inputs(refresh=gate.owner_play)
            args = [self.fill(a, step_start) for a in runner_cfg.get("args", [])]
            if any(a is None for a in args):
                self.finish(gate, "skip", f"input missing: {self.missing}")
                return
            res = self.runner(
                self.m0_argv("check", gate.id, *[a for a in args if a is not None]),
                cwd=self.repo, capture_output=True, text=True,
            )
            if int(res.returncode) != 0 and not self.fresh_auto(gate.id, start):
                self.finish(gate, "fail", f"sae m0 check exited {res.returncode} without a result")
                return
        if gate.judged:
            self.ask_verdict(gate)
            self.wait(lambda: self.manual_since(gate.id, start))
        self.settle(gate)

    def ask_verdict(self, gate: Gate) -> None:
        """Ask for a verdict: an ``owner`` event plus the same line on stdout (the dashboard may be off)."""
        cmd = f"uv run sae m0 record {gate.id} pass|fail --value KEY=VALUE --note TEXT"
        self.emit("owner", f"record your verdict on the dashboard (or: {cmd})", gate.id)
        print(f"{gate.id}: record your verdict on the dashboard, or run: {cmd}", flush=True)
        self.open_refs[gate.id] = True

    def fresh_auto(self, gate_id: str, start: datetime) -> bool:
        try:
            entry = m0_results.load(self.repo / "m0_results", gate_id)
        except (ValueError, OSError):
            return False
        auto = (entry or {}).get("auto")
        at = _when(auto.get("at")) if isinstance(auto, dict) else None
        return at is not None and at >= start.replace(microsecond=0)

    def settle(self, gate: Gate) -> None:
        try:
            entry = m0_results.load(self.repo / "m0_results", gate.id)
        except (ValueError, OSError) as exc:
            self.finish(gate, "fail", f"unreadable result: {type(exc).__name__}")
            return
        status = m0_results.status_of(entry)
        if status in ("pass", "fail"):
            self.finish(gate, status, f"{gate.id} {status}")
        else:
            self.finish(gate, "skip", "no verdict recorded" if status == "pending" else f"recorded {status}")

    def run_bench_gate(self, gate: Gate) -> None:
        step_start = time.time()
        if gate.owner_play:
            self.maybe_launch()
            if not self.wait_done(gate):
                return
        args = [self.fill(a, step_start) for a in gate.runner["args"]]
        if any(a is None for a in args):
            self.finish(gate, "skip", f"input missing: {self.missing}")
            return
        res = self.runner(
            ["uv", "run", "sae", "bench", *[a for a in args if a is not None]],
            cwd=self.repo, capture_output=True, text=True,
        )
        ok = int(res.returncode) == 0
        self.finish(gate, "pass" if ok else "fail", f"sae bench exited {res.returncode}")

    def run_judged_only(self, gate: Gate) -> None:
        """Runner ``none`` and judged (R-3, R-5): the owner plays, then records a verdict."""
        start = self.now()
        if gate.owner_play:
            self.maybe_launch()
            if not self.wait_done(gate):
                return
        self.ask_verdict(gate)
        self.wait(lambda: self.manual_since(gate.id, start))
        self.settle(gate)

    def run_gate(self, gate: Gate) -> None:
        self.begin(gate)
        reason = skip_reason(gate, self.pf, self.found)
        if reason is not None:
            self.finish(gate, "skip", reason)
            return
        kind = gate.runner.get("kind")
        if kind == "m0":
            self.run_m0_gate(gate)
        elif kind == "bench":
            self.run_bench_gate(gate)
        else:
            self.run_judged_only(gate)

    # -- launch (owner-authorized 2026-10-28) --

    def maybe_launch(self) -> None:
        """With ``--launch``, stage ``sae_bridge`` and start Stellaris once, before the first owner step.

        UNVERIFIED(A-01): the launcher accepts the staged ``.mod`` with its relative ``path=``. M0-2 tests it.
        Never touches priority, affinity or the process itself beyond the Steam start URL.
        """
        if not self.launch or self.launched:
            return
        self.launched = True
        user = self.pf.get("user_dir")
        if not user:
            self.emit("info", "launch: no Stellaris user folder; nothing staged")
            return
        mods = Path(user) / "mod"
        dest = mods / "sae_bridge"
        modfile = mods / "sae_bridge.mod"
        if not (_safe_dest(dest, Path(user), self.repo) and _safe_dest(modfile, Path(user), self.repo, file=True)):
            self.emit("info", "launch refused: mod destination is a symlink or outside <user_dir>/mod; nothing changed")
            return
        if self.pf.get("game_running") or game_running(self.runner):
            self.emit("info", "launch: Stellaris is running; mod not copied, game not launched")
            return
        self.emit("info", f"launch: mod_stale={self.pf.get('mod_stale')} mod_staged={self.pf.get('mod_staged')}")
        stage = self.runner(self.m0_argv("stage", "M0-2"), cwd=self.repo, capture_output=True, text=True)
        if int(stage.returncode) != 0:
            self.emit("info", f"launch: aborted, sae m0 stage M0-2 exited {stage.returncode}")
            return
        source = self.repo / "mod" / "sae_bridge"
        wanted = {p.relative_to(source) for p in _mod_files(source)}
        mods.mkdir(exist_ok=True)  # both destinations were verified above; nothing was changed before this line
        dest.mkdir(exist_ok=True)
        removed = _remove_extras(dest, wanted)
        copied = 0
        try:
            for rel in sorted(wanted):
                _copy_into(source, dest, rel)
                copied += 1
        except OSError as exc:
            self.emit("info", f"launch: aborted while copying the mod: {type(exc).__name__}")
            return
        staged_mod = self.repo / "m0_results" / "stage" / "sae_bridge.mod"
        if not staged_mod.is_file():
            self.emit("info", "launch: aborted, m0_results/stage/sae_bridge.mod is missing after staging; not started")
            print("launch: aborted, the staged sae_bridge.mod is missing; Stellaris was not started", flush=True)
            return
        try:
            _write_atomic(staged_mod, modfile)
        except OSError as exc:
            self.emit("info", f"launch: aborted while writing sae_bridge.mod: {type(exc).__name__}")
            return
        self.emit("info", f"launch: copied {copied} mod files (removed {removed} extra) and sae_bridge.mod")
        self.runner(["cmd.exe", "/c", "start", "", f"steam://rungameid/{STELLARIS_APP_ID}"],
                    capture_output=True, text=True)
        self.emit("info", "launch: Steam asked to start Stellaris")

    # -- X3 / X4 pytest stages --

    def run_pytest_stage(self, stage: str, group: Sequence[Gate]) -> None:
        runnable: list[Gate] = []
        collect: Gate | None = None
        for gate in group:
            self.begin(gate)
            reason = skip_reason(gate, self.pf, self.found)
            if reason is not None:
                self.finish(gate, "skip", reason)
            elif gate.runner.get("kind") == "collect":
                collect = gate
            else:
                runnable.append(gate)
        if collect is not None:
            self.run_collect(collect, group)
        if runnable and not self.stand_down(f"{stage.upper()} parse gates"):
            for gate in runnable:
                self.finish(gate, "skip", "Stellaris is running; close it or rerun with --while-game")
            return
        env = self.ensure_inputs() if runnable else {}
        pending: list[Gate] = []
        for gate in runnable:
            if "SAE_GAME_SAVES_DIR" in gate.inputs and "SAE_GAME_SAVES_DIR" not in env:
                self.finish(gate, "skip", "save locked" if self.pf.get("user_dir") else "setup needed: user folder")
            else:
                pending.append(gate)
        if pending:
            self.run_deferred(stage, pending, env)

    def run_deferred(self, stage: str, pending: Sequence[Gate], env: Mapping[str, str]) -> None:
        select = " or ".join(str(g.runner["select"]) for g in pending)
        stage_out = self.out / stage
        argv = ["uv", "run", "python3", "tools/game/run_deferred.py", "--out", str(stage_out), "--", "-k", select]
        junit = stage_out / "private" / "junit.xml"
        junit.unlink(missing_ok=True)  # a stale junit from an earlier run must never produce a result
        res = self.runner(argv, cwd=self.repo, env={**self.environ, **env})
        code = int(res.returncode)
        if not junit.is_file():
            for gate in pending:
                self.finish(gate, "fail", f"run_deferred exit {code}: no junit file")
            return
        by_gate = from_junit(junit, f"pipeline/{self.out.name}/{stage}", _iso(self.now()), default_scrub)
        props = junit_properties(junit)
        self.collect_results(props)
        for gate in pending:
            got = by_gate.get(gate.deferred_id or "")
            if got is None:
                self.finish(gate, "skip", "not collected")
                continue
            msg = got["evidence"].get("message", "")
            self.finish(gate, got["status"], msg or got["status"])

    def collect_results(self, props: Mapping[str, Mapping[str, str]]) -> None:
        """Fold ``parse_costs`` and ``run_dir`` junit properties into ``results.json``."""
        for values in props.values():
            raw = values.get("parse_costs")
            if raw:
                try:
                    rows = json.loads(raw)
                    self.results["parse"] = [
                        {
                            "label": str(r["save"]), "wall_s": float(r["wall_s"]), "rss_bytes": int(r["rss_bytes"]),
                            "platform": self.platform or platform_label(), "source": self.source(),
                        }
                        for r in rows
                    ]
                except (ValueError, KeyError, TypeError) as exc:
                    _LOG.warning("parse_costs property unusable: %s", exc)
            if values.get("run_dir"):
                self.results["run_dir"] = values["run_dir"]

    def source(self) -> str:
        """Where the parsed saves came from: always a copy; ``wsl-copy`` when this runs under WSL."""
        return "wsl-copy" if "wsl" in (self.platform or platform_label()) else "copy"

    def run_collect(self, gate: Gate, group: Sequence[Gate]) -> None:
        """X3-G0: every X3-G1..G11 has a marker, and ``run_deferred.py --list`` exits 0."""
        want = sorted(g.deferred_id for g in group if g.deferred_id)
        every = markers(self.repo)
        absent = [i for i in want if i not in every]
        res = self.runner(["uv", "run", "python3", "tools/game/run_deferred.py", "--list"],
                          cwd=self.repo, capture_output=True, text=True)
        if absent:
            self.finish(gate, "fail", f"no pytest.mark.game marker for {', '.join(absent)}")
        elif int(res.returncode) != 0:
            self.finish(gate, "fail", f"run_deferred.py --list exited {res.returncode}")
        else:
            self.finish(gate, "pass", f"markers found for {len(want)} gates; run_deferred --list ok")

    # -- interruption --

    def interrupted(self) -> int:
        for ref, owner_open in list(self.open_refs.items()):
            self.emit("skip", "interrupted", ref)
            if owner_open:
                self.emit("done", "interrupted", ref)  # owner state is closed by done, pass or fail only
            self.state["gates"][ref] = "interrupted"
        self.open_refs.clear()
        self.save_state()
        return _STOP_AFTER


def _safe_dest(dest: Path, user: Path, repo: Path, *, file: bool = False) -> bool:
    """True when ``dest`` is exactly ``<user_dir>/mod/<name>`` with no symlink on the way, and not in the repo.

    An existing ``dest`` must not be a symlink and must resolve to ``<user_dir>/mod/<its name>``. A missing one needs
    its parent to be a real folder that resolves to exactly ``<user_dir>/mod``. An existing ``dest`` of the wrong type
    (not a folder, or with ``file=True`` not a regular file) is rejected.
    """
    try:
        mods = user.resolve() / "mod"
        parent = dest.parent
        if parent.is_symlink() or parent.resolve() != mods:
            return False
        if dest.is_symlink():
            return False
        if dest.exists() or dest.is_symlink():
            if dest.resolve() != mods / dest.name:
                return False
            mode = dest.lstat().st_mode
            if not (stat.S_ISREG(mode) if file else stat.S_ISDIR(mode)):
                return False
        return not dest.resolve().is_relative_to(repo.resolve())
    except OSError:
        return False


def _remove_extras(dest: Path, wanted: set[Path]) -> int:
    """Delete what the repo copy does not have under ``dest``; never follows a symlink. Returns entries removed.

    A symlinked file or folder is unlinked (the link only, never its target); real files not in ``wanted`` are
    deleted; then empty folders are removed.
    """
    removed = 0
    if not dest.is_dir() or dest.is_symlink():
        return 0
    for base, dirs, files in os.walk(dest, followlinks=False):
        for name in list(dirs):
            link = Path(base) / name
            if link.is_symlink():
                link.unlink()
                dirs.remove(name)
                removed += 1
        for name in files:
            path = Path(base) / name
            if path.is_symlink() or path.relative_to(dest) not in wanted:
                path.unlink(missing_ok=True)
                removed += 1
    for base, _dirs, _files in os.walk(dest, topdown=False, followlinks=False):
        with contextlib.suppress(OSError):
            if Path(base) != dest:
                Path(base).rmdir()  # only succeeds when empty
    return removed


def _copy_into(source: Path, dest: Path, rel: Path) -> None:
    """Copy ``source/rel`` to ``dest/rel`` without writing through a symlink; refuses to leave ``dest``."""
    real = dest.resolve()
    current = dest
    for part in rel.parent.parts:
        current = current / part
        if current.is_symlink():
            current.unlink()
        current.mkdir(exist_ok=True)
    target = dest / rel
    if target.is_symlink():
        target.unlink()
    if not target.parent.resolve().is_relative_to(real):
        raise OSError(f"refusing to write outside {dest}: {rel}")
    _write_atomic(source / rel, target)


def _write_atomic(src: Path, target: Path) -> None:
    """Copy ``src`` to ``target`` through a temp file in the same folder and ``os.replace``.

    The directory entry is replaced, so a hardlink or symlink at ``target`` is never written through.
    """
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=".sae-tmp-")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as handle:
            shutil.copyfileobj(handle, out)
        shutil.copystat(src, tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def junit_properties(junit: Path) -> dict[str, dict[str, str]]:
    """``{deferred_id: {property: value}}`` straight from the junit file (values unscrubbed, for results.json)."""
    out: dict[str, dict[str, str]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        props = {p.get("name", ""): p.get("value", "") for p in case.iter("property")}
        gate = props.get("deferred_id", "")
        if gate:
            out.setdefault(gate, {}).update(props)
    return out


# --- CLI ------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pipeline", description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--stages", default=",".join(ALL_STAGES), help="comma list of x3,x4,m0,x2,r (default all)")
    ap.add_argument("--from", dest="start", metavar="ID", help="start at this gate id (resumes the newest run)")
    ap.add_argument("--out", type=Path, help="output folder (default .sae-dev/pipeline/<timestamp>)")
    ap.add_argument("--rerun", action="store_true", help="re-run gates that already passed")
    ap.add_argument("--launch", action="store_true", help="stage the mod and start Stellaris before owner steps")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and preflight, run nothing")
    ap.add_argument("--max-saves", type=int, default=13, help="autosaves to copy (default 13)")
    ap.add_argument("--while-game", action="store_true",
                    help="run parse stages and copy saves even while Stellaris runs (default: wait for it to close)")
    ap.add_argument("--keep-runs", type=int, default=KEEP_RUNS,
                    help=f"keep raw save copies (private/) only for the newest N runs (default {KEEP_RUNS})")
    return ap


def _newest_run(dev: Path) -> Path | None:
    try:
        runs = sorted(p for p in (dev / "pipeline").iterdir() if p.is_dir())
    except OSError:
        return None
    return runs[-1] if runs else None


def _describe(gate: Gate, pf: Mapping[str, Any], found: set[str]) -> str:
    reason = skip_reason(gate, pf, found)
    if reason is not None:
        return f"skip: {reason}"
    note = " (owner plays)" if gate.owner_play else ""
    return f"run{note}{' (owner verdict)' if gate.judged else ''}"


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = subprocess.run,
    sleep: Sleep = time.sleep,
    now: Now = lambda: datetime.now(UTC),
    registry: Path | None = None,
    roots: Sequence[Path] = DEFAULT_ROOTS,
    repo: Path = _REPO,
    environ: Mapping[str, str] | None = None,
    input_fn: Callable[[], str] = input,
    isatty: Callable[[], bool] | None = None,
) -> int:
    """Run the pipeline; returns 0 (no gate failed), 1 (a gate failed), 2 (usage) or 130 (interrupted)."""
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    stages = {s.strip().lower() for s in args.stages.split(",") if s.strip()}
    unknown = sorted(stages - set(ALL_STAGES))
    if unknown or not stages:
        sys.stderr.write(f"pipeline: unknown stage(s) {unknown or args.stages!r}; use {','.join(ALL_STAGES)}\n")
        return 2
    if args.keep_runs < 1 or args.max_saves < 1:
        sys.stderr.write("pipeline: --keep-runs and --max-saves must be at least 1\n")
        return 2
    env = os.environ if environ is None else environ
    try:
        planned = plan(gate_registry.load_gates(registry), stages, args.start)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"pipeline: {exc}\n")
        return 2
    dev = dev_dir(repo)
    pf = preflight(runner, roots=roots, repo=repo, environ=env)
    found = markers(repo)
    if args.dry_run:
        print("preflight:\n" + default_scrub(json.dumps(pf, indent=2)))
        print("plan:")
        for g in planned:
            print(default_scrub(f"  {g.id:7} {g.stage:3} {g.priority:8} {_describe(g, pf, found)}  {g.title}"))
        return 0
    fresh = dev / "pipeline" / now().strftime("%Y%m%d-%H%M%S")
    out: Path = args.out or (_newest_run(dev) if args.start else None) or fresh
    run = _Run(
        repo=repo, dev=dev, out=out, pf=pf, runner=runner, sleep=sleep, now=now, max_saves=args.max_saves,
        launch=args.launch, rerun=args.rerun, found=found, input_fn=input_fn, environ=env,
        isatty=isatty or _stdin_isatty, while_game=args.while_game, platform=platform_label(),
    )
    run.state = _load_json(out / "state.json") or {"gates": {}, "started": _iso(now())}
    run.state.setdefault("gates", {})
    run.results = _load_json(out / "results.json") or {"parse": [], "budget_s": BUDGET_S, "impact": [], "run_dir": None}
    run.results.setdefault("budget_s", BUDGET_S)
    run.save_state()
    pruned = prune_runs(dev, args.keep_runs, out)
    if pruned:
        run.emit("info", f"pruned {len(pruned)} older private folder(s); keeping {args.keep_runs} runs")
    print(default_scrub(f"pipeline output: {out}"), flush=True)
    done_stages: set[str] = set()
    try:
        for gate in planned:
            if gate.stage in ("x3", "x4"):
                if gate.stage in done_stages:
                    continue
                done_stages.add(gate.stage)
                group = [g for g in planned if g.stage == gate.stage and _todo(run, g)]
                if group:
                    run.run_pytest_stage(gate.stage, group)
            elif _todo(run, gate):
                run.run_gate(gate)
    except KeyboardInterrupt:
        return run.interrupted()
    run.save_state()
    return 1 if run.failed else 0


def _stdin_isatty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _todo(run: _Run, gate: Gate) -> bool:
    """False when the gate already passed in this run folder and ``--rerun`` was not given."""
    if not run.rerun and run.state["gates"].get(gate.id) == "pass":
        print(f"[pass] {gate.id} already passed; skipped (use --rerun)", flush=True)
        return False
    return True


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
