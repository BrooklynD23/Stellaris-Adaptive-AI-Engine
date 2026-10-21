"""``sae m0``: the maintainer's M0 spike kit (T8).

Prints the manual steps for each check, stages files in the results folder, reads the evidence the
maintainer produces (read-only) and records pass/fail. It never launches Stellaris and never writes inside
the game's user folder, its ``mod`` folder or the install folder: anything the game must see is staged
under ``<results>/stage/`` and the maintainer copies it by hand.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sae.adapters.paths import ResolvedPaths, resolve_paths
from sae.contracts.c2_ini import CountryCommand, IniFile, render_ini
from sae.contracts.c8_config import PathsConfig
from sae.contracts.common import EconPlan, Posture
from sae.m0 import doctor, evidence, pack, results
from sae.m0.checks import BY_ID, CHECKS, Check
from sae.m0.tail import run_tail

REPO = Path(__file__).resolve().parents[3]
EXPIRY_MONTHS = 3
# UNVERIFIED(A-01): launcher accepts a relative path= and this supported_version (M0-2 checks it).
_MOD_FILE_LINES = ('name="SAE Adaptive Engine Bridge"', 'path="mod/sae_bridge"', 'supported_version="v4.4.*"')


#: A folder holding any of these is a Stellaris install (Steam, GOG, Xbox, Paradox launcher).
_INSTALL_MARKERS = ("stellaris.exe", "launcher-settings.json")


class UsageError(Exception):
    """Bad arguments or an unsafe results folder (exit 2)."""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sae m0", description="M0 spike kit: steps, evidence checks, results.")
    p.add_argument("--results", type=Path, default=Path("m0_results"), help="results folder (default ./m0_results)")
    p.add_argument("--user-dir", help="Stellaris user folder (default: C8 auto-detection)")
    p.add_argument("--game-log", help="game.log path (default <user_dir>/logs/game.log)")
    p.add_argument("--parser-exe", help="sae-parse binary (default: PATH, then parser/target/release)")
    p.add_argument("--kit-dir", type=Path, help="field kit folder (default: repo if it has mod/sae_bridge, else cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="all checks with their recorded status")
    sub.add_parser("steps", help="print the manual steps of one check").add_argument("check")
    init = sub.add_parser("init", help="record the session header")
    for key in results.ENV_KEYS:
        init.add_argument(f"--{key.replace('_', '-')}", default="", required=key in {"game_version", "checksum"})
    stage = sub.add_parser("stage", help="write a file for you to copy by hand (M0-2 .mod file, M0-6 sae_in.ini)")
    stage.add_argument("check", choices=["M0-2", "M0-6"])
    stage.add_argument("--sid", type=int, help="M0-6: managed AI sid (from a SNAP line)")
    stage.add_argument("--m", type=int, help="M0-6: latest HB m value")
    stage.add_argument("--seq", type=int, help="M0-6: batch seq (default: last staged seq + 1, else 1)")
    stage.add_argument("--target", type=int, help="M0-6: player sid; adds a deterrent posture toward it (A-23)")
    check = sub.add_parser("check", help="judge the evidence of an automatic check")
    check.add_argument("check", choices=[c.id for c in CHECKS if c.auto])
    check.add_argument("--starnet-dir", type=Path, help="M0-1: StarNet Workshop folder")
    check.add_argument("--save", type=Path, help="M0-11/M0-14: autosave to parse (read-only)")
    tail = sub.add_parser("tail", help="M0-4 timestamping tailer")
    tail.add_argument("--seconds", type=float, default=300.0)
    rec = sub.add_parser("record", help="record your verdict for a check")
    rec.add_argument("check", choices=list(BY_ID))
    rec.add_argument("status", choices=["pass", "fail", "skip"])
    rec.add_argument("--note", default="")
    rec.add_argument("--value", action="append", default=[], metavar="KEY=VALUE")
    sub.add_parser("doctor", help=f"preflight checks; writes <results>/{doctor.DOCTOR_FILE}")
    pk = sub.add_parser("pack", help="zip the scrubbed result files to send back")
    pk.add_argument("--out", type=Path, help="zip path (default ./m0_results_<UTC timestamp>.zip)")
    sub.add_parser("summary", help=f"write <results>/{results.SUMMARY_FILE}")
    return p


def _paths(args: argparse.Namespace) -> ResolvedPaths:
    return resolve_paths(PathsConfig(user_dir=args.user_dir, game_log=args.game_log, parser_exe=args.parser_exe))


def _safe_results(results_dir: Path, paths: ResolvedPaths) -> Path:
    """Refuse a results folder inside the game's user folder (which holds ``mod/``, saves and logs)."""
    target = results_dir.resolve()
    user = paths.user_dir.resolve()
    in_install = "steamapps" in (p.lower() for p in target.parts) or any(
        (d / name).exists() for d in (target, *target.parents) for name in _INSTALL_MARKERS
    )
    if target == user or user in target.parents or in_install:
        raise UsageError(f"results folder {target} is inside a game folder; choose another --results")
    return target


