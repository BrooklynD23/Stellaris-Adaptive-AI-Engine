"""Snapshot derivation: everything the dashboard shows, computed from files on disk.

:func:`snapshot` is pure apart from reading files and the optional injected ``gh`` callable. Inputs that are files
(task board, gate index, ``m0_results/``, pipeline results and the legal bus) are cached by ``(path, signature)``
so an unchanged file is not re-parsed. Missing inputs give empty lists or ``None``, never invented numbers.

Only the legal ``bus.jsonl`` is ever read; ``bus.god.jsonl`` is never opened. Stellaris-facing facts (A-xx ids) are
shown as ``unverified`` until M0 proves them.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from gates import DEFAULT_PATH, Gate, load_gates

from devdash import events as ev
from devdash import tasks as task_board
from devdash.gateindex import GateResult, default_scrub, from_m0, load_index

__all__ = ["STALE_AFTER", "snapshot"]

_LOG = logging.getLogger(__name__)


STALE_AFTER: Final = timedelta(minutes=30)
"""A running item with no event for this long is shown stale."""
FEED_MAX: Final = 200
BELIEF_MAX: Final = 500
BUDGET_DEFAULT_S: Final = 3.0
_TONES: Final = frozenset({"ok", "info", "warn", "bad", "muted"})
_CLOSERS: Final = frozenset({"done", "pass", "fail", "skip"})
_OWNER_CLOSERS: Final = frozenset({"done", "pass", "fail"})
_STAGE_NAMES: Final[dict[str, str]] = {
    "x3": "X3 Harness and real saves",
    "x4": "X4 Extras",
    "m0": "M0 In-game proofs",
    "x2": "X2 Expansion",
    "r": "R Release readiness",
}
_BAD_IS_TRUE: Final = frozenset({"game_running", "mod_stale", "onedrive"})
_BAD_IS_FALSE: Final = frozenset({"windows_uv", "mod_staged"})

_CACHE: dict[tuple[str, Any], Any] = {}
_LOCK = threading.Lock()
_Loader = Callable[[], Any]


def _cached(path: Path, sig: Any, load: _Loader, *, immutable: bool = False) -> Any:
    """Return a copy of ``load()`` for ``path``, reusing the last result while ``sig`` is unchanged.

    The dict is guarded by a lock (the server may snapshot from several threads) and callers get deep copies, so
    mutating a snapshot never changes what the next one sees.
    """
    key = (str(path), sig)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key] if immutable else copy.deepcopy(_CACHE[key])
    value = load()
    with _LOCK:
        for old in [k for k in _CACHE if k[0] == key[0]]:
            del _CACHE[old]
        _CACHE[key] = value
    return value if immutable else copy.deepcopy(value)


def _file_sig(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _dir_sig(path: Path) -> tuple[tuple[str, int, int, int], ...]:
    out: list[tuple[str, int, int, int]] = []
    try:
        for child in sorted(path.glob("*.json")):
            sig = _file_sig(child)
            if sig is not None:
                out.append((child.name, sig[0], sig[1], sig[2]))
    except OSError:
        return ()
    return tuple(out)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _branch(repo: Path) -> str:
    """Read the branch from ``.git/HEAD`` (ref name, or short sha when detached); ``unknown`` if unreadable."""
    git = repo / ".git"
    try:
        if git.is_file():
            pointer = git.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                git = (repo / pointer.split(":", 1)[1].strip()).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    prefix = "ref: refs/heads/"
    if head.startswith(prefix):
        return head[len(prefix) :]
    if head.startswith("ref: "):
        return head[5:]
    return head[:7] or "unknown"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- env ----------------------------------------------------------------------------------------------------------


def _env_tone(key: str, value: Any) -> str:
    if value is None:
        return "muted"
    if isinstance(value, bool):
        if key in _BAD_IS_TRUE:
            return "warn" if value else "ok"
        if key in _BAD_IS_FALSE:
            return "ok" if value else "warn"
        return "ok" if value else "muted"
    return "info"


def _env_rows(raw: Any) -> list[dict[str, str]]:
    """Turn ``env.json`` into display rows. A ready-made ``env`` list wins; otherwise raw keys are rendered."""
    if not isinstance(raw, dict):
        return []
    listed = raw.get("env")
    if (
        isinstance(listed, list)
        and listed
        and all(isinstance(r, dict) and {"k", "v", "tone"} <= set(r) for r in listed)
    ):
        return [
            {
                "k": default_scrub(str(r["k"])),
                "v": default_scrub(str(r["v"])),
                "tone": r["tone"] if r["tone"] in _TONES else "muted",
            }
            for r in listed
        ]
    rows: list[dict[str, str]] = []
    for key, value in raw.items():
        if key == "env":
            continue
        text = "—" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
        rows.append({"k": default_scrub(str(key)), "v": default_scrub(text), "tone": _env_tone(str(key), value)})
    return rows


# --- events -------------------------------------------------------------------------------------------------------


class _Item:
    """One open running item."""

    def __init__(self, event: Mapping[str, Any], key: str) -> None:
        self.key = key
        self.source = str(event["source"])
        self.title = str(event["msg"])
        self.step = str(event["msg"])
        self.pct: int | None = event.get("pct")
        self.started = str(event["ts"])
        self.last = str(event["ts"])


def _key(event: Mapping[str, Any]) -> str:
    return str(event.get("ref") or event["source"])


def _derive(events: Sequence[Mapping[str, Any]], now: datetime) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    """Return ``(running, fresh)`` from ordered events; ``fresh`` marks keys with an open, non-stale ``start``."""
    open_items: dict[str, list[_Item]] = {}
    for event in events:
        key = _key(event)
        kind = event["kind"]
        if kind == "start":
            open_items.setdefault(key, []).append(_Item(event, key))
        elif kind in _CLOSERS:
            queue = open_items.get(key)
            if queue:
                queue.pop(0)
        elif open_items.get(key):
            # Any other event for a running key (owner included) proves it is alive.
            for item in open_items[key]:
                item.last = str(event["ts"])
            if kind in ("progress", "info"):
                newest = open_items[key][-1]
                newest.step = str(event["msg"])
                if event.get("pct") is not None:
                    newest.pct = event["pct"]
    running: list[dict[str, Any]] = []
    fresh: dict[str, bool] = {}
    for queue in open_items.values():
        for item in queue:
            stale = now - _parse_ts(item.last) > STALE_AFTER
            running.append(
                {
                    "key": item.key,
                    "title": item.title,
                    "source": item.source,
                    "step": item.step,
                    "pct": item.pct,
                    "started": item.started,
                    "last": item.last,
                    "stale": stale,
                }
            )
            if not stale:
                fresh[item.key] = True
    running.sort(key=lambda r: r["started"])
    return running, fresh


def _open_owners(events: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Map ref to its open owner event (no later done/pass/fail for the same ref)."""
    opened: dict[str, Mapping[str, Any]] = {}
    for event in events:
        key = _key(event)
        if event["kind"] == "owner":
            opened[key] = event
        elif event["kind"] in _OWNER_CLOSERS:
            opened.pop(key, None)
    return opened


