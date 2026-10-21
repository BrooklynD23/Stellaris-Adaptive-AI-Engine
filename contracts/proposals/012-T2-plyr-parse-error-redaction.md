# 012-T2 — Redact PLYR lines in `log_parse_error` details

Status: ACCEPTED-WITH-CHANGES (architect, 2026-10-19; C1 stays v1, clarification: enforces the existing C6 §4
fog boundary on the error path). Changes: the trigger is any line containing `PLYR` (not only a parsed type
token), the date field is kept only if it matches `YYYY.MM.DD`, and `version_skew` details on such lines carry no
line bytes. Proposed Author: T2 lead. Owner: architect. Consumers: T6 (bus), T7 (fog CI test). Breaking: no.

## Problem
C1 §5 says a `LogLineError` is reported as `health log_parse_error` "with the code and the first 200 chars".
`health` goes to the fog-legal `bus.jsonl` (`fow = none`). A malformed `PLYR` line (e.g. `fleet=-1`) would put the
player's true fleet/energy/alloys into the legal bus — a god-view leak through the error path (T2 adversary probe).

## Proposed change (C1 §5 row `LogLineError`)
"…the code and the first 200 chars; for a line whose record type is `PLYR` (or whose header cannot be parsed past the
type token `PLYR`) only `SAE|v1|PLYR|<date>` and the code." T7's fog grep/CI test may assert no `PLYR|…=` text in
`bus.jsonl`.

## Interim
T2 already redacts this way (`src/sae/adapters/logtail.py`).
