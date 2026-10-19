"""Bus readers and checkers for the e2e harness (T7 plan section 1).

Pure functions over ``bus.jsonl`` events as plain dicts. Two different sequence numbers exist and are never
mixed: the envelope ``seq`` (one per bus line) and the batch ``payload.batch.seq`` / ``payload.seq`` (C2/C3).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

Event = dict[str, Any]
Switch = tuple[int, str]

#: Event types left out of the determinism digest (wall-clock or run-specific by design).
_DIGEST_SKIP_TYPES = frozenset({"health", "run_started"})
_DIGEST_DROP_KEYS = ("run_id", "t_wall", "t_mono_ns", "seq")


def load(run_dir: Path, *, god: bool = False) -> list[Event]:
    """Read ``bus.jsonl`` (or ``bus.god.jsonl``) of a run. A final line without LF is ignored (C5 section 1)."""
    text = (run_dir / ("bus.god.jsonl" if god else "bus.jsonl")).read_text(encoding="utf-8")
    lines = text.split("\n")
    lines.pop()  # text after the last LF: empty, or a partial line a reader must ignore
    return [json.loads(ln) for ln in lines if ln]


def month_index(game_date: str) -> int:
    """``YYYY.MM.DD`` -> months since year 0."""
    y, m, _ = game_date.split(".")
    return int(y) * 12 + int(m) - 1


def _of(events: Iterable[Event], kind: str) -> list[Event]:
    return [e for e in events if e["type"] == kind]


def check_chain(
    events: Sequence[Event], *, min_decisions: int = 12, require_applied: bool = True
) -> list[str]:
    """Verify the decision chain (plan section 1). Returns problems; an empty list means clean.

    Includes non-vacuity: at least one emitted batch, ``min_decisions`` decisions and (unless
    ``require_applied`` is False, for LOST-only runs) one APPLIED ack.
    """
    bad: list[str] = []
    seen: dict[tuple[str, str], list[int]] = {}
    for e in events:
        if e.get("decision_id") is not None:
            seen.setdefault((e["type"], e["decision_id"]), []).append(e["seq"])

    def before(kind: str, d: str, seq: int) -> bool:
        return any(s < seq for s in seen.get((kind, d), []))

    selected = _of(events, "strategy_selected")
    for s in selected:
        for need in ("observation", "belief_update"):
            if (need, s["decision_id"]) not in seen:
                bad.append(f"strategy_selected {s['decision_id']} (seq {s['seq']}) has no {need}")
    emitted = _of(events, "intent_emitted")
    acks = _of(events, "intent_ack")
    for em in emitted:
        d, batch = em["decision_id"], em["payload"]["batch"]
        if not em["payload"]["receipt"]["ok"]:
            continue
        for need in ("observation", "belief_update", "strategy_selected"):
            if not before(need, d, em["seq"]):
                bad.append(f"intent_emitted batch {batch['seq']} ({d}): no {need} before it")
        countries = {i["country"] for i in batch["intents"]}
        mine = [a for a in acks if a["payload"]["seq"] == batch["seq"] and a["payload"]["country"] in countries]
        if not any(a["seq"] > em["seq"] for a in mine):
            kind = "ack before emit" if mine else "no ack"
            bad.append(f"intent_emitted batch {batch['seq']} ({d}, envelope seq {em['seq']}): {kind}")
    for a in acks:
        p = a["payload"]
        owners = [
            em
            for em in emitted
            if em["payload"]["batch"]["seq"] == p["seq"]
            and p["country"] in {i["country"] for i in em["payload"]["batch"]["intents"]}
        ]
        if not owners:
            bad.append(f"orphan intent_ack (envelope seq {a['seq']}, batch seq {p['seq']}, country {p['country']})")
    if not emitted:
        bad.append("non-vacuity: no intent_emitted")
    if len(selected) < min_decisions:
        bad.append(f"non-vacuity: {len(selected)} decisions < {min_decisions}")
    if require_applied and not any(a["payload"]["status"] == "APPLIED" for a in acks):
        bad.append("non-vacuity: no APPLIED intent_ack")
    return bad


def switches(events: Iterable[Event]) -> list[Switch]:
    """``(month_index, strategy)`` of every ``strategy_selected`` with ``switched=true``, in bus order."""
    return [
        (month_index(e["game_date"]), e["payload"]["strategy"])
        for e in events
        if e["type"] == "strategy_selected" and e["payload"]["switched"]
    ]


def final_strategy(events: Iterable[Event]) -> str:
    """Strategy of the last ``strategy_selected``. Raises ``ValueError`` when there is none."""
    last = [e["payload"]["strategy"] for e in events if e["type"] == "strategy_selected"]
    if not last:
        raise ValueError("no strategy_selected on the bus")
    return str(last[-1])


def ci1_matched(a: Sequence[Switch], b: Sequence[Switch], window: int) -> int:
    """One-to-one greedy (by time) matches: same strategy and ``|delta month| < window``."""
    free = sorted(b)
    matched = 0
    for month, strategy in sorted(a):
        for k, (bm, bs) in enumerate(free):
            if bs == strategy and abs(bm - month) < window:
                del free[k]
                matched += 1
                break
    return matched


def ci1_rate(a: Sequence[Switch], b: Sequence[Switch], window: int) -> float:
    """``2 * matched / (|a| + |b|)``; 1.0 when both are empty (callers assert non-vacuity separately)."""
    total = len(a) + len(b)
    return 1.0 if total == 0 else 2 * ci1_matched(a, b, window) / total


def digest(events: Iterable[Event]) -> str:
    """Determinism digest: sha256 over non-health, non-run_started events minus wall/run fields and ``receipt.path``."""
    h = hashlib.sha256()
    for e in events:
        if e["type"] in _DIGEST_SKIP_TYPES:
            continue
        slim = {k: v for k, v in e.items() if k not in _DIGEST_DROP_KEYS}
        receipt = slim.get("payload", {}).get("receipt")
        if isinstance(receipt, dict):
            slim["payload"] = {**slim["payload"], "receipt": {k: v for k, v in receipt.items() if k != "path"}}
        h.update(json.dumps(slim, sort_keys=True, separators=(",", ":")).encode())
        h.update(b"\n")
    return str(h.hexdigest())
