# Fable adversarial review: "Persistent player profiles and a bounded self-improvement loop"

Reviewer: sae-adversary (Fable 5.1). Date: 2026-10-19. Inputs: `proposal.md`, `verification_notes.md`,
`haiku_reverification.md`, `contracts/C4_sae_parse.md`, `contracts/C5_bus.md`, `contracts/C6_game_adapter.md` §4,
`docs/status/WAVE3_review.md`, `pyproject.toml`.

**Verdict: ACCEPT WITH CHANGES.** Part A (profile) is sound only in `strict` mode and only after it stops routing
god-view data through the legal bus and into the engine. Part B (gate, registry, replay tuning of the detector)
is sound; the Stage 2 statistics as written cannot pass at the stated run budget and must be re-scoped. Nothing in
the proposal reopens a settled decision, but B2 and B3 would breach C5/C6 if built as drawn.

## BLOCKERS (fix before any of this enters a plan)

| # | Where | Evidence | Scenario | Smallest fix |
|---|---|---|---|---|
| B1 | §3.2 pipeline, §3.9 item 1 | `player_snapshot` carries `legality ∈ {legal, past_godview}` and is "append[ed] to bus (new event type)". C5 §1: `bus.jsonl` holds `fow = legal/none` only; god data goes to `bus.god.jsonl`, which "the engine never opens" (C6 §4.2). A mixed-legality event cannot validate under C5 §2 (`fow` is fixed per type). | T6 adds the type with `fow=legal`; tech-path and fleet-count god aggregates now sit in the file ReplayAdapter, DuckDB views and `sae ctl status` read. T7's grep test (C6 §4.4) does not fire because the field names are new. | Split into two types: `player_snapshot` (`fow=legal`, only fields already in `LegalView`, written to `bus.jsonl`) and `player_snapshot_god` (`fow=god`, `bus.god.jsonl` only). The profile builder in `strict` mode reads only the former. |
| B2 | §3.4 R2, §3.6 `reputation` mode (proposed default), §3.9 item 5 "engine reads priors at campaign start" | In `reputation` mode `b_0`, the transition matrix and the likelihood offsets are functions of god-view aggregates (tech shares, fleet counts, §3.3 rows marked "god-view"). C6 §4.4: `sae.core` "must not read ... `GodView`, `GodObservation`". A prior derived from god data is god data with extra steps; the engine's first 30 years of decisions then depend on information no AI in the seat could have had. | Player opens campaign 6. SAE starts at `b_0 = 0.8 tech_rush` because campaigns 1–5's `tech_status` diffs (never visible to any observer) said so. The proposed fog-perturbation test (§5.4 step 4) is blind to it because it randomises only *this* campaign's god fields. | Ship `strict` and `off` only; `reputation` goes to §9 as a user decision with the fairness cost stated. Add to the fog-perturbation test: randomise every `bus.god.jsonl` of every *past* campaign and require bit-identical decisions. `profile_loaded` must record `mode`. |
| B3 | §3.2 "campaign end, or abandoned > N days"; §3.6 "rejects `past_godview` for any campaign still in progress" | No campaign identity exists. C5 `run_id` is per daemon run (C8), not per campaign; a player restarts the daemon ten times per campaign. The save folder name (`unitednations_1`, C4 §4) is reused when the same empire is started again (UNVERIFIED). "Abandoned > N days" is a guess that a holiday breaks. | Campaign summarised after 30 idle days; player resumes it; the profile already contains that campaign's god aggregates while it is live, and `b_0` for the *same* campaign has been recomputed from its own future. | Define `campaign_key = (save folder, first `run_started` game_date, playset hash)` and let a campaign close only on an explicit `sae profile close <key>` or on a new campaign in the same folder with an earlier date. Never on idle time. Label the folder-reuse assumption UNVERIFIED (M0). |
| B4 | §5.4 Stage 2 steps 2–3; §5.7 arithmetic | 3 archetypes × 10 pairs; "LCB > 0" **and** "≤ 5 pp worse in every matchup" **and** no regression on the held-out split. With n = 10 pairs a binary win-rate has SE ≈ 15 pp; the 5 pp non-inferiority bound cannot be established, so either every candidate fails (honest) or the bound is read as a point estimate (Goodhart). Racing "stop after 10 pairs" stops at the full budget. | A candidate that is genuinely +8 pp fails the per-matchup bound in one archetype by noise; the next one that is +0 passes it by noise. Promotion becomes a lottery at ~1–4 draws/month. | Pre-register one primary metric on a continuous outcome (the existing `outcome.reward ∈ [-1,1]`, averaged over 30-year windows), drop the per-matchup 5 pp rule to a *reported* diagnostic, and put a minimum n (≥ 30 pairs on the primary) into the gate so the gate refuses to run Stage 2 under-powered. |

