# M0 results (template)

`sae m0 summary` writes the filled version of this file to `m0_results/m0_results.md`. Use this template only if
you record by hand.

- game_version: (e.g. 4.4.4; main menu)
- checksum: (main menu, bottom-left)
- starnet_build: (Workshop id and update date, or "vanilla")
- playset: (mods in load order)
- machine: (CPU, RAM, GPU, Windows version)

| Check | Tier | Status (pass/fail/skip) | Evidence / values | Note |
|---|---|---|---|---|
| M0-1 | core | | hook files, supported_version | |
| M0-2 | core | | error.log sae lines | |
| M0-3 | core | | HELLO/HB counts, prefix, raw date, starnet_monthly, truncated_on_launch | |
| M0-4 | core | | batched polls, median s/month, paused_flush_s | |
| M0-5 | core | | SNAP count, fleet vs UI | |
| M0-6 | core | | ACK status/code, stutter, opinion modifier | |
| M0-7 | stretch | | corvette_share | |
| M0-8 | core | | fleet_a, fleet_b, ratio | |
| M0-9 | later | | dpm_off, dpm_on | |
| M0-10 | later | | (backlog) | |
| M0-11 | core | | wall_s, rss_peak_mb, size, fields_match_ui | |
| M0-12 | stretch | | s_month_off, s_month_on, game_rss_mb, fault counter, process name | |
| M0-13 | stretch | | resource trend | |
| M0-14 | stretch | | ironman flag, mod_allowed, console_blocked | |
| M1-P | later | | | |
| R-3 | later | | | |
| R-5 | later | | | |

Go/no-go: M0-3, M0-6 and M0-11 pass → live dev mode viable. M0-3 fails but M0-11 passes → save-only observation
with between-session control. M0-8 fails and M0-7 did not pass → knobs too weak for live adaptation.
