"""Tests for packaging/merge_changelog.py, including a dry run on the real fragments."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("merge_changelog", ROOT / "packaging" / "merge_changelog.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["merge_changelog"] = module
    spec.loader.exec_module(module)
    return module


mc = _load()


def test_groups_by_kind_then_team() -> None:
    section = mc.build_section({
        "T2.md": "### Fixed (T2 Adapters)\n- b\n\n### Added (T2 Adapters)\n- a2\n",
        "T1.md": "### Added (T1 Mod)\n- a1\n  - nested\n",
    })
    assert section.index("### Added") < section.index("#### T1 Mod") < section.index("#### T2 Adapters")
    assert section.index("### Added") < section.index("### Fixed")
    assert "  - nested" in section


@pytest.mark.parametrize("bad", ["### Tweaked (T1)\n- x\n", "- before heading\n", "## Added (T1)\n"])
def test_bad_fragment_rejected(bad: str) -> None:
    with pytest.raises(mc.FragmentError):
        mc.parse_fragment("T9.md", bad)


def test_merge_replaces_only_unreleased() -> None:
    old = "# Changelog\n\n## [Unreleased]\n\nold text\n\n## [0.1.0] - 2026-01-01\n\n- kept\n"
    new = mc.merge(old, "### Added\n\n- new\n")
    assert "old text" not in new and "- new" in new and "## [0.1.0] - 2026-01-01\n\n- kept" in new
    with pytest.raises(mc.FragmentError):
        mc.merge("# Changelog\n", "x")


def test_real_fragments_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    before = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert mc.main(["--check", "--root", str(ROOT)]) == 0
    assert "## [Unreleased]" in capsys.readouterr().out
    assert (ROOT / "CHANGELOG.md").read_text(encoding="utf-8") == before


def test_write_mode(tmp_path: Path) -> None:
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "T8.md").write_text("### Added (T8 Release)\n- kit\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\npending\n", encoding="utf-8")
    assert mc.main(["--root", str(tmp_path)]) == 0
    assert "#### T8 Release\n\n- kit" in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (tmp_path / "changelog.d" / "T8.md").exists()
