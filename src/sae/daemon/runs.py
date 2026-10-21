"""Run directory helpers (C8 section 6): CURRENT lock, resolved config, re-exec (C7 section 4.6)."""

from __future__ import annotations

import hashlib
import itertools
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psutil

from sae.contracts.c8_config import CURRENT_RUN_FILE, RESOLVED_FILE, SaeConfig

log = logging.getLogger(__name__)

__all__ = ["RunLockError", "acquire_current", "config_sha256", "make_reexec", "reexec_argv", "release_current",
           "write_resolved"]


class RunLockError(Exception):
    """Another live ``sae`` daemon owns the runs directory."""


def _read_current(path: Path) -> tuple[str, int] | None:
    """Parse ``"<run_id> <pid>"``; ``None`` if missing or malformed (treated as stale)."""
    try:
        run_id, pid = path.read_text(encoding="utf-8").split()
        return run_id, int(pid)
    except (OSError, ValueError):
        return None


def _is_sae_argv(argv: list[str]) -> bool:
    """True for a real ``sae`` invocation: an argv token whose basename is sae/sae.exe, or ``-m sae``."""
    names = {"sae", "sae.exe"}
    if any(os.path.basename(a.replace("\\", "/")) in names for a in argv):
        return True
    return any(a == "-m" and b == "sae" for a, b in itertools.pairwise(argv))


def _is_sae_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and _is_sae_argv(proc.cmdline())
    except (psutil.Error, OSError):
        return False


_EMPTY_GRACE_S = 5.0  # an empty CURRENT younger than this is a starter mid-create, i.e. live


def _create_exclusive(path: Path, text: str) -> bool:
    """Create ``path`` holding ``text`` atomically and exclusively; False if it already exists.

    Writes a temp file then ``os.link`` (content is complete the instant CURRENT appears). Without hard
    links, falls back to ``O_EXCL`` create + write + fsync (readers treat a young empty file as live).
    """
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    except (AttributeError, NotImplementedError, OSError):
        log.warning("os.link unavailable; falling back to O_EXCL create")
    finally:
        tmp.unlink(missing_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    return True


def _is_live_lock(path: Path, run_id: str, resume: bool) -> bool | None:
    """True if ``path`` blocks us, False if stale, ``None`` if it vanished meanwhile."""
    cur = _read_current(path)
    if cur is None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return st.st_size == 0 and time.time() - st.st_mtime < _EMPTY_GRACE_S
    other_id, pid = cur
    own = pid in (os.getpid(), os.getppid())
    if resume and other_id == run_id and own:
        return False
    return _is_sae_alive(pid) and pid != os.getpid()


def acquire_current(runs_dir: Path, run_id: str, resume: bool) -> None:
    """Take ``CURRENT`` exclusively or raise :class:`RunLockError`.

    Create is ``link``/``O_EXCL``; a stale holder (dead or non-sae pid) is renamed aside to
    ``CURRENT.stale.<pid>`` (the loser of that race raises) and the create is retried once. With
    ``resume``, a CURRENT naming the same ``run_id`` whose pid is this process or its parent is accepted.
    """
    path = runs_dir / CURRENT_RUN_FILE
    runs_dir.mkdir(parents=True, exist_ok=True)
    text = f"{run_id} {os.getpid()}"
    for _attempt in range(2):
        if _create_exclusive(path, text):
            return
        live = _is_live_lock(path, run_id, resume)
        if live:
            cur = _read_current(path)
            raise RunLockError(f"another sae daemon is running: {cur[0]} pid {cur[1]}" if cur
                               else "another sae daemon is starting in this runs directory")
        if live is None:
            continue
        stale = path.with_name(f"{path.name}.stale.{os.getpid()}")
        try:
            os.rename(path, stale)
        except OSError as e:
            raise RunLockError("another sae daemon just claimed the runs directory") from e
        stale.unlink(missing_ok=True)
    raise RunLockError("another sae daemon just claimed the runs directory")


def release_current(runs_dir: Path, run_id: str) -> None:
    """Remove ``CURRENT`` only if it names this run and this process (a Windows child keeps its own)."""
    path = runs_dir / CURRENT_RUN_FILE
    cur = _read_current(path)
    if cur == (run_id, os.getpid()):
        path.unlink(missing_ok=True)


def write_resolved(run_dir: Path, cfg: SaeConfig) -> Path:
    """Write ``config.resolved.json`` and return its path."""
    path = run_dir / RESOLVED_FILE
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
    return path


def config_sha256(cfg: SaeConfig) -> str:
    """SHA-256 hex of the canonical resolved config JSON."""
    return hashlib.sha256(cfg.model_dump_json().encode("utf-8")).hexdigest()


def reexec_argv(run_id: str, runs_dir: Path) -> list[str]:
    """Argv of the resumed daemon (C7 section 4.6)."""
    return [sys.executable, "-m", "sae", "daemon", "--resume", run_id, "--runs-dir", str(runs_dir)]


def make_reexec(run_id: str, runs_dir: Path) -> Callable[[], None]:
    """Build the re-exec callable: POSIX ``os.execv``; Windows spawn a child, record its pid, return."""
    argv = reexec_argv(run_id, runs_dir)

    def _reexec() -> None:
        log.info("re-exec: %s", argv)
        if sys.platform.startswith("win"):
            # Windows branch: not executed in CI on this machine (mock-tested only).
            child = subprocess.Popen(argv)
            (runs_dir / CURRENT_RUN_FILE).write_text(f"{run_id} {child.pid}", encoding="utf-8")
            return
        os.execv(argv[0], argv)

    return _reexec
