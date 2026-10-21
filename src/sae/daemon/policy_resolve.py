"""Policy resolution at daemon start (C10 section 4, steps 1-4, 6, 7). Runs before ``open_sink``.

Never raises for a bad policy: any failure yields the built-in policy with a short ``fallback_reason`` (no paths,
at most 256 characters). Only :func:`check_resume` raises, because a changed policy must not continue a run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from sae.contracts.c5_bus import PolicyRef
from sae.contracts.c8_config import EngineConfig, SaeConfig, sae_dir
from sae.contracts.c10_profile import (
    PolicyVersion,
    RegistryError,
    RegistryRow,
    fold_registry,
    policy_sha256,
)
from sae.core.policy import ActivePolicy, from_doc, load_builtin

__all__ = ["MAX_POLICY_BYTES", "PolicyChanged", "check_resume", "effective_engine_config", "resolve_policy"]

log = logging.getLogger(__name__)
MAX_POLICY_BYTES: Final = 1024 * 1024
_REG_MAX_BYTES: Final = MAX_POLICY_BYTES


class PolicyChanged(Exception):
    """``--resume`` with a policy whose sha differs from the one recorded in the run's ``run_started``."""

    def __init__(self) -> None:
        super().__init__("policy changed; start a new run")


class _Fallback(Exception):
    """Internal: carries the ``fallback_reason``."""


def _read_rows(path: Path) -> list[RegistryRow]:
    """Valid registry rows; invalid lines are skipped and counted (C5 section 5), surfacing as a seq gap."""
    try:
        if not path.exists():
            return []
        if path.stat().st_size > _REG_MAX_BYTES:
            raise _Fallback("registry too large")
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _Fallback("registry unreadable") from exc
    except UnicodeDecodeError as exc:
        raise _Fallback("registry unreadable") from exc
    rows: list[RegistryRow] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(RegistryRow.model_validate_json(line))
        except ValueError:  # ValidationError and JSON errors
            skipped += 1
    if skipped:
        log.warning("registry: skipped %d invalid line(s)", skipped)
    return rows


def _load_file(pdir: Path, sha: str) -> PolicyVersion:
    path = pdir / f"{sha}.json"
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise _Fallback(f"policy file {sha[:8]} missing") from exc
    except OSError as exc:
        raise _Fallback(f"policy file {sha[:8]} unreadable") from exc
    if size > MAX_POLICY_BYTES:
        raise _Fallback(f"policy file {sha[:8]} too large")
    try:
        doc = PolicyVersion.model_validate_json(path.read_bytes())
    except (ValidationError, ValueError, OSError) as exc:
        raise _Fallback(f"policy file {sha[:8]} invalid") from exc
    if policy_sha256(doc) != sha:
        raise _Fallback(f"policy file {sha[:8]} hash mismatch")
    return doc


def _wanted(config: SaeConfig, builtin_sha: str, pdir: Path) -> tuple[str | None, str]:
    """``(sha to load or None for built-in, source)`` per steps 1-3; raises ``_Fallback``."""
    pin = config.policy.pin
    if pin == "builtin":
        return None, "builtin"
    try:
        state = fold_registry(_read_rows(pdir / "registry.jsonl"), builtin_sha)
    except RegistryError as exc:
        raise _Fallback(f"registry error: {str(exc)[:120]}") from exc
    if pin is not None:
        if pin not in state.approved:
            raise _Fallback(f"pinned policy {pin[:8]} not approved")
        return pin, "pinned"
    if state.current is None or state.current == builtin_sha:
        return None, "builtin"
    return state.current, "registry"


def resolve_policy(config: SaeConfig) -> ActivePolicy:
    """Resolve the policy to run with (C10 section 4 steps 1-4); any failure falls back to the built-in policy."""
    builtin = load_builtin()
    pdir = sae_dir(config) / "policies"
    try:
        sha, source = _wanted(config, builtin.sha256, pdir)
        if sha is None:
            return builtin
        doc = _load_file(pdir, sha)
        if doc.fixed.counter_table != builtin.doc.fixed.counter_table:
            raise _Fallback("policy fixed tables differ")
    except _Fallback as exc:
        reason = str(exc)[:256]
        log.warning("policy fallback to built-in: %s", reason)
        return load_builtin(reason)
    ref = PolicyRef(version=doc.version, sha256=sha, source="registry" if source == "registry" else "pinned")
    log.info("policy %s %s (%s)", doc.version, sha[:8], source)
    return from_doc(doc, ref)


def recorded_sha(run_dir: Path, builtin_sha: str) -> str | None:
    """``run_started.policy.sha256`` of an existing bus (pre-v1.3 buses mean the built-in).

    None only when there is no bus file. Any other failure (unreadable, corrupt, truncated, over-long first line or
    a first line that is not ``run_started``) raises :class:`PolicyChanged`: the run's policy cannot be proven
    unchanged (C10 section 4 rule 6).
    """
    try:
        with (run_dir / "bus.jsonl").open(encoding="utf-8") as fh:
            line = fh.readline(MAX_POLICY_BYTES)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.error("resume refused: bus unreadable")
        raise PolicyChanged from exc
    if not line.endswith("\n"):
        log.error("resume refused: bus first line unterminated or too long")
        raise PolicyChanged
    try:
        first = json.loads(line)
    except ValueError as exc:
        log.error("resume refused: bus first line is not JSON")
        raise PolicyChanged from exc
    if not isinstance(first, dict) or first.get("type") != "run_started":
        log.error("resume refused: bus first line is not run_started")
        raise PolicyChanged
    payload = first.get("payload")
    pol = payload.get("policy") if isinstance(payload, dict) else None
    if pol is None:
        return builtin_sha
    sha = pol.get("sha256") if isinstance(pol, dict) else None
    return sha if isinstance(sha, str) else None


def check_resume(policy: ActivePolicy, run_dir: Path) -> None:
    """Step 6: raise :class:`PolicyChanged` if the resolved sha differs from the bus's ``run_started`` policy.

    A missing bus has nothing to compare and is allowed through; a corrupt one is refused.
    """
    old = recorded_sha(run_dir, load_builtin().sha256)
    if old is not None and old != policy.sha256:
        log.error("resume refused: policy %s != recorded %s", policy.sha256[:8], old[:8])
        raise PolicyChanged


def effective_engine_config(cfg: EngineConfig, policy: ActivePolicy) -> EngineConfig:
    """Step 7: a concrete ``EngineConfig`` whose ``rho``/``likelihood_clip``/``belief_threshold`` come from the
    policy unless the config sets them (explicit number wins; ``None`` = from the policy, the C8 v1.3 default).

    A resumed ``config.resolved.json`` keeps the nulls, so the pinned policy (see :func:`check_resume`) still decides.
    """
    d = policy.doc.detector
    upd: dict[str, float] = {}
    for name, pol in (("rho", d.rho), ("likelihood_clip", d.likelihood_clip), ("belief_threshold", d.belief_threshold)):
        val = getattr(cfg, name)
        if val is None:
            upd[name] = pol
    return cfg.model_copy(update=upd)
