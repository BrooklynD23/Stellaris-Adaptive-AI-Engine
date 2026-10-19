# Sign-off review: merged resource-architecture proposal

Reviewer: Fable 5.1 (senior advisor). Date: 2026-10-18. Reviewed `proposal_merged.md` against both drafts and both cross-reviews. Two web lookups spent, both on the Windows Nested Jobs page.

## Verdict: **sign off with changes**

The merge is faithful. Every resolution in `review_fable_on_opus.md` §3a–j and `review_opus_on_fable.md` §3a–i appears in the merged text, and the disagreement log (§9) matches the reviews line for line. The four architect-only decisions are sound:

- **spm alone caps at H1.** Correct. Speed changes and late-game drift are not SAE overhead; H1 (skip saves) is the cheapest, most reversible action, so a false positive costs almost nothing.
- **2 GB worker only in CLOSED.** Correct. A paused game holds its working set; a suspended worker holds its own.
- **First-emit slot counter.** Correct and uniform: a running counter gives residues 0..period−1 evenly, and still spreads if `sae_emit_period` changes mid-game.
- **Worker IDLE + background + EcoQoS; Job on worker only.** Correct. My lookup upgrades the nesting claim from HYPOTHESIS to **VERIFIED**: the Nested Jobs page lists "commit charge" among the effective limits that "cannot be less restrictive" than the parent's ([learn.microsoft.com, Nested Jobs](https://learn.microsoft.com/en-ie/Windows/Win32/procthread/nested-jobs)). A 1 GB worker inside a 250 MB daemon Job would indeed be clamped. Background mode already implies IDLE CPU priority, so the explicit class is redundant but harmless.

## Required changes (text-level)

1. **§4 Sensing / H3 and `sae_quiet`: add a heartbeat.** spm and pause detection both read "wall-clock gaps between monthly `SAE|v1|` lines", but H3 sets `sae_quiet=1`, which "suppresses all emits except ACK". At H3 the Governor goes blind: no spm, no "missing monthly line" pause confirmation, and the spm de-escalation test (ratio < 1.05) can never pass. Change `sae_quiet` to: "suppresses all emits except ACK and a one-line monthly heartbeat `SAE|v1|hb|<date>`." Add the heartbeat to the mod-script budget (it is one `log` effect per month).
2. **§3 and §8, Job memory cap: say what "kill on breach" actually is.** `JOB_OBJECT_LIMIT_PROCESS_MEMORY` is a commit limit; it makes allocations fail rather than terminating the process. Rust aborts on allocation failure, so the worker dies, but the daemon must treat *any* non-zero exit or missing JSON as `health:budget_breach`, not wait for a Job message. Replace "kills on breach" with "allocations fail at the cap; the worker aborts; the daemon treats non-zero exit or absent JSON as a breach". Also add `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so a crashed daemon cannot orphan a worker.
3. **§4 Triggers: resolve the 2 s / 10 s contradiction.** "Escalation happens within one 2 s poll and must be reached within 10 s" reads two ways. Replace with: "RAM and fault triggers are evaluated every 2 s poll and act on the same poll; CI-3 and R-4 require the target level to be reached within 10 s end to end."
4. **§8 R-4: balloon target is on the H2 boundary.** H2 enters at available RAM < 1.5 GB; R-4 leaves "about 1.5 GB", so the test can pass or fail on noise. Change to "about 1.2 GB available" (or state the target as "H2 threshold minus 0.3 GB").
5. **§4 H2 trigger: define the hard-fault "baseline band".** It is undefined. Add: "baseline = median and MAD of `Pages Input/sec` over the first 10 min of the session (or the (a) run in R-4); trigger = above median + 3×MAD for 3 consecutive polls."
6. **§3 daemon self-limit: do not exit mid-session on RSS alone.** "exits cleanly if RSS stays above 150 MB" stops the live loop for a soft fault. Change to: "logs `health:budget_breach` at 150 MB and keeps running; exits only above 300 MB, and the launcher restarts it." 150 MB stays the CI-4 gate.
7. **§5 JAX row: soften the wording.** "Native Windows is CPU-only and experimental" conflates two statements in the JAX table (native Windows CPU is supported; GPU is the experimental part). Keep the verdict, reword to "no supported native-Windows GPU path". Not re-checked this pass; one of the drafts marked it VERIFIED, so confirm when the row is next touched.

## Optional cuts for a solo developer

- **Worker CPU-rate hard cap (§3, §8).** IDLE class plus background mode already makes the worker yield to everything. The cap adds a Windows-only code path, the 10000/N arithmetic, and nothing measurable. Cut it; keep the memory limit.
- **§3 Affinity paragraph.** It says "never hard-pin" and then proposes soft CPU sets as a HYPOTHESIS. Delete; EcoQoS already steers to E-cores.
- **In-situ self-A/B (§4).** Diagnostic only, never acted on, "too short to be an estimate" by its own admission. Defer to M3 or drop.
- **PAUSED state and the opportunistic queue (M2).** None of the listed pause-time jobs feed a decision. Ship M1–M3 with PLAYING/CLOSED only and add PAUSED only if M2 telemetry shows pause fraction is large *and* CLOSED time is scarce.
- **M1 telemetry process.** M1 can write `bus.jsonl` only; the Rerun streamer is a dev tool and can arrive with M2.
- **Linux/Proton equivalents** until a Linux user exists.

Remaining labels are honest: the one-reader Rerun flag, the pause fraction, the "spare cores" anecdote, and the parse-wall-time figure are all marked HYPOTHESIS or REPORT, and nothing in the document is claimed more strongly than its source supports.

## Is it buildable?

Yes. §3 gives process boundaries, priorities and IPC precisely enough to start M1; §4 gives numeric triggers; §8 gives pass criteria. The gaps left for M1 are appropriate build details: the `health` event schema, the `sae-parse` command line, and the numeric paused CPU band (which M1-P exists to find).

## Advisor's bottom line

This is a sound, deliberately small design, and the architect's own decisions are the right ones. Build it. Expect the Governor to live at H1 for most of a late modded campaign on the 16 GB reference machine, because the game itself will leave less than 2 GB free; that is the design working, not failing, and it is why the 1 GB parse cap and section-selective parsing (M0-11) matter more than any CPU knob. The one change you must make before M1 is the heartbeat line (change 1); without it the quiet mode blinds the Governor that put it there. Everything in the optional list can be cut without loss; if time is short, cut PAUSED first and the worker CPU cap second.

Sources: [Nested Jobs (Microsoft Learn)](https://learn.microsoft.com/en-ie/Windows/Win32/procthread/nested-jobs).
