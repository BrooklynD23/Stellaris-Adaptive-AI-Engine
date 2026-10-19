# Contract changes

Newest first. Every accepted proposal adds an entry: date, contracts, version change, proposal file,
summary.

## 2026-10-19 — Architecture review follow-ups (no version moves)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| Telemetry RSS cap | C8 §2 | `sae-telemetry` budget is **≤ 150 MiB**, as in proposal §2/§8; `signoff_fable.md` changes only the daemon (150 breach / 300 exit) and leaves telemetry alone. No measurement supports 300 MB, so the code and docs at 300 MB are drift. | clarification (doc only; no model change) |
| Synth runs on a low-RAM host | C7 §3 (`forced_level`) | No contract change. The review measured 494 `save_skipped` on WSL2: the Governor reads the host's real available RAM (< 2 GiB → H1). This is correct behaviour. **The synth path does not force H0 by default**, because that would hide the Governor from synth runs. Runs that need H0 numbers (acceptance, CI-1 baselines, bench budgets) pass `--set governor.forced_level='"H0"'` explicitly and record it in `config.resolved.json`. No `synth` preset is added. | decision |

Follow-ups: T6 — `src/sae/telemetry/stream.py:25` `RSS_LIMIT = 150 * 1024 * 1024`; docstring `stream.py:130` "300 MB" →
"150 MiB"; `docs/components/T6.md:75`, `docs/plans/T6_plan.md:163`, `docs/status/T6.md:37`; T7 —
`docs/user_guide.md:62` "300 MB" → "150 MB". If T6 measures rerun-sdk steady RSS above 150 MiB, file a proposal
with the numbers. T7 — synth acceptance/bench runs set `governor.forced_level` to `H0` where they compare against H0 numbers.

## 2026-10-19 — Proposal 018-T4 ACCEPTED (C3 → v1.2, C5 v1.2)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| 018-T4 `deferred` audit id | C3 §4; C5 `intent_rejected` row; `RejectRule` | `RejectRule.DEFERRED = "deferred"`: proposed intent held back (no `m` yet, or one batch per game month), re-proposed later; detail `deferred: …`; not a validator failure; `rate_limit` stays a refusal | additive enum, v1.2 |

Follow-ups: T4 — `core/engine_tick.py` `_deferred()` → `RejectRule.DEFERRED`. T6 — views/status count
`deferred` apart from refusals. T7 — bus checks accept it.

## 2026-10-19 — Proposal 017 amendment (clarification, stays v1.2)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| Ironman + exit (maintainer) | C2 §2.1; C6 §1.9, §1.10 b | the `close()` neutral write happens even while Ironman observe-only is latched; Ironman still blocks every intent write and the `mp=1` neutral write | clarification |

Follow-up: T2 — `live.py` `close()` writes the neutral file regardless of the Ironman latch; test it.

## 2026-10-19 — Proposal 017-Wave3 ACCEPTED (C2, C5, C6 → v1.2; C4 clarification)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| N3 neutral ini (maintainer) | C2 §2.1, §5; C6 §1.9; C5 §3 | adapter rewrites `sae_in.ini` atomically as the knob-only neutral file (latest `HB`/`HELLO` `period`/`quiet`, else 1/0; no country lines, no seq) on `close()` (clean exit incl. SIGTERM) and on each transition to `HELLO mp=1`; never deletes; daemon closes the adapter before the sink; `health ini_neutralised` (detail `close`/`multiplayer`), failure → `ini_write_failed` outside the `ack_alarm` count | additive enum + rule, v1.2 |
| N6 Ironman observe only (maintainer) | C6 §1.10, §3, §7 (A-20); C5 §3; C4 §1 | LIVE parses `ironman.sav` (modified after start) with `SaveParser.parse(path, sections=("meta",))`; `meta.ironman == True` latches: one `health ironman_observe_only`, `emit()` → `EmitReceipt(ok=false, error="ironman")`, no writes; engine tracks no ACK for such batches (no LOST, re-emit, `ack_alarm`, `ini_write_failed`); C4 `--sections meta` covers meta-only (no new mode) | additive enum + keyword + rule, v1.2 |

