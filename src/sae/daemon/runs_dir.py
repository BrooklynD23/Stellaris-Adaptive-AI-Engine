"""Default runs directory (C8 section 3)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def default_runs_dir(environ: Mapping[str, str], platform: str) -> Path:
    """Windows ``%LOCALAPPDATA%\\sae\\runs``; else ``${XDG_STATE_HOME:-~/.local/state}/sae/runs``."""
    if platform.startswith("win"):
        local = environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "sae" / "runs"
    xdg = environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "sae" / "runs"


__all__ = ["default_runs_dir"]
