"""``state.json``: atomic snapshot of :class:`~sae.core.state.EngineState` for ``--resume`` (C8 section 6)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from sae.core.state import EngineState

log = logging.getLogger(__name__)

__all__ = ["StateFileError", "load_state", "save_state"]


class StateFileError(Exception):
    """``state.json`` is missing, unreadable, corrupt or of an unsupported version."""


def save_state(path: Path, state: EngineState) -> None:
    """Write ``state`` atomically (``.tmp``, flush, fsync, then ``os.replace``)."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(state.model_dump_json())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(path: Path) -> EngineState:
    """Load a snapshot; raise :class:`StateFileError` with a clear reason on any problem."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise StateFileError(f"state file missing: {path}") from e
    except OSError as e:
        raise StateFileError(f"state file unreadable: {path}: {e}") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise StateFileError(f"state file corrupt (not JSON): {path}") from e
    if not isinstance(data, dict):
        raise StateFileError(f"state file corrupt (not an object): {path}")
    version = data.get("state_version")
    if version != 1:
        raise StateFileError(f"state file version mismatch: found {version!r}, expected 1")
    try:
        return EngineState.model_validate(data)
    except ValidationError as e:
        raise StateFileError(f"state file corrupt (invalid fields): {path}: {e.error_count()} errors") from e