Why v1.2: models change (`HealthKind` +2, `EmitReceipt.error` + `ironman`, `SaveParser.parse(*, sections=None)`),
but no wire field moves and the neutral file is a knob-only file a v1 mod already accepts.
Follow-ups: T2 — neutral write + Ironman latch in `live.py`, fakes accept `sections`. T4 — `saveparser.py:105`
`sections`, close adapter before sink on every exit path, ignore `ironman` receipts for ACK tracking. T7 —
user guide §6/§7. T8 — M0-14 `ironman=yes` check.

## 2026-10-19 — Wave 3 N1 clarification (no version moves)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| N1 `systems_owned` description | C4 §3 `LegalView.systems_owned` | field description now matches the C4 table and `parser/src/features.rs`: all player-owned systems (v0 approximation, not intel-masked; A-19 UNVERIFIED); schema regenerated | clarification (description only) |

## 2026-10-19 — Proposal 015-T7 ACCEPTED-WITH-CHANGES (no version moves)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| 015 synth session recorder | C6 §3 `synth` row; C8 §5 `sae synth` row | `SynthAdapter(*, record_dir=None)` writes the LiveAdapter record format: `game.log` (pulse lines incl. ACKs), `game.log.ts` (`<size> <i*1e9>`), `saves/<offset:012d>-autosave_<date>.sav`; `sae synth --record <dir>`; non-empty dir → exit 2; record error → one `invalid_event`, run continues | additive keyword + CLI flag |

Follow-ups: T2 — implement + unit test (synth record → `replay --ack-model from_recording` gives the same
strategy sequence). T7 — move CI-1 to record + replay; delete `tests/e2e/_session.py` once T2 lands.

## 2026-10-19 — Proposal 016-T6 ACCEPTED (C9 → v1.1)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| 016 command expiry | C9 §2, §3, §3.1; C5 `control` row | `ControlResult.error` adds `expired`; daemon rejects at pickup a command issued before its session started, > `COMMAND_MAX_AGE_S` (60 s) old, or > `COMMAND_MAX_FUTURE_S` (5 s) ahead (`command_expired`); event keeps the parsed command; `sae ctl` writes nothing unless `runs/CURRENT` names the run with a live pid | additive enum + rule, v1.1 |
| 016 `set_layer` | C8 §2 | canonical `c8_config.set_layer(sets)`; shares a helper with `env_layer` | additive function |

Follow-ups: T6 — `open_inbox`/`FileInbox` take `started_at` and apply `command_expired`; `sae ctl` liveness
check before writing; drop `control/cli.py:62` `set_layer`. T4 — `daemon/control.py` `apply_item` completes
items that carry both a command and an error without calling the engine; drop `daemon/cli.py:52`
`_set_layer`. T2 — drop `adapters/cli.py:55` `_set_layer`.

## 2026-10-19 — Proposal 014-T2 decided (no version moves)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| 014-T2 `parser_exe` debug fallback | C8 §3, `PathsConfig.parser_exe` description | default lookup PATH → `parser/target/release` → `parser/target/debug`; fixed order (no mtime pick); repo paths are dev-only and absent in a packaged install (falls through to "not found"); every parser-spawning entry point resolves once and passes the path on; resolved path logged at start. Accepted with changes: the last two clauses | clarification (default only; field unchanged) |

## 2026-10-19 — Proposals 009–013 decided; counters decision (no version moves)

| Item | Contracts | Change | Kind |
|---|---|---|---|
| 009-T2 adapter health hook (option C) | C6 §1.8, C8 §5.1 | `make_adapter(config, parser, god_sink, *, health=None)`, `health: Callable[[HealthKind, str \| None], None]`; caller stamps component/state/level/date; `health=None` → log only, no `sae.core` import. Options A/B rejected | clarification (C8 §5.1 already allows extra keyword-only params) |
| 010-T6 optional extras | C8 §7, `pyproject.toml` | extras `telemetry = rerun-sdk==0.39.0`, `console = duckdb>=1.1,<2`; runtime deps unchanged | additive |
| 011-T2 replay/synth lockstep | C6 §1.7 | in `replay`/`synth` the consumer handles observation N (incl. awaited `emit` + receipt) before pulling N+1, and handles ACKs yielded before N+1 first; adapter yields a pulse's ACKs before its observation | clarification of the C6 §3 determinism promise |
| 012-T2 PLYR redaction | C1 §5 | any line containing `PLYR` → `log_parse_error` detail is the code + `SAE|v1|PLYR` (+ `|<date>` if field 4 is `YYYY.MM.DD`); `version_skew` detail on such lines has no line bytes | clarification (enforces C6 §4 on the error path); accepted with changes |
| 013-T8 registry | README §5 | A-43 game process names, A-44 `IsIconic`, A-45 `process_iter` < 100 ms (M0-12, C7) | doc only |
| Counters (maintainer, 2026-10-19) | C3 §6 | `outscale_siege` and `early_punish` both kept in v0; identical game values (econ military, offensive → player), differ only in `sae_p_reason`; split returns with the hull mix after M0-7 (A-22) | decision record, doc only |

