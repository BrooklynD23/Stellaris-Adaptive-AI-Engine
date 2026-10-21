"""Field-kit tests: `sae m0 doctor`, `sae m0 pack`, --kit-dir. Game-free: fake folders and a fake parser."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from sae.adapters.paths import ResolvedPaths
from sae.cli import main as sae_main
from sae.m0 import cli, doctor, pack

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "parser" / "fixtures" / "turtle_small"
GIB = 2**30
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="shebang fake binary")


def _parse_doc() -> dict[str, object]:
    doc: dict[str, object] = json.loads((FIXTURE / "expected.json").read_text())
    doc["parser_version"] = "0.1.0"
    doc["save"] = {"path": "x.sav", "size_bytes": 10, "mtime_ns": 1}
    doc["cost"] = {"rss_peak_bytes": 2**20, "cpu_s": 0.1, "wall_s": 0.1}
    doc["meta"] = {**doc["meta"], "ironman": False}  # type: ignore[dict-item]
    return doc


def _fake(tmp_path: Path, *, version: str = "sae-parse 0.1.0", parse: object = None, fixture_rc: int = 0,
          version_rc: int = 0, name: str = "fake-sae-parse") -> Path:
    """A parser stand-in that handles --version, fixture and parse."""
    body = parse if parse is not None else _parse_doc()
    exe = tmp_path / name
    exe.write_text(
        f"#!{sys.executable}\nimport sys\na = sys.argv[1:]\n"
        f"if a[0] == '--version':\n    print({version!r}); sys.exit({version_rc})\n"
        f"if a[0] == 'fixture':\n    open(a[a.index('--out') + 1], 'wb').write(b'x'); sys.exit({fixture_rc})\n"
        f"sys.stdout.write({json.dumps(json.dumps(body))})\n"
    )
    exe.chmod(0o755)
    return exe


def _paths(tmp_path: Path, exe: Path | None, user: Path | None = None) -> ResolvedPaths:
    u = user if user is not None else tmp_path / "Stellaris"
    return ResolvedPaths(u, u / "logs" / "game.log", u / "save games", u / "sae_in.ini", exe)


def _kit(tmp_path: Path) -> Path:
    mod = tmp_path / "kit" / "mod" / "sae_bridge"
    mod.mkdir(parents=True)
    (mod / "descriptor.mod").write_text('name="x"\n')
    return tmp_path / "kit"


def _rows(text: str) -> dict[str, str]:
    return {line.split("  ")[1]: line.split("  ")[0] for line in text.splitlines()}


# --- doctor ------------------------------------------------------------------------------------------------------


@posix_only
def test_doctor_all_pass(tmp_path: Path) -> None:
    (tmp_path / "Stellaris").mkdir()
    res = tmp_path / "res"
    text, code = doctor.run_doctor(_paths(tmp_path, _fake(tmp_path)), res, _kit(tmp_path), None)
    assert code == 0 and set(_rows(text).values()) <= {"PASS", "WARN"}
    assert _rows(text)["c4-schema"] == "PASS" and "copy by hand:" in text
    assert (res / "doctor.txt").read_text().strip() == text


def test_doctor_refused_results_fails_and_writes_nothing(tmp_path: Path) -> None:
    res = tmp_path / "res"
    text, code = doctor.run_doctor(_paths(tmp_path, None), res, _kit(tmp_path), "inside a game folder")
    rows = _rows(text)
    assert code == 1 and rows["results"] == "FAIL" and rows["sae-parse"] == "FAIL"
    assert not res.exists()


@posix_only
def test_doctor_bad_version_and_schema(tmp_path: Path) -> None:
    assert doctor.check_parser_version(_fake(tmp_path, version="sae-parse beta"))[0] == "FAIL"
    assert doctor.check_parser_version(_fake(tmp_path, version_rc=3))[0] == "FAIL"
    assert doctor.check_parser_version(tmp_path / "missing")[0] == "FAIL"
    assert doctor.check_schema(_fake(tmp_path, fixture_rc=4))[0] == "FAIL"
    assert doctor.check_schema(_fake(tmp_path, parse={"nope": 1}))[0] == "FAIL"
    err = {"schema_version": 1, "status": "error", "parser_version": "0.1.0", "exit_code": 4,
           "kind": "format", "message": "not a save"}
    assert doctor.check_schema(_fake(tmp_path, parse=err))[0] == "FAIL"
    assert doctor.check_schema(tmp_path / "missing")[0] == "FAIL"


def test_doctor_user_dir_rows(tmp_path: Path) -> None:
    assert doctor.check_user_dir(tmp_path / "none")[0][0] == "WARN"
    assert "--user-dir" in doctor.check_user_dir(tmp_path / "none")[0][2]
    docs, home = tmp_path / "OneDrive" / "Documents", tmp_path / "home"
    (docs / "Paradox Interactive" / "Stellaris").mkdir(parents=True)
    (home / "Documents" / "Paradox Interactive" / "Stellaris").mkdir(parents=True)
    rows = doctor.check_user_dir(tmp_path, platform="win32", documents=lambda: docs, home=home)
    assert [r[0] for r in rows] == ["PASS", "WARN"] and "A-33 UNVERIFIED" in rows[1][2]
    same = doctor.check_user_dir(tmp_path, platform="win32", documents=lambda: home / "Documents", home=home)
    assert len(same) == 1
    assert len(doctor.check_user_dir(tmp_path, platform="win32", documents=lambda: tmp_path / "x", home=home)) == 1


def test_doctor_mod_rows(tmp_path: Path) -> None:
    kit, user = _kit(tmp_path), tmp_path / "Stellaris"
    assert doctor.check_mod(kit, user)[0] == "PASS" and "already exists" not in doctor.check_mod(kit, user)[2]
    (user / "mod" / "sae_bridge").mkdir(parents=True)
    assert "already exists" in doctor.check_mod(kit, user)[2]
    assert doctor.check_mod(tmp_path / "empty", user)[0] == "FAIL"


@pytest.mark.parametrize(("free", "status"), [(6 * GIB, "PASS"), (5 * GIB, "PASS"), (2 * GIB, "WARN"),
                                              (GIB, "WARN"), (GIB - 1, "FAIL")])
def test_doctor_disk_thresholds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, free: int, status: str) -> None:
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(0, 0, free))
    assert doctor.check_disk(tmp_path / "not" / "yet")[0] == status


def test_doctor_versions_and_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.check_versions()[0] == "PASS"
    user = tmp_path / "Stellaris"
    user.mkdir()
    args = ["m0", "--user-dir", str(user), "--results", str(tmp_path / "res"), "--kit-dir", str(_kit(tmp_path))]
    # no parser anywhere -> FAIL -> exit 1
    assert sae_main([*args, "--parser-exe", str(tmp_path / "nope"), "doctor"]) == 1
    assert "FAIL  sae-parse" in capsys.readouterr().out
    assert sae_main(["m0", "--user-dir", str(user), "--results", str(user / "r"), "doctor"]) == 1
    assert not (user / "r").exists()


# --- pack --------------------------------------------------------------------------------------------------------


def _results(tmp_path: Path) -> Path:
    res = tmp_path / "res"
    (res / "stage").mkdir(parents=True)
    (res / "stage" / "sae_in.ini").write_text("x")
    for name in ("env.json", "M0-3.json", "M0-4.tail.jsonl", "m0_results.md", "doctor.txt"):
        (res / name).write_text('{"a": 1}\n' if name.endswith("json") else "text\n")
    (res / "M0-6.stage.json").write_text("{}")
    for junk in ("run.log", "a.sav", "b.ini", "c.rrd", "bus1.jsonl", "notes.txt"):
        (res / junk).write_text("junk")
    return res


def test_pack_allowlist_and_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    res, out = _results(tmp_path), tmp_path / "o.zip"
    user = tmp_path / "Stellaris"
    user.mkdir()
    code = sae_main(["m0", "--user-dir", str(user), "--results", str(res), "pack", "--out", str(out)])
    assert code == 0
    names = set(zipfile.ZipFile(out).namelist())
    assert names == {"env.json", "M0-3.json", "M0-4.tail.jsonl", "m0_results.md", "doctor.txt"}
    stdout = capsys.readouterr().out
    assert "M0-3.json" in stdout and "bytes" in stdout and "5 numeric player-empire totals" in stdout
    assert (res / "M0-3.json").read_text() == '{"a": 1}\n'  # original untouched


def test_pack_scrubs_home_and_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = tmp_path / "res"
    res.mkdir()
    home = tmp_path / "Users" / "Alice"
    monkeypatch.setattr("getpass.getuser", lambda: "Alice")
    monkeypatch.setattr(Path, "home", classmethod(lambda _c: home))
    h = str(home)
    (res / "m0_results.md").write_text(f"saw {h}/x and {h.upper()} and alice's file; malice\n")
    (res / "env.json").write_text(json.dumps({"machine": h.replace("/", "\\") + "\\PC", "playset": "ALICE rig"}))
    out = tmp_path / "o.zip"
    pack.build_zip(res, out)
    zf = zipfile.ZipFile(out)
    md, env = zf.read("m0_results.md").decode(), zf.read("env.json").decode()
    assert "Alice" not in md and "alice" not in md.replace("malice", "") and "malice" in md and "~/x" in md
    assert "Alice" not in env and "ALICE" not in env and "~" in env


def test_pack_short_user_not_scrubbed(tmp_path: Path) -> None:
    res = tmp_path / "res"
    res.mkdir()
    (res / "doctor.txt").write_text("al and bo\n")
    pack.build_zip(res, tmp_path / "o.zip", home=tmp_path / "h", user="al")
    assert zipfile.ZipFile(tmp_path / "o.zip").read("doctor.txt") == b"al and bo\n"


def test_pack_redaction(tmp_path: Path) -> None:
    res = tmp_path / "res"
    res.mkdir()
    m5 = {"check": "M0-5", "auto": {"status": "pass", "notes": ["name=Grand Foo Union seen"],
          "evidence": {"snap": 3, "sample": ["sid=1 fleet=2 name=Grand Foo Union"]}}}
    m11 = {"auto": {"status": "fail", "notes": ["missing save: /x/save games/foo_123/a.sav",
          str(subprocess.TimeoutExpired(["p", "--save", "C:\\U\\save games\\bar_9\\a.sav"], 60))], "evidence": {
        "error": {"detail": "D" * 2000, "kind": "format"}, "save": {"path": "save games/foo_123/x.sav"},
        "path": "save games/foo_123/x.sav", "wall_s": 1.5}}}
    (res / "M0-5.json").write_text(json.dumps(m5))
    (res / "M0-11.json").write_text(json.dumps(m11))
    (res / "M0-14.json").write_text("{not json Foo")
    (res / "M0-4.tail.jsonl").write_text('{"x": "name=Grand Foo Union"}\n')
    (res / "M0-3.json").write_text(json.dumps({"auto": {"evidence": {"error": "E" * 500}}}))
    (res / "m0_results.md").write_text("- missing save: /x/save games/foo_123/a.sav\n")
    out = tmp_path / "o.zip"
    pack.build_zip(res, out, home=tmp_path / "h", user="")
    zf = zipfile.ZipFile(out)
    blob = b"".join(zf.read(n) for n in zf.namelist()).decode()
    assert "sample" not in blob and "Foo" not in blob and "foo_123" not in blob and "bar_9" not in blob
    assert "save games/<redacted>" in blob
    d = json.loads(zf.read("M0-11.json"))
    assert len(d["auto"]["evidence"]["error"]["detail"]) <= 200 and d["auto"]["evidence"]["wall_s"] == 1.5
    assert len(json.loads(zf.read("M0-3.json"))["auto"]["evidence"]["error"]) == 200
    assert json.loads(zf.read("M0-14.json")) == {"redacted": "unparseable"}
    assert "name=<redacted>" in json.loads(zf.read("M0-5.json"))["auto"]["notes"][0]


def test_pack_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    user = tmp_path / "Stellaris"
    user.mkdir()
    res = _results(tmp_path)
    base = ["m0", "--user-dir", str(user)]
    assert sae_main([*base, "--results", str(res), "pack", "--out", str(user / "o.zip")]) == 2
    assert "inside a game folder" in capsys.readouterr().err
    assert sae_main([*base, "--results", str(tmp_path / "none"), "pack", "--out", str(tmp_path / "o.zip")]) == 2
    (tmp_path / "empty").mkdir()
    assert sae_main([*base, "--results", str(tmp_path / "empty"), "pack", "--out", str(tmp_path / "o.zip")]) == 2
    assert not (tmp_path / "o.zip").exists()


def test_pack_default_out_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user = tmp_path / "Stellaris"
    user.mkdir()
    res = _results(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert sae_main(["m0", "--user-dir", str(user), "--results", str(res), "pack"]) == 0
    assert len(list(work.glob("m0_results_????????-??????.zip"))) == 1


# --- --kit-dir ---------------------------------------------------------------------------------------------------


def test_kit_dir_default_and_repo_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    import argparse
    assert cli._kit_dir(argparse.Namespace(kit_dir=None)) == cli.REPO  # this checkout has mod/sae_bridge
    monkeypatch.setattr(cli, "REPO", tmp_path / "norepo")
    monkeypatch.chdir(tmp_path)
    assert cli._kit_dir(argparse.Namespace(kit_dir=None)) == Path.cwd()
    user = tmp_path / "Stellaris"
    user.mkdir()
    kit = tmp_path / "mykit"
    code = sae_main(["m0", "--user-dir", str(user), "--results", str(tmp_path / "r"), "--kit-dir", str(kit),
                     "steps", "M0-11"])
    out = capsys.readouterr().out
    assert code == 0 and f"{kit}/parser" in out and "bundled in the field kit" in out and "{" not in out
