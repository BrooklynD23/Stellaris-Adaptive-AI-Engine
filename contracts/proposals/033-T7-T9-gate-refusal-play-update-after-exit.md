# 033-T7/T9 — `sae gate` refuses while the game runs; `sae play` updates the profile after the daemon exits (D2)

Status: **ACCEPTED** (2026-11-15; maintainer-approved, Wave 1). Reporters: T7 (gate), T9 (launcher). Owner:
architect. Work order W1-A0. Plan: `docs/plans/opt/IMPLEMENTATION_WAVES.md` §3, §9; plan review B6; Wave D review 7b.
Contracts: C8 §5 (v1.6 → **v1.7**; `v` stays 1). No model or schema change.
Consumers: T7 (`sae.bench.gate.cli`, W1-5), T9 (`sae.profile.launcher`, W1-7), T8 (user guide), T4 (none: the
daemon's policy resolution, C10 §4, is unchanged).

## Problem
1. `sae gate` replays campaigns and can run the repo suite (CPU-heavy) with no check for a running game, unlike
   `sae profile update` and `sae tune`.
2. `sae play` runs `sae profile update` **before** the daemon. The update can take up to 600 s, so the daemon
   misses the start of the session; and it parses saves while the player is loading the game.

## Change 1 — `sae gate` refusal (C8 §5, v1.7)
- `sae gate stage1|stage2` refuses while the game process is detected, before any stage work: exit code **5**,
  one stderr line, no registry row, no evidence file, no sealed query spent.
- `--force` (both verbs) runs anyway.
- When it runs, it lowers the priority of **its own process only** (idle). It never touches the game's priority
  or affinity (settled decision).
- Exit codes line gains: `5` refused because the game process is detected (`sae gate` only).
- As built and unchanged: `sae profile update` exits 1 and `sae tune` exits 2 for the same refusal.

## Change 2 — `sae play` order (C8 §5, v1.7; maintainer D2, 2026-11-15)
New row text:

1. Start `sae daemon` with the same flags, first.
2. After the daemon returns, when `profile.mode != "off"` and the config resolved: print one line saying the
   profile update is starting (it can hold the terminal up to 600 s), then run `sae profile update` with stdin
   closed and **never** `--force`.
3. Return the **daemon's** exit code, whatever the update does.
4. Update exit 1 → print `run sae profile update after closing the game` (the usual cause: the game is still
   open). Any other update failure → a visible notice only.
5. Never launches Stellaris, never touches its install or mod folder (unchanged).

Consequences (stated in C8 §5):
- A session's daemon runs on the profile and policy as of the **previous** session's update.
- The update is refused while the game runs, which is common right after the daemon stops. Then no update runs
  until the user runs `sae profile update` by hand, or until a later `sae play` ends with the game closed.
- Closing the console window on Windows kills the launcher within about 5 s, so no update runs. UNVERIFIED (A-48).

## Migration
- T9 (W1-7): reorder `launcher.py`; `_game_running` / `_lower_priority` in `sae.profile.cli` keep their names.
- T7 (W1-5): import those two names unchanged; add `--force` and exit 5.
- Scripts that relied on `sae play` refreshing the profile before the daemon must call `sae profile update` first.

## UNVERIFIED
- A-48 (new, README §5): a Windows console close ends the launcher within about 5 s, before any update (M0-12).
- A-43: process-name detection of the game (existing).
