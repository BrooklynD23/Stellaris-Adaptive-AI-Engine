"""Export JSON Schemas for every registered contract type.

Usage: ``uv run python -m sae.contracts.export [OUT_DIR]`` (default ``contracts/schemas``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sae.contracts.registry import MODELS


def render_schemas() -> dict[str, str]:
    """Return ``{file name: JSON text}`` for every registered type (stable key order)."""
    return {
        f"{name}.schema.json": json.dumps(adapter.json_schema(mode="validation"), indent=2, sort_keys=True) + "\n"
        for name, adapter in sorted(MODELS.items())
    }


def main(argv: list[str] | None = None) -> int:
    """Write the schemas; return 0."""
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0] if args else "contracts/schemas")
    out.mkdir(parents=True, exist_ok=True)
    for name, text in render_schemas().items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(MODELS)} schemas to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
