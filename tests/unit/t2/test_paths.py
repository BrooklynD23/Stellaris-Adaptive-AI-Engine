from __future__ import annotations

from pathlib import Path

from sae.adapters.paths import resolve_paths
from sae.contracts.c8_config import PathsConfig


def test_linux_defaults(tmp_path: Path) -> None:
    r = resolve_paths(PathsConfig(), platform="linux", home=tmp_path, which=lambda _: None, repo_root=tmp_path)
    u = tmp_path / ".local/share/Paradox Interactive/Stellaris"
    assert (r.user_dir, r.game_log, r.save_dir, r.ini_path) == (
        u,
        u / "logs/game.log",
        u / "save games",
        u / "sae_in.ini",
    )
    assert r.parser_exe is None


def test_win32_documents_mocked_and_repo_exe(tmp_path: Path) -> None:
    exe = tmp_path / "parser/target/release/sae-parse.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    r = resolve_paths(
        PathsConfig(), platform="win32", documents=lambda: Path("D:/Docs"), which=lambda _: None, repo_root=tmp_path
    )
    assert r.user_dir == Path("D:/Docs") / "Paradox Interactive" / "Stellaris"
    assert r.parser_exe == exe


def test_path_exe_and_overrides(tmp_path: Path) -> None:
    cfg = PathsConfig(user_dir=str(tmp_path / "u"), ini_path=str(tmp_path / "i.ini"), parser_exe=str(Path("/x/p")))
    r = resolve_paths(cfg, platform="linux", which=lambda _: "/usr/bin/sae-parse")
    assert r.user_dir == tmp_path / "u" and r.ini_path == tmp_path / "i.ini"
    assert r.game_log == tmp_path / "u/logs/game.log" and r.parser_exe == Path("/x/p")
    r2 = resolve_paths(PathsConfig(user_dir=str(tmp_path)), platform="linux", which=lambda _: "/usr/bin/sae-parse")
    assert r2.parser_exe == Path("/usr/bin/sae-parse")


def test_repo_debug_build_fallback(tmp_path: Path) -> None:
    dbg = tmp_path / "parser/target/debug/sae-parse"
    dbg.parent.mkdir(parents=True)
    dbg.touch()
    r = resolve_paths(PathsConfig(), platform="linux", home=tmp_path, which=lambda _: None, repo_root=tmp_path)
    assert r.parser_exe == dbg
    rel = tmp_path / "parser/target/release/sae-parse"
    rel.parent.mkdir(parents=True)
    rel.touch()
    r2 = resolve_paths(PathsConfig(), platform="linux", home=tmp_path, which=lambda _: None, repo_root=tmp_path)
    assert r2.parser_exe == rel  # release wins over debug


def test_default_repo_root_finds_real_build() -> None:
    from sae.adapters.paths import _REPO_ROOT

    assert (_REPO_ROOT / "parser" / "Cargo.toml").is_file()


def test_resolve_parser_exe_config_wins_and_none_when_absent(tmp_path: Path) -> None:
    from sae.adapters.paths import resolve_parser_exe

    assert resolve_parser_exe(PathsConfig(parser_exe="/x/p")) == str(Path("/x/p"))