Follow-ups: T4 — pass `health=` in `wiring.live_adapter_factory` and the replay/synth factories; implement
C6 §1.7 in `loop._feed_obs` for REPLAY/SYNTH; keep both counters distinct. T2 — make `health` keyword-only;
widen `logtail._redact` to the 012 rule (today `SAE|v1|PLYR|fleet=12010|…`, `SAE|v1|PLYRfleet=12010` and
`SAE|v1| PLYR|…` leak field values); drop the `_sink_health` fallback once T4 wires `health=`. T7 — fog test:
no `PLYR` field text in `bus.jsonl`; CI-4 on the regenerated lock. T8 — rename X-01..X-03 to A-43..A-45.

## 2026-10-19 — Proposal 008 ACCEPTED; maintainer decisions recorded (C2, C3, C5, C6, C7, C8 → v1.1)

New rule (README §3.7): **v1.N** = models or rules change but every wire version field stays at the
major and every byte a v1 producer emits stays acceptable to the deployed mod. `CONTRACT_VERSIONS`
and `run_started.contracts` keep `1` for all nine contracts.

| Item | Contracts | Change | Kind |
|---|---|---|---|
| 008-T5 §1 factories | C7 §1, C8 §5.1 | `make_governor(cfg, min_level, preset, *, cpu_count, emit_period)`, `make_sensors(game_names)`, `make_platform()`; daemon calls `lower_self_priority()` once | doc (interface record) |
| 008 §2 | C7 §7, C5 | `Trigger.BUDGET_BREACH = "budget_breach"` | additive enum, v1.1 |
| 008 §3 | C7 §4.4 | step test skipped only when **both** months < 5 × poll_s; such samples stay out of the ratio windows | rule change, v1.1 |
| 008 §4 | C7 §4.2 | `GovernorConfig.fault_mad_floor = 1.0` (≥ 0); threshold uses max(MAD, floor) | additive config, v1.1 |
| 008 §5, §7, §8 | C7 §3, §4.3, §4.5 | sustained H2 counts only H2-triggered months; CLOSED month floored at 5 × poll_s; `months > spm_short_months` is a discontinuity | clarification |
| 008 §6 | C7 §9 | `GovernorConfig` rejects `h3 + margin < h2` or `h2 + margin < h1` (defaults pass) | config tightening, v1.1 |
| Composition root | C8 §5.1 | `sae.bus.open_sink(run_dir, run_id, config, *, resume)`, `sae.control.open_inbox(run_dir)`, `sae.adapters.make_adapter(config, parser, god_sink)` recorded as contract | doc (interface record) |
| M-1 CUSUM | C5 §3 | `change_score` is diagnostic only, logged, never acted on: **settled for v0** (was D-18, now maintainer-confirmed) | clarification |
| M-2 default preset | C8 §4 | `low_impact` = H1 floor, `balanced` default: **confirmed** (D-12) | clarification |
| M-3 fleet bias dropped | C3 §2/§3/§4/§6/§7/§8, C2 §3/§4/§5/§6, C8 §2 | `FleetCompositionBias` removed from the `Intent` union and moved to `RESERVED_KINDS` (validator `reserved_kind`); `CountryCommand.ship` removed; the five `sae_p_ship_<size>` slots stay in the C2 template, always -1 (`SHIP_SLOTS`), `parse_ini` rejects other values; bias columns removed from the counter table; C2/C3 batch-17 example without the bias intent; `engine.safety_floor_ship_min` kept but unused (removing a key is C8 v2) | narrowing, v1.1 |
| M-4 multiplayer guard | C6 §1.6, C2 §5, C5 | adapter (T2) refuses every write while the latest `HELLO` said `mp=1`: `EmitReceipt(ok=false, error="multiplayer")`; visible as `intent_emitted` + engine `health ini_write_failed` (`detail` `seq <n>: multiplayer`); excluded from the `ack_alarm` failure count | additive enum + rule, v1.1 |

