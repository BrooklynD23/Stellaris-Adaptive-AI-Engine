# C5 — `bus.jsonl` envelope and event types

| | |
|---|---|
| Version | 1.3 (`v = 1` in every line) |
| Status | FROZEN v1.2 (2026-10-19; payload enums follow C3/C6/C7 v1.1; CUSUM settled; v1.2 health kinds `ini_neutralised`, `ironman_observe_only` and receipt error `ironman`, proposal 017; v1.3 `run_started.policy` (019 Part A), health kinds `record_capped` (022) and `policy_pin_mismatch` (021)) |
| Owner | architect (contract); T6 implements the writer and readers |
| Producer | the daemon only (single writer per run): engine (T4), Governor (T5), adapters (T2) via the bus writer (T6) |
| Consumers | `sae-telemetry` and Rerun (T6), DuckDB views (T6), ReplayAdapter (T2), CI-1/CI-2 and e2e checks (T7), `sae ctl status` (T6) |
| Code | `src/sae/contracts/c5_bus.py` — `BusEvent`, `GodBusEvent`, payload models, `BUS_EVENT_ADAPTER` |

## 1. Files

`runs/<run_id>/bus.jsonl` (fog-legal and operational events) and `runs/<run_id>/bus.god.jsonl`
(god-view only). The bus is the single source of truth, the replay format and the audit store; the
decision audit is a DuckDB view joining rows on `decision_id`.

- One JSON object per line, UTF-8, LF, ≤ 1 MiB per line, no pretty printing.
- Append-only. The writer flushes after every event (`flush()`; `fsync` at most once per game month and on exit).
- `seq` starts at 0 and increases by exactly 1 per line **within each file**.
- The first line of each file is `run_started`; a resumed or re-executed daemon (C7 §4.6, `--resume`)
  appends without a second `run_started` and continues `seq`.
- Crash safety: a reader ignores a final line without a trailing LF. A writer reopening an existing
  file truncates such a partial last line before appending.
- No rotation in v1 (a 200-year campaign is tens of MB; budget ≤ 100 MB bus, ≤ 300 MB god file).
- `bus.god.jsonl` is written by the bus writer on behalf of adapters (`GodSink`, C6) and read only by
  developer tools (console god-view overlay, offline analysis). The engine never opens it (C6 §4).

## 2. Envelope

| Field | Type | Rule |
|---|---|---|
| `v` | 1 | contract version |
| `run_id` | `YYYYMMDD-HHMMSS-<mode>-<4 hex>` | C8 `new_run_id`, UTC |
| `seq` | int ≥ 0 | per file, gapless |
| `t_wall` | ISO 8601 with timezone (`Z`) | writer's UTC clock |
| `t_mono_ns` | int ≥ 0 | `time.monotonic_ns()`; ordering within a run only |
| `game_date` | `YYYY.MM.DD` or null | game date the event refers to |
| `type` | event type below | discriminator |
| `decision_id` | `d-YYYY.MM-NN` or null | required for `belief_update`, `strategy_selected`, `intent_emitted`, `intent_rejected`; optional elsewhere |
| `fow` | `legal` / `god` / `none` | fixed per type (table); a mismatch is invalid |
| `payload` | object | per type |

`decision_id` = game month of the decision plus a 2-digit counter within that month (00–99).

## 3. Event types

| type | fow | Payload (model) | Emitted when |
|---|---|---|---|
| `run_started` | none | `RunStartedPayload`: engine version, contract versions, adapter mode, preset, config sha256, platform, python; since v1.3 `policy` (`PolicyRef`: `version`, `sha256`, `source` `builtin\|registry\|pinned`, `fallback_reason`; C10 §4) | first line of each file |
| `observation` | legal | C6 `Observation` | each observation the engine consumes |
| `observation_god` | god | C6 `GodObservation` | **bus.god.jsonl only** |
| `belief_update` | legal | prior, loglik, posterior over the 4 archetypes; entropy; features used (`null` = unobserved); ≤ 5 `top_evidence` (feature, archetype, llr); optional diagnostic `change_score` (CUSUM: logged, **never acted on**; settled for v0 by the maintainer 2026-10-19) | each belief step |
| `strategy_selected` | legal | strategy, previous, switched, reason (`table`, `commitment_hold`, `below_threshold`, `forced`, `safety_floor`), top archetype, confidence, commitment window, reason_key | each decision tick |
| `intent_emitted` | none | the `IntentBatch` (C3) and the `EmitReceipt` (C6) | each batch write attempt |
| `intent_rejected` | none | the intent, validator `rule` (C3 §4; v1.2 adds the non-refusal audit id `deferred`, proposal 018-T4), detail | each validator rejection |
| `intent_ack` | none | seq, country, status, code, intent_ids, attempt, ack lag (months) | each mod ACK, and each engine LOST/SUPERSEDED |
| `outcome` | legal | strategy, window, forced flag, metrics, reward ∈ [−1, 1] | commitment window end |
| `control` | none | C9 `ControlCommand` (`null` if the inbox file was unreadable, too large or invalid; present for `expired`) and `ControlResult` (`error` adds `expired`, C9 §3.1, v1.1) | each operator command (status included) |
| `health` | none | kind, component, Governor state and level, trigger, metrics, detail | C7 §9 and error tables in C1/C2/C4 |

