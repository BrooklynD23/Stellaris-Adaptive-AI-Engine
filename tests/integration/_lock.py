"""``uv.lock`` helpers: runtime dependency closure of the ``sae`` package."""

from __future__ import annotations

import tomllib
from typing import Any

from .conftest import REPO


def load_lock() -> dict[str, dict[str, Any]]:
    """Lock packages keyed by normalised name."""
    data = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    return {p["name"]: p for p in data["package"]}


def runtime_closure(lock: dict[str, dict[str, Any]]) -> set[str]:
    """Names reachable from ``sae``'s non-optional ``dependencies`` (no extras, no dev group)."""
    seen: set[str] = set()
    todo = [d["name"] for d in lock["sae"].get("dependencies", [])]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(d["name"] for d in lock[name].get("dependencies", []))
    return seen
