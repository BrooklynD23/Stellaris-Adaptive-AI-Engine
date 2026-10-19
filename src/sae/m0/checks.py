"""M0 check registry: one entry per M0/M1-P/R test the maintainer runs by hand.

Every game, console and save behaviour named here is UNVERIFIED until the maintainer records evidence.
Steps are printed, never executed: the kit does not launch Stellaris or write to its folders.
Placeholders in ``steps``: ``{user_dir}``, ``{game_log}``, ``{save_dir}``, ``{repo}``, ``{results}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["core", "stretch", "later"]


@dataclass(frozen=True)
class Check:
    """One M0 test: what it settles, how to run it by hand, and how it is judged."""

    id: str
    title: str
    tier: Tier
    assumptions: tuple[str, ...]
    steps: tuple[str, ...]
    evidence: str
    pass_rule: str
    fallback: str
    auto: bool  # True: `sae m0 check <id>` judges the evidence; False: `sae m0 record` only


_NEW_GAME = (
    "In the launcher, enable a playset with StarNet (if used) then sae_bridge last. Start the game yourself, "
    "begin a new single-player, non-Ironman game (small galaxy, note the seed), and stay paused on day 1."
)

CHECKS: tuple[Check, ...] = (
    Check(
        "M0-1", "StarNet build and ship-size hook", "core", ("A-22",),
        (
            "Find the StarNet Workshop folder: <Steam>/steamapps/workshop/content/281990/<id>/ (id from the "
            "Workshop page URL).",
            "sae m0 check M0-1 --starnet-dir \"<that folder>\"",
        ),
        "Workshop id, descriptor supported_version, files containing str_var_ai_ship_data_factor_",
        "the hook string is present in a 4.x build",
        "vanilla profile only; SAE writes its own ship-mix script values (fleet-size intent stays dropped)",
        True,
    ),
    Check(
        "M0-2", "sae_bridge loads cleanly", "core", ("A-01", "A-33"),
        (
            "sae m0 stage M0-2   (writes {results}/stage/sae_bridge.mod; nothing is copied for you)",
            "Copy the mod folder by hand: robocopy \"{repo}/mod/sae_bridge\" \"{user_dir}/mod/sae_bridge\" /E",
            "Copy {results}/stage/sae_bridge.mod to \"{user_dir}/mod/sae_bridge.mod\".",
            "Check the launcher lists the mod. If the game's folder is not {user_dir} (OneDrive), rerun with "
            "--user-dir and note it (A-33).",
            _NEW_GAME,
            "Unpause for one month, then quit to desktop.",
            "sae m0 check M0-2",
        ),
        "{user_dir}/logs/error.log after a fresh launch",
        "zero error.log lines mentioning sae",
        "fix descriptor/load order; bisect by deleting common/ files until error.log is clean",
        True,
    ),
    Check(
        "M0-3", "log lines reach game.log", "core",
        ("A-02", "A-03", "A-04", "A-05", "A-14", "A-15", "A-16", "A-35", "A-39", "A-40", "A-42"),
        (
            "Note the size of {game_log} and copy its first lines (about 512 bytes), then start the game "
            "(A-15: the game should truncate it; X-10: they should differ from the previous launch).",
            _NEW_GAME,
            "Unpause for 3 months at normal speed. If StarNet is enabled, confirm its monthly behaviour still "
            "runs (A-14); otherwise record A-14 as skipped.",
            "sae m0 check M0-3",
            "Save mid-month (not on the 1st), reload it once, play 1 month (A-35 is informational: "
            "hello_after_reload is expected no with mod 0.1.0), then check "
            "again: a reload to an earlier point starts a new session, so replayed months are not duplicates. "
            "Do not load a LATER save between checks: the jump looks like a skipped month.",
            "sae m0 record M0-3 pass|fail --value starnet_monthly=yes|no|n/a --value truncated_on_launch=yes|no "
            "--value banner_differs=yes|no --value hello_after_reload=yes|no",
        ),
        "SAE| lines in {game_log}: prefix text, date token, names, one HB per month",
        "HELLO and HB lines parse with the C1 parser; exactly one HB per game month; names resolved",
        "bucketed flags + constant strings; or autosave-only observation (no live log channel)",
        True,
    ),
    Check(
        "M0-4", "log flush latency <= 5 s", "core", ("A-07",),
        (
            "In a second terminal: sae m0 tail --seconds 300",
            "Unpause at normal speed for at least 10 months.",
            "Then pause within 2 game days after a month starts and wait: the new month's HB must print within "
            "5 s while still paused (hand-timed, UNVERIFIED A-07).",
            "Stop the tail with Ctrl+C or let it time out; both record the automatic verdict.",
            "sae m0 record M0-4 pass|fail --value paused_flush_s=<seconds>",
        ),
        "{results}/M0-4.tail.jsonl (wall time per line, lines per poll), the paused-on-the-1st observation",
        "no poll delivers HB lines of 2+ game months (no batching) and the paused HB appears within 5 s",
        "save-channel only: autosave observations, the log is used for acks only",
        True,
    ),
    Check(
        "M0-5", "variable printing and counters", "core",
        ("A-06", "A-17", "A-31", "A-36", "A-37", "A-38", "A-41"),
        (
            "Continue the M0-3 game until you meet another empire, then 12 more months. Only sids from SNAP "
            "lines are managed AIs (in contact with you, A-32).",
            "sae m0 check M0-5",
            "Compare one SNAP fleet= value with that empire's fleet power in the UI: "
            "sae m0 record M0-5 pass|fail --value fleet_matches_ui=yes|no",
        ),
        "SNAP lines: sid, fleet, navcap values as plain decimals",
        "at least one SNAP parses with numeric fleet power and sid >= 1; values match the UI",
        "bucketed-flag emission (coarse categories) instead of printed numbers",
        True,
    ),
    Check(
        "M0-6", "console run of sae_in.ini is applied and acked", "core",
        ("A-08", "A-09", "A-10", "A-11", "A-12", "A-13", "A-23", "A-32", "A-39", "A-41"),
        (
            "First save the game as m0_8_base (the unflagged twin for M0-8). From the M0-5 game: pick a "
            "managed AI's sid from a SNAP line and the latest HB m value "
            "(sae m0 check M0-5 prints both).",
            "sae m0 stage M0-6 --sid <sid> --m <m> --target <player sid from a PLYR line>   "
            "(econ=military plus a posture toward the player; writes {results}/stage/sae_in.ini only)",
            "Copy it by hand: copy \"{results}/stage/sae_in.ini\" \"{user_dir}/sae_in.ini\"",
            "In game (paused), open the console and type: run sae_in.ini",
            "Unpause until the next month starts; watch for stutter at the moment of `run` (A-12).",
            "sae m0 check M0-6",
            "sae m0 record M0-6 pass|fail --value stutter=none|minor|bad --value opinion_modifier_visible=yes|no",
            "Retrying? Stage again: --seq defaults to the last staged seq + 1 (an equal seq gets no ACK), and "
            "the batch expires 3 months after --m, so restage with a fresh m if more time passed.",
            "Delete {user_dir}/sae_in.ini by hand afterwards if you want a clean folder.",
        ),
        "ACK line for the staged (sid, seq); diplomacy screen shows the sae_posture opinion modifier",
        "ACK status=APPLIED code=OK for the staged sid and seq",
        "`event sae.2` with pre-staged globals; worst case between-session control only",
        True,
    ),
    Check(
        "M0-7", "ship-size knob efficacy (only if M0-1 passed)", "stretch", ("A-22",),
        (
            "Only if M0-1 found the hook: in an observe game, set str_var_ai_ship_data_factor_corvette = 100 "
            "and the other sizes 0 on one AI via the console.",
            "Run 10 game years at fastest speed (unattended), then save.",
            "Count that AI's new ships by size in the UI or the fleet manager.",
            "sae m0 record M0-7 pass|fail --value corvette_share=<0..1>",
        ),
        "share of corvettes among new builds",
        "corvette share >= 0.70",
        "fleet-size intent stays dropped (C3 v1.1); re-entry only through a C3 v1.2 proposal",
        False,
    ),
    Check(
        "M0-8", "flag-gated budget knob efficacy (A/B)", "core", ("A-21",),
        (
            "Stellaris has no seed field (UNVERIFIED): use the base save m0_8_base made right after M0-5 passed "
            "and BEFORE M0-6 staged anything (applied knobs never revert, so a later save is not an unflagged "
            "twin). No base save? Start a new game, play to contact, save m0_8_base.",
            "Game B: load m0_8_base; give that AI econ=military with the M0-6 procedure (sae m0 stage M0-6 "
            "--sid <sid> --m <m>, copy, console `run sae_in.ini`) and wait for the APPLIED ack (sae m0 check "
            "M0-6); then type `observe` in the console and run 15 game years at fastest speed (unattended, "
            "overnight is fine). Save.",
            "Game A: load m0_8_base again, type `observe` at once, run the same 15 years. Save. One game at a time.",
            "Read the same AI's fleet power in both games.",
            "sae m0 record M0-8 pass|fail --value fleet_a=<n> --value fleet_b=<n>",
        ),
        "fleet power of the flagged AI vs its unflagged twin after 15 years",
        "fleet_b / fleet_a >= 1.3",
        "between-session defines and full economic-plan overrides (per-campaign, not live, adaptation)",
        False,
    ),
    Check(
        "M0-9", "SNAP emission cost", "later", (),
        (
            "Set emit period 1 (`sae_emit_period`), run 20 game years at speed 0.95, with and without the mod, "
            "and time days per minute.",
            "sae m0 record M0-9 pass|fail --value dpm_off=<n> --value dpm_on=<n>",
        ),
        "days per minute with and without the mod",
        "under 5 % change",
        "emit every 3 months (low_impact preset)",
        False,
    ),
    Check(
        "M0-10", "keystroke injection", "later", (),
        ("Backlog (T2): not part of v0; the player presses the hotkey. Record skip.",
         "sae m0 record M0-10 skip --note backlog"),
        "10 runs sent by AHK/xdotool while paused and unpaused",
        "10 of 10 with no stray input",
        "hotkey only (v0 default)",
        False,
    ),
    Check(
        "M0-11", "autosave parse <= 3 s and fields match the UI", "core",
        ("A-18", "A-19", "A-24", "A-30", "A-34", "A-36"),
        (
            "Build the parser once: cd {repo}/parser && cargo build --release -j 1",
            "In the M0-6 game, set autosaves to monthly in the game settings (A-24, UNVERIFIED that the option "
            "exists), play 1 month, pause.",
            "Find the newest autosave under {save_dir}/<empire>/ (A-34: autosave_*.sav).",
            "sae m0 check M0-11 --save \"<that autosave>\"   (parses twice, judges the warm run; read-only)",
            "Compare the printed player fleet power, systems, tech count and alloy income with the UI: "
            "sae m0 record M0-11 pass|fail --value fields_match_ui=yes|no",
            "Optional: repeat on your largest mid/late-game 4.4 save and record its size.",
        ),
        "parse wall time, peak RSS, save size, raw date, extracted god-view fields, sae_* variables present",
        "exit 0, valid C4 output, wall <= 3 s, fields match the UI",
        "parse a subset of sections (--sections); dashboard parser cross-check",
        True,
    ),
    Check(
        "M0-12", "resource baseline", "stretch", ("A-26", "A-29", "A-43", "A-44", "A-45"),
        (
            "On your real late-game save: time 12 game months at fastest speed with sae_bridge disabled, "
            "then enabled (stopwatch or `sae m0 tail` s/month).",
            "Note the game's RAM (Task Manager) and available RAM.",
            "Note whether Performance Monitor shows \\Memory\\Pages Input/sec moving during play (A-26).",
            "Note the game process name in Task Manager (expected stellaris.exe) and whether minimising "
            "shows as minimised (A-43, A-44; A-45 is measured later with the daemon).",
            "sae m0 record M0-12 pass|fail --value s_month_off=<n> --value s_month_on=<n> --value game_rss_mb=<n>",
        ),
        "s/month with and without the mod; game RSS; fault counter; process name",
        "mod costs <= 1 % of s/game-year",
        "redesign emission (fewer records, longer period) before engine work continues",
        False,
    ),
    Check(
        "M0-13", "additive economic subplan", "stretch", ("A-21",),
        (
            "Add a zz_ economic_plans file with one extra subplan in a scratch copy of the mod (by hand).",
            "Run 10 years in observe mode; compare the resource trend with an unmodified twin.",
            "sae m0 record M0-13 pass|fail --note <what changed>",
        ),
        "resource-trend comparison",
        "the new subplan is active and StarNet's subplans are kept",
        "flag-gated budgets only",
        False,
    ),
    Check(
        "M0-14", "Ironman check", "stretch", ("A-20",),
        (
            "Start an Ironman game with sae_bridge enabled; note whether the launcher allows it. Play 2 months.",
            "sae m0 check M0-14 --save \"{save_dir}/<empire>/ironman.sav\"",
            "Open a copy of ironman.sav as a zip and read its meta entry: it should contain ironman=yes "
            "(key name UNVERIFIED, A-20).",
            "Meta-only read, as the daemon does it: sae-parse parse --save \"<copy of ironman.sav>\" --sections meta "
            "-> meta.ironman must be true.",
            "sae m0 record M0-14 pass|fail --value mod_allowed=yes|no --value console_blocked=yes|no "
            "--value meta_ironman_yes=yes|no --value sections_meta_ironman=true|false|null",
        ),
        "meta.ironman flag; SAE lines in game.log during Ironman; console blocked",
        "Ironman save parses and is flagged ironman; behaviour recorded (no live loop is expected)",
        "Ironman stays observe-only (no live loop, settled)",
        True,
    ),
    Check(
        "M1-P", "pause detectability", "later", ("A-25",),
        ("M1 experiment (T5): CPU-time rate of the game paused vs running. Not this weekend.",
         "sae m0 record M1-P skip --note M1"),
        "CPU-time rate bands", "separable bands with a 4-6 s debounce", "pause harvesting only when CLOSED", False,
    ),
    Check(
        "R-3", "mod-script cost scaling", "later", ("A-29",),
        ("Release gate: same save with sae_quiet, period=1, period=1 with 5x emits; slope = per-emit cost.",
         "sae m0 record R-3 skip --note release"),
        "s/year per configuration", "projected cost <= 1 % of s/year", "fewer records per month", False,
    ),
    Check(
        "R-5", "Windows Job and background mode", "later", ("A-27", "A-28"),
        ("Release gate: pathological save on real Windows; 1 GiB Job cap; observe kill and priority.",
         "sae m0 record R-5 skip --note release"),
        "worker exit under the Job cap; I/O priority in Process Explorer", "Job kills at 1 GB; background mode set",
        "lower parse frequency (H2) instead of the cap", False,
    ),
)

BY_ID: dict[str, Check] = {c.id: c for c in CHECKS}

#: Go/no-go rule (integration plan): which core checks decide the live loop.
GO_RULE = (
    "M0-3, M0-6 and M0-11 pass -> live dev mode is viable. M0-3 fails but M0-11 passes -> save-only "
    "observation with between-session control. M0-8 fails and M0-7 did not pass (failed, skipped or not run) -> "
    "knobs too weak for live adaptation."
)
