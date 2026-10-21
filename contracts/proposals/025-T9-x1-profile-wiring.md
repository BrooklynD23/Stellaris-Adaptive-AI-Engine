# 025-T9 — X1 wiring: dispatch `profile`/`play`, land `append_registry_row`, C10 §1–§2 clarifications

Status: **ACCEPTED-WITH-CHANGES** (architect, 2026-10-20, work order X1-A; see Decision). Proposed 2026-10-20 by the T9 lead; revised 2026-10-20 after Fable plan review, docs/status/X1_plan_review.md. Reporter: T9. Owner: architect. Decide in X1 batch 1 (work order X1-A).
Contracts: C8 §5, §7 (v1.3 → v1.4, additive); C10 §1, §2, §5 (v1 → v1.1, clarifications only, no schema change).
Consumers: T9 (implements), T7 (F3 test), T8 (docs). Plan: `docs/plans/T9_plan.md` (X1).

## 1. Dispatch (C8 §5, `src/sae/cli.py`)
Move two rows from "Planned, not dispatched yet" to the dispatched table and add them to `_COMMANDS`:

| Command | Module (owner) |
|---|---|
| `sae profile update\|show\|delete\|close\|exclude\|label` | `sae.profile.cli` (T9) |
| `sae play` | `sae.profile.launcher` (T9) |

Safe before the modules exist: the dispatcher already prints `not available yet (owner T9)` and exits 2.
`tune`, `policy`, `gate` stay planned (WU-6/WU-7, need M2 data).

## 2. `c10_profile.append_registry_row` lands (C10 §5, no text change beyond "landed")
Exactly as C10 §5 specifies; review-2 follow-up item 5. Signature:

```python
def append_registry_row(path: Path, row: RegistryRow) -> RegistryRow:
    """Append ``row`` with seq = last seq + 1 (0 for a new file); return the row as written."""
```
1. `row.by.startswith("human:")` and not (`sys.stdin.isatty()` and `sys.stdout.isatty()`) → `RegistryError`
   **before** the lock is taken (no lock file, no write).
2. Lock `<path>.lock` with `os.open(O_CREAT|O_EXCL|O_WRONLY)`; already present → `RegistryError("registry locked: <name>")`.
3. Re-read the file (skip-and-count invalid lines), next seq = last valid seq + 1; `row.model_copy(update={"seq": n})`.
4. Append one canonical line + LF, flush, `os.fsync`; remove the lock in `finally`.
Only `sae policy …` (WU-7, later) will call it with `human:` rows; nothing in X1 writes the registry.

## 3. C10 §1–§2 clarifications (implementation-defined today; make them contract)
1. **Run segments.** A run is indexed per `(run_id, save_folder_key)` segment: consecutive `observation`s with the
   same non-null `save_folder_key`. Observations with a null key are skipped. (A live run can load a different
   save mid-run.)
2. **Idempotent update.** `sae profile update` appends a `seen` row only for a new run segment or a later
   `last_date`. A second update over unchanged runs appends nothing. Fold of several `seen` rows per campaign:
   union of `run_ids`, `game_versions`, `policy_sha256`; min `start_date`; max `last_date`; `folder_key` from the first.
   **Run lookup** (one folder may hold several campaigns after a "new game" split or a delete), per segment:
   (a) `run_id` in any non-deleted campaign → that campaign (`seen` only if `last_date` is later);
   (b) `run_id` in a deleted campaign → skip, no row;
   (c) otherwise the folder's current campaign = the latest non-deleted campaign with that `folder_key`, ordered by
   first `seen`; then the 5-year split rule (item 3); no current campaign → new campaign
   `c-` + sha256(`folder_key|run_id`)[:16].
3. **Split prompt.** When stdin is not a TTY, a split candidate is reported on stderr and left pending (no row);
   it is asked again at the next update. "New game" writes `closed` for the old campaign (if not closed) then
   `seen` with the new id; "reload" writes `seen` on the old campaign.
