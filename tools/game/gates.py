"""Game-gate registry: the single list of every game-PC gate (X3, X4, M0, X2 and R).

The dev dashboard's Game gates view and the game-test pipeline both read ``gates.json`` through this module.
Stdlib only, so it runs under bare ``python3``. Every Stellaris behaviour a gate depends on stays UNVERIFIED until
the gate itself passes on the game PC; the ``assumptions`` field carries the A-xx ids.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent / "gates.json"

STAGES = ("x3", "x4", "m0", "x2", "r")
HARDWARE = ("one_pc", "second_machine", "laptop_4core")
PRIORITIES = ("critical", "core", "stretch", "later", "backlog")
RUNNER_KINDS = ("collect", "pytest", "m0", "bench", "none")

_ASSUMPTION = re.compile(r"^A-\d{2}$")
_INPUT = re.compile(r"^SAE_GAME_[A-Z0-9_]+$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_FIELDS = (
    "id", "stage", "title", "runner", "inputs", "owner_play", "owner_instruction", "judged", "question",
    "evidence", "assumptions", "hardware", "windows_only", "pending_source", "priority", "milestone_later",
    "deferred_id",
)


@dataclass(frozen=True)
class Gate:
    """One game-PC gate. Field meanings are fixed by the dev-dashboard plan (task 3)."""

    id: str
    stage: str
    title: str
    runner: Mapping[str, Any]
    inputs: tuple[str, ...]
    owner_play: bool
    owner_instruction: str
    judged: bool
    question: str
    evidence: tuple[Mapping[str, str], ...]
    assumptions: tuple[str, ...]
    hardware: str
    windows_only: bool
    pending_source: str | None
    priority: str
    milestone_later: bool
    deferred_id: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the gate as a JSON-ready dict (inverse of :func:`load_gates` for one row)."""
        return {
            "id": self.id, "stage": self.stage, "title": self.title, "runner": _thaw(self.runner),
            "inputs": list(self.inputs), "owner_play": self.owner_play, "owner_instruction": self.owner_instruction,
            "judged": self.judged, "question": self.question, "evidence": _thaw(self.evidence),
            "assumptions": list(self.assumptions), "hardware": self.hardware, "windows_only": self.windows_only,
            "pending_source": self.pending_source, "priority": self.priority,
            "milestone_later": self.milestone_later, "deferred_id": self.deferred_id,
        }


def _freeze(value: Any) -> Any:
    """Recursively turn dicts into read-only mappings and lists into tuples."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """Inverse of :func:`_freeze`, for JSON output."""
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(v) for v in value]
    return value


def _fail(gid: str, msg: str) -> ValueError:
    return ValueError(f"gate {gid}: {msg}")


def _str(gid: str, row: Mapping[str, Any], key: str, *, nullable: bool = False) -> Any:
    value = row[key]
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(gid, f"{key} must be a non-empty string")
    return value


def _bool(gid: str, row: Mapping[str, Any], key: str) -> bool:
    value = row[key]
    if not isinstance(value, bool):
        raise _fail(gid, f"{key} must be a boolean")
    return value


def _strs(gid: str, row: Mapping[str, Any], key: str, pattern: re.Pattern[str] | None = None) -> tuple[str, ...]:
    value = row[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _fail(gid, f"{key} must be a list of strings")
    for item in value:
        if pattern is not None and not pattern.match(item):
            raise _fail(gid, f"{key} entry {item!r} is malformed")
    return tuple(value)


def _argvs(gid: str, runner: Mapping[str, Any], key: str) -> None:
    value = runner.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv) for argv in value
    ):
        raise _fail(gid, f"runner.{key} must be a list of non-empty argv lists")


def _check_runner(gid: str, runner: Any) -> Mapping[str, Any]:
    if not isinstance(runner, dict) or runner.get("kind") not in RUNNER_KINDS:
        raise _fail(gid, f"runner.kind must be one of {RUNNER_KINDS}")
    kind = runner["kind"]
    if kind == "pytest" and not (isinstance(runner.get("select"), str) and runner["select"]):
        raise _fail(gid, "pytest runner needs a non-empty select")
    if kind == "m0":
        if not isinstance(runner.get("auto"), bool):
            raise _fail(gid, "m0 runner needs a boolean auto")
        _argvs(gid, runner, "pre")
        args = runner.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise _fail(gid, "m0 runner.args must be a list of strings")
    if kind == "bench":
        args = runner.get("args")
        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
            raise _fail(gid, "bench runner needs a non-empty args list")
    return runner


def _build(row: Any) -> Gate:
    if not isinstance(row, dict):
        raise ValueError("gate row must be an object")
    gid = row.get("id")
    if not isinstance(gid, str) or not gid:
        raise ValueError(f"gate row without a string id: {row!r}")
    missing = [f for f in _FIELDS if f not in row]
    extra = sorted(set(row) - set(_FIELDS))
    if missing or extra:
        raise _fail(gid, f"missing fields {missing}, unknown fields {extra}")
    for key, allowed in (("stage", STAGES), ("hardware", HARDWARE), ("priority", PRIORITIES)):
        if row[key] not in allowed:
            raise _fail(gid, f"{key} {row[key]!r} not in {allowed}")
    evidence = row["evidence"]
    if not isinstance(evidence, list) or not all(
        isinstance(e, dict) and set(e) == {"key", "label"} and all(isinstance(v, str) and v for v in e.values())
        and _KEY.match(e["key"])
        for e in evidence
    ):
        raise _fail(gid, "evidence must be a list of {key (snake_case), label} objects")
    if len({e["key"] for e in evidence}) != len(evidence):
        raise _fail(gid, "evidence keys must be unique")
    judged = _bool(gid, row, "judged")
    question = row["question"]
    if not isinstance(question, str) or (judged and not question.strip()):
        raise _fail(gid, "judged gates need a question")
    return Gate(
        id=gid, stage=row["stage"], title=_str(gid, row, "title"), runner=_freeze(_check_runner(gid, row["runner"])),
        inputs=_strs(gid, row, "inputs", _INPUT), owner_play=_bool(gid, row, "owner_play"),
        owner_instruction=_str(gid, row, "owner_instruction"), judged=judged, question=question,
        evidence=_freeze(evidence), assumptions=_strs(gid, row, "assumptions", _ASSUMPTION),
        hardware=row["hardware"], windows_only=_bool(gid, row, "windows_only"),
        pending_source=_str(gid, row, "pending_source", nullable=True), priority=row["priority"],
        milestone_later=_bool(gid, row, "milestone_later"),
        deferred_id=_str(gid, row, "deferred_id", nullable=True),
    )


def load_gates(path: Path | None = None) -> list[Gate]:
    """Load and validate the registry (default ``gates.json`` beside this file), in pipeline order.

    Raises ``ValueError`` naming the offending gate id for a bad field, a duplicate id or a malformed runner.
    """
    source = path or DEFAULT_PATH
    rows = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{source}: registry must be a list")
    built: list[Gate] = []
    seen: set[str] = set()
    for row in rows:
        gate = _build(row)
        if gate.id in seen:
            raise _fail(gate.id, "duplicate id")
        seen.add(gate.id)
        built.append(gate)
    return built


def by_id(gs: Sequence[Gate]) -> dict[str, Gate]:
    """Index gates by id, keeping order."""
    return {g.id: g for g in gs}
