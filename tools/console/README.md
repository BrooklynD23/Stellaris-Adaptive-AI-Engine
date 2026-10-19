# SAE offline console

Offline analysis of a finished (or running) run. Never touches the game; never read by the daemon.

**Fog rule:** the views read `<run_dir>/bus.jsonl` (legal bus) only. `bus.god.jsonl` is never read here.

## Run

    uv sync --extra console
    uv run python tools/console/query.py <run_dir>                      # default: v_decisions
    uv run python tools/console/query.py <run_dir> "SELECT * FROM v_health WHERE level <> 'H0'"

Exit codes: 0 ok, 1 duckdb missing ("install the console extra: uv sync --extra console"), 2 bad run dir or SQL.
Truncated last lines and invalid lines are skipped (`ignore_errors=true`); `payload` is read as JSON.

## Views (`views.sql`)

| View | Content |
|------|---------|
| `v_events` | all `v=1` events |
| `v_decisions` | one row per `decision_id`: observation date, top archetype + max posterior, entropy, strategy/reason/switched, n intents, n rejected, emit ok, final ack, outcome reward |
| `v_intents` | emitted and rejected intents (`disposition`, `rule`, `emit_ok`, `emit_error`) |
| `v_acks_final` | LAST `intent_ack` per (country, batch seq) |
| `v_health` | kind, component, state, level, trigger, detail, metrics JSON |
| `v_governor` | monthly Governor metrics: avail_ram_bytes, fault_rate, spm, spm_med6, spm_med24, last parse rss/cpu/wall, saves_skipped, workers_killed |
| `v_deferred` | intents deferred (one batch per month, or no month counter yet): `intent_rejected` with detail `deferred: ...` (rule `rate_limit`, or `deferred` if proposal 018 adds it) |
| `v_unemitted` | every proposed-but-unemitted intent, `why` = `deferred` or `rejected` |
| `v_control` | operator commands and results (`cmd` NULL when command is null) |
| `v_belief` | posterior per archetype; `change_score_diagnostic` is diagnostic only |

## Compact the god bus (offline, refuses a live run)

    uv run python tools/console/compact_god.py <run_dir> [--keep-every-months 12]

## Remote Rerun viewer (pinned rerun-sdk 0.39)

Flags confirmed against `.venv/bin/rerun --help`. The viewer never runs on the player's machine by default.

1. Viewing host: `rerun --memory-limit 2GiB` (native viewer, listens on 0.0.0.0:9876).
2. Player machine: `sae telemetry --run <id> --rerun-url rerun+http://HOST:9876/proxy`.
3. Port 9876 has no authentication: LAN only.
4. Alternative: write an `.rrd` (`--rrd`) and open it later on a dev machine: `rerun --memory-limit 2GiB file.rrd`.

`--server-memory-limit` only bounds the `--serve-web`/`--serve-grpc` buffer (it has a default); it is a no-op here.

## Compaction caveats

- Compacting `bus.god.jsonl` while an offline T2 `sae telemetry` is reading it makes the reader see a shorter,
  renumbered file; it restarts from offset 0 and re-streams the god file.
- On Windows, compaction fails (`os.replace` raises `PermissionError`) if a reader holds the god file open. Close
  readers first.
