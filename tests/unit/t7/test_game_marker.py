"""The root conftest ``game`` marker, tested through pytester with the REAL conftest [R5]."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[3]
BODY = """
import pytest

@pytest.mark.game("SAE_GAME_X", "SAE_GAME_Y", id="X3-G9")
def test_g():
    pass

def test_plain():
    pass
"""


def _inner(
    pt: pytest.Pytester, mp: pytest.MonkeyPatch, *args: str, env: dict[str, str] | None = None
) -> pytest.RunResult:
    pt.makeconftest((REPO / "tests/conftest.py").read_text())
    pt.makepyfile(BODY)
    for k in ("SAE_GAME_X", "SAE_GAME_Y"):
        mp.delenv(k, raising=False)
    for k, v in (env or {}).items():
        mp.setenv(k, v)
    return pt.runpytest_subprocess("--strict-markers", "-rs", *args)


def test_unset_skips_with_exact_reason(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    res = _inner(pytester, monkeypatch)
    res.assert_outcomes(passed=1, skipped=1)
    res.stdout.fnmatch_lines(["*game input missing: SAE_GAME_X, SAE_GAME_Y (docs/handoff/deferred/X3.md#x3-g9)*"])


def test_empty_counts_as_unset(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    res = _inner(pytester, monkeypatch, env={"SAE_GAME_X": "", "SAE_GAME_Y": ""})
    res.assert_outcomes(passed=1, skipped=1)


def test_set_runs(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    res = _inner(pytester, monkeypatch, env={"SAE_GAME_X": str(tmp_path), "SAE_GAME_Y": str(tmp_path)})
    res.assert_outcomes(passed=2)


def test_set_but_missing_errors_without_value(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gone = str(tmp_path / "nope-secret")
    res = _inner(pytester, monkeypatch, env={"SAE_GAME_X": gone, "SAE_GAME_Y": str(tmp_path)})
    res.assert_outcomes(passed=1, errors=1)
    res.stdout.fnmatch_lines(["*SAE_GAME_X set but path not found*"])
    assert "nope-secret" not in res.stdout.str()


def test_marker_select(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    res = _inner(pytester, monkeypatch, "-m", "game")
    res.assert_outcomes(skipped=1)
    assert res.parseoutcomes().get("deselected") == 1


def test_no_id_omits_anchor(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    pytester.makeconftest((REPO / "tests/conftest.py").read_text())
    pytester.makepyfile('import pytest\n@pytest.mark.game("SAE_GAME_Z")\ndef test_a(): pass\n')
    monkeypatch.delenv("SAE_GAME_Z", raising=False)
    res = pytester.runpytest_subprocess("--strict-markers", "-rs")
    res.stdout.fnmatch_lines(["*game input missing: SAE_GAME_Z"])
    assert "docs/handoff" not in res.stdout.str()


def test_deferred_id_in_junit(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    xml = pytester.path / "r.xml"
    _inner(pytester, monkeypatch, f"--junitxml={xml}")
    text = xml.read_text()
    assert 'name="deferred_id"' in text and 'value="X3-G9"' in text
