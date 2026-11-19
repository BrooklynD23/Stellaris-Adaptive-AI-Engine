# Shared id reservations

One ledger for the three id spaces that parallel waves collide on. Rule (`CLAUDE.md` "Shared ids"): take the next
free value, add your row here, then write the plan, proposal or work order. The architect keeps the "Current"
column in step with `contracts/README.md` §1 and §5 when a proposal is ACCEPTED. `contracts/` stays the source of
truth for accepted values; this file only holds what is promised but not yet accepted.

Seeded 2026-11-16 from `contracts/README.md`, `contracts/CHANGES.md`, `contracts/proposals/038`, `039` and
`docs/plans/opt/IMPLEMENTATION_WAVES.md` §3. The architect should confirm the rows once.

## Next free

| Space | Next free | Check |
|---|---|---|
| Proposal number | 040 | `ls contracts/proposals` (036, 037 are reserved below, not free) |
| Assumption id | A-56 | `contracts/README.md` §5 (A-50..A-54 reserved below) |

## Contract versions

| Contract | Current (accepted) | Reserved next | Reserved by |
|---|---|---|---|
| C1 | 1 | | |
| C2 | 1.2 | | |
| C3 | 1.3 | | |
| C4 | 1.3 | | |
| C5 | 1.5 | 1.6 | proposal 036 (Wave 3) |
| C6 | 1.3 | 1.4 | proposal 036 (Wave 3, "C6 +1 minor") |
| C7 | 1.3 | | |
| C8 | 1.8 | 1.9 | proposal 036 (Wave 3) |
| C9 | 1.1 | | |
| C10 | 1.4 | 1.5, then 1.6 | 1.5 = proposal 036 (Wave 3); 1.6 = proposal 037 (Wave 4) |

## Open reservations

| Reserved | Id | Owner | For | Status |
|---|---|---|---|---|
| 2026-11-15 | Proposal 036 | architect / T4 | Wave 3 carried campaign state (`docs/plans/opt/W3_DESIGN.md`) | reserved, not written |
| 2026-11-15 | Proposal 037 | architect / T4 | Wave 4 scale-free outcome metrics | reserved, not written |
| 2026-11-16 | A-50..A-54 | proposal 036 | Wave 3 assumptions (W3_DESIGN §"Ids are provisional") | reserved |

Delete a row when its proposal is ACCEPTED or REJECTED and the "Current" column is updated.