def _kit_dir(args: argparse.Namespace) -> Path:
    if args.kit_dir is not None:
        return Path(args.kit_dir)
    return REPO if (REPO / "mod" / "sae_bridge").exists() else Path.cwd()


def _steps(check: Check, paths: ResolvedPaths, res: Path, kit: Path) -> str:
    fill = {"user_dir": paths.user_dir, "game_log": paths.game_log, "save_dir": paths.save_dir,
            "repo": kit, "results": res}
    lines = [f"{check.id}  {check.title}  [{check.tier}]  assumptions: {', '.join(check.assumptions) or '-'}", ""]
    lines += [f"  {i}. {s.format(**fill)}" for i, s in enumerate(check.steps, 1)]
    lines += ["", f"  Evidence: {check.evidence.format(**fill)}", f"  Pass: {check.pass_rule}",
              f"  Fallback: {check.fallback}",
              "  The kit never launches the game or writes to its folders: every step above is yours."]
    return "\n".join(lines)


def _stage(args: argparse.Namespace, res: Path, paths: ResolvedPaths) -> str:
    stage_dir = res / "stage"
    stage_dir.mkdir(parents=True, exist_ok=True)
    if args.check == "M0-2":
        out = stage_dir / "sae_bridge.mod"
        out.write_text("\n".join(_MOD_FILE_LINES) + "\n", encoding="utf-8")
        return f"wrote {out}\ncopy it by hand to: {paths.user_dir / 'mod' / 'sae_bridge.mod'}"
    if args.sid is None or args.m is None:
        raise UsageError("stage M0-6 needs --sid and --m (from `sae m0 check M0-5`)")
    seq = args.seq if args.seq is not None else int(results.load_stage(res).get("staged_seq", 0)) + 1
    try:
        posture = {"mil": Posture.DETERRENT.code, "mil_target": args.target} if args.target is not None else {}
        cmd = CountryCommand(sid=args.sid, seq=seq, expires=args.m + EXPIRY_MONTHS,
                             econ=EconPlan.MILITARY.code, **posture)
        text = render_ini(IniFile(countries=(cmd,)))
    except ValueError as exc:
        raise UsageError(f"invalid staging values: {exc}") from exc
    out = stage_dir / "sae_in.ini"
    out.write_bytes(text.encode("ascii"))
    results.save_stage(res, {"staged_sid": args.sid, "staged_seq": seq})
    return (f"wrote {out}\ncopy it by hand to: {paths.ini_path}\nthen in the game console: run sae_in.ini\n"
            f"then: sae m0 check M0-6")


def _check(args: argparse.Namespace, res: Path, paths: ResolvedPaths) -> evidence.Verdict:
    cid = args.check
    if cid == "M0-1":
        if args.starnet_dir is None:
            raise UsageError("M0-1 needs --starnet-dir")
        return evidence.check_starnet(args.starnet_dir)
    if cid == "M0-2":
        return evidence.check_error_log(paths.game_log.parent / "error.log")
    if cid == "M0-3":
        return evidence.check_log_channel(paths.game_log)
    if cid == "M0-4":
        raise UsageError("M0-4 is judged by `sae m0 tail` and `sae m0 record M0-4`")
    if cid == "M0-5":
        return evidence.check_snapshots(paths.game_log)
    if cid == "M0-6":
        staged = results.load_stage(res)
        if "staged_sid" not in staged or "staged_seq" not in staged:
            raise UsageError("run `sae m0 stage M0-6 --sid N --m M` first")
        return evidence.check_ack(paths.game_log, int(staged["staged_sid"]), int(staged["staged_seq"]))
    if args.save is None:
        raise UsageError(f"{cid} needs --save")
    return evidence.check_parse(paths.parser_exe, args.save, want_ironman=cid == "M0-14")