# --- gates --------------------------------------------------------------------------------------------------------


def _load_m0(repo: Path) -> dict[str, GateResult]:
    folder = repo / "m0_results"
    return dict(_cached(folder, _dir_sig(folder), lambda: from_m0(folder, scrub=default_scrub)))


def _load_index(dev: Path) -> dict[str, GateResult]:
    path = dev / "gates.json"
    return dict(_cached(path, _file_sig(path), lambda: load_index(dev)))


_M0 = "uv run sae m0 --user-dir {user_dir}"


def _cmd(gate: Gate) -> str:
    """Return a display command for running a gate by hand. ``{name}`` placeholders are left for the reader to fill."""
    runner = gate.runner
    kind = runner.get("kind")
    if kind == "collect":
        return "uv run python3 tools/game/run_deferred.py --list"
    if kind == "pytest":
        return (
            f"uv run python3 tools/game/run_deferred.py --out .sae-dev/pipeline/<run> -- -k {runner.get('select', '')}"
        )
    if kind == "m0":
        steps = [" ".join([_M0, *pre]) for pre in runner.get("pre", [])]
        if runner.get("auto"):
            steps.append(" ".join([_M0, "check", gate.id, *runner.get("args", [])]))
            return " && ".join(steps)
        steps.append(f"uv run sae m0 steps {gate.id}")
        steps.append(f"uv run sae m0 record {gate.id} pass|fail --value k=v")
        return " && ".join(steps[:-1]) + f" then {steps[-1]}"
    if kind == "bench":
        return " ".join(["uv run sae bench", *runner.get("args", [])])
    return "see bench/README.md" if gate.stage == "r" else "manual"


def _detail(status: str, gate: Gate, result: GateResult | None) -> str:
    if status == "setup":
        return "setup needed: Windows uv"
    if status == "hardware":
        return f"needs other hardware ({gate.hardware})"
    if status == "later":
        return "later milestone"
    if status == "waiting":
        return f"waiting on {gate.pending_source}"
    if status == "owner":
        return gate.owner_instruction
    if status == "run":
        return "running"
    if result is not None and result["evidence"]:
        return "; ".join(f"{k}={v}" for k, v in sorted(result["evidence"].items()))
    return {"todo": "No data", "skip": "skipped"}.get(status, status)


