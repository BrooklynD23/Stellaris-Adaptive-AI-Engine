# Work-order template and test-area map

Standing rules live in `CLAUDE.md` and `.claude/agents/*.md`, which every subagent already has in context. A work
order carries only what is specific to the job. Do not restate: no git writes, never launch Stellaris, UNVERIFIED labels,
changelog handling, linters, test tiers, hand-back shape.

## 1. Implementation order (`sae-implementer`)

```text
WO <id>: <title>
SPEC: <plan path> §<sections>            (cite, do not paste)
OWNED: <files this order may edit; disjoint from every parallel order>
IDS: <contract version / A-id / proposal number reserved in docs/process/RESERVATIONS.md, or "none">
BEHAVIOUR: <1-3 lines: what must be true afterwards, as a user or consumer would see it>
PROVE: <the test(s) that must exist, each naming the behaviour; "mutation check" if safety-critical>
STOP RULE: <number or condition that decides success; "none" if not a measured change>
AREA: uv run pytest -x -q -m "not slow" <paths from §4>
NOTES: <only what the plan does not say: a trap, a parallel order touching a neighbour file>
Output style: follow ~/.claude/rules/common/subagent-output.md (i-have-adhd)
```

## 2. Review order (`sae-reviewer`, `sae-adversary`)

```text
REVIEW <code | plan | architecture | closure>: <wave or plan name>
READ: <plan path §sections, or for closure: only the changed sections/files>
CHANGE SET: <files changed in the wave, from the implementers' FILES / OUT-OF-SET lists>
REPORTED: <BEYOND-SPEC and RE-PINNED items from the hand-backs, to be checked one by one>
FOCUS: <2-4 risks worth the reviewer's time: fog, persisted state, ordering, a threshold>
WRITE TO: docs/status/<name>_review.md
Output style: follow ~/.claude/rules/common/subagent-output.md (i-have-adhd)
```

A closure order is sent only when a blocking item was tagged SEMANTIC (`CLAUDE.md` "Review gates").

## 3. Coordinator checklist per wave

1. Reserve ids in `RESERVATIONS.md`. Write the plan (2,500 words at most). Plan gate: one adversary review.
2. Cut work orders with disjoint OWNED sets. At most one test-running subagent at a time on this machine, or give
   parallel implementers Tier 1 only and run each AREA command yourself, one after the other.
3. On each hand-back: read FILES, OUT-OF-SET, BEYOND-SPEC, RE-PINNED. Collect CHANGELOG lines into `changelog.d/`.
4. Full suite once (Tier 3), then the code gate and the architecture gate with the collected change set.
5. Fix loops: Tier 1 and Tier 2 only; closure check only for SEMANTIC items; full suite once more at the end if
   code changed.

## 4. Test-area map (src -> tests)

Derived on 2026-11-16 from the ownership table in `docs/developer_guide.md` §1 and from what each test directory
imports. File-level coverage was not traced; team leads should correct rows that miss a known dependency.

| You changed | AREA paths |
|---|---|
| `src/sae/adapters/**` | `tests/unit/t2 tests/e2e/test_replay_chain.py tests/e2e/test_synth_chain.py tests/e2e/test_plyr_redaction.py` |
| `src/sae/core/**`, `src/sae/daemon/**` | `tests/unit/t4 tests/e2e/test_replay_chain.py tests/e2e/test_synth_chain.py tests/e2e/test_fog.py tests/e2e/test_fog_f3.py tests/integration/test_fog_f4.py tests/integration/test_ci4_imports.py` |
| `src/sae/core/features.py`, `state.py`, policy or outcome code | the row above plus `tests/unit/t9 tests/unit/t7` (profile, tune and gate read them) |
| `src/sae/governor/**`, `src/sae/platform/**` | `tests/unit/t5 tests/integration/test_balloon.py tests/integration/test_ci2.py` |
| `src/sae/bus/**`, `src/sae/telemetry/**`, `src/sae/control/**`, `tools/console/**` | `tests/unit/t6 tests/e2e/test_bus_checker.py`; bus event order or shape: add `tests/e2e/test_replay_chain.py` |
| `src/sae/bench/**`, `.github/workflows/**` | `tests/unit/t7 tests/integration/test_workflow.py` |
| `src/sae/profile/**`, `src/sae/tune/**` | `tests/unit/t9 tests/unit/t7` |
| `src/sae/m0/**`, `tools/m0/**`, `packaging/**`, `docs/m0_checklist.md` | `tests/unit/t8` |
| `src/sae/cli.py` | `tests/test_cli.py tests/unit/t8` |
| `src/sae/contracts/**`, `contracts/**` | `tests/contract` plus the unit directory of every consumer the proposal names |
| `parser/**` | in `parser/`: `cargo clippy -j 1 --all-targets -- -D warnings && cargo test -j 1`; then `tests/integration/test_parser_fixtures.py tests/e2e/test_fog_parser.py` (needs `cargo build --release -j 1`) |
| `mod/sae_bridge/**` | `uv run python3 mod/sae_bridge/tools/check_mod.py && uv run pytest -q mod/sae_bridge/tests` |
| daemon memory behaviour (loop, state retention, caches) | add `tests/unit/t4/test_boundary.py -m slow` (the 120-year RSS run, minutes) |

Size of the tiers (2026-11-16, 1,621 tests collected; `tests/unit` = 1,058 test functions in 89 files,
`tests/e2e` 41, `tests/integration` 21, `tests/contract` 83): see `WORKFLOW_OPTIMIZATION.md` §2 for timings.
