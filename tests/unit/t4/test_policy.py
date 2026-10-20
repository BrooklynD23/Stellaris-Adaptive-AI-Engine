"""WU-2: policy resolution (C10 section 4), built-in equals code constants, run_started pin, resume refusal."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import replace
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c5_bus import HealthKind, HealthPayload, PolicyRef
from sae.contracts.c8_config import EngineConfig, SaeConfig
from sae.contracts.c10_profile import PolicyVersion, RegistryRow, canonical_json, policy_sha256
from sae.contracts.common import COUNTER_TABLE, Archetype, Strategy
from sae.core import policy as core_policy
from sae.core.belief import LikelihoodModel
from sae.core.expansion import DEFAULT_EXPANSIONS
from sae.core.outcome import WEIGHTS
from sae.core.policy import builtin_policy_doc, load_builtin
from sae.daemon import policy_resolve, run_engine, wiring
from sae.daemon.policy_resolve import PolicyChanged, check_resume, effective_engine_config, resolve_policy

from .fake_synth import Sim
from .fakes import FakeAdapter, FakeGovernor, FakeInbox, FakePlatform, FakeSensors, FakeSink, make_deps
from .fakes_cfg import eff

T0 = datetime(2026, 10, 20, tzinfo=UTC)
HEX = "a" * 64
BUILTIN_SHA = policy_sha256(builtin_policy_doc())  # computed: C10 section 4 calls the literal informative


def cfg_for(tmp: Path, pin: str | None = None) -> SaeConfig:
    return SaeConfig.model_validate({"paths": {"sae_dir": str(tmp)}, "policy": {"pin": pin}})


def candidate(rho: float = 0.05) -> PolicyVersion:
    b = builtin_policy_doc()
    det = b.detector.model_copy(update={"rho": rho})
    return b.model_copy(update={"version": "0.2.0", "parent_sha256": policy_sha256(b), "detector": det})


def put(tmp: Path, doc: PolicyVersion, name: str | None = None) -> str:
    sha = policy_sha256(doc)
    (tmp / "policies").mkdir(exist_ok=True)
    (tmp / "policies" / f"{name or sha}.json").write_bytes(canonical_json(doc))
    return sha


def rows(*spec: tuple[str, str, str | None]) -> str:
    out = []
    for i, (action, sha, prev) in enumerate(spec):
        human = action in {"prereg", "approve", "promote", "rollback", "reject"}
        by = "human:t" if human else ("gate" if action.startswith("stage") else "tuner")
        ev = HEX if action in {"prereg", "stage1_pass", "stage2_pass", "stage1_fail", "stage2_fail"} else None
        out.append(RegistryRow(seq=i, at=T0, action=action, sha256=sha, by=by,  # type: ignore[arg-type]
                               evidence_sha256=ev, previous_sha256=prev).model_dump_json())
    return "\n".join(out) + "\n"


def chain(sha: str) -> list[tuple[str, str, str | None]]:
    return [(a, sha, None) for a in ("propose", "prereg", "stage1_pass", "stage2_pass", "approve")]


def write_registry(tmp: Path, text: str) -> None:
    (tmp / "policies").mkdir(exist_ok=True)
    (tmp / "policies" / "registry.jsonl").write_text(text, encoding="utf-8")


# -- built-in equals code constants -------------------------------------------------------------------------
def test_packaged_builtin_equals_code_constants() -> None:
    text = (resources.files("sae") / "policies" / core_policy.BUILTIN_FILE).read_text(encoding="utf-8")
    packaged = PolicyVersion.model_validate_json(text)
    assert packaged == builtin_policy_doc()


def test_builtin_tables_round_trip_to_engine_constants() -> None:
    p = load_builtin()
    assert p.likelihood == LikelihoodModel()
    assert p.templates == DEFAULT_EXPANSIONS
    assert dict(p.weights) == WEIGHTS
    assert p.doc.fixed.counter_table == COUNTER_TABLE
    e = eff()
    d = p.doc.detector
    assert (d.rho, d.likelihood_clip, d.belief_threshold) == (e.rho, e.likelihood_clip, e.belief_threshold)
    assert p.ref.source == "builtin" and p.ref.fallback_reason is None and p.sha256 == BUILTIN_SHA


def test_missing_packaged_file_uses_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    class Gone:
        def __truediv__(self, _o: str) -> Gone:
            return self

        def read_text(self, **_k: Any) -> str:
            raise FileNotFoundError

    monkeypatch.setattr(core_policy.resources, "files", lambda _n: Gone())
    assert load_builtin().sha256 == BUILTIN_SHA


# -- resolution happy paths ---------------------------------------------------------------------------------
def test_no_registry_is_builtin_without_fallback(tmp_path: Path) -> None:
    p = resolve_policy(cfg_for(tmp_path))
    assert p.ref.source == "builtin" and p.ref.fallback_reason is None


def test_pin_builtin_ignores_a_broken_registry(tmp_path: Path) -> None:
    write_registry(tmp_path, "garbage\n")
    p = resolve_policy(cfg_for(tmp_path, "builtin"))
    assert p.ref.source == "builtin" and p.ref.fallback_reason is None


def test_registry_current_is_loaded(tmp_path: Path) -> None:
    sha = put(tmp_path, candidate())
    write_registry(tmp_path, rows(*chain(sha), ("promote", sha, None)))
    p = resolve_policy(cfg_for(tmp_path))
    assert (p.ref.source, p.ref.sha256, p.ref.version) == ("registry", sha, "0.2.0")
    assert p.doc.detector.rho == 0.05


def test_rollback_to_builtin_is_builtin_source(tmp_path: Path) -> None:
    sha = put(tmp_path, candidate())
    write_registry(tmp_path, rows(*chain(sha), ("promote", sha, None), ("rollback", BUILTIN_SHA, sha)))
    p = resolve_policy(cfg_for(tmp_path))
    assert p.ref.source == "builtin" and p.ref.fallback_reason is None


def test_pinned_approved_sha(tmp_path: Path) -> None:
    sha = put(tmp_path, candidate())
    write_registry(tmp_path, rows(*chain(sha)))  # approved, never promoted
    p = resolve_policy(cfg_for(tmp_path, sha))
    assert (p.ref.source, p.ref.sha256) == ("pinned", sha)


# -- one unit test per fallback reason ----------------------------------------------------------------------
def _fallback(tmp: Path, pin: str | None = None) -> str:
    p = resolve_policy(cfg_for(tmp, pin))
    assert p.ref.source == "builtin" and p.sha256 == BUILTIN_SHA
    reason = p.ref.fallback_reason
    assert reason is not None and len(reason) <= 256 and str(tmp) not in reason
    return reason


def _promoted(tmp: Path) -> str:
    sha = put(tmp, candidate())
    write_registry(tmp, rows(*chain(sha), ("promote", sha, None)))
    return sha


def test_fallback_file_missing(tmp_path: Path) -> None:
    sha = _promoted(tmp_path)
    (tmp_path / "policies" / f"{sha}.json").unlink()
    assert "missing" in _fallback(tmp_path)


def test_fallback_file_too_large(tmp_path: Path) -> None:
    sha = _promoted(tmp_path)
    (tmp_path / "policies" / f"{sha}.json").write_bytes(b" " * (policy_resolve.MAX_POLICY_BYTES + 1))
    assert "too large" in _fallback(tmp_path)


def test_fallback_invalid_model(tmp_path: Path) -> None:
    sha = _promoted(tmp_path)
    (tmp_path / "policies" / f"{sha}.json").write_text('{"version": "0.2.0"}', encoding="utf-8")
    assert "invalid" in _fallback(tmp_path)


def test_fallback_not_json(tmp_path: Path) -> None:
    sha = _promoted(tmp_path)
    (tmp_path / "policies" / f"{sha}.json").write_bytes(b"\xff\xfe not json")
    assert "invalid" in _fallback(tmp_path)


def test_fallback_hash_mismatch(tmp_path: Path) -> None:
    sha = _promoted(tmp_path)
    (tmp_path / "policies" / f"{sha}.json").write_bytes(canonical_json(candidate(0.07)))
    assert "hash mismatch" in _fallback(tmp_path)


def test_fallback_registry_error_seq_gap(tmp_path: Path) -> None:
    sha = put(tmp_path, candidate())
    lines = rows(*chain(sha), ("promote", sha, None)).splitlines()
    write_registry(tmp_path, "\n".join(lines[:2] + lines[3:]) + "\n")
    assert "registry error" in _fallback(tmp_path)


def test_fallback_registry_skips_invalid_line_as_gap(tmp_path: Path) -> None:
    sha = put(tmp_path, candidate())
    lines = rows(*chain(sha), ("promote", sha, None)).splitlines()
    lines[1] = '{"bad": 1}'
    write_registry(tmp_path, "\n".join(lines) + "\n")
    assert "registry error" in _fallback(tmp_path)


def test_fallback_registry_unreadable(tmp_path: Path) -> None:
    (tmp_path / "policies" / "registry.jsonl").mkdir(parents=True)  # a directory where the file should be
    assert "registry unreadable" in _fallback(tmp_path)


def test_fallback_registry_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_registry(tmp_path, "x" * 64)
    monkeypatch.setattr(policy_resolve, "_REG_MAX_BYTES", 10)
    assert "registry too large" in _fallback(tmp_path)


def test_fallback_pinned_sha_not_approved(tmp_path: Path) -> None:
    sha = put(tmp_path, candidate())
    write_registry(tmp_path, rows(("propose", sha, None)))
    assert "not approved" in _fallback(tmp_path, sha)


def test_fallback_pin_with_no_registry(tmp_path: Path) -> None:
    assert "not approved" in _fallback(tmp_path, HEX)


def test_fallback_reason_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any) -> Any:
        raise policy_resolve._Fallback("x" * 400)

    monkeypatch.setattr(policy_resolve, "_wanted", boom)
    p = resolve_policy(cfg_for(tmp_path))
    assert p.ref.fallback_reason is not None and len(p.ref.fallback_reason) == 256


def test_resumed_resolved_config_keeps_policy_detector(tmp_path: Path) -> None:
    _promoted(tmp_path)
    cfg = cfg_for(tmp_path)
    pol = resolve_policy(cfg)
    written = cfg.model_copy(update={"engine": effective_engine_config(cfg.engine, pol)})
    back = SaeConfig.model_validate_json(written.model_dump_json())
    assert effective_engine_config(back.engine, pol).rho == 0.05


def test_registry_policy_with_different_fixed_tables_falls_back(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    # PolicyVersion validation already rejects a changed table on load; model_copy skips validation, so this
    # drives the defence-in-depth check in resolve_policy directly.
    b = builtin_policy_doc()
    table = dict(b.fixed.counter_table)
    k = next(iter(table))
    table[k] = next(v for v in table.values() if v != table[k])
    doc = candidate().model_copy(update={"fixed": b.fixed.model_copy(update={"counter_table": table})})
    sha = policy_sha256(candidate())
    write_registry(tmp_path, rows(*chain(sha), ("promote", sha, None)))
    monkeypatch.setattr(policy_resolve, "_load_file", lambda _p, _s: doc)
    pol = resolve_policy(cfg_for(tmp_path))
    assert pol.ref.source == "builtin" and pol.ref.fallback_reason == "policy fixed tables differ"


# -- config overrides (step 7) ------------------------------------------------------------------------------
def test_engine_config_from_policy_unless_set(tmp_path: Path) -> None:
    sha = _promoted(tmp_path)
    pol = resolve_policy(cfg_for(tmp_path))
    assert pol.sha256 == sha
    eff = effective_engine_config(EngineConfig(), pol)
    assert eff.rho == 0.05 and eff.likelihood_clip == 3.0
    eff2 = effective_engine_config(EngineConfig(rho=0.1), pol)
    assert eff2.rho == 0.1  # explicit number wins
    assert eff2.belief_threshold == 0.45


# -- resume (step 6) ----------------------------------------------------------------------------------------
def _bus(run_dir: Path, policy: dict[str, Any] | None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"engine_version": "x"}
    if policy is not None:
        payload["policy"] = policy
    (run_dir / "bus.jsonl").write_text(json.dumps({"type": "run_started", "payload": payload}) + "\n")


def test_resume_same_sha_ok_and_changed_sha_refused(tmp_path: Path) -> None:
    pol = load_builtin()
    _bus(tmp_path / "r", {"version": "0.1.0", "sha256": BUILTIN_SHA, "source": "builtin"})
    check_resume(pol, tmp_path / "r")
    _bus(tmp_path / "r2", {"version": "0.2.0", "sha256": HEX, "source": "registry"})
    with pytest.raises(PolicyChanged, match="start a new run"):
        check_resume(pol, tmp_path / "r2")


def test_resume_pre_v13_bus_means_builtin(tmp_path: Path) -> None:
    _bus(tmp_path / "r", None)
    check_resume(load_builtin(), tmp_path / "r")
    sha = put(tmp_path, candidate())
    write_registry(tmp_path, rows(*chain(sha), ("promote", sha, None)))
    with pytest.raises(PolicyChanged):
        check_resume(resolve_policy(cfg_for(tmp_path)), tmp_path / "r")


def test_resume_missing_bus_passes(tmp_path: Path) -> None:
    check_resume(load_builtin(), tmp_path / "nothing")


@pytest.mark.parametrize("content", [b"{garbage\n", b"", b"[1]\n", b'{"type": "health"}\n',
                                     b'{"type": "run_started"}', b"\xff\xfe\n"])
def test_resume_corrupt_bus_first_line_refused(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "bus.jsonl").write_bytes(content)
    with pytest.raises(PolicyChanged):
        check_resume(load_builtin(), tmp_path)


def test_resume_oversized_first_line_refused(tmp_path: Path) -> None:
    (tmp_path / "bus.jsonl").write_bytes(b"x" * (policy_resolve.MAX_POLICY_BYTES + 10))
    with pytest.raises(PolicyChanged):
        check_resume(load_builtin(), tmp_path)


def test_resume_bus_is_directory_refused(tmp_path: Path) -> None:
    (tmp_path / "bus.jsonl").mkdir()
    with pytest.raises(PolicyChanged):
        check_resume(load_builtin(), tmp_path)


def test_recorded_sha_odd_policy_shapes(tmp_path: Path) -> None:
    def line(payload: Any) -> None:
        (tmp_path / "bus.jsonl").write_text(json.dumps({"type": "run_started", "payload": payload}) + "\n")

    line({"policy": "bad"})
    assert policy_resolve.recorded_sha(tmp_path, "b") is None
    line({"policy": {"sha256": 5}})
    assert policy_resolve.recorded_sha(tmp_path, "b") is None
    line("notadict")
    assert policy_resolve.recorded_sha(tmp_path, "b") == "b"


# -- wiring -------------------------------------------------------------------------------------------------
def _stub(monkeypatch: pytest.MonkeyPatch, open_sink: Any) -> None:
    def mod(name: str, **attrs: Any) -> None:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)

    mod("sae.bus", open_sink=open_sink)
    mod("sae.governor", make_governor=lambda g, lvl, pre, emit_period=None: FakeGovernor())
    mod("sae.platform", make_sensors=lambda: FakeSensors(), make_platform=lambda: FakePlatform())
    mod("sae.control", open_inbox=lambda run_dir: FakeInbox())


def _wcfg(tmp: Path, pin: str | None = None) -> SaeConfig:
    return SaeConfig.model_validate({"paths": {"runs_dir": str(tmp / "runs"), "parser_exe": "px",
                                               "sae_dir": str(tmp / "sae")}, "policy": {"pin": pin}})


def test_wiring_passes_policy_when_open_sink_takes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[PolicyRef | None] = []
    _stub(monkeypatch, lambda run_dir, run_id, cfg, *, resume=False, policy=None: seen.append(policy) or FakeSink())
    deps = wiring.default_deps(_wcfg(tmp_path), "r1")
    assert seen == [deps.policy.ref] and deps.policy is not None  # type: ignore[union-attr]
    assert seen[0].sha256 == BUILTIN_SHA  # type: ignore[union-attr]


def test_wiring_without_policy_keyword_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, lambda run_dir, run_id, cfg, resume=False: FakeSink())
    assert wiring.default_deps(_wcfg(tmp_path), "r1").policy is not None


def test_wiring_resolves_before_open_sink_and_resume_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    _stub(monkeypatch, lambda run_dir, run_id, cfg, resume=False: opened.append("sink") or FakeSink())
    sae = tmp_path / "sae"
    sae.mkdir()
    sha = put(sae, candidate())
    write_registry(sae, rows(*chain(sha), ("promote", sha, None)))
    _bus(tmp_path / "runs" / "r1", {"version": "0.1.0", "sha256": BUILTIN_SHA, "source": "builtin"})
    with pytest.raises(PolicyChanged):
        wiring.default_deps(_wcfg(tmp_path), "r1", resume=True)
    assert opened == []  # refused before the bus was reopened
    assert wiring.default_deps(_wcfg(tmp_path), "r2").policy.sha256 == sha  # type: ignore[union-attr]


# -- engine uses the policy ---------------------------------------------------------------------------------
def test_pin_mismatch_health_once_policy_kept(tmp_path: Path) -> None:
    sim = Sim(Archetype.TURTLE, 1)
    obs = []
    for _ in range(3):
        obs.append(sim.obs("log").model_copy(update={"game_version": "Cepheus v4.3.9"}))
        sim.t += 1
    sink = FakeSink()
    deps = replace(make_deps(FakePlatform(), sink=sink), policy=load_builtin())
    asyncio.run(run_engine(SaeConfig(), FakeAdapter(obs), "r", deps, tmp_path, None, poll_s=0.001, inbox_s=0.001))
    hits = [d.payload for d in sink.drafts if isinstance(d.payload, HealthPayload)
            and d.payload.kind is HealthKind.POLICY_PIN_MISMATCH]
    assert len(hits) == 1 and hits[0].component == "engine" and hits[0].detail == "v4.4.* vs Cepheus v4.3.9"


def test_matching_or_absent_game_version_emits_nothing(tmp_path: Path) -> None:
    sim = Sim(Archetype.TURTLE, 1)
    obs = [sim.obs("log").model_copy(update={"game_version": "Cepheus v4.4.4"})]
    sim.t += 1
    obs.append(sim.obs("log"))
    sink = FakeSink()
    deps = replace(make_deps(FakePlatform(), sink=sink), policy=load_builtin())
    asyncio.run(run_engine(SaeConfig(), FakeAdapter(obs), "r", deps, tmp_path, None, poll_s=0.001, inbox_s=0.001))
    assert not [d for d in sink.drafts if isinstance(d.payload, HealthPayload)
                and d.payload.kind is HealthKind.POLICY_PIN_MISMATCH]


def _run_sim(ctx: Any, months: int = 30) -> list[Any]:
    from sae.core.engine import initial_state

    sim = Sim(Archetype.TURTLE, 1)
    sim.ctx = lambda: ctx  # type: ignore[method-assign]
    state = initial_state(eff())
    out: list[Any] = []
    for _ in range(months):
        state, outs = sim.step(state)
        out += [o.drafts for o in outs] + [o.batch for o in outs]
    return out


def test_builtin_policy_is_identical_to_no_policy_and_other_tables_change_output() -> None:
    from sae.core.state import EngineCtx

    cfg, mode = eff(), Sim(Archetype.TURTLE, 1).mode
    pol = load_builtin()
    base = _run_sim(EngineCtx(mode=mode, cfg=cfg))
    withp = _run_sim(EngineCtx(mode=mode, cfg=cfg, likelihood=pol.likelihood, templates=pol.templates,
                               outcome_weights=pol.weights))
    assert base == withp
    post = DEFAULT_EXPANSIONS[Strategy.EARLY_PUNISH].posture
    flipped = {s: replace(t, posture=post) for s, t in pol.templates.items()}
    assert _run_sim(EngineCtx(mode=mode, cfg=cfg, templates=flipped)) != base
    skewed = replace(pol.likelihood, war_now={a: 0.9 for a in Archetype})
    assert _run_sim(EngineCtx(mode=mode, cfg=cfg, likelihood=skewed)) != base