4. **Deleted is terminal.** No row is appended for a `deleted` campaign; its runs are never re-indexed.
5. **Campaign order** (decay age, rolling-origin folds, "latest" for drift) = order of each campaign's first
   `seen` row. Decay age is relative to the campaign being predicted: in fold k (fit 1..k, score k+1) campaign k has
   age 0; for drift, the fit on all but the newest gives the second-newest age 0.
6. **`ProfileDoc.built_at`** = `at` of the last valid `campaigns.jsonl` row folded (not wall time), so a rebuild
   from the same index and buses is byte-identical (plan §3 F3). Profile files are written as
   `json.dumps(model_dump(mode="json"), sort_keys=True, indent=1) + "\n"`, atomically (tmp + `os.replace`).
7. **`profile.mode = "off"`**: `sae profile update` exits 2 ("profile.mode is off"); `show`, `close`, `exclude`,
   `label`, `delete` still work (privacy commands must always work). `exclude <id>` and `delete --campaign <id>` also
   remove `summaries/<id>.json` and `profile.json` at once (the next `update` rebuilds), so a privacy command takes
   effect even when `update` is blocked (mode off, game running).
8. **Sealed-query count** for the split rule comes from the registry fold; with no gate in X1 the count is 0
   (no sealed campaign retires yet).
9. **Index writer lock.** Every `campaigns.jsonl` append takes `campaigns.jsonl.lock` with
   `os.open(O_CREAT|O_EXCL|O_WRONLY)` (same pattern as C10 §5); lock present → the command exits 1 ("index locked:
   campaigns.jsonl.lock"; a stale lock after a crash is removed by hand). Under the lock: if the file is non-empty and
   does not end with LF, write one LF first (the partial line becomes a skipped invalid line); seq = last valid seq + 1;
   flush, `os.fsync`; remove the lock in `finally`. Recovery from "campaigns.jsonl corrupted" (seq gap): rename the
   index and re-run `update` (ids are deterministic; labels keyed by id survive; split answers are asked again).
10. **Labels are never overwritten.** `label` refuses (exit 2) when the target `labels/<id>.json` or
    `<dir>/label.json` exists (for synth recordings that file is the ground-truth schedule). No `--overwrite` in X1.

## 4. C8 §7
Add `tests/unit/t9/**` to the T9 row.

## Check
`uv run pytest tests/contract tests/test_cli.py -q`, `uv run mypy --strict src/sae`, `uv run ruff check`.

## Decision (architect, 2026-10-20) — ACCEPTED-WITH-CHANGES

Applied: §1 → C8 v1.4 §5 + `src/sae/cli.py`; §2 → `c10_profile.append_registry_row` + `tests/contract/test_c10_registry_writer.py`;
§3 → C10 v1.1 §1 rules 7–15 and §2 rules 1–3; §4 → C8 §7. Reasons: all ten items close real ambiguity or the
blocking Fable findings, none changes a schema, and the dispatcher is safe before `sae.profile` exists (exit 2).

Changes (the T9 implementer must build against these, not §3 as first written):

1. **§3.2 run lookup matches on the segment pair `(run_id, folder_key)`, never on `run_id` alone.** New rule:
   (a) a non-deleted campaign **with this `folder_key`** whose `run_ids` contain `run_id` → that campaign (`seen`
   only if `last_date` is later); (b) a deleted campaign **with this `folder_key`** whose `run_ids` contain
   `run_id` → skip, no row; (c) unchanged. Why: §3.1 makes a run that loads saves A, B, A three segments; with
   lookup on `run_id` alone, the B segment would be filed under campaign A (its run_id is already there), and a
   deleted A would silently drop B. Test: one run with segments in folders A then B yields two campaigns.
2. **§3.9 (and §2 for the registry): seq is counted after the LF repair.** If the final line without LF parses as a
   complete row (crash after the JSON, before the LF) it counts: next seq = its seq + 1. Only a line that fails
   validation is skipped. Why: counting it as ignored and then terminating it would write a repeated seq, which
   the next read rejects as corruption. Test: file = one valid row with no LF → next append gets seq 1, fold clean.
3. `append_registry_row` serialises with `model_dump_json(exclude_none=True)` (compact, model field order); a null
   `previous_sha256` on the first promote is therefore omitted, which parses to the same row.
