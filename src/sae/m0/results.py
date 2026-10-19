"""M0 results store: one JSON file per check plus ``env.json``, rendered into ``m0_results.md``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sae.m0.checks import CHECKS, GO_RULE

ENV_FILE = "env.json"
SUMMARY_FILE = "m0_results.md"
STAGE_FILE = "M0-6.stage.json"
ENV_KEYS = ("game_version", "checksum", "starnet_build", "playset", "machine")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(results: Path, check_id: str) -> dict[str, Any] | None:
    """Return the stored result of ``check_id``, or None if never recorded."""
    path = results / f"{check_id}.json"
    if not path.is_file():
        return None
    return _read_json(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise ValueError(f"{path}: corrupt results file ({exc}); delete or fix it") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: corrupt results file (not a JSON object); delete or fix it")
    return data


def save_stage(results: Path, staged: dict[str, Any]) -> Path:
    """Remember what ``stage M0-6`` wrote (kept apart so restaging never overwrites a verdict)."""
    path = results / STAGE_FILE
    _write_json(path, {**staged, "at": _now()})
    return path


def load_stage(results: Path) -> dict[str, Any]:
    """The last ``stage M0-6`` values, or an empty dict."""
    path = results / STAGE_FILE
    return _read_json(path) if path.is_file() else {}


def save_auto(results: Path, check_id: str, status: str, evidence: dict[str, object], notes: list[str]) -> Path:
    """Store an automatic verdict; keeps any manual verdict already recorded."""
    old = load(results, check_id) or {}
    data = {**old, "check": check_id, "auto": {"status": status, "evidence": evidence, "notes": notes, "at": _now()}}
    path = results / f"{check_id}.json"
    _write_json(path, data)
    return path


def save_manual(results: Path, check_id: str, status: str, note: str, values: dict[str, str]) -> Path:
    """Store the maintainer's verdict; keeps any automatic evidence already recorded."""
    old = load(results, check_id) or {}
    data = {**old, "check": check_id, "manual": {"status": status, "note": note, "values": values, "at": _now()}}
    path = results / f"{check_id}.json"
    _write_json(path, data)
    return path


def save_env(results: Path, env: dict[str, str]) -> Path:
    """Store the session header (game version, checksum, StarNet build, playset, machine)."""
    path = results / ENV_FILE
    _write_json(path, {**env, "started": _now()})
    return path


def status_of(entry: dict[str, Any] | None) -> str:
    """Final status: the manual verdict wins over the automatic one; ``pending`` if neither exists."""
    if entry is None:
        return "pending"
    for key in ("manual", "auto"):
        if key in entry:
            return str(entry[key]["status"])
    return "pending"


def go_verdict(statuses: dict[str, str]) -> str:
    """Apply the integration plan's go/no-go rule to the recorded statuses."""
    passed = {k for k, v in statuses.items() if v == "pass"}
    if {"M0-3", "M0-6", "M0-11"} <= passed:
        channel = "channels GO: live dev mode is viable"
    elif statuses.get("M0-3") == "fail" and "M0-11" in passed:
        channel = "channels PARTIAL: save-only observation with between-session control"
    else:
        channel = "channels UNDECIDED: M0-3, M0-6 and M0-11 are not all passed"
    if passed & {"M0-7", "M0-8"}:
        knobs = "knobs GO: at least one knob moved the AI"
    elif statuses.get("M0-8") == "fail" and statuses.get("M0-7", "pending") in {"fail", "skip", "pending"}:
        knobs = "knobs NO-GO: levers too weak for live adaptation (defines / full plan override)"
    else:
        knobs = "knobs UNDECIDED: record M0-8 (and M0-7 if M0-1 passed)"
    return f"{channel}; {knobs}."


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_summary(results: Path) -> str:
    """Render ``m0_results.md`` from ``env.json`` and every check's result file."""
    env_path = results / ENV_FILE
    env: dict[str, Any] = _read_json(env_path) if env_path.is_file() else {}
    lines = ["# M0 results", ""]
    lines += [f"- {key}: {_cell(env.get(key, '(not recorded: sae m0 init)'))}" for key in ENV_KEYS]
    lines += ["", "| Check | Tier | Status | Assumptions | Auto notes | Manual note / values |"]
    lines += ["|---|---|---|---|---|---|"]
    statuses: dict[str, str] = {}
    for check in CHECKS:
        entry = load(results, check.id)
        statuses[check.id] = status_of(entry)
        auto = (entry or {}).get("auto", {})
        manual = (entry or {}).get("manual", {})
        manual_text = " ".join(filter(None, [manual.get("note", ""), json.dumps(manual.get("values", {}))]))
        row = [check.id, check.tier, statuses[check.id], ", ".join(check.assumptions) or "-",
               "; ".join(auto.get("notes", [])) or "-", manual_text if manual else "-"]
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    lines += ["", f"**Go/no-go:** {go_verdict(statuses)}", "", f"Rule: {GO_RULE}", ""]
    return "\n".join(lines)