def _gate_status(
    gate: Gate, owners: Mapping[str, Any], fresh: Mapping[str, bool], result: GateResult | None, windows_uv: bool | None
) -> str:
    if gate.id in owners:
        return "owner"
    if fresh.get(gate.id):
        return "run"
    # A ``pending`` result is "no verdict yet": it falls through to the static rules below (controller ruling).
    if result is not None and result["status"] != "pending":
        return result["status"]
    if gate.hardware != "one_pc":
        return "hardware"
    if gate.milestone_later:
        return "later"
    if gate.pending_source:
        return "waiting"
    if gate.windows_only and windows_uv is False:
        return "setup"
    return "todo"


def _registry() -> list[Gate]:
    """Load the gate registry, cached by file signature. An invalid registry gives ``[]`` and one stderr line."""
    path = DEFAULT_PATH

    def load() -> list[Gate]:
        try:
            return load_gates()
        except (ValueError, OSError) as exc:
            sys.stderr.write(f"devdash.state: gate registry unreadable: {exc}\n")
            return []

    return list(_cached(path, _file_sig(path), load, immutable=True))


def _gates(
    owners: Mapping[str, Any], fresh: Mapping[str, bool], repo: Path, dev: Path, windows_uv: bool | None
) -> tuple[list[dict[str, Any]], int, int]:
    index = _load_index(dev)
    m0 = _load_m0(repo)
    groups: dict[str, dict[str, Any]] = {}
    passed_total = results_total = 0
    for gate in _registry():
        result = m0.get(gate.id) or index.get(gate.id)
        status = _gate_status(gate, owners, fresh, result, windows_uv)
        group = groups.setdefault(
            gate.stage,
            {
                "stage": gate.stage,
                "name": _STAGE_NAMES.get(gate.stage, gate.stage),
                "passed": 0,
                "total": 0,
                "items": [],
            },
        )
        group["total"] += 1
        if status == "pass":
            group["passed"] += 1
            passed_total += 1
        if result is not None and result["status"] in ("pass", "fail"):
            results_total += 1
        group["items"].append(
            {
                "id": gate.id,
                "title": gate.title,
                "status": status,
                "detail": _detail(status, gate, result),
                "cmd": _cmd(gate),
                "when": result["at"] if result is not None else "",
                "judged": gate.judged,
                "question": gate.question,
                "evidence": [dict(e) for e in gate.evidence],
                "unverified": list(gate.assumptions),
            }
        )
    return list(groups.values()), passed_total, results_total


