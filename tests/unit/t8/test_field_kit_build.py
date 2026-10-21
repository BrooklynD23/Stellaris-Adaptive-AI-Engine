"""Field kit assembler and setup.ps1 checks (static on all platforms, live only on Windows)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
KIT_SRC = ROOT / "tools/m0/field_kit"
SETUP = KIT_SRC / "setup.ps1"
UV_URL = "https://github.com/astral-sh/uv/releases/download/0.13.1/uv-x86_64-pc-windows-msvc.zip"

_spec = importlib.util.spec_from_file_location("build_kit", KIT_SRC / "build_kit.py")
assert _spec is not None and _spec.loader is not None
build_kit = importlib.util.module_from_spec(_spec)
sys.modules["build_kit"] = build_kit
_spec.loader.exec_module(build_kit)


def _fakes(tmp: Path) -> list[str]:
    wheel = tmp / "sae-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"whl")
    exe = tmp / "parser.exe"
    exe.write_bytes(b"MZ")
    con = tmp / "c.txt"
    con.write_text("pydantic==2.0\n", encoding="utf-8")
    return ["--wheel", str(wheel), "--parser-exe", str(exe), "--constraints", str(con)]


def _build(tmp: Path, name: str = "kit") -> Path:
    out = tmp / name
    assert build_kit.main(["--out", str(out), *_fakes(tmp), "--git-sha", "abc123"]) == 0
    return out


def test_layout_and_exclusions(tmp_path: Path) -> None:
    out = _build(tmp_path)
    for rel in (
        "setup.ps1", "README.md", "m0_checklist.md", "m0_results_template.md",
        "sae-0.1.0-py3-none-any.whl", "constraints.txt", "bin/sae-parse.exe",
        "KIT_INFO.json", "SHA256SUMS", "mod/sae_bridge/descriptor.mod",
    ):  # fmt: skip
        assert (out / rel).is_file(), rel
    mod = out / "mod/sae_bridge"
    assert not (mod / "tests").exists() and not (mod / "tools").exists()
    assert not list(mod.rglob("*.md")) and not list(mod.rglob("__pycache__"))


def test_sums_and_info(tmp_path: Path) -> None:
    out = _build(tmp_path)
    lines = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    paths = [ln.split("  ", 1)[1] for ln in lines]
    assert paths == sorted(paths) and "SHA256SUMS" not in paths
    assert set(paths) == {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    for ln in lines:
        digest, rel = ln.split("  ", 1)
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    info = json.loads((out / "KIT_INFO.json").read_text(encoding="utf-8"))
    assert info["git_sha"] == "abc123" and info["sae_version"] == "0.1.0"
    assert info["parser_version"] is None and info["built_at"].endswith("+00:00")


def test_refuses_nonempty_out_and_bad_wheel(tmp_path: Path) -> None:
    out = tmp_path / "kit"
    out.mkdir()
    (out / "x").write_text("x", encoding="utf-8")
    assert build_kit.main(["--out", str(out), *_fakes(tmp_path)]) == 2
    bad = tmp_path / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    args = _fakes(tmp_path)
    args[1] = str(bad)
    assert build_kit.main(["--out", str(tmp_path / "k2"), *args]) == 2


_WRITERS = ("Copy-Item", "Set-Content", "Out-File", "New-Item", "Move-Item", "Expand-Archive", "robocopy")


def test_setup_static() -> None:
    text = SETUP.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "Paradox" in line:
            assert not any(w.lower() in line.lower() for w in _WRITERS), line
    assert "Start-Process" not in text and "steam://" not in text
    assert UV_URL in text and "python-build-standalone" in text
    m = re.search(r"\$UvSha256 = '([^']*)'", text)
    assert m is not None
    v = m.group(1)
    assert re.fullmatch(r"[0-9a-f]{64}", v) and len(set(v)) > 1
    assert any("doctor" in ln and "--results" in ln for ln in text.splitlines())


pytestmark_win = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell only")
_SHELLS = [s for s in ("pwsh", "powershell.exe") if shutil.which(s)]


def _snapshot(p: Path) -> set[str]:
    return {x.relative_to(p).as_posix() for x in p.rglob("*")}


@pytestmark_win
@pytest.mark.parametrize("shell", _SHELLS)
def test_setup_parses(shell: str) -> None:  # pragma: no cover - Windows only
    exe = shutil.which(shell)
    assert exe
    cmd = (
        "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{SETUP}',[ref]$null,[ref]$e);$e.Count"
    )
    r = subprocess.run([exe, "-NoProfile", "-Command", cmd], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "0"


def _user_path(exe: str) -> str:  # pragma: no cover - Windows only
    cmd = "[Environment]::GetEnvironmentVariable('Path','User')"
    return subprocess.run([exe, "-NoProfile", "-Command", cmd], capture_output=True, text=True, check=True).stdout


@pytestmark_win
@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize("strip_uv", [False, True])
def test_setup_dry_run(tmp_path: Path, shell: str, strip_uv: bool) -> None:  # pragma: no cover - Windows only
    exe = shutil.which(shell)
    assert exe
    kit = _build(tmp_path)
    local, temp = tmp_path / "local", tmp_path / "temp"
    local.mkdir()
    temp.mkdir()
    env = dict(os.environ, LOCALAPPDATA=str(local), TEMP=str(temp), TMP=str(temp))
    if strip_uv:
        dirs = [d for d in env["PATH"].split(os.pathsep) if d and not (Path(d) / "uv.exe").exists()]
        env["PATH"] = os.pathsep.join(dirs)
    before_files, before_path = _snapshot(kit), _user_path(exe)
    r = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(kit / "setup.ps1"), "-DryRun"],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DRY-RUN would:" in r.stdout
    if strip_uv:
        assert UV_URL in r.stdout
    assert not (local / "sae-kit").exists() and not (temp / "uv-x86_64-pc-windows-msvc.zip").exists()
    assert _snapshot(kit) == before_files and _user_path(exe) == before_path
