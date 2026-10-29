"""Gate index: newest real result per game gate, from pytest junit files and ``m0_results/`` JSON.

``from_junit`` and ``from_m0`` each return ``{gate_id: GateResult}``. ``merge_newest`` combines two such maps and
``update_index`` persists the merge to ``<dev>/gates.json``. ``m0_results/`` is never stored in the index; the
snapshot reads it directly through ``from_m0`` so manual ``sae m0 record`` runs always show.

Rules: ``at`` values are RFC3339 UTC strings compared as parsed datetimes; a ``skip`` never replaces a ``pass`` or
``fail`` (a run without game inputs must not wipe a real result), even when the skip is newer.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from sae.m0 import results as m0_results

__all__ = ["GateResult", "default_scrub", "from_junit", "from_m0", "load_index", "merge_newest", "update_index"]

_LOG = logging.getLogger(__name__)
_EVIDENCE_MAX = 500
_STATUSES = frozenset({"pass", "fail", "skip", "pending"})
_REAL = frozenset({"pass", "fail"})
_EPOCH = datetime.min.replace(tzinfo=UTC)
_M0_SKIP_FILES = frozenset({m0_results.ENV_FILE})
_STAGE_SUFFIX = ".stage.json"
INDEX_NAME = "gates.json"


class GateResult(TypedDict):
    """One gate's newest result. ``status`` is ``pass``, ``fail``, ``skip`` or ``pending``."""

    status: str
    evidence: dict[str, str]
    source: str
    run: str
    at: str


def _when(text: str) -> datetime:
    """Parse an RFC3339 timestamp to an aware UTC datetime; unparseable values sort oldest."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return _EPOCH
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def default_scrub(text: str) -> str:
    """Replace the home directory (and its forward-slash form) with ``<HOME>``."""
    home = str(Path.home())
    for form in sorted({home, home.replace("\\", "/")}, key=len, reverse=True):
        if form and form != "/":
            text = text.replace(form, "<HOME>")
    return text


def _identity(text: str) -> str:
    return text


def _clip(text: str) -> str:
    return " ".join(text.split())[:_EVIDENCE_MAX]


def _case_outcome(case: ET.Element) -> tuple[str, str]:
    """Return ``(status, message)`` for one junit testcase."""
    for tag, status in (("failure", "fail"), ("error", "fail"), ("skipped", "skip")):
        el = case.find(tag)
        if el is not None:
            return status, _clip(el.get("message") or el.text or "")
    return "pass", ""


def from_junit(
    junit: Path, run: str, at: str, scrub: Callable[[str], str] = _identity
) -> dict[str, GateResult]:
    """Fold a pytest junit file into one result per ``deferred_id``.

    Several testcases of one gate: any fail gives fail, else any pass gives pass, else skip. Evidence holds the
    other ``<property>`` entries plus a ``message`` (skip reason or failure text, at most 500 characters).
    Testcases without a ``deferred_id`` property are ignored. ``scrub`` is applied to every evidence value
    and to ``run``.
    """
    groups: dict[str, list[tuple[str, str, dict[str, str]]]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        props = {p.get("name", ""): p.get("value", "") for p in case.iter("property")}
        gate = props.pop("deferred_id", "")
        if not gate:
            continue
        status, message = _case_outcome(case)
        groups.setdefault(gate, []).append((status, message, props))
    out: dict[str, GateResult] = {}
    for gate, cases in groups.items():
        statuses = {c[0] for c in cases}
        status = "fail" if "fail" in statuses else "pass" if "pass" in statuses else "skip"
        evidence: dict[str, str] = {}
        for _, _, props in cases:
            evidence.update(props)
        message = next((m for s, m, _ in cases if s == status and m), "")
        if message:
            evidence["message"] = message
        evidence = {k: scrub(v) for k, v in evidence.items()}
        out[gate] = GateResult(status=status, evidence=evidence, source="junit", run=scrub(run), at=at)
    return out


def _m0_result(entry: Mapping[str, Any], results: Path, scrub: Callable[[str], str]) -> GateResult:
    manual = entry.get("manual")
    auto = entry.get("auto")
    stamps = [part["at"] for part in (manual, auto) if isinstance(part, dict) and isinstance(part.get("at"), str)]
    at = max(stamps, key=_when) if stamps else ""
    evidence: dict[str, str] = {}
    if isinstance(manual, dict):
        values = manual.get("values")
        if isinstance(values, dict):
            evidence.update({str(k): str(v) for k, v in values.items()})
        if manual.get("note"):
            evidence["note"] = str(manual["note"])
    elif isinstance(auto, dict) and isinstance(auto.get("evidence"), dict):
        evidence.update({str(k): str(v) for k, v in auto["evidence"].items()})
    status = m0_results.status_of(dict(entry))
    if status not in _STATUSES:
        status = "pending"
    evidence = {k: scrub(v) for k, v in evidence.items()}
    return GateResult(status=status, evidence=evidence, source="m0", run=scrub(str(results)), at=at)


def from_m0(results: Path, scrub: Callable[[str], str] = _identity) -> dict[str, GateResult]:
    """Read every ``<ID>.json`` in an ``m0_results`` folder (not ``env.json`` or ``*.stage.json``).

    The manual verdict wins over the automatic one. ``at`` is the newer of ``manual.at`` and ``auto.at``.
    Unreadable or wrongly shaped files are skipped with one stderr line. ``scrub`` is applied to evidence and
    ``run``. A missing folder gives an empty map.
    """
    out: dict[str, GateResult] = {}
    if not results.is_dir():
        return out
    for path in sorted(results.glob("*.json")):
        if path.name in _M0_SKIP_FILES or path.name.endswith(_STAGE_SUFFIX):
            continue
        try:
            entry = m0_results.load(results, path.stem)
            if entry is not None:
                out[path.stem] = _m0_result(entry, results, scrub)
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            sys.stderr.write(f"gateindex: skipping {path.name}: {type(exc).__name__}: {exc}\n")
    return out


def merge_newest(old: Mapping[str, GateResult], new: Mapping[str, GateResult]) -> dict[str, GateResult]:
    """Per gate keep the entry with the larger ``at`` (ties go to ``new``).

    A ``skip`` never replaces a ``pass`` or ``fail``, even when the skip is newer; a real result always beats a skip.
    """
    merged: dict[str, GateResult] = dict(old)
    for gate, fresh in new.items():
        prior = merged.get(gate)
        if prior is None:
            merged[gate] = fresh
            continue
        if fresh["status"] == "skip" and prior["status"] in _REAL:
            continue
        if prior["status"] == "skip" and fresh["status"] in _REAL:
            merged[gate] = fresh
            continue
        if _when(fresh["at"]) >= _when(prior["at"]):
            merged[gate] = fresh
    return merged


def _valid(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("status"), str)
        and isinstance(entry.get("evidence"), dict)
        and all(isinstance(entry.get(k), str) for k in ("source", "run", "at"))
    )


def load_index(dev: Path) -> dict[str, GateResult]:
    """Read ``<dev>/gates.json``; a missing, corrupt or malformed file yields only its valid entries (maybe none)."""
    path = dev / INDEX_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        _LOG.warning("ignoring unreadable %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if _valid(v)}


def update_index(dev: Path, new: Mapping[str, GateResult]) -> dict[str, GateResult]:
    """Merge ``new`` into ``<dev>/gates.json`` (atomic replace) and return the merged index."""
    merged = merge_newest(load_index(dev), new)
    dev.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gates.", suffix=".tmp", dir=dev)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(merged, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, dev / INDEX_NAME)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return merged
