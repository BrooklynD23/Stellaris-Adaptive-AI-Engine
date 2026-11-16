# 032-T3 — Meta-only fast path (`--sections meta` skips the `gamestate` inflate)

Status: **ACCEPTED** (2026-11-15; maintainer-approved, Wave 1). Reporter: T3 (optimization study, Wave B item 6).
Owner: architect (C4 §1 wording; T3 does not edit `contracts/C4_sae_parse.md` or `contracts/CHANGES.md`).
Work order W1-A0. Plan: `docs/plans/opt/IMPLEMENTATION_WAVES.md` §3; plan review B6.
Contracts: C4 §1 (v1.1 → **v1.2**, rule change; `schema_version` stays 1). No model, schema, CLI-flag or exit-code
change.
Consumers: T2 LiveAdapter Ironman detection (C6 §1.10), T7 `sae bench` meta probe / `spy`, T8 `tools/m0` (M0-14
meta-only read), T5 (no change: same spawn, same exit classification).

## Problem
C4 §1 says a meta-only call "still inflates and top-level-scans `gamestate` (for `date`/`version` fallback)". The
inflate is almost all of the call's time and memory, and the zip `meta` entry normally already holds both fields.

## Change (C4 §1, v1.2)
Replace that sentence with:

1. **Fast path.** When the call is exactly `--sections meta` and the zip `meta` entry holds both `date` and
   `version`, the parser does **not** inflate `gamestate`. It still checks that a `gamestate` entry exists in the
   zip; a save without one fails with the same exit code as before.
2. **Full path.** When `meta` lacks `date` or lacks `version` (or the save has no `meta` entry), the parser
   inflates and top-level-scans `gamestate` as in v1.1.
3. **Same output.** On every input where both paths succeed, the JSON is identical except `cost`. A malformed
   `date` gives the same exit code on both paths.
4. **Accepted divergence (the only one).** A save with a valid `meta` entry and a corrupt `gamestate` entry
   (bad deflate stream, bad CRC, unparseable text) is not detected by a meta-only call: the fast path exits 0
   where v1.1 returned its error code (C4 §2). A later full parse of that save reports the error.
5. No new CLI flag. "Force the full path" is a library argument for T3's tests only.
6. Lands in parser 0.1.2 (`sae-parse --version`); `parser_version` in the output tells the two behaviours apart.

Any call with more sections than `meta` is unchanged.

## Impact
- C6 §1.10 Ironman detection uses only `meta.ironman` from the zip `meta` entry, so its result is unchanged. A
  corrupt `ironman.sav` no longer raises `parse_failed` from the detection call; the corruption surfaces only if
  that file is later parsed in full. Accepted.
- `cost` of a meta-only call drops (no inflate). Do not compare meta-only `cost` across parser 0.1.1 and 0.1.2.
- Exit codes, `ParseResult`, `ParseError` and the daemon classification (C4 §2) are unchanged.

## Migration
None for consumers. T3 (W1-3): `parser/src/input.rs`, `main.rs`, `extract.rs`, tests, version bump to 0.1.2.

## UNVERIFIED
A-47 (new, README §5): the zip `meta` entry of a real 4.4 save holds `date` and `version`. If it does not, the
fast path never fires and behaviour equals v1.1 (M0-11). Fixtures and the synthetic save do hold both.
