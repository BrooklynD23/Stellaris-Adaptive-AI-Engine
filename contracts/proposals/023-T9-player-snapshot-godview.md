# 023-T9 — Player snapshot inside the god view (`GodView.snapshot`)

Status: PROPOSED — **CONDITIONAL** (2026-10-19, T9 lead). Build only if the maintainer approves plan
decision D1 (a `reputation` profile mode, or snapshot capture for offline analysis only). The v1 profile is
strict and needs none of this (Fable proposal review B2 and cut list); the architecture review's S3 seam is
satisfied by this text being ready. Reporter: T9 (expansion: player profile and bounded RSI).
Contracts: C4 §3, §4, §7 (v1 → v1.1, additive); C6 `GodObservation` (unchanged shape, carries the new field);
C5 `observation_god` (unchanged type, larger payload). Consumers: T3 (producer), T2 (routes god data, no code
change expected), T6 (god-file reader), T7 (fixtures, CI-4/CI-5 budgets), T9 (offline profile builder).
Plan: `docs/expansion/player_profile_and_rsi_plan.md` §2.

## Problem
A save is a snapshot. Player history (expansion tempo, hull mix, research mix, war timing) can only be
rebuilt by diffing successive autosaves, and autosaves rotate (A-50). SAE must keep a compact per-save record
of the player at parse time or the history is lost. The research proposal (§3.2) put this record on
`bus.jsonl`; that breaks C5 §1 / C6 §4 (god data only in `bus.god.jsonl`). This proposal keeps the record
on the existing god path instead, so **no new bus event and no new engine input**.

## Decision
1. `GodView` gains one optional field `snapshot: PlayerSnapshot | None = None` (C4 v1.1, additive).
   `null` unless all six sections ran (same rule as `god` itself, proposal 001).
2. T3 fills it in the same pass that builds `god` (no new section name, no extra file read).
3. T2 changes nothing: `ParseResult.god` already goes untouched to `GodSink` → `bus.god.jsonl` as
   `observation_god.payload.player` (C6 §4.2). The engine still never sees it (C6 §4.4 grep test unchanged).
4. Every key is UNVERIFIED until M0-11 (new A-47..A-49, below). A key not found → that field `null`, never
   an error (C4 §6).

## Exact model (`src/sae/contracts/c4_parse.py`)

(`Field(ge=0, le=1_000_000)` on ints is the existing `GameCount` alias in `common.py`; spelled out here for reading.)

```python
class HullCounts(Frozen):
    corvette: int = Field(ge=0, le=1_000_000)
    destroyer: int = Field(ge=0, le=1_000_000)
    cruiser: int = Field(ge=0, le=1_000_000)
    battleship: int = Field(ge=0, le=1_000_000)
    titan: int = Field(ge=0, le=1_000_000)
    other: int = Field(ge=0, le=1_000_000)          # juggernaut, colossus, unknown sizes

class ResearchIncome(Frozen):
    physics: float = Field(ge=0)
    society: float = Field(ge=0)
    engineering: float = Field(ge=0)

class SnapshotWar(Frozen):
    war_id: int = Field(ge=0, le=1_000_000)         # save-local id; diff key only, never leaves bus.god.jsonl
    start_date: GameDate
    player_attacker: bool
    opponents: int = Field(ge=0, le=64)

class PlayerSnapshot(Frozen):
    snapshot_version: Literal[1] = 1
    colonies: int | None = Field(default=None, ge=0, le=1_000_000)          # A-47
    starbases_upgraded: int | None = Field(default=None, ge=0, le=1_000_000)  # above outpost, A-49
    border_systems: int | None = Field(default=None, ge=0, le=1_000_000)    # player systems 1 jump from ANY other country
    fortified_border_starbases: int | None = Field(default=None, ge=0, le=1_000_000)
    naval_cap_used: int | None = Field(default=None, ge=0, le=1_000_000)
    naval_cap: int | None = Field(default=None, ge=0, le=1_000_000)
    military_fleets: int | None = Field(default=None, ge=0, le=1_000_000)
    top_fleet_power: float | None = Field(default=None, ge=0)
    hulls: HullCounts | None = None                                          # A-47
    research_income: ResearchIncome | None = None                            # A-48
    wars: tuple[SnapshotWar, ...] = Field(default=(), max_length=32)
```

