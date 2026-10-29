"""Tests for the game-gate registry (``tools/game/gates.json`` and ``gates.py``)."""

from __future__ import annotations

import ast
import json
import re
import string
import types
from pathlib import Path

import gates
import pytest

from sae.bench import cli as bench_cli
from sae.m0 import cli as m0_cli
from sae.m0.checks import BY_ID, CHECKS

_ROOT = Path(__file__).resolve().parents[3]
_DUMMY = {"user_dir": "/u", "save": "/s.sav", "starnet_dir": "/sn", "parser_exe": "/p", "ironman_save": "/i.sav",
          "r1_csv": "/r1.csv", "run_dir": "/run"}


def _markers() -> set[str]:
    """Every ``id=`` of a ``pytest.mark.game(...)`` call under ``tests/`` (AST scan)."""
    found: set[str] = set()
    for path in (_ROOT / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "pytest.mark.game":
                found.update(
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                )
    return found


def _fill(template: str) -> str | None:
    """Fill a template with dummy paths, or None when it holds a placeholder the registry cannot fill."""
    names = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    return None if names - _DUMMY.keys() else template.format(**_DUMMY)


def _fill_all(argv: list[str]) -> list[str] | None:
    out = [_fill(a) for a in argv]
    return None if any(a is None for a in out) else [a for a in out if a is not None]


def test_registry_loads() -> None:
    loaded = gates.load_gates()
    assert len(loaded) >= 39
    assert len({g.id for g in loaded}) == len(loaded)
    assert set(gates.by_id(loaded)) == {g.id for g in loaded}


def test_markers_in_registry() -> None:
    registry = gates.by_id(gates.load_gates())
    markers = _markers()
    assert markers
    for marker in markers:
        assert marker in registry, marker
        assert registry[marker].runner["kind"] == "pytest", marker
        assert registry[marker].deferred_id == marker


def test_registry_pytest_gates_have_markers() -> None:
    markers = _markers()
    for g in gates.load_gates():
        if g.runner["kind"] == "pytest" and g.pending_source is None:
            assert g.id in markers, g.id
            assert g.deferred_id == g.id
        elif g.id not in markers:
            assert g.deferred_id is None, g.id


def test_m0_rows_in_registry() -> None:
    registry = gates.by_id(gates.load_gates())
    text = (_ROOT / "docs" / "m0_checklist.md").read_text(encoding="utf-8")
    ids = set(re.findall(r"^\|\s*(M0-\d+)\s*\|", text, flags=re.MULTILINE))
    assert len(ids) == 14
    assert {gid for gid, g in registry.items() if g.stage == "m0"} == ids


def test_judged_ids_recordable() -> None:
    parser = m0_cli._parser()
    sub = next(a for a in parser._actions if a.dest == "cmd")
    record = sub.choices["record"]  # type: ignore[union-attr]
    choices = next(a for a in record._actions if a.dest == "check").choices
    assert choices is not None
    for g in gates.load_gates():
        if g.judged:
            assert g.id in choices, g.id
        if g.stage == "m0":
            assert g.judged, g.id


def test_bad_field_rejected(tmp_path: Path) -> None:
    rows = json.loads(gates.DEFAULT_PATH.read_text(encoding="utf-8"))
    rows[3]["hardware"] = "mars"
    bad = tmp_path / "gates.json"
    bad.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match=rows[3]["id"]):
        gates.load_gates(bad)


def test_duplicate_and_runner_rejected(tmp_path: Path) -> None:
    rows = json.loads(gates.DEFAULT_PATH.read_text(encoding="utf-8"))
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps([rows[0], rows[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match=rows[0]["id"]):
        gates.load_gates(dup)
    rows[1]["runner"] = {"kind": "pytest"}
    badr = tmp_path / "runner.json"
    badr.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match=rows[1]["id"]):
        gates.load_gates(badr)


def test_m0_args_match_cli() -> None:
    parser = m0_cli._parser()
    checked = 0
    for g in gates.load_gates():
        if g.runner["kind"] != "m0":
            continue
        argvs = [list(p) for p in g.runner.get("pre", [])]
        if g.runner["auto"]:
            argvs.append(["check", g.id, *g.runner.get("args", [])])
        for argv in argvs:
            filled = _fill_all(argv)
            if filled is None:
                continue  # a placeholder the registry cannot fill (for example {sid}): skipped by ruling R2
            parser.parse_args(["--user-dir", "/x", *filled])
            checked += 1
    assert checked == 9  # 7 auto checks + M0-2 stage + M0-4 tail
    auto_ids = {c.id for c in CHECKS if c.auto}
    registry = gates.by_id(gates.load_gates())
    for cid in BY_ID:
        if cid.startswith("M0-"):
            assert registry[cid].runner["auto"] == (cid in auto_ids and cid != "M0-4"), cid


def test_r_scope() -> None:
    registry = gates.by_id(gates.load_gates())
    for gid in ("R-3", "R-5"):
        assert registry[gid].priority != "backlog"
        assert not registry[gid].milestone_later
        assert registry[gid].judged
    assert [g.id for g in registry.values() if g.milestone_later] == ["X4-G0"]
    assert registry["R-2"].runner["kind"] == "none"
    assert registry["R-4"].runner["kind"] == "none"
    assert registry["R-1"].hardware != "one_pc"
    assert registry["R-6"].hardware != "one_pc"
    assert registry["R-5"].windows_only
    assert registry["X3-G4"].windows_only
    assert registry["M0-10"].priority == "backlog"
    assert {g.id for g in registry.values() if g.priority == "critical"} == {"M0-2", "M0-3", "M0-5", "M0-6", "M0-11"}
    assert registry["M0-6"].runner.get("pre", ()) == ()


def test_bench_args_match_cli() -> None:
    parser = bench_cli._build()
    seen = 0
    for g in gates.load_gates():
        if g.runner["kind"] == "bench":
            filled = _fill_all(list(g.runner["args"]))
            assert filled is not None, g.id
            parser.parse_args(filled)
            seen += 1
    assert seen == 2


def test_m0_evidence_covers_check_values() -> None:
    registry = gates.by_id(gates.load_gates())
    for cid, check in BY_ID.items():
        if not cid.startswith("M0-"):
            continue
        wanted = set(re.findall(r"--value (\w+)=", " ".join(check.steps)))
        have = {e["key"] for e in registry[cid].evidence}
        assert wanted <= have, (cid, wanted - have)


def test_m0_titles_and_assumptions_match_checks() -> None:
    registry = gates.by_id(gates.load_gates())
    for cid, check in BY_ID.items():
        if cid.startswith(("M0-", "R-")):
            assert registry[cid].assumptions == check.assumptions, cid
        if cid.startswith("M0-"):
            assert registry[cid].title == check.title, cid


def test_pytest_select_is_marked_function() -> None:
    owner: dict[str, str] = {}
    for path in (_ROOT / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and ast.unparse(deco.func) == "pytest.mark.game":
                        owner.update(
                            {kw.value.value: node.name for kw in deco.keywords
                             if kw.arg == "id" and isinstance(kw.value, ast.Constant)}
                        )
    for g in gates.load_gates():
        if g.runner["kind"] == "pytest" and g.pending_source is None:
            assert g.runner["select"] == owner[g.id], g.id


def test_gate_is_read_only() -> None:
    g = gates.by_id(gates.load_gates())["M0-2"]
    assert isinstance(g.runner, types.MappingProxyType)
    with pytest.raises(TypeError):
        g.runner["kind"] = "none"  # type: ignore[index]
    assert isinstance(g.to_dict()["runner"]["pre"], list)
