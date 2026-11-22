# SAE optimization study: synthesis

Sources: `parser.md`, `daemon-io.md`, `engine-core.md`, `profile-offline.md`, `stack.md` (same folder), built on `../ARCH_OPTIMIZATION_REVIEW.md`. All 5 specialists returned. Numbers are from WSL2 plus a few Windows 11 runs; real-save and game-PC figures are UNVERIFIED. Nothing here changed code, contracts or docs.

## 1. Overall verdict
1. SAE is not CPU- or memory-bound (engine 196 us per game month; daemon 42 MB vs 150 MiB). No speed rewrite is justified.
2. The real wins are correctness and adaptiveness: a spm trigger that fires on noise, a Windows `--background` flag that distorts the parser, sessions that forget the campaign, a saturating outcome reward.
3. Do not convert the stack. Keep Python 3.12 + pydantic daemon and the Rust parser.

## 2. Merged ranked list
| # | Change | Source | Gain (measured unless noted) | Effort | Contract |
|---|---|---|---|---|---|
| 1 | Sample spm once per poll; ratio from window means, only when median(24) >= 10 s | daemon-io r1 | Real daemon went H1 at constant 1 s/3 s per month; sim 96 % false-H1 months -> 0 %; real +15 % slowdown found 8/8 (today 0/8) | 4 h | C7 4.3 proposal (touches T5 governor) |
| 2 | Parser: `THREAD_MODE_BACKGROUND_BEGIN` instead of process mode (`parser/src/background.rs:22`); `sae bench parse` passes `--background` (`src/sae/bench/cli.py:59`) | parser r1+r2; daemon-io note 3 confirms | CPU 0.431->0.336 s, faults 57k->29k, truthful RSS (33.6->107 MB). Prevents a wrong memory cap (67 MB < 103 MB commit) | 4 h | C4 1, C7 8 proposal |
| 3 | Carry belief, feature memory, commitment across sessions of a campaign | engine-core r1 | Saves 24 baseline months + 2 batches per session | 12 h | C8 6 / C5 proposal |
| 4 | Scale-free outcome metrics; clamp `expansion_rate` before Gaussian | engine-core r2, r3 | Reward +-0.92 at +-2 % change today; belief flip 28.5->3.0 months (synth, overstated) | 5 h + 2 h | C10 outcome_weights (r2); none (r3) |
| 5 | fsync only on month change if 30 s elapsed + on close; none for state.json in SYNTH/REPLAY | daemon-io r2 | Offline 30-year run -45 to -55 %; 1,084 -> 3 fsyncs | 3 h | none |
| 6 | Parser meta-only fast path (`input.rs:217-231`) | parser r3 | Linux 0.236->0.007 s, 99.9->14.9 MB | 2 h | C4 1 proposal |
| 7 | `stdin=DEVNULL` in `sae play` update (`profile/launcher.py:44`); game-running refusal + idle priority in `sae gate` | profile-offline r1, r2 | Removes up to 600 s daemon-start stall; gate no longer competes with game | 0.5 h + 1 h | C8 5 additive (gate) |
| 8 | Save watcher: poll hot folder, full rescan every 15th poll | daemon-io r3 | Idle CPU -77 to -83 % at 20-300 folders; first save after switch up to 36 s late | 4 h | none |
| 9 | Memoise tuner grid (`tune/fit.py:212-233`); profile-update cache; `lru_cache` on `normalize_game_date` | profile-offline | tune 54->12.4 s; update 9.3->0.25 s CPU unchanged history; cold update -20 % | 1.5 h / 6 h / 1 h | cache file needs C10 additive |
| 10 | Fix kit consent host (`setup.ps1:58`, `releases.astral.sh`); add Windows working-set/CPU row at M0-12 | stack r2, r3 | Avoids blocked install; only evidence that can flip the stack verdict | 1.5 h | none |

Gated on M0-11 (real large save): parser streaming filter (RSS -72 %, 12 h), zlib-rs backend, `MAX_SPAN` to 16 MiB (hostile input only).

## 3. Conflicts resolved
- fsync gain: baseline says -69 %; daemon-io measured -45 to -55 %. Use daemon-io. Its "once per game month" rule also fixes the baseline's double-sync-while-paused bug (C5 1).
- Outcome reward: baseline says "widen range"; engine-core shows the cause is simulated AIs ignoring intents and saturation. Use scale-free metrics (row 4), not wider range.
- Profile cache: baseline says per-run, no contract; profile-offline shows per-campaign and C10 additive. Use profile-offline. If maintainers move the `sae play` update after daemon exit (D5), row 9 cache drops to later.
- Pydantic: not a daemon CPU cost (all agree); 12.8 MiB RSS (stack) and 64 % of offline update (profile-offline). Fix offline with caching, not by replacing pydantic.
- Daemon import: baseline 0.88 s; stack measured 0.25 s on ext4. Treat startup as non-issue pending Windows data.

## 4. Stack decision
Don't convert. msgspec saves ~13 MiB / 0.19 s for 2-3 weeks across 10 frozen contracts; 3.13/3.14/JIT measured worse (+1.1 to +10.4 MiB, slower belief.update); mypyc breaks the pure wheel; PyO3 and Rust rewrite save at most 14 / 39.6 MiB for 6-16 weeks and duplicate the engine for 7,166 lines of Python tools. Flip if Windows daemon private working set > 100 MiB, sustained CPU > 1 % of a core, or engine work > 50 ms per game month. First lever then: msgspec.

## 5. Waves (each needs `sae-adversary` review of plan before code, and of result after, per CLAUDE.md)
| Wave | Items | Validation test |
|---|---|---|
| A (hours, no contract) | 5, 7a, 8, 10, 9 tuner memo | synth 30-year run time halves with identical bus content hash; `sae play` on a pipe ends < 1 s; tuner policy sha256 unchanged; 300-folder idle CPU < 5 ms/s |
| B (contract proposals) | 1, 2, 6 | Real-daemon run at constant 1 s and 3 s/month stays H0; injected +15 % slowdown detected in 8/8 phases; Windows parse working set matches commit (~103 MB), bench CPU matches production |
| C (adaptiveness) | 3, 4 | Two sequential sessions of one campaign: 0 baseline months, 0 revert batches in session 2; reward spread on synth wider than +-0.02 for > 20 % of rewards; V-3 re-run on a reacting adapter |
| D (gated) | streaming filter, zlib-rs, MAX_SPAN, 7b gate refusal | After M0-11: output identical on real save, RSS target met; M0-12 Windows daemon numbers recorded |

## 6. Do-not-do (union)
Skip-when-unchanged in engine (<= 224 us/month); pydantic `defer_build`; suppressing no-op bus events (needs C5 change); libdeflate; LTO; `sae_sid` prefilter; parallelism for tune/update; any stack conversion now.

## 7. Needs maintainer decision
1. `below_threshold` window as abstention a table decision may pre-empt (C3 6): lag 21->0 months for 3/4 archetypes, recommend yes (engine-core).
2. Early break of counter window: keep closed until V-3 on real recordings.
3. Per-AI safety floor (today always true): decide after M0-6.
4. Move `sae play` profile update after daemon exit (D5).
5. Parser in-process "one binary" touches the three-process rule: keep settled.
6. Build profile from saves the player already has: not before M0-11.

## 8. Caveats
All adaptiveness numbers come from circular synth data. Specialist side effects: a `~/.local/state/sae/salt` file and a `~/.local/bin/python3.14t` symlink were created and removed; uv wheel cache gained entries; a dummy `stellaris` process name was used for idle tests (game never launched).