## MAJOR (fix in the same revision)

| # | Where | Evidence | Scenario | Smallest fix |
|---|---|---|---|---|
| M1 | §3.1 row 2, §3.2 | Autosave diffs assume a monotone save sequence. C6 §2 `session_epoch` bumps on "date regression (save reload)". Players reload to undo wars. | Snapshot 2240.03 (war declared) then reload and snapshot 2240.01 (no war). Diff emits `war declared` then `war ended` in zero months; the profile learns a war that never happened. Also a fairness point: the AI learns from a timeline the player erased. | Diff only within one `session_epoch` and only forward in `game_date`; on regression drop the snapshots newer than the reloaded date before continuing. |
| M2 | §3.6 "covers Ironman between-session updates"; §3.1 dashboard "works with Ironman" | C6 §1.10: `ironman.sav` is parsed **meta-only** and never yielded; it is one file overwritten in place. There is no Ironman snapshot stream to profile from. | Ironman player expects a profile; gets nothing, or the plan adds full `ironman.sav` parsing in LIVE, which is new parser load during PLAYING that C7 never budgeted. | State plainly: Ironman campaigns contribute to the profile only through an opt-in CLOSED-state parse of the final `ironman.sav` (one snapshot, dispositions only). |
| M3 | §3.8, §7 X1 exit "beats the hyperprior on the user's campaigns"; §6 rank 5 "needs ≥ 3 campaigns" | Rolling-origin on 3–5 campaigns with 12 parameters: each fold has one test point. "≥ 25 % faster detection" measured on one campaign per fold is indistinguishable from noise. | X1 is declared done because 2 of 3 folds improved. | Make the X1 exit a *measurement*, not a pass: report log-score deltas per fold with n; the gate for feeding the profile into the engine is ≥ 8 closed campaigns **and** a positive mean delta. Until then the profile ships as the panel only. |
| M4 | §5.5 row 3 "crashes and timeouts invalidate a run" | Invalidating is survivorship bias: a candidate whose templates stall the game when losing deletes its losses. | θ drifts toward a posture that makes the player-AI crash late; metric rises. | A crash or timeout counts as a **loss for the candidate arm** and is reported; two in one batch halt Stage 2 for human review. |
| M5 | §5.5 row 2 counter-specificity metric | "advantage on matched minus advantage on mismatched" is maximised by sandbagging the mismatched counter. | Candidate makes `outscale_siege` deliberately weak against `early_aggression`; specificity soars; players feel a stupid AI off-archetype. | Specificity is a *constraint* (matched ≥ mismatched + δ) with the safety floor as a separate hard minimum per matchup; never a maximised objective. |
| M6 | §5.4 Stage 2 step 6 "each candidate may query the sealed set at most once"; §5.7 | Sealed set size and rotation source are unstated. With ~1–4 Stage 2 runs/month and a handful of mid-game start saves, the set is exhausted in a quarter; "rotation" needs a human to author new start saves, which is not budgeted anywhere. | The outer loop learns the sealed set through accepted/rejected feedback across candidates (the AIDE² leak the proposal itself cites). | Size the sealed bank (≥ 3 × the per-candidate draw), generate new mid-game starts from the farm's own runs, and retire a scenario after k uses. Put the authoring cost in §5.7. |
| M7 | §3.6 privacy; §3.9 `profile/<local_player_key>/` | `player_snapshot` includes empire data; user-authored empire, species and leader names identify people as well as usernames do. `local_player_key` is undefined (open question 8). `--record` dirs already leak raw `PLYR` (WAVE3 N2). | User shares `runs/` for a bug report; profile folder goes with it. | Snapshots and summaries hold ids only, hashed with a per-install salt; never names. `local_player_key = sha256(salt ‖ OS user)`; the CLI prints the salt location so `delete` is meaningful. |
| M8 | §3.4 R2 "pure NumPy" | NumPy is in the resource proposal's whitelist but not in `pyproject.toml` (`dependencies = ["pydantic", "psutil"]`); CI-4 asserts the lock closure (WAVE3 N5). | First profile PR fails CI-4 or quietly widens the runtime closure. | The profile builder is a CLOSED-state tool; put it in an optional extra (`sae[profile]`) and keep the daemon's closure unchanged. 12 conjugate updates do not need NumPy; stdlib `statistics` is enough. |