Why v1.1, not v2: the mod sees identical bytes (C2 template, variable order, `sae_p_schema = 1`;
ship slots were already allowed to be -1), log lines are unchanged, and the bus `v` stays 1 because
every reader validates through the contract models updated here. Why not a bare v1 clarification:
models changed, and T2/T4 code that built `FleetCompositionBias` or `CountryCommand.ship` breaks.

Why keep the C2 ship slots: removing them is a template change (C2 v2, `sae_p_schema = 2`, mod
change) and M0-7 re-entry would be a second one. Re-entry condition (backlog): M0-7 shows an AI
script reading a per-size knob (A-22) → a C3 v1.2 proposal restores the v1 kind (0..100, 50 =
neutral, `safety_floor` ≥ 10) with no mod or template change.

Open: with fleet bias gone, `outscale_siege` and `early_punish` expand to the same knobs and differ
only in `reason_key` (maintainer item). A file already on disk from a single-player session is not
neutralised when `mp=1` (the engine never deletes the file).

Follow-ups: T1 none required (template unchanged; SEMANTICS may note slots are always -1). T2: drop
`ship=` in simulator/roundtrip tests, implement C6 §1.6 in the shared write path. T4: drop
`FleetCompositionBias` from expansion/validator/strategy, pass `emit_period=`, call
`lower_self_priority()`, exclude `multiplayer` from `ack_alarm`. T6: build `open_sink`/`open_inbox`
to C8 §5.1. T5: `Trigger.BUDGET_BREACH`, `fault_mad_floor`.

## 2026-10-19 — Proposal 006 ACCEPTED-WITH-CHANGES (C1, C2, C3, C6 stay v1)

| Proposal | Contracts | Change | Kind |
|---|---|---|---|
| 006-T1 mod semantics | README §5, C1 §3/§5/§7, C2 §1/§3/§5/§7, C3 header, C6 §2 | Registry A-36..A-42; A-14 reworded (global `on_monthly_pulse`); A-35 marked unused by mod 0.1.0; C2 §5 rule order = SEMANTICS §6 (R1–R3, R5, R8, R10); sticky `sae_managed` (R4); HELLO also on mid-campaign init (R6); first HB without HELLO opens a session at the current epoch; `applied_seq` may go down across epochs; mod 0.1.0 omits `en/mi/al/al_inc` (R9); monthly event named `sae.0`; MP-desync and no-op fleet-bias notes | clarification (doc only, no model or wire change) |

Changes vs the proposal: A-42 fallback is a constant `name=?` (valid v1), not a v2 bump. The mod-side
cap `@sae_max_managed = 16` (C6 §2) stays required; SEMANTICS §4 step 4 and the 0.1.0 script lack it
(T1 conformance gap). `gid=`/`sidn=` stay backlog. Consumers: T2 (simulator + first-HB session),
T8 (M0 checklist), T4 (already conformant: seq floor never lowers `next_seq`; reload detected by date
regression).

## 2026-10-19 — Proposal 007 ACCEPTED (C1, C4, C6 stay v1)

| Proposal | Contracts | Change | Kind |
|---|---|---|---|
| 007-T4 int bounds | C1 §3, C4 §3, C6 §2, README A-17 | `le=CLAUSEWITZ_INT_MAX` (1,000,000) on SNAP `seq`, ACK `applied`, `SelfState`/`ManagedState`/`FixtureCountry` `applied_seq`/`pending_seq`, `IntentAck.applied`, all `LegalView` ints; new aliases `SeqOrZero`, `GameCount` | tightening, v1 clarification |

