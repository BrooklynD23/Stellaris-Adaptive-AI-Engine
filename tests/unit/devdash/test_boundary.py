"""Import boundary: nothing under ``src/sae`` may reach the dev-dashboard tooling (spec D2)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "sae"
# Substrings checked in every source text (covers string literals such as paths and dynamic import names).
FORBIDDEN = (
    "import devdash",
    "from devdash",
    "tools.devdash",
    "tools/devdash",
    "tools\\devdash",
    "tools.game",
    "tools/game",
    "tools\\game",
)


def _is_devdash(name: str) -> bool:
    """True for ``devdash``, ``devdash.x``, ``tools.devdash`` and ``tools.devdash.x``."""
    return (
        name.split(".")[0] == "devdash"
        or name in ("tools.devdash", "tools.game")
        or name.startswith(("tools.devdash.", "tools.game."))
    )


def _string_is_devdash(value: str) -> bool:
    """True for a string constant naming the package, as passed to ``import_module`` or ``__import__``."""
    return _is_devdash(value)


def _ast_hits(tree: ast.AST) -> list[str]:
    """Return a description of every import statement, or dynamic import call, that reaches devdash."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(f"import {a.name}" for a in node.names if _is_devdash(a.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            from_tools = module == "tools" and any(a.name in ("devdash", "game") for a in node.names)
            if node.level == 0 and (_is_devdash(module) or from_tools):
                hits.append(f"from {module} import ...")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ("tools\\devdash" in node.value or "tools\\game" in node.value)
        ):
            hits.append(f"path literal {node.value!r}")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            arg = node.args[0]
            if (
                name in ("import_module", "__import__")
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _string_is_devdash(arg.value)
            ):
                hits.append(f"{name}({arg.value!r})")
    return hits


def find_violations(src: Path) -> list[str]:
    """Return ``"<file>: <what>"`` for each forbidden reference in a ``.py`` or ``.pyi`` file under ``src``."""
    found: list[str] = []
    paths = sorted([*src.rglob("*.py"), *src.rglob("*.pyi")])
    for path in paths:
        rel = path.relative_to(src).as_posix()
        text = path.read_text(encoding="utf-8")
        found.extend(f"{rel}: {needle}" for needle in FORBIDDEN if needle in text)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            found.append(f"{rel}: unparseable")
            continue
        found.extend(f"{rel}: {hit}" for hit in _ast_hits(tree))
    return found


def test_src_never_imports_devdash() -> None:
    """The daemon's import graph never contains ``tools/devdash`` (guards the future)."""
    assert SRC.is_dir()
    assert find_violations(SRC) == []


CASES = {
    "from_import": "from devdash import cli\n",
    "import_dotted": "import devdash.events as ev\n",
    "from_tools_multiline": "from tools import (\n    devdash,\n)\n",
    "from_tools_dotted": "from tools.devdash import x\n",
    "import_list": "import sys, devdash\n",
    "double_space": "import  devdash\n",
    "import_tools_dotted": "import tools.devdash.devdash\n",
    "import_module": "import importlib\nimportlib.import_module('devdash')\n",
    "dunder_import": "__import__('devdash')\n",
    "windows_path": "p = 'tools\\\\devdash\\\\dd.py'\n",
    "posix_path": "p = 'tools/devdash/dd.py'\n",
    "game_from_import": "from tools.game import pipeline\n",
    "game_from_tools": "from tools import game\n",
    "game_import": "import tools.game.pipeline\n",
    "game_path": "p = 'tools/game/pipeline.py'\n",
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_detector_catches_planted_violation(tmp_path: Path, name: str) -> None:
    """Prove the check can fail: plant each form in a temporary tree, never in ``src``."""
    (tmp_path / "clean.py").write_text("import json\nfrom tools import other\n", encoding="utf-8")
    assert find_violations(tmp_path) == []
    (tmp_path / "bad.py").write_text(CASES[name], encoding="utf-8")
    assert find_violations(tmp_path) != []


def test_detector_checks_pyi_files(tmp_path: Path) -> None:
    """A stub file that imports devdash is flagged."""
    (tmp_path / "x.pyi").write_text("import devdash\n", encoding="utf-8")
    assert any(v.startswith("x.pyi") for v in find_violations(tmp_path))