## MINOR

| # | Where | Evidence | Smallest fix |
|---|---|---|---|
| m1 | §3.9 item 4 "keep every Nth raw save" | WAVE3 N4: no retention for `runs/`; a save is ~20 MB. | Drop raw-save retention from v1; snapshots are KBs and are enough. |
| m2 | §5.4 Stage 1 step 4 | Fog-perturbation on *current* campaign god fields is already enforced by type (C6 §4.1, models forbid extra fields); as written the test passes vacuously. | Keep it, but its value is the past-campaign extension in B2 and a check that `PlayerRecord`/`GodObservation` randomisation changes the god bus and nothing else. |
| m3 | §3.3 "Response to AI pressure" | Before/after on SAE's own switch dates is confounded by the reason SAE switched (belief crossed a threshold because the player was already changing). | Label as descriptive; do not feed into priors until randomised logging exists (§6 rank 12). |
| m4 | §3.7 item 4 self-report | Weak prior from a question, down-weighted. | Cut. One question in a settings file is UI work for a prior you then ignore. |
| m5 | §5.3 "raw traces beat summaries for proposers (unverified claim)" | Source is a community skill file. | Mark UNVERIFIED in the table, or drop the sentence. |

## What to cut

1. **X0 hooks as drawn.** The legal feature stream already persists on `bus.jsonl` per run (`observation.player_legal`,
   `belief_update`, `strategy_selected`, `outcome`). In `strict` mode the profile needs no new parser output at all;
   it needs the campaign index (B3). Build the index first and the profile from existing buses; add
   `player_snapshot_god` only if `reputation` is ever approved.
2. **`reputation` mode** from v1 (B2). It is the whole fairness argument of the project traded for a faster prior.
3. **R3 (HMM), R4 (choice models), prototype matching (§3.7 item 3), self-report (m4).** Nothing in §3.8 can measure
   them below ~10 campaigns.
4. **Raw-save retention** (m1) and **"abandoned > N days"** (B3).
5. **The per-matchup 5 pp non-inferiority rule** at the current run budget (B4); keep it as a report line.
6. **Outer loop (X4) until X3 has promoted one tuned version.** The gate and registry (X2) are the real deliverable;
   an LLM proposer feeding a 1–4 candidate/month gate is not a bottleneck worth building yet.

Keep: campaign index, strict-mode profile from existing buses with per-fold reporting, PolicyVersion registry +
Stage 1 + one-step rollback, I1 leave-one-campaign-out detector tuning, the past-campaign fog-perturbation test.

## Citation corrections (against `haiku_reverification.md`)

| Proposal | Correction |
|---|---|
| §2 row 2: "Level 1 (net positive)", "about 90% of proposals rejected" | Both are blog-only. The paper gives 7 accepted of 99 (which is ~93 % rejected); say "7/99 accepted (paper)" and attribute "Level 1" to the blog. Keep the two reward-hacking series separate: blog 63→42→34, paper 55→39→32. |
| §2 row 4, §4 table: Falsifiable Release Gates "five invariants" | The abstract page lists INV-1 to INV-6. Say six, or list the five you use and say so. |
| §2 row 5: Clark claim "INACCURATE" | Primary (Import AI 455) returned 404 in re-check; only secondary sources carry the 30 %/60 %+ figures. Status: UNVERIFIED (primary unreachable); the 2027 framing is still wrong on the secondary numbers. |
| §4 table: "MetaRSI ... declared change equals the actual diff" | From the PDF, not confirmed on the abstract page; keep, but cite the PDF section. |
| §5.4 step 5, §5.6 item 3: "Hamlet warning" | Source lives in the core report (Hunicke, Hamlet PDF) but is absent from this proposal's source list. Add it. |
| §3.1 row 1: "last checked for 2.1" wiki; dashboard comments on 4.4/4.5 | Both already labelled; also label "finished wars resolved through timeline events" as inferred from code, not documented behaviour. |

Bottom line: the profile is worth building only as a fog-legal reputation of what the AI could have seen, and the
proposal's own fog test must cover past campaigns or it proves nothing. The gate design is right; its statistics
are sized for a farm ten times larger than the one described.