Why no version bump: A-17/D-06 already cap every integer SAE writes into the game at ±1,000,000, and the
mod only stores values SAE wrote (seq) or its own counters (`sid`, `m`, same cap via `Sid`/`MonthIndex`).
LegalView counts are game counts orders of magnitude below the cap. No document a conforming v1 producer
can emit becomes invalid, so this states an existing limit rather than changing a type or meaning
(C1 §6, C4 §7, C6 §6). Precedent: 002/003 tightenings stayed v1. Consumers: T2, T3, T4 (see proposal).

## 2026-10-19 — Proposals 001–005 ACCEPTED (all contracts stay v1)

T4's proposals were renumbered 002-T4 → 004-T4 and 003-T4 → 005-T4 (number collision with T3).

| Proposal | Contracts | Change | Kind |
|---|---|---|---|
| 001-T3 section subset semantics | C4 §1 | `--sections` subset: `meta` always runs; `self_state = {}`, `god = null` unless all six ran; features of skipped sections `null`; live loop passes all sections | clarification (doc only) |
| 002-T3 strict `FixtureSpec` | C4 §5 | `FixtureSpec`/`FixtureCountry`/`FixtureStarbase`/`FixtureWar` fields per-field `Strict()`; ids ≥ 0; `pad_bytes` ≤ 2 GiB; no self-loop hyperlanes or self-contacts | tightening; generated specs unaffected |
| 003-T3 unique `observer_sid` | C4 §3 | `ParseResult` rejects duplicate `observer_sid` in `legal`; duplicate-`sae_sid` countries excluded from `self_state`/`legal` | tightening; T3 already complies |
| 004-T4 adapter factory | C8 §5, C6 | `run(config, adapter: GameAdapter \| AdapterFactory, run_id)`; `AdapterFactory` alias in `c6_adapter` | additive |
| 005-T4 unloggable rejections | C5 §3/§4, C9 §3, C3 §4 | `ControlPayload.command` nullable (only for rejected `invalid`/`too_large`/`unknown_cmd`); raw-input order `reserved_kind` before `schema`, logged as `health invalid_event` | optional widening (C5 §6) + doc note |

## 2026-10-19 — C1–C9 FROZEN v1

Three `sae-adversary` rounds (the cap) plus one `sae-reviewer` code review. Round 3's single blocker
was fixed before freezing; no blocking issue is open.

| Round | Blocking | What changed |
|---|---|---|
| 1 (adversary) | 5 | `SelfState` gained `applied_seq`, `pending_seq`, `applied_econ`, `applied_mil` (seq continuity under `quiet`/restart); ini write failures raise `ack_alarm` instead of auto-pausing (S is operator-only); `SpmSample` measured at poll resolution with `months`; mod-side cap `@sae_max_managed = 16`, `engine.max_managed ≤ 16`; daemon never reads `ParseResult.god` (T7 grep test). Plus 15 non-blocking: DUPLICATE semantics aligned C1/C2, `sid` on first pulse, knobs read from the player country, A-34 autosave names, `ack_alarm` cadence, release-only `mode` rule, health state stamping, observer mode `legal = []`, name truncation, LF in A-11. |
| review (code) | 1 HIGH | `SAE_CONFIG` skipped by `env_layer`; ACK status/code pairs enforced; `extra`/`name` injection rejected; ASCII-only digits with 15-digit cap; strict knob-line order; final LF required; `version_skew` before field count; CLI import errors not masked. |
| 2 (adversary) | 3 | `ManagedState.pending_seq`; seq floor rule on every observation and ACK; `S` rejected as `forced_level`/`min_level`. Plus: H3 exit restores `emit_period`; spm step test skipped below 10 s/month; `EmitReceipt` `closed` defined; Windows-safe re-exec; `runs/CURRENT` holds `<run_id> <pid>` with a liveness guard; A-35 HELLO-on-load; `runs_dir` absolute per-user default; marker `SAE|v`. |
| 3 (adversary) | 1 | Re-exec passes `--runs-dir`, `--resume` takes config from `config.resolved.json`, and the single-instance guard admits the resuming child. Plus: DUPLICATE ACK carries `applied=`; `sae ctl` takes common flags; release-mode control semantics; log identity change (inode/creation time) resets the tail offset. |

Deferred, non-blocking: stricter `invalid` example assertions (fence `reason=`); frozen models
holding `dict` fields are not hashable (documented in docstrings).

## 2026-10-19 — Wave 0: C1–C9 drafted (v1)

