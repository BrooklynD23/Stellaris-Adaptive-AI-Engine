# 024-T8 — `tests/contract/test_doc_examples.py` glob skips C10

Status: **PROPOSED** (2026-10-19, T8). Reporter: T8. Owner: the owner of `tests/contract/` (T7).
Contracts: none changed; test-only.

## Problem
`tests/contract/test_doc_examples.py` lines 52 and 104 glob `C[0-9]_*.md`. That matches C1..C9 only, so
`contracts/C10_profile_policy.md` is never checked for doc-example drift.

## Change
Use `C[0-9]*_*.md` at both lines. No contract text changes.

## Check
Run `uv run pytest tests/contract -q` and confirm C10's examples are collected.