`GodView` (C4 §3): add `snapshot: PlayerSnapshot | None = None`.

Size: the model has no free-text strings and caps `wars` at 32, so the serialised snapshot is ≤ 2 KiB.

## Example (C4 §4 `god`, v1.1)

```json
{"fleet_power": 12010, "systems_owned": 41, "tech_researched": 58, "alloys_income": 40,
 "fortified_starbases": 9,
 "snapshot": {"snapshot_version": 1, "colonies": 9, "starbases_upgraded": 6, "border_systems": 11,
   "fortified_border_starbases": 5, "naval_cap_used": 118, "naval_cap": 160, "military_fleets": 4,
   "top_fleet_power": 7400, "hulls": {"corvette": 2, "destroyer": 10, "cruiser": 18, "battleship": 4,
   "titan": 0, "other": 0}, "research_income": {"physics": 210.5, "society": 140, "engineering": 188.25},
   "wars": [{"war_id": 3, "start_date": "2229.11.01", "player_attacker": true, "opponents": 1}]}}
```

## Fog boundary
Unchanged and strengthened: the snapshot is god data, lives only in `bus.god.jsonl`, and the engine never
reads it. Under v1 (strict) it never reaches the AI at all; it would reach it only if a later proposal adds a
`reputation` mode (closed campaigns, map-free aggregates), and that proposal must extend the fog-perturbation
test to randomise every past campaign's `bus.god.jsonl` (plan §3, check F3). No coordinates, system ids, empire names or player names
exist in the model (the only id is `war_id`, which never leaves the god file).

## Budget (resource proposal: parse ≤ 3 s, ≤ 1 GB; god file ≤ 300 MB per campaign)
- Parse: the six sections are already walked; snapshot adds counters to the existing country/fleet/starbase
  walks plus a ship-size lookup per ship. Target ≤ +10 % `cost.wall_s` and no change to `rss_peak_bytes`
  (spans stay one at a time). Measured by T3 on the 2× pathological fixture and on M0-11's real save.
- God file: ≤ 2 KiB × saves. 200 years of monthly saves = 2,400 × 2 KiB ≈ 5 MB.
- stdout cap (4 MiB) unaffected.

## New UNVERIFIED assumptions (to T8 for `docs/m0_checklist.md`)

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-47 | Player colonies, ships with hull size, fleet membership and naval capacity used/max are locatable in a 4.4 save | M0-11 | Field `null` |
| A-48 | Monthly research income per category (physics/society/engineering) is in the country budget block | M0-11 | `research_income = null` |
| A-49 | Starbase level (outpost vs upgraded) is locatable | M0-11 | `starbases_upgraded = null` |
| A-50 | Autosaves rotate (only N kept), so history must be extracted at parse time | M0-11 | none needed (design assumes rotation) |

## Migration
C4 v1 → v1.1; `schema_version` stays 1 (C4 §7: new optional output field, contract and models updated first).
`expected.json` of existing fixtures: unchanged when T3 leaves `snapshot` absent for fixtures whose spec has no
snapshot inputs; `FixtureSpec` gains matching optional inputs in a follow-up (T3 plan), not in this proposal.

## Follow-ups
1. architect: models, schema export, C4 doc + example, CHANGES.md.
2. T3: extraction + key map rows in `parser/README.md` (A-47..A-49 labels); cost check.
3. T7: CI-5 fixture still under cap; fog grep test unchanged.
4. T8: A-47..A-50 in the M0 checklist.
