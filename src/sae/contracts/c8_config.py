"""C8: configuration model, presets and layering.

Semantics, repo layout and CLI entry points: ``contracts/C8_config_cli.md``.
Layer order (later wins): built-in defaults < preset < TOML file < ``SAE_*`` env < CLI flags.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import Field, model_validator

from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c7_governor import GovernorConfig, TelemetryTier
from sae.contracts.common import Frozen, Sha256Hex

SCHEMA_VERSION: Final = 1
ENV_PREFIX = "SAE_"
CONFIG_ENV = "SAE_CONFIG"  # path of the TOML file, not a config key
RESOLVED_FILE = "config.resolved.json"
CURRENT_RUN_FILE = "CURRENT"

PresetName = Literal["low_impact", "balanced", "dev"]


class PathsConfig(Frozen):
    """Filesystem locations. ``None`` = auto-detect (C8 section 3)."""

    user_dir: str | None = Field(default=None, description="Documents/Paradox Interactive/Stellaris.")
    game_log: str | None = Field(default=None, description="Default <user_dir>/logs/game.log (UNVERIFIED A-03).")
    save_dir: str | None = Field(default=None, description="Default <user_dir>/save games.")
    ini_path: str | None = Field(default=None, description="Default <user_dir>/sae_in.ini (UNVERIFIED A-08).")
    runs_dir: str | None = Field(default=None, description="None = %LOCALAPPDATA%/sae/runs or ~/.local/state/sae/runs.")
    parser_exe: str | None = Field(default=None, description="Default: PATH, else repo parser/target/{release,debug}.")
    sae_dir: str | None = Field(
        default=None,
        description="Per-user SAE state (profile, policies, salt); None = %LOCALAPPDATA%/sae or ~/.local/state/sae.",
    )


class EngineConfig(Frozen):
    """Engine core numbers (T4).

    ``rho``/``likelihood_clip``/``belief_threshold``: ``None`` (default, C8 v1.3) = take the value from the loaded
    PolicyVersion's ``detector`` (C10 section 4); a number overrides the policy. The engine core only ever sees the
    effective config with all three filled (``sae.daemon.policy_resolve.effective_engine_config``).
    """

    rho: float | None = Field(default=None, ge=0.0, le=0.2, description="Fixed-share forgetting/month; null = policy.")
    likelihood_clip: float | None = Field(default=None, gt=0, description="L: |log-lik| cap per feature; null=policy.")
    belief_threshold: float | None = Field(
        default=None, gt=0.25, lt=1.0, description="Below: baseline; null = policy."
    )
    commitment_months: int = Field(default=24, ge=1)
    max_managed: int = Field(default=16, ge=1, le=16, description="Mod caps sae_managed at 16 (C6 section 2).")
    safety_floor_ship_min: int = Field(
        default=10, ge=0, le=100, description="Unused while fleet bias is dropped (C3 v1.1); kept so C8 stays v1."
    )
    ttl_months: int = Field(default=3, ge=1, le=36)
    seed: int = 0


class BridgeConfig(Frozen):
    """Desired in-game knob values at session start (C7 section 6)."""

    emit_period: int = Field(default=1, ge=1, le=12)
    quiet: Literal[0, 1] = 0


class TelemetryConfig(Frozen):
    """sae-telemetry (T6). Off for players by default."""

    enabled: bool = False
    tier: TelemetryTier = TelemetryTier.T1
    rerun_url: str | None = Field(default=None, description="e.g. rerun+http://HOST:9876/proxy")
    rrd_path: str | None = None
    viewer_memory_limit: str = "512MB"


class ProfileConfig(Frozen):
    """Player profile (C10 sections 1-2, proposal 020). The daemon reads no profile file in v1."""

    mode: Literal["off", "strict"] = "off"
    player_key: str = Field(default="default", pattern=r"^[a-z0-9_]{1,32}$")


class PolicyConfig(Frozen):
    """Which PolicyVersion the daemon loads (C10 sections 4-5, proposal 021)."""

    pin: Literal["builtin"] | Sha256Hex | None = Field(
        default=None, description="None = registry current, else built-in; 'builtin'; or a policy sha256."
    )


class RecordConfig(Frozen):
    """Session recording hygiene for ``--record`` (proposal 022)."""

    max_bytes: int = Field(default=4 * 1024**3, ge=0, description="Per recording dir; then saves stop, log goes on.")
    saves: Literal["all", "none", "every_n"] = "all"
    save_every: int = Field(default=12, ge=1, le=1000, description="With saves = every_n: copy every N-th save.")
    redact_plyr: bool = Field(default=True, description="False only with preset = dev (GOD_VIEW_INSIDE marker).")


class SaeConfig(Frozen):
    """The fully resolved configuration; written to ``runs/<id>/config.resolved.json``."""

    v: Literal[1] = SCHEMA_VERSION
    preset: PresetName = "balanced"
    adapter: AdapterMode = AdapterMode.LIVE
    min_level: Literal["H0", "H1", "H2", "H3"] = Field(default="H0", description="Preset floor; never S (C7).")
    paths: PathsConfig = PathsConfig()
    engine: EngineConfig = EngineConfig()
    bridge: BridgeConfig = BridgeConfig()
    governor: GovernorConfig = GovernorConfig()
    telemetry: TelemetryConfig = TelemetryConfig()
    profile: ProfileConfig = ProfileConfig()
    policy: PolicyConfig = PolicyConfig()
    record: RecordConfig = RecordConfig()

    @model_validator(mode="after")
    def _raw_plyr_only_in_dev(self) -> SaeConfig:
        if not self.record.redact_plyr and self.preset != "dev":
            raise ValueError("record.redact_plyr = false is allowed only with preset = dev")
        return self


#: Preset overrides, applied on top of defaults (C8 section 4).
PRESETS: dict[str, dict[str, Any]] = {
    "low_impact": {"min_level": "H1", "bridge": {"emit_period": 3}, "telemetry": {"enabled": False}},
    "balanced": {},
    "dev": {"telemetry": {"enabled": True, "tier": "T2"}, "bridge": {"emit_period": 1}},
}


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dict: ``override`` merged into ``base`` recursively (inputs untouched)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _put(layer: dict[str, Any], path: list[str], raw: str, name: str) -> None:
    """Set ``layer[path...] = json-or-string(raw)`` in place, creating sections."""
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    node = layer
    for part in path[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"{name}: {part!r} is both a value and a section")
        node = child
    node[path[-1]] = value


def set_layer(sets: Sequence[str]) -> dict[str, Any]:
    """Build the CLI layer from repeated ``--set <section>.<key>=<json-or-string>`` (C8 section 2).

    Raises:
        ValueError: an item has no ``=`` or empty key, or uses one segment both as a value and as a section.
    """
    layer: dict[str, Any] = {}
    for item in sets:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ValueError(f"--set expects K=V, got {item!r}")
        _put(layer, key.split("."), raw, f"--set {key}")
    return layer


def env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    """Build a layer from ``SAE_<SECTION>__<KEY>=value`` variables (values parsed as JSON if possible).

    ``SAE_PRESET=dev`` sets a top-level key. Keys are lower-cased. ``SAE_CONFIG`` is skipped. A string
    value that looks like JSON (e.g. ``123``) must be JSON-quoted: ``SAE_PATHS__USER_DIR='"123"'``.

    Raises:
        ValueError: a name uses one segment both as a value and as a section.
    """
    layer: dict[str, Any] = {}
    for name, raw in environ.items():
        if not name.startswith(ENV_PREFIX) or name == CONFIG_ENV:
            continue
        _put(layer, name[len(ENV_PREFIX) :].lower().split("__"), raw, name)
    return layer


def resolve_config(*layers: Mapping[str, Any]) -> SaeConfig:
    """Resolve layers (file, env, CLI; lowest first) on top of defaults and the chosen preset.

    The preset is taken from the last layer that names one. Raises ``pydantic.ValidationError``
    on any unknown key or bad value (C8 exit code 3).
    """
    preset = "balanced"
    for layer in layers:
        preset = str(layer.get("preset", preset))
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    merged: dict[str, Any] = deep_merge({}, PRESETS[preset])
    for layer in layers:
        merged = deep_merge(merged, layer)
    merged["preset"] = preset
    return SaeConfig.model_validate(merged)


def new_run_id(mode: AdapterMode, now: datetime) -> str:
    """``YYYYMMDD-HHMMSS-<mode>-<4 hex>`` in UTC (C5 RunId)."""
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{mode.value}-{secrets.token_hex(2)}"


def default_sae_dir(environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """``%LOCALAPPDATA%\\sae`` on Windows, else ``${XDG_STATE_HOME:-~/.local/state}/sae`` (C8 section 3)."""
    env = os.environ if environ is None else environ
    if (sys.platform if platform is None else platform).startswith("win"):
        local = env.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "sae"
    xdg = env.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "state") / "sae"


def sae_dir(config: SaeConfig, environ: Mapping[str, str] | None = None) -> Path:
    """``paths.sae_dir`` if set, else :func:`default_sae_dir`. Every team resolves ``<sae_dir>`` here."""
    return Path(config.paths.sae_dir) if config.paths.sae_dir else default_sae_dir(environ)
