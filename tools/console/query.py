"""Offline console: run SQL over ``<run_dir>/bus.jsonl`` with in-memory DuckDB (legal bus only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

DEFAULT_SQL = "SELECT * FROM v_decisions ORDER BY decision_id"
VIEWS = Path(__file__).with_name("views.sql")


def load_views(conn: Any, bus: Path) -> None:
    """Create the views for ``bus``; the path is substituted with single quotes escaped."""
    script = VIEWS.read_text(encoding="utf-8").replace("{{BUS}}", str(bus).replace("'", "''"))
    for stmt in script.split(";\n"):
        if stmt.strip():
            conn.execute(stmt)


def format_table(cols: list[str], rows: list[tuple[Any, ...]]) -> str:
    """Render rows as a plain aligned table."""
    cells = [cols] + [["" if v is None else str(v) for v in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in cells)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("sql", nargs="?", default=DEFAULT_SQL)
    args = ap.parse_args(argv)
    try:
        import duckdb
    except ImportError:
        print("install the console extra: uv sync --extra console", file=sys.stderr)
        return 1
    if any(c in str(args.run_dir) for c in "*?[{"):
        print("glob characters in run_dir", file=sys.stderr)
        return 2
    bus = args.run_dir / "bus.jsonl"
    if not bus.is_file():
        print(f"no bus.jsonl in {args.run_dir}", file=sys.stderr)
        return 2
    conn = duckdb.connect(":memory:")
    try:
        load_views(conn, bus)
        cur = conn.execute(args.sql)
        cols = [d[0] for d in cur.description]
        print(format_table(cols, cur.fetchall()))
    except duckdb.IOException as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 1
    except duckdb.Error as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