# --- results ------------------------------------------------------------------------------------------------------


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read_bus(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(belief, commitments)`` from a legal bus file; bad lines are skipped."""
    beliefs: list[tuple[int, dict[str, Any]]] = []
    chosen: dict[str, tuple[int, dict[str, Any]]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
                    continue
                raw_seq = row.get("seq")
                seq = raw_seq if isinstance(raw_seq, int) else 0
                date = default_scrub(str(row.get("game_date", "")))
                payload = row["payload"]
                if row.get("type") == "belief_update":
                    post = payload.get("posterior")
                    if isinstance(post, dict) and post and all(_num(v) for v in post.values()):
                        beliefs.append(
                            (seq, {"date": date, "p": {default_scrub(str(k)): float(v) for k, v in post.items()}})
                        )
                elif row.get("type") == "strategy_selected" and isinstance(row.get("decision_id"), str):
                    prior = chosen.get(row["decision_id"])
                    if prior is None or seq >= prior[0]:
                        chosen[row["decision_id"]] = (
                            seq,
                            {
                                "date": date,
                                "strategy": default_scrub(str(payload.get("strategy", ""))),
                                "reason": default_scrub(str(payload.get("reason", ""))),
                            },
                        )
    except OSError:
        return [], []
    beliefs.sort(key=lambda pair: pair[0])
    commits = sorted(chosen.values(), key=lambda pair: pair[0])
    return [b for _, b in beliefs[-BELIEF_MAX:]], [c for _, c in commits]


def _legal_bus(run_dir: Path, base: Path, repo: Path, dev: Path) -> Path | None:
    """Return the confined ``bus.jsonl`` of a run directory, or None when the path is unsafe or missing.

    ``run_dir`` is resolved (relative paths against ``base``) and must be an existing directory inside ``dev`` or
    ``repo``. The bus file must not be a symlink and must resolve to a file literally named ``bus.jsonl``, so a
    results.json can never steer the reader at ``bus.god.jsonl`` or at files elsewhere.
    """
    folder = (run_dir if run_dir.is_absolute() else base / run_dir).resolve()
    roots = (dev.resolve(), repo.resolve())
    if not folder.is_dir() or not any(folder.is_relative_to(root) for root in roots):
        return None
    bus = folder / "bus.jsonl"
    if bus.is_symlink() or bus.resolve().name != "bus.jsonl":
        return None
    return bus


def _is_parse_row(row: Any) -> bool:
    return isinstance(row, dict) and "label" in row and _num(row.get("wall_s"))


def _parse_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One parse-cost row for the Results view: label, wall seconds, and the optional RSS bytes, platform and source.

    The numbers come from the Linux ``sae-parse`` reading a WSL copy, not from the Windows budget machine, so the UI
    labels them UNVERIFIED A-24 (M0 would measure them on the game PC with the Windows build).
    """
    out: dict[str, Any] = {"label": default_scrub(str(row["label"])), "wall_s": float(row["wall_s"])}
    rss: Any = row.get("rss_bytes")
    if _num(rss) and float(rss) >= 0:
        out["rss_bytes"] = int(float(rss))
    for key in ("platform", "source"):
        if isinstance(row.get(key), str):
            out[key] = default_scrub(str(row[key]))[:64]
    return out


def _results(repo: Path, dev: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": None,
        "budget_s": BUDGET_DEFAULT_S,
        "parse": [],
        "belief": [],
        "commitments": [],
        "impact": [],
    }
    newest: tuple[int, Path] | None = None
    try:
        for path in (dev / "pipeline").glob("*/results.json"):
            sig = _file_sig(path)
            if sig is not None and (newest is None or sig[0] > newest[0]):
                newest = (sig[0], path)
    except OSError:
        return out
    if newest is None:
        return out
    path = newest[1]
    data = _cached(path, _file_sig(path), lambda: _read_json(path))
    if not isinstance(data, dict):
        return out
    out["source"] = path.parent.name
    if _num(data.get("budget_s")):
        out["budget_s"] = float(data["budget_s"])
    out["parse"] = [_parse_row(r) for r in data.get("parse", []) if _is_parse_row(r)]
    out["impact"] = [
        {"label": default_scrub(str(r["label"])), "value": default_scrub(str(r["value"])), "pct": r["pct"]}
        for r in data.get("impact", [])
        if isinstance(r, dict) and "label" in r and "value" in r and _num(r.get("pct"))
    ]
    run_dir = data.get("run_dir")
    if isinstance(run_dir, str) and run_dir:
        bus = _legal_bus(Path(run_dir), path.parent, repo, dev)
        if bus is not None:
            bus_sig = _file_sig(bus)
            if bus_sig is not None:
                out["belief"], out["commitments"] = _cached(bus, bus_sig, lambda: _read_bus(bus))
    return out


# --- gh -----------------------------------------------------------------------------------------------------------


def _call_gh(gh: Callable[[list[str]], str | None], args: list[str]) -> Any:
    try:
        text = gh(args)
    except Exception as exc:  # the injected callable may fail in any way; the dashboard must not
        _LOG.warning("gh call failed: %s", exc)
        return None
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _checks(rollup: Any) -> str:
    if not isinstance(rollup, list) or not rollup:
        return "none"
    verdicts = {
        str(r.get("conclusion") or r.get("state") or r.get("status") or "").upper()
        for r in rollup
        if isinstance(r, dict)
    }
    if verdicts & {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE"}:
        return "fail"
    if verdicts - {"SUCCESS", "NEUTRAL", "SKIPPED"}:
        return "pending"
    return "pass"


def _github(
    gh: Callable[[list[str]], str | None] | None, branch: str
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    if gh is None:
        return [], None
    prs: list[dict[str, Any]] = []
    raw = _call_gh(gh, ["pr", "list", "--state", "open", "--json", "number,title,isDraft,statusCheckRollup"])
    if isinstance(raw, list):
        prs = [
            {
                "number": r["number"],
                "title": str(r.get("title", "")),
                "isDraft": bool(r.get("isDraft")),
                "checks": _checks(r.get("statusCheckRollup")),
            }
            for r in raw
            if isinstance(r, dict) and isinstance(r.get("number"), int)
        ]
    ci: dict[str, str] | None = None
    runs = _call_gh(gh, ["run", "list", "--branch", branch, "--limit", "1", "--json", "status,conclusion,url"])
    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
        run = runs[0]
        status = str(run.get("conclusion") or run.get("status") or "")
        if status:
            ci = {"status": status, "url": str(run.get("url", ""))}
    return prs, ci


# --- snapshot -----------------------------------------------------------------------------------------------------


def _ci_tone(status: str) -> str:
    if status == "success":
        return "ok"
    if status in ("failure", "timed_out", "startup_failure", "cancelled"):
        return "bad"
    return "info"


def snapshot(
    repo: Path,
    dev: Path,
    *,
    now: datetime,
    gh: Callable[[list[str]], str | None] | None = None,
    events: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the full dashboard state as a JSON-serialisable dict.

    ``events`` lets the server pass its bounded tail; when ``None`` both event logs under ``dev`` are read in full.
    ``gh`` runs the GitHub CLI with the given argv and returns stdout or ``None``; any failure means no PRs and no CI.
    """
    invalid = 0
    if events is None:
        events, invalid = ev.read_events([ev.events_path(dev, "wsl"), ev.events_path(dev, "win")])
    ordered = sorted(events, key=lambda e: str(e["ts"]))
    branch = _branch(repo)
    env_raw = _read_json(dev / "env.json")
    uv = env_raw.get("windows_uv") if isinstance(env_raw, dict) else None
    windows_uv = uv if isinstance(uv, bool) else None

    running, fresh = _derive(ordered, now)
    owners = _open_owners(ordered)
    banner: dict[str, Any] | None = None
    if owners:
        newest = max(owners.values(), key=lambda e: str(e["ts"]))
        ref = _key(newest)
        gate = next((g for g in _registry() if g.id == ref), None)
        banner = {
            "ref": ref,
            "msg": str(newest["msg"]),
            "judged": gate.judged if gate else False,
            "question": gate.question if gate else "",
            "evidence": [dict(e) for e in gate.evidence] if gate else [],
            "since": str(newest["ts"]),
        }
    gate_groups, passed, with_result = _gates(owners, fresh, repo, dev, windows_uv)
    total_gates = sum(g["total"] for g in gate_groups)

    board = repo / "docs" / "dev" / "tasks.json"

    def _load_board() -> list[dict[str, Any]]:
        try:
            return task_board.load_tasks(board)
        except (ValueError, OSError) as exc:
            _LOG.warning("task board unreadable: %s", exc)
            return []

    items = _cached(board, _file_sig(board), _load_board)
    tasks: dict[str, list[dict[str, Any]]] = {s: [] for s in task_board.STATUSES}
    for task in items:
        tasks[task["status"]].append(dict(task))

    prs, ci = _github(gh, branch)
    ci_value = ci["status"] if ci else "—"
    kpis = [
        {
            "label": "Done",
            "value": str(len(tasks["done"])),
            "sub": f"of {len(items)} tasks" if items else "No tasks",
            "chip": "tasks",
            "tone": "ok" if tasks["done"] else "muted",
        },
        {
            "label": "In progress",
            "value": str(len(tasks["in_progress"])),
            "sub": f"{len(running)} running now" if running else "Nothing running",
            "chip": "tasks",
            "tone": "info" if tasks["in_progress"] else "muted",
        },
        {
            "label": "Pending",
            "value": str(len(tasks["pending"])),
            "sub": "Not started",
            "chip": "tasks",
            "tone": "muted",
        },
        {
            "label": "Blocked",
            "value": str(len(tasks["blocked"])),
            "sub": "Waiting on something" if tasks["blocked"] else "Nothing blocked",
            "chip": "tasks",
            "tone": "bad" if tasks["blocked"] else "muted",
        },
        {
            "label": "Gates passed",
            "value": f"{passed}/{total_gates}" if with_result else "—",
            "sub": f"{with_result} with a result" if with_result else "No data",
            "chip": "gates",
            "tone": "ok" if with_result and passed == total_gates else "info" if with_result else "muted",
        },
        {
            "label": "CI",
            "value": ci_value,
            "sub": branch if ci else "No data",
            "chip": "github",
            "tone": _ci_tone(ci_value) if ci else "muted",
        },
    ]
    return {
        "generated": _stamp(now),
        "branch": branch,
        "enabled": ev.is_enabled(dev),
        "invalid_lines": invalid,
        "env": _env_rows(env_raw),
        "owner": banner,
        "kpis": kpis,
        "running": running,
        "feed": [
            {"ts": e["ts"], "source": e["source"], "kind": e["kind"], "ref": e.get("ref", ""), "msg": e["msg"]}
            for e in reversed(ordered[-FEED_MAX:])
        ],
        "tasks": tasks,
        "prs": prs,
        "gates": gate_groups,
        "results": _results(repo, dev),
        "ci": ci,
    }
