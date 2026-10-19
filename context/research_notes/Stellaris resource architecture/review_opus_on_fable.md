# Review: Opus 5.5 on Fable 5.1's Draft F (resource architecture)

Date: 2026-10-18. I read both drafts in full, plus the context report. I used three web lookups (Dev Diary #410, PyPI cupy-cuda12x, Microsoft SetPriorityClass). Tags: **CHECKED** means I verified it this pass. **HYPOTHESIS** means it is unverified.

## 1. Where we agree

- **No GPU on the player's machine through M4.** The online workload is about 10² flops per month. Save parsing is branchy and serial. CuPy is NVIDIA-only and heavy. The DirectML EP is in sustained engineering.
- **The game's main thread is the host.** We never touch Stellaris's priority or affinity. The monthly pulse uses the other cores too, so "spare cores" only exist between ticks.
- **Save parsing runs in a separate process.** That process exits after each save, has a hard memory cap, and runs at background priority. It waits until the save file is fully written before reading it.
- **The mod's in-game script is the cost no governor can reach.** It needs its own budget and its own A/B test.
- **Heavy compute stays off the online path.** It runs either when the game is closed or on a second machine. The most valuable use of a second machine is the M4 benchmark farm, because each Stellaris instance is limited by its main thread.
- **Hard budgets, enforced by Job Objects or cgroups, and A/B acceptance tests on the user's real 30+ mod playset.** Rerun stays off on the game PC by default. DuckDB is never resident in the engine.

## 2. Where Fable is better, and I concede

1. **RAM is the binding constraint.** Fable ranks memory above CPU and builds the budget around it. My table names RAM as "tight" but still organises the design around CPU and spm. Fable's ordering is right, because paging the game is the worst outcome.
2. **A fixed delay of at least 8 s after the autosave closes, before parsing starts.** This is simpler and more robust than my "mtime stable for 2 s". It also keeps the parse clear of the threaded burst that produced the save. Adopt both: wait for mtime stability, and wait at least 8 s.
3. **Suspend on unpause instead of killing.** Fable resumes the worker at the next pause. That is cheaper than my "preempt within one Governor poll". The caveat is in §3.
4. **Skip autosaves before reducing thread count.** Fable says to parse only every second or third save before cutting threads. My L2 did both at once. Fable's order is better.
5. **Rerun with a remote viewer is the default, not an option.** I agree. My `--memory-limit 512MB` rule still covers developers who run the viewer locally.
6. **Plain `sae-sync` (rsync or HTTP push) instead of my msgpack-over-TCP job protocol with tokens and TLS.** For a solo developer at M4, shipping files over the LAN is enough. A general job protocol across local, GPU and LAN is speculative structure. Defer it until a second job kind exists.
7. **Test A5 (low-end laptop), user presets ("Low impact / Balanced / Dev"), pause-fraction telemetry, and "zero GPU bytes" as a manifest rule.** All four are good. Risk 5, the social pressure to "use the GPU", is a real maintenance argument that I missed.
8. **NVML.** Fable cuts GPU sensing entirely through M4. My draft only senses VRAM when the compute worker is enabled. In practice nothing would import it before M5, so the sensing row should be removed from the v0–M4 Governor spec.

## 3. Disagreements and proposed resolutions

**a. Engine language: Python vs a Rust daemon.** Fable's diagram describes `sae-engine` as a "Rust binary via PyO3 or standalone; ~20 MB RSS". That silently reverses the report's decision to use a Python companion. It also conflicts with the rest of the design: pydantic bus envelopes, ReplayAdapter and SynthAdapter, and Claude-assisted development in Python. Fable's table budget is ≤ 150 MB, which a lean Python process meets (HYPOTHESIS: about 60–100 MB with NumPy and pydantic). The memory risk is the parse spike, and both drafts already move that into a Rust child process. **Resolution:** keep the daemon in Python under a strict import whitelist and Fable's tighter ≤ 150 MB RSS cap. Rewrite it in Rust only if AT-1/A1 attributes measurable overhead to the daemon itself.

**b. IDLE vs below-normal priority for the parse worker.** Here Fable is mostly right and I narrow my objection. My warning about `IDLE_PRIORITY_CLASS` was aimed at the daemon, which must keep timing log lines during the tick. For the parse worker, being preempted during the tick is exactly what we want. One mechanical correction applies (**CHECKED**, [SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)): `PROCESS_MODE_BACKGROUND_BEGIN` "can be specified only if hProcess is a handle to the current process". The worker has to set it on itself; the governor cannot apply it at spawn time, as Fable's diagram implies. **Resolution:** the daemon runs at BELOW_NORMAL with EcoQoS. The parse worker runs at IDLE class, sets background mode on itself as its first call, and adds EcoQoS. The IDLE class is inherited by children, which is fine here.

**c. Mod-script budget: ms per tick vs % of the game year.** Fable's A4 is "≤ 15 ms per monthly tick, measured via the game's own timing in `game.log`". I know of no evidence that `game.log` records tick timing, and neither draft found a script profiler. **Resolution:** the enforceable budget is ≤ 1% of seconds per game year, measured by an interleaved A/B with confidence intervals. That figure can be converted to ms per month for reporting. In the late game a month takes roughly 1–4 s at fast speed, so 1% is about 10–40 ms, which is consistent with Fable's 15 ms. Switch to a ms-per-tick figure only if M0 finds a real profiler.

**d. Pause detection.** Fable reads `game_paused` "from the log or the save header" and debounces at 3 s. `game_paused` is a console command, not a logged state. No autosave is written while the game is paused, so the save header cannot show a pause in real time. A 3 s debounce is impossible with a monthly signal. My method has its own problem. It waits for a missing monthly line longer than 3× the spm median and then checks for a drop in Stellaris's CPU rate, so it takes tens of seconds to fire. **Resolution:** run M1 test M1-P first. It checks whether the game process's CPU rate (psutil `cpu_times` delta, sampled every 2 s) separates paused from running on the real playset. If it does, that becomes the primary signal, with roughly a 4–6 s debounce, and the missing monthly line serves as confirmation. If it does not, pause harvesting only runs in game-closed or minimised states.

**e. Four governor modes vs the L0–L5 ladder.** These are two separate axes, and each draft only modelled one. Fable's PLAYING, PAUSED and IDLE describe the **game state**. My ladder describes **pressure**. Fable's single BACKOFF state also has weak triggers. A 25% rise over a 12-month baseline is too blunt, because late-game spm drifts upward on its own and changes in game speed confound it. **Resolution:** use a 3 × 4 grid. The game-state axis is {PLAYING, PAUSED, CLOSED/IDLE}. The pressure axis is {Normal, Sparse (skip saves), Decision-only (parse at commitment boundaries only), Quiet (log only)}, plus Suspended as a user command. Keep my hysteresis and the in-situ self-A/B so that natural drift is not read as SAE overhead.

**f. Opportunistic compute while paused.** Fable allows up to N−2 cores and a 2 GB worker. A paused game still renders and still holds its RAM. A *suspended* worker keeps its memory, so suspension saves CPU but not RAM. **Resolution:** while paused, use at most min(N−2, 4) cores, all with EcoQoS. Check available RAM before each pause-time job. If the game resumes and RAM is below the floor, kill the worker instead of suspending it.

**g. DuckDB placement.** This is a small difference. **Resolution:** DuckDB is an on-demand CLI or analysis tool. It runs on the LAN box, or locally only when the game is closed. It is never imported by the daemon.

**h. The M5 surrogate/GPU track.** Fable has no surrogate and limits GPU use to offline LLM work. I keep the idea but concede the framing. Making it a roadmap milestone invites exactly the scope creep Fable warns about. **Resolution:** demote M5 to a "parking lot" note. It is gated on M4 producing at least a few hundred validated runs and on a held-out validation of the surrogate. Nothing ships for it, and it needs no interface stubs.

**i. Something both drafts underweight.** Fable assumes the player is "on Ironman more often than not". In Ironman the report's live loop does not exist, because the console and `run` are disabled. During play, the companion would only watch and parse, and all adaptation would happen between sessions. That makes the online budget even smaller and moves almost all compute to game-closed time. The architecture should say so explicitly.

## 4. Factual claims in Fable's draft that are wrong or unverified

1. **Dev Diary #410 figures were conflated (CHECKED, minor).** "31% faster" is the full-run average to about 2500. The figures 11.54 vs 30.52 s per year are only the 2200 start point, where the difference is about 62%. By 2269, 4.3 had slowed to 3.14's starting pace. The direction Fable draws is correct, but the numbers should be quoted separately. The diary gives no main-thread figures, as Fable correctly notes.
2. **CuPy (CHECKED, correct).** cupy-cuda12x 14.2.0 Windows wheels are 98.8–100.2 MB. The page says "You need to install CUDA Toolkit 12.x locally", with `[ctk]` as an alternative. Fable is accurate. "Gigabytes" for `[ctk]` is plausible but unverified.
3. **Unverified or doubtful claims:**
   - `game_paused` "from the log or the save header" is unsupported (§3d).
   - "Timing in `game.log`" is unsupported (§3c).
   - "jomini can parse sections independently" is a HYPOTHESIS. jomini's text parser works over a whole buffer.
   - The `rerun --serve` flag may be stale. Recent Rerun CLIs split serving into gRPC and web modes. Check it against the current CLI reference before writing docs.
   - "30–60% of a session is paused" is labelled as a hypothesis, and it should stay one.
   - Microsoft only states that background mode "lowers the resource scheduling priorities". The specific claim that it lowers "I/O and memory priority" is a reasonable reading but not stated on the page.

**Net:** Fable's draft is tighter and more operational than mine on memory, timing and simplicity. Mine is stronger on the pressure ladder, the in-situ A/B, the Job CPU-rate caps and acceptance-test rigour. The merged design should use Fable's process topology and modes, my pressure axis and test protocol, a Python daemon, and no GPU code before M4.
