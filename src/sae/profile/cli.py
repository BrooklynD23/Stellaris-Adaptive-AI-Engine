"""``sae profile update|show|close|exclude|delete|label`` (C8 section 5, C10 sections 1-2, proposal 025).

Exit codes: 0 ok, 1 runtime, 2 usage, 3 config.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import ValidationError

from sae.contracts.c8_config import (
    CONFIG_ENV,
    SaeConfig,
    deep_merge,
    env_layer,
    resolve_config,
    sae_dir,
    set_layer,
)
from sae.contracts.c10_profile import CampaignEvent, CampaignLabel, CampaignSummary, LabelSegment, ProfileDoc
from sae.platform.sensors import make_sensors
from sae.profile import store
from sae.profile.index import CampaignState, campaign_state, plan_update, scan_runs
from sae.profile.model import build_profile
from sae.profile.summary import build_summary

log = logging.getLogger(__name__)

__all__ = ["config_from_args", "main"]

_CONFIG_ERRORS = (ValidationError, ValueError, tomllib.TOMLDecodeError, OSError)
_WIN_IDLE = "IDLE_PRIORITY_CLASS"
_NICE_IDLE = 19


def _err(msg: str, code: int) -> int:
    print(f"sae profile: {msg}", file=sys.stderr)
    return code


# --- config -------------------------------------------------------------------------------------------------


def config_from_args(args: argparse.Namespace) -> SaeConfig:
    """Layer file, env and CLI exactly as ``sae daemon`` does (``--adapter`` only when the parser has it)."""
    file_layer: dict[str, Any] = {}
    path = args.config or os.environ.get(CONFIG_ENV)
    if path:
        with open(path, "rb") as f:
            file_layer = tomllib.load(f)
    cli: dict[str, Any] = {}
    if args.preset:
        cli["preset"] = args.preset
    if getattr(args, "adapter", None):
        cli["adapter"] = args.adapter
    if args.runs_dir:
        cli["paths"] = {"runs_dir": args.runs_dir}
    cli = deep_merge(cli, set_layer(args.sets))
    return resolve_config(file_layer, env_layer(os.environ), cli)


@dataclass(frozen=True)
class _Ctx:
    cfg: SaeConfig
    sae: Path
    runs_dir: Path
    key: str

    @property
    def index(self) -> Path:
        return self.sae / store.INDEX_FILE

    @property
    def pdir(self) -> Path:
        return store.profile_dir(self.sae, self.key)

    @property
    def profile_path(self) -> Path:
        return self.pdir / "profile.json"

    def summary_path(self, cid: str) -> Path:
        return self.pdir / "summaries" / f"{cid}.json"

    def label_path(self, cid: str) -> Path:
        return self.pdir / "labels" / f"{cid}.json"


def _context(args: argparse.Namespace) -> _Ctx | int:
    try:
        cfg = config_from_args(args)
    except _CONFIG_ERRORS as e:
        return _err(f"invalid config: {e}", 3)
    sae = sae_dir(cfg)
    runs = Path(cfg.paths.runs_dir) if cfg.paths.runs_dir else sae / "runs"
    return _Ctx(cfg, sae, runs, cfg.profile.player_key)


# --- helpers ------------------------------------------------------------------------------------------------


def _game_running() -> bool:
    """True when Stellaris looks to be running; a probe failure counts as not running."""
    # UNVERIFIED (T5 process names): M0 runs this with the real game open and closed on the target machine.
    try:
        return bool(make_sensors().sample().game_running)
    except Exception as e:
        log.warning("game process probe failed: %s", e)
        return False


def _lower_priority() -> None:
    """Run this process at idle priority (never the game's)."""
    try:
        if sys.platform == "win32":
            psutil.Process().nice(getattr(psutil, _WIN_IDLE))
        else:
            os.nice(_NICE_IDLE)
    except (OSError, psutil.Error):
        log.warning("could not lower process priority", exc_info=True)


def _ask_split(cid: str) -> Literal["new", "reload"] | None:
    if not sys.stdin.isatty():
        return None
    try:
        reply = input(f"A run in campaign {cid}'s folder starts >5 game years earlier. [n]ew game, [r]eload, skip? ")
    except EOFError:
        return None
    letter = reply.strip().lower()[:1]
    return "new" if letter == "n" else "reload" if letter == "r" else None


def _load(ctx: _Ctx) -> tuple[dict[str, CampaignState], list[CampaignEvent]] | int:
    events, invalid, gap = store.read_index(ctx.index)
    if gap:
        return _err("campaigns.jsonl corrupted; rename it and re-run update", 1)
    if invalid:
        print(f"sae profile: {invalid} invalid line(s) in campaigns.jsonl skipped", file=sys.stderr)
    return campaign_state(events), events


def _append(ctx: _Ctx, rows: list[CampaignEvent]) -> int:
    """Append rows; return 0 or the exit code for a lock/corruption failure."""
    if not rows:
        return 0
    try:
        store.append_events(ctx.index, rows)
    except store.IndexLocked as e:
        return _err(str(e), 1)
    except store.IndexCorrupt as e:
        return _err(str(e), 1)
    return 0


def _row(cid: str, event: Literal["closed", "excluded", "deleted"], reason: str | None = None) -> CampaignEvent:
    return CampaignEvent(seq=0, at=store.now(), campaign_id=cid, event=event, reason=reason)


def _target(states: dict[str, CampaignState], cid: str) -> CampaignState | None:
    s = states.get(cid)
    return None if s is None or s.status == "deleted" else s


def _unlink_derived(ctx: _Ctx, cid: str) -> None:
    ctx.summary_path(cid).unlink(missing_ok=True)
    ctx.profile_path.unlink(missing_ok=True)


# --- update -------------------------------------------------------------------------------------------------


def _rebuild(ctx: _Ctx, states: dict[str, CampaignState], built_at: datetime) -> int:
    summaries: list[CampaignSummary] = []
    for s in states.values():
        if s.status != "closed":
            continue
        summary = build_summary(s, ctx.runs_dir)
        if summary is None:
            continue
        store.write_atomic(ctx.summary_path(s.campaign_id), store.canonical_bytes(summary))
        summaries.append(summary)
    profile = build_profile(summaries, ctx.key, built_at)
    store.write_atomic(ctx.profile_path, store.canonical_bytes(profile))
    return len(summaries)


def _cmd_update(args: argparse.Namespace, ctx: _Ctx) -> int:
    if ctx.cfg.profile.mode == "off":
        return _err("profile.mode is off; set profile.mode=strict to build a profile", 2)
    if _game_running() and not args.force:
        return _err("Stellaris is running; close it or pass --force", 1)
    t0 = time.monotonic()
    _lower_priority()
    loaded = _load(ctx)
    if isinstance(loaded, int):
        return loaded
    states, _events = loaded
    rows, pending = plan_update(states, scan_runs(ctx.runs_dir, args.include_synth), _ask_split)
    for seg in pending:
        print(f"sae profile: split candidate left pending: run {seg.run_id} (asked again next update)", file=sys.stderr)
    if code := _append(ctx, rows):
        return code
    events, _invalid, gap = store.read_index(ctx.index)
    if gap:
        return _err("campaigns.jsonl corrupted; rename it and re-run update", 1)
    n_sum = 0
    if events:
        n_sum = _rebuild(ctx, campaign_state(events), events[-1].at)
    print(
        f"update: {len(rows)} new row(s), {len(pending)} pending, {n_sum} summary(ies), "
        f"{'profile written' if events else 'no profile'} in {time.monotonic() - t0:.1f}s"
    )
    return 0


# --- show ---------------------------------------------------------------------------------------------------


def _read_profile(ctx: _Ctx) -> ProfileDoc | None:
    try:
        return ProfileDoc.model_validate_json(ctx.profile_path.read_bytes())
    except (OSError, ValidationError):
        return None


def _cmd_show(args: argparse.Namespace, ctx: _Ctx) -> int:
    loaded = _load(ctx)
    if isinstance(loaded, int):
        return loaded
    states, _ = loaded
    profile = _read_profile(ctx)
    salt = str(ctx.sae / "salt")  # path only, never the bytes
    camps = [
        {"id": s.campaign_id, "status": s.status, "split": s.split, "start_date": s.start_date,
         "last_date": s.last_date}
        for s in states.values()
    ]
    if args.json:
        doc = {"campaigns": camps, "profile": profile.model_dump(mode="json") if profile else None, "salt": salt}
        print(json.dumps(doc, sort_keys=True))
        return 0
    print(f"campaigns: {len(camps)}")
    for c in camps:
        print(f"  {c['id']}  {c['status']}  split={c['split']}  {c['start_date']} .. {c['last_date']}")
    if profile is None:
        print("no profile yet")
    else:
        print(
            f"profile: eligible={profile.eligible} drift_alarm={profile.drift_alarm} "
            f"drift_outside={profile.drift_outside}"
        )
        for name, f in profile.features.items():
            print(f"  {name}: {f.player_mu:.3f} +/- {f.player_sd:.3f}")
        print("  alpha: " + ", ".join(f"{a.value}={v:.2f}" for a, v in profile.archetype_alpha.items()))
        print(f"index as of {profile.built_at.isoformat()}")
    print(f"salt: {salt}")
    return 0


# --- close / exclude / delete / label -----------------------------------------------------------------------


def _cmd_close(args: argparse.Namespace, ctx: _Ctx) -> int:
    loaded = _load(ctx)
    if isinstance(loaded, int):
        return loaded
    states, _ = loaded
    target = _target(states, args.campaign)
    if target is None:
        return _err(f"unknown campaign {args.campaign}", 2)
    if target.status == "closed":
        print(f"{args.campaign} is already closed")
        return 0
    if code := _append(ctx, [_row(args.campaign, "closed")]):
        return code
    print(f"closed {args.campaign}")
    return 0


def _cmd_exclude(args: argparse.Namespace, ctx: _Ctx) -> int:
    loaded = _load(ctx)
    if isinstance(loaded, int):
        return loaded
    states, _ = loaded
    target = _target(states, args.campaign)
    if target is None:
        return _err(f"unknown campaign {args.campaign}", 2)
    try:
        rows = [] if target.status == "excluded" else [_row(args.campaign, "excluded", args.reason)]
    except ValidationError as e:
        return _err(f"invalid reason: {e}", 2)
    if code := _append(ctx, rows):
        return code
    _unlink_derived(ctx, args.campaign)
    print(f"excluded {args.campaign}")
    return 0


def _confirm_delete() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input("Type DELETE to confirm: ").strip() == "DELETE"
    except EOFError:
        return False


def _cmd_delete(args: argparse.Namespace, ctx: _Ctx) -> int:
    loaded = _load(ctx)
    if isinstance(loaded, int):
        return loaded
    states, _ = loaded
    if args.campaign is not None and _target(states, args.campaign) is None:
        return _err(f"unknown campaign {args.campaign}", 2)
    if not args.yes and not _confirm_delete():
        return _err("delete needs --yes, or a terminal and the typed word DELETE", 2)
    if args.campaign is not None:
        ids = [args.campaign]
    else:
        ids = [cid for cid, s in states.items() if s.status != "deleted"]
    if code := _append(ctx, [_row(cid, "deleted") for cid in ids]):
        return code
    if args.campaign is None:
        try:
            shutil.rmtree(ctx.pdir)
        except FileNotFoundError:
            pass
        except OSError as e:
            return _err(f"could not remove {ctx.pdir}: {e}", 1)
    else:
        ctx.label_path(args.campaign).unlink(missing_ok=True)
        _unlink_derived(ctx, args.campaign)
    print(f"deleted {len(ids)} campaign(s)")
    return 0


def _parse_segments(items: Sequence[str]) -> tuple[LabelSegment, ...]:
    segs: list[LabelSegment] = []
    for item in items:
        date, sep, arche = item.partition("=")
        if not sep:
            raise ValueError(f"--segment expects DATE=ARCHETYPE, got {item!r}")
        segs.append(LabelSegment.model_validate({"from_date": date, "archetype": arche}))
    return tuple(segs)


def _cmd_label(args: argparse.Namespace, ctx: _Ctx) -> int:
    if (args.campaign is None) == (args.recording is None):
        return _err("give exactly one of CAMPAIGN or --recording DIR", 2)
    cid: str | None = args.campaign
    if cid is not None:
        loaded = _load(ctx)
        if isinstance(loaded, int):
            return loaded
        if _target(loaded[0], cid) is None:
            return _err(f"unknown campaign {cid}", 2)
        target = ctx.label_path(cid)
    else:
        rec = Path(args.recording)
        if not rec.is_dir():
            return _err(f"recording dir not found: {rec}", 2)
        target = rec / "label.json"
    if target.exists():
        return _err(f"label exists: {target}", 2)
    try:
        label = CampaignLabel(
            campaign_id=cid, source="human", segments=_parse_segments(args.segment), labelled_at=store.now(),
            blind=not args.not_blind, note=args.note,
        )  # fmt: skip
    except (ValidationError, ValueError) as e:
        return _err(f"invalid label: {e}", 2)
    data = store.canonical_bytes(label)
    store.write_atomic(target, data)
    if cid is not None:
        row = CampaignEvent(
            seq=0, at=store.now(), campaign_id=cid, event="labelled", label_sha256=hashlib.sha256(data).hexdigest()
        )
        if code := _append(ctx, [row]):
            target.unlink(missing_ok=True)
            return code
    print(f"label written: {target}")
    return 0


# --- entry --------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    common.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    common.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    common.add_argument("--runs-dir", help="runs directory")
    p = argparse.ArgumentParser(prog="sae profile", description="Player profile (C10).")
    sub = p.add_subparsers(dest="sub", required=True)
    u = sub.add_parser("update", parents=[common], help="index runs and rebuild the profile")
    u.add_argument("--include-synth", action="store_true", help="test only: index synth and replay runs")
    u.add_argument("--force", action="store_true", help="run even if Stellaris is running")
    s = sub.add_parser("show", parents=[common], help="show campaigns and the profile")
    s.add_argument("--json", action="store_true")
    sub.add_parser("close", parents=[common]).add_argument("campaign")
    e = sub.add_parser("exclude", parents=[common])
    e.add_argument("campaign")
    e.add_argument("--reason")
    d = sub.add_parser("delete", parents=[common])
    d.add_argument("--campaign")
    d.add_argument("--yes", action="store_true")
    lb = sub.add_parser("label", parents=[common])
    lb.add_argument("campaign", nargs="?")
    lb.add_argument("--recording", metavar="DIR")
    lb.add_argument("--segment", action="append", required=True, metavar="DATE=ARCHETYPE")
    lb.add_argument("--note")
    lb.add_argument("--not-blind", action="store_true")
    return p


_HANDLERS = {
    "update": _cmd_update, "show": _cmd_show, "close": _cmd_close,
    "exclude": _cmd_exclude, "delete": _cmd_delete, "label": _cmd_label,
}  # fmt: skip


def main(command: str, argv: list[str]) -> int:
    """Entry point for ``sae profile`` (``command`` is ``"profile"``)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    ctx = _context(args)
    if isinstance(ctx, int):
        return ctx
    return _HANDLERS[args.sub](args, ctx)