def _store(res: Path, cid: str, verdict: evidence.Verdict, keep: dict[str, object] | None = None) -> str:
    ev = {**(keep or {}), **verdict.evidence}
    path = results.save_auto(res, cid, verdict.status, ev, list(verdict.notes))
    return "\n".join([f"{cid}: {verdict.status.upper()}", *[f"  {n}" for n in verdict.notes], f"  saved {path}"])


def _run(args: argparse.Namespace) -> str:
    paths = _paths(args)
    res = _safe_results(args.results, paths)
    if args.cmd == "list":
        rows = [f"{c.id:6} {c.tier:8} {results.status_of(results.load(res, c.id)):12} {c.title}" for c in CHECKS]
        return "\n".join(rows)
    if args.cmd == "steps":
        if args.check not in BY_ID:
            raise UsageError(f"unknown check {args.check}; see `sae m0 list`")
        return _steps(BY_ID[args.check], paths, res, _kit_dir(args))
    if args.cmd == "init":
        env = {k: getattr(args, k) for k in results.ENV_KEYS}
        return f"saved {results.save_env(res, env)}"
    if args.cmd == "stage":
        return _stage(args, res, paths)
    if args.cmd == "check":
        stage = results.load_stage(res) if args.check == "M0-6" else {}
        keep = {k: v for k, v in stage.items() if k.startswith("staged_")}
        return _store(res, args.check, _check(args, res, paths), keep)
    if args.cmd == "tail":
        res.mkdir(parents=True, exist_ok=True)
        with (res / "M0-4.tail.jsonl").open("a", encoding="utf-8") as tail_out:
            print(f"tailing {paths.game_log} for {args.seconds:.0f} s (Ctrl+C stops early)")
            verdict = run_tail(paths.game_log, tail_out, args.seconds)
        return _store(res, "M0-4", verdict)
    if args.cmd == "record":
        values: dict[str, str] = {}
        for item in args.value:
            key, sep, val = item.partition("=")
            if not sep or not key:
                raise UsageError(f"--value {item!r} must be KEY=VALUE with a non-empty KEY")
            if key in values:
                raise UsageError(f"--value key {key!r} given twice")
            values[key] = val
        return f"saved {results.save_manual(res, args.check, args.status, args.note, values)}"
    summary = res / results.SUMMARY_FILE
    res.mkdir(parents=True, exist_ok=True)
    summary.write_text(results.render_summary(res), encoding="utf-8")
    return f"wrote {summary}"


def _run_kit(args: argparse.Namespace) -> tuple[str, int]:
    """``doctor`` and ``pack``: they return an exit code of their own."""
    paths = _paths(args)
    if args.cmd == "doctor":
        try:
            _safe_results(args.results, paths)
            refusal = None
        except UsageError as exc:
            refusal = str(exc)
        return doctor.run_doctor(paths, args.results, _kit_dir(args), refusal)
    out = args.out or Path.cwd() / f"m0_results_{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.zip"
    res = _safe_results(args.results, paths)
    _safe_results(out, paths)
    try:
        return pack.render_manifest(pack.build_zip(res, out), out), 0
    except pack.PackError as exc:
        raise UsageError(str(exc)) from exc


def main(command: str, argv: list[str]) -> int:
    """Entry point called by ``sae.cli`` (C8 section 5). Exit codes: 0 ok, 1 runtime failure, 2 usage."""
    del command
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        text, code = _run_kit(args) if args.cmd in {"doctor", "pack"} else (_run(args), 0)
        print(text)
    except UsageError as exc:
        print(f"sae m0: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:  # ValueError covers JSON and UTF-8 decode errors
        print(f"sae m0: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("sae m0: interrupted", file=sys.stderr)
        return 1
    return code
