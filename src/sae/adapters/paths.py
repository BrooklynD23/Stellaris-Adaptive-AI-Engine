"""Path auto-detection (C8 section 3)."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sae.contracts.c8_config import PathsConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WINDLL = "windll"  # via getattr: absent from non-Windows typeshed
_STELLARIS = Path("Paradox Interactive") / "Stellaris"


@dataclass(frozen=True)
class ResolvedPaths:
    """Concrete filesystem locations. Proton is not auto-detected; set ``paths.user_dir``."""

    user_dir: Path
    game_log: Path
    save_dir: Path
    ini_path: Path
    parser_exe: Path | None


def windows_documents() -> Path:  # pragma: no cover - Windows only, mocked in tests
    """Shell Documents folder (follows OneDrive redirection; A-33 UNVERIFIED: M0-2 compares with the game's folder)."""
    import ctypes
    import uuid
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    g = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")  # FOLDERID_Documents
    guid = _GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))
    out = ctypes.c_wchar_p()
    shell32 = getattr(ctypes, _WINDLL).shell32
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        raise OSError("SHGetKnownFolderPath failed")
    try:
        return Path(out.value or "")
    finally:
        getattr(ctypes, _WINDLL).ole32.CoTaskMemFree(out)


def resolve_paths(
    cfg: PathsConfig,
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    documents: Callable[[], Path] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    repo_root: Path = _REPO_ROOT,
) -> ResolvedPaths:
    """Resolve C8 section 3 paths; explicit config values win."""
    if cfg.user_dir is not None:
        user = Path(cfg.user_dir)
    elif platform == "win32":
        user = (documents or windows_documents)() / _STELLARIS
    else:
        user = (home or Path.home()) / ".local" / "share" / _STELLARIS
    exe_name = "sae-parse.exe" if platform == "win32" else "sae-parse"
    if cfg.parser_exe is not None:
        exe: Path | None = Path(cfg.parser_exe)
    elif (found := which("sae-parse")) is not None:
        exe = Path(found)
    else:
        # C8 section 3: PATH, then the repo build (release, then debug: proposal 014-T2)
        builds = (repo_root / "parser" / "target" / kind / exe_name for kind in ("release", "debug"))
        exe = next((b for b in builds if b.is_file()), None)
    return ResolvedPaths(
        user_dir=user,
        game_log=Path(cfg.game_log) if cfg.game_log else user / "logs" / "game.log",
        save_dir=Path(cfg.save_dir) if cfg.save_dir else user / "save games",
        ini_path=Path(cfg.ini_path) if cfg.ini_path else user / "sae_in.ini",
        parser_exe=exe,
    )


def resolve_parser_exe(cfg: PathsConfig) -> str | None:
    """C8 section 3 (proposal 014-T2): ``paths.parser_exe``, else PATH, else the repo build (release, then debug;
    dev-only). The one resolver every command that starts the parser uses (adapters CLI, ``sae daemon`` wiring)."""
    exe = resolve_paths(cfg).parser_exe
    return None if exe is None else str(exe)