Initial drafts by the architect from the design report, the merged resource proposal as amended by
`signoff_fable.md`, and `advisor_review.md`.

### Decisions beyond the source documents

| # | Contract | Decision | Why |
|---|---|---|---|
| D-01 | C1, C2 | Countries are identified by `sid`, a mod-assigned running counter stored in `sae_sid`; it is also the stagger slot. Targets in C2 are sids. | `[This.GetID]` doubtful (advisor); names are not unique or safe as flag names; the signoff's first-emit counter already exists. |
| D-02 | C1 | `name=` is always the last field and runs to end of line; unknown keys kept in `extra`. | Names may contain `|`; forward compatibility. |
| D-03 | C1 | The heartbeat record is `HB` (uppercase), not `hb`. | One grammar for all record types. |
| D-04 | C1, C2 | Mod month counter `m` in HB; batch expiry is `sae_p_expires` in `m` units, enforced by the mod. | `ttl_months` must be enforceable in script without date arithmetic. |
| D-05 | C2, C3 | Every country line sets all 14 variables (`-1` = keep); the engine restates the full desired knob set (desired-state rule); the file holds one batch; overwritten batches become `SUPERSEDED`. | Makes overwrite races and missed hotkey presses harmless. |
| D-06 | C2 | Integers only, |x| ≤ 1,000,000. | Clausewitz fixed-point variables; no float formatting risk. |
| D-07 | C3 | Ack unit is (country, seq); statuses add `DUPLICATE`, `STALE`, `SUPERSEDED`; re-emission after LOST uses a new seq and the same intent ids. | The report's per-intent ack is not expressible per variable in script. |
| D-08 | C4, C7 | Exit 0 = ok, 2–6 with a `ParseError` JSON = clean failure, anything else = budget breach. | Signoff change 2 without misclassifying a missing file as a memory breach. |
| D-09 | C4 | `sae-parse fixture` renders synthetic saves; only T3 knows the save layout. | One renderer, no drift between Python and Rust. |
| D-10 | C4 | Diplomacy-screen categories approximated by ratio thresholds 0.5 / 0.8 / 1.25 / 2. | The report's fairness approximation needs numbers. |
| D-11 | C7 | Daemon above 300 MiB re-execs itself in place (no supervisor). | Signoff change 6 says "the launcher restarts it"; a supervisor would be a fourth process. |
| D-12 | C7, C8 | `low_impact` preset = H1 floor (sparse parsing) + `emit_period 3`; H3 knob request is `emit_period 12`, not `quiet`. | The proposal used "Low impact" both as an H3 trigger and as "sparse parsing". |
| D-13 | C7 | Signoff optional cuts applied: no worker CPU-rate cap, no CPU sets, no self-A/B; PAUSED off by default; no worker suspension in v1. | Signoff recommendation for a solo developer. |
| D-14 | C9 | Control plane is a file inbox under `runs/<id>/control/`, polled every 2 s; commands pause, resume, force_strategy (null clears), status. | No network protocol before M5; advisor cut the command set. |
| D-15 | C8 | Added `sae telemetry` subcommand (T6) and package paths `src/sae/bench` (T7), `src/sae/m0` (T8), `tests/unit/<team>`. | The third process and the `sae bench`/`sae m0` CLIs need homes. |
| D-16 | README | M0 ids: integration plan numbering, M0-12 = resource baseline, M0-14 = Ironman check (renumbered). | The resource proposal reused M0-12. |
| D-17 | common, C3 | Archetypes turtle, tech_rush, early_aggression, balanced (orchestrator's settled list, replacing the report's turtle/aggressor/builder); tech_rush → `early_punish`, balanced → `baseline`. | Settled decision. |
| D-18 | C5, TEAM_PLAN | CUSUM only as the diagnostic `change_score`; never acts. | Report cuts CUSUM from v0; orchestrator's T4 brief lists it. Flagged to the orchestrator. |
| D-19 | C3 | Fleet bias 50 = neutral; validator `safety_floor` rejects any size weight < 10. | The safety floor needs a checkable rule. |
| D-20 | C5 | Single `bus.jsonl` (no rotation); first line `run_started`; new event `intent_rejected`; `health` kind `invalid_event`. | Proposal size budget fits one file; plan/action gap must be measurable. |