`health.kind` values: `level_change`, `state_change`, `monthly`, `budget_breach`, `parse_failed`,
`save_skipped`, `worker_killed`, `log_parse_error`, `version_skew`, `reconnect`, `ini_write_failed`,
`ack_alarm`, `daemon_exit`, `invalid_event`, and since v1.2 (proposal 017, component `adapter`):
`ini_neutralised` (detail `close` or `multiplayer`; the adapter rewrote `sae_in.ini` as the C2 §2.1
neutral file) and `ironman_observe_only` (once per daemon run; detail names the save; C6 §1.10).
Since v1.3: `record_capped` (component `adapter`; once per recording dir when C8 `record.max_bytes` stops save
copies; `metrics.bytes_used`; proposal 022) and `policy_pin_mismatch` (component `engine`; once per run when the
first observed `game_version` does not fit the loaded policy's `game_version_pin`; the policy is kept; C10 §4).
`intent_emitted.receipt.error` may be `ironman` (C6 v1.2). Monthly `health` metrics keys (T0 tier, resource proposal §7):
`avail_ram_bytes`, `fault_rate`, `spm`, `spm_med6`, `spm_med24`, `daemon_rss_bytes`, `daemon_cpu_pct`,
`parse_rss_peak_bytes`, `parse_cpu_s`, `parse_wall_s`, `saves_skipped`, `workers_killed`, `pause_fraction`.

`health.trigger` takes the C7 `Trigger` values, including `budget_breach` (C7 §7, v1.1).

`health.state`/`level` on events raised outside the Governor (adapter, parser, bus) are stamped by
the bus writer from the most recent `GovernorDecision` (PLAYING/H0 before the first poll).

A full decision chain for one month shares one `decision_id`:
`observation → belief_update → strategy_selected → intent_emitted (or intent_rejected) → intent_ack` (later)
and, at window end, `outcome` (which carries the decision_id of the decision that opened the window).

## 4. Examples

A run's bus (each line validates as `BusEvent`):

```jsonl model=BusEvent
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":0,"t_wall":"2026-10-19T03:15:00.000Z","t_mono_ns":1000,"game_date":null,"type":"run_started","fow":"none","payload":{"engine_version":"0.1.0","contracts":{"C1":1,"C2":1,"C3":1,"C4":1,"C5":1,"C6":1,"C7":1,"C8":1,"C9":1},"adapter_mode":"synth","preset":"dev","config_sha256":"3f0c9a8d2b7e4f61a5c3d9e8b7a6f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4","platform":"linux-x86_64","python":"3.12.3"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":1,"t_wall":"2026-10-19T03:15:01.000Z","t_mono_ns":2000,"game_date":"2231.04.01","decision_id":"d-2231.04-07","type":"observation","fow":"legal","payload":{"game_date":"2231.04.01","m":375,"session_epoch":1,"source":"save","player_sid":1,"managed":[{"sid":3,"name":"Tzynn Empire","applied_seq":16,"applied_econ":1,"applied_mil":2,"fleet_power":8450,"snap_date":"2231.04.01"}],"player_legal":[{"observer_sid":3,"in_contact":true,"systems_owned":41,"frontier_systems":6,"fortified_border_starbases":4,"fleet_power_cat":1,"tech_cat":1,"economy_cat":0,"at_war_with_observer":false,"player_war_count":0}],"legal_as_of":"2231.04.01","events":["save_parsed"]}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":2,"t_wall":"2026-10-19T03:15:01.010Z","t_mono_ns":3000,"game_date":"2231.04.01","decision_id":"d-2231.04-07","type":"belief_update","fow":"legal","payload":{"prior":{"turtle":0.59,"tech_rush":0.16,"early_aggression":0.12,"balanced":0.13},"loglik":{"turtle":-1.1,"tech_rush":-2.3,"early_aggression":-3.0,"balanced":-2.2},"posterior":{"turtle":0.68,"tech_rush":0.13,"early_aggression":0.06,"balanced":0.13},"entropy":0.71,"features":{"border_fort_ratio":0.67,"expansion_rate":2.5,"fleet_power_cat":1,"first_war_month":null,"tech_cat":1},"top_evidence":[{"feature":"border_fort_ratio","archetype":"turtle","llr":1.9}],"change_score":0.4}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":3,"t_wall":"2026-10-19T03:15:01.020Z","t_mono_ns":4000,"game_date":"2231.04.01","decision_id":"d-2231.04-07","type":"strategy_selected","fow":"legal","payload":{"strategy":"outscale_siege","previous":"baseline","switched":true,"reason":"table","top_archetype":"turtle","confidence":0.68,"commit_from":"2231.04.01","commit_until":"2233.04.01","reason_key":"turtle_siege"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":4,"t_wall":"2026-10-19T03:15:01.030Z","t_mono_ns":5000,"game_date":"2231.04.01","decision_id":"d-2231.04-07","type":"intent_emitted","fow":"none","payload":{"batch":{"seq":17,"issued_date":"2231.04.01","issued_m":375,"intents":[{"intent_id":"i-000381","decision_id":"d-2231.04-07","country":3,"kind":"SET_ECON_PLAN","plan":"military","reason_key":"turtle_siege"}]},"receipt":{"seq":17,"ok":true,"path":"/home/p/.local/share/Paradox Interactive/Stellaris/sae_in.ini","bytes_written":905,"sha256":"9c1185a5c5e9fc54612808977ee8f548b2258d31b6e1a5b3b7a2c1d0e9f8a7b6"}}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":5,"t_wall":"2026-10-19T03:15:01.040Z","t_mono_ns":6000,"game_date":"2231.04.01","decision_id":"d-2231.04-07","type":"intent_rejected","fow":"none","payload":{"intent":{"intent_id":"i-000382","decision_id":"d-2231.04-07","country":4,"kind":"SET_MILITARY_POSTURE","posture":"offensive","target":1},"rule":"not_in_contact","detail":"sid 4 has no contact with the player"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":6,"t_wall":"2026-10-19T03:16:30.000Z","t_mono_ns":7000,"game_date":"2231.05.01","decision_id":"d-2231.04-07","type":"intent_ack","fow":"none","payload":{"seq":17,"country":3,"status":"APPLIED","code":"OK","intent_ids":["i-000381"],"attempt":1,"ack_lag_months":1}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":7,"t_wall":"2026-10-19T03:16:30.100Z","t_mono_ns":8000,"game_date":"2231.05.01","type":"health","fow":"none","payload":{"kind":"monthly","component":"governor","state":"PLAYING","level":"H0","metrics":{"avail_ram_bytes":3200000000,"spm":4.1,"spm_med6":4.0,"spm_med24":3.9,"daemon_rss_bytes":61000000,"daemon_cpu_pct":0.4}}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":8,"t_wall":"2026-10-19T03:17:00.000Z","t_mono_ns":9000,"game_date":"2231.05.01","type":"health","fow":"none","payload":{"kind":"level_change","component":"governor","state":"PLAYING","level":"H2","trigger":"ram","metrics":{"avail_ram_bytes":1400000000},"detail":"H0 -> H2"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":9,"t_wall":"2026-10-19T03:18:00.000Z","t_mono_ns":10000,"game_date":"2231.05.01","type":"control","fow":"none","payload":{"command":{"command_id":"c-20261019T031800Z-a1b2c3","issued_at":"2026-10-19T03:18:00Z","operator":"danny","reason":"test","command":{"cmd":"pause"}},"result":{"command_id":"c-20261019T031800Z-a1b2c3","accepted":true,"applied_at":"2231.05.01","status":null}}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":10,"t_wall":"2026-10-19T04:00:00.000Z","t_mono_ns":11000,"game_date":"2233.04.01","decision_id":"d-2231.04-07","type":"outcome","fow":"legal","payload":{"strategy":"outscale_siege","window_from":"2231.04.01","window_to":"2233.04.01","forced":false,"metrics":{"self_fleet_power_delta":0.21},"reward":0.35}}
```

Proposal 017 events: an Ironman run (detection, then a refused batch), and a clean exit in another run:

```jsonl model=BusEvent
{"v":1,"run_id":"20261019-031500-live-0a1b","seq":40,"t_wall":"2026-10-19T05:00:00.000Z","t_mono_ns":40000,"game_date":"2231.05.01","type":"health","fow":"none","payload":{"kind":"ironman_observe_only","component":"adapter","state":"PLAYING","level":"H0","detail":"save games/united_nations/ironman.sav: console disabled in Ironman; observe only"}}
{"v":1,"run_id":"20261019-031500-live-0a1b","seq":41,"t_wall":"2026-10-19T05:00:30.000Z","t_mono_ns":41000,"game_date":"2231.06.01","decision_id":"d-2231.06-03","type":"intent_emitted","fow":"none","payload":{"batch":{"seq":18,"issued_date":"2231.06.01","issued_m":377,"intents":[{"intent_id":"i-000390","decision_id":"d-2231.06-03","country":3,"kind":"SET_ECON_PLAN","plan":"military","reason_key":"turtle_siege"}]},"receipt":{"seq":18,"ok":false,"error":"ironman"}}}
{"v":1,"run_id":"20261019-061500-live-1c2d","seq":30,"t_wall":"2026-10-19T07:10:00.000Z","t_mono_ns":30000,"game_date":"2231.06.01","type":"health","fow":"none","payload":{"kind":"ini_neutralised","component":"adapter","state":"PLAYING","level":"H0","detail":"close"}}
```

The matching god file:

```jsonl model=GodBusEvent
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":0,"t_wall":"2026-10-19T03:15:00.000Z","t_mono_ns":1000,"game_date":null,"type":"run_started","fow":"none","payload":{"engine_version":"0.1.0","contracts":{"C5":1},"adapter_mode":"synth","preset":"dev","config_sha256":"3f0c9a8d2b7e4f61a5c3d9e8b7a6f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4","platform":"linux-x86_64","python":"3.12.3"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":1,"t_wall":"2026-10-19T03:15:01.000Z","t_mono_ns":2000,"game_date":"2231.04.01","type":"observation_god","fow":"god","payload":{"game_date":"2231.04.01","source":"save","player":{"fleet_power":12010,"systems_owned":41,"tech_researched":58,"alloys_income":40,"fortified_starbases":9},"plyr":{}}}
```

A rejected, unreadable inbox file (`command` is `null` only when `accepted=false` and `error` is
`invalid`, `too_large` or `unknown_cmd`; proposal 005-T4):

```jsonl model=BusEvent
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":11,"t_wall":"2026-10-19T04:00:01.000Z","t_mono_ns":12000,"game_date":"2233.04.01","type":"control","fow":"none","payload":{"command":null,"result":{"command_id":"unknown","accepted":false,"error":"invalid","detail":"Expecting value: line 1 column 1","applied_at":null,"status":null}}}
```

Invalid lines (each must fail `BusEvent` validation):

```jsonl model=BusEvent invalid
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":1,"t_wall":"2026-10-19T03:15:01.000Z","t_mono_ns":2000,"game_date":"2231.04.01","type":"observation_god","fow":"god","payload":{"game_date":"2231.04.01","source":"save","plyr":{}}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":3,"t_wall":"2026-10-19T03:15:01.020Z","t_mono_ns":4000,"game_date":"2231.04.01","type":"strategy_selected","fow":"legal","payload":{"strategy":"baseline","previous":null,"switched":false,"reason":"below_threshold","top_archetype":null,"confidence":null,"commit_from":null,"commit_until":null,"reason_key":null}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":7,"t_wall":"2026-10-19T03:16:30.100Z","t_mono_ns":8000,"game_date":"2231.05.01","type":"health","fow":"god","payload":{"kind":"monthly","component":"governor","state":"PLAYING","level":"H0"}}
{"v":2,"run_id":"20261019-031500-synth-0a1b","seq":7,"t_wall":"2026-10-19T03:16:30.100Z","t_mono_ns":8000,"game_date":"2231.05.01","type":"health","fow":"none","payload":{"kind":"monthly","component":"governor","state":"PLAYING","level":"H0"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":7,"t_wall":"2026-10-19T03:16:30.100","t_mono_ns":8000,"game_date":"2231.05.01","type":"health","fow":"none","payload":{"kind":"monthly","component":"governor","state":"PLAYING","level":"H0"}}
{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":11,"t_wall":"2026-10-19T04:00:01.000Z","t_mono_ns":12000,"game_date":"2233.04.01","type":"control","fow":"none","payload":{"command":null,"result":{"command_id":"unknown","accepted":true,"applied_at":null,"status":null}}}
```

v1.3 `run_started` with a policy (built-in after a registry fallback) and a capped recording:

```jsonl model=BusEvent
{"v":1,"run_id":"20261101-200000-live-3e4f","seq":0,"t_wall":"2026-11-01T20:00:00.000Z","t_mono_ns":1000,"game_date":null,"type":"run_started","fow":"none","payload":{"engine_version":"0.1.0","contracts":{"C1":1,"C2":1,"C3":1,"C4":1,"C5":1,"C6":1,"C7":1,"C8":1,"C9":1,"C10":1},"adapter_mode":"live","preset":"balanced","config_sha256":"3f0c9a8d2b7e4f61a5c3d9e8b7a6f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4","platform":"win-amd64","python":"3.12.3","policy":{"version":"0.1.0","sha256":"0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc","source":"builtin","fallback_reason":"registry: seq gap at line 4"}}}
{"v":1,"run_id":"20261101-200000-live-3e4f","seq":90,"t_wall":"2026-11-01T23:00:00.000Z","t_mono_ns":90000,"game_date":"2261.01.01","type":"health","fow":"none","payload":{"kind":"record_capped","component":"adapter","state":"PLAYING","level":"H0","metrics":{"bytes_used":4294000000},"detail":"record.max_bytes reached; saves no longer copied"}}
{"v":1,"run_id":"20261101-200000-live-3e4f","seq":2,"t_wall":"2026-11-01T20:01:00.000Z","t_mono_ns":3000,"game_date":"2230.01.01","type":"health","fow":"none","payload":{"kind":"policy_pin_mismatch","component":"engine","state":"PLAYING","level":"H0","detail":"v4.4.* vs Corvus v4.5.0"}}
```

A policy with a fallback reason must be the built-in one:

```jsonl model=BusEvent invalid
{"v":1,"run_id":"20261101-200000-live-3e4f","seq":0,"t_wall":"2026-11-01T20:00:00.000Z","t_mono_ns":1000,"game_date":null,"type":"run_started","fow":"none","payload":{"engine_version":"0.1.0","contracts":{"C5":1},"adapter_mode":"live","preset":"balanced","config_sha256":"3f0c9a8d2b7e4f61a5c3d9e8b7a6f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4","platform":"win-amd64","python":"3.12.3","policy":{"version":"0.2.0","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","source":"registry","fallback_reason":"x"}}}
```

(Line 1: god event in the legal bus. Line 2: decision event without `decision_id`. Line 3: wrong
`fow`. Line 4: unknown version. Line 5: timestamp without timezone. Line 6: `command: null` on an
accepted result.)

## 5. Reader rules and error handling

- Validate each line with `BUS_EVENT_ADAPTER` (or `GOD_BUS_EVENT_ADAPTER`). An invalid line in a
  replayed or synced file is skipped and counted; the reader reports the count, never crashes.
- A `seq` gap or repeat means a corrupted or concatenated file: report it; do not reorder.
- Consumers take the **last** `intent_ack` per (country, seq) as the final status.
- The writer validates every event before writing. On failure (a producer bug) it raises in tests
  and in the `dev` preset; in `balanced`/`low_impact` it drops the event and writes a `health` event
  `kind=invalid_event`, `component=bus`, with the event type and the first error in `detail`.

## 6. Versioning

`v` is in every line. Any change to a payload model (even an optional field) is made through the
change protocol and lands in contract, models, schemas and all consumers together; it stays `v = 1`
only if every reader in the repo is updated in the same change. Removing or retyping a field, or
changing an event's meaning, is `v = 2`; readers reject versions they do not know.

v1.1 stays `v = 1`: every reader validates with the contract adapters, which change in the same
commit (new `Trigger` and `EmitReceipt.error` values; `FLEET_COMPOSITION_BIAS` intents no longer
validate). A pre-v1.1 dev bus holding fleet-bias intents loses those `intent_emitted` lines as
counted invalid lines on replay (§5); no player bus exists yet.

v1.3 stays `v = 1`: `run_started.policy` is optional (`null`/absent on older buses; readers assume the built-in
policy 0.1.0) and the two health kinds are additive. Whole-bus comparisons (tests, determinism hashes) drop
`run_started.policy`; replay-determinism hashes also drop `observation.payload.save_folder_key` (salted per
install, C6 §2).

## 7. UNVERIFIED assumptions

None about the game. Budget sizes (≤ 100 MB / ≤ 300 MB per campaign) are resource-proposal targets
to be measured in M2 (pause-fraction and size telemetry).
