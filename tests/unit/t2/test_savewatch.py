from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sae.adapters.savewatch import SaveWatcher

ROOT = Path("/saves")


class FS:
    def __init__(self) -> None:
        self.now = 0.0
        self.files: dict[Path, tuple[int, int]] = {}

    def put(self, name: str, size: int, mtime: int, camp: str = "c1") -> Path:
        p = ROOT / camp / name
        self.files[p] = (size, mtime)
        return p

    def stat(self, p: Path) -> SimpleNamespace:
        if p not in self.files:
            raise FileNotFoundError(p)
        return SimpleNamespace(st_size=self.files[p][0], st_mtime_ns=self.files[p][1])

    def listdir(self, p: Path) -> list[str]:
        if p == ROOT:
            return sorted({f.parent.name for f in self.files})
        names = [f.name for f in self.files if f.parent == p]
        if not names:
            raise FileNotFoundError(p)
        return names

    def watcher(self, **kw: object) -> SaveWatcher:
        return SaveWatcher(ROOT, monotonic=lambda: self.now, stat=self.stat, listdir=self.listdir, **kw)  # type: ignore[arg-type]


def test_baseline_only_newest_then_once() -> None:
    fs = FS()
    fs.put("autosave_a.sav", 10, 1)
    new = fs.put("autosave_b.sav", 10, 2, camp="c2")
    w = fs.watcher()
    assert w.poll() is None
    fs.now = 7.9
    assert w.poll() is None
    fs.now = 8.0
    assert w.poll() == new
    fs.now = 30
    assert w.poll() is None


def test_growing_file_not_eligible_until_stable() -> None:
    fs = FS()
    w = fs.watcher()
    assert w.poll() is None
    p = fs.put("autosave_1.sav", 100, 5)
    for t, size in ((1, 100), (5, 200), (9, 300), (13, 400)):
        fs.now = t
        fs.put("autosave_1.sav", size, 5 + t)
        assert w.poll() is None
    fs.now = 21.0
    assert w.poll() == p


def test_future_mtime_still_eligible() -> None:
    fs = FS()
    w = fs.watcher()
    w.poll()
    p = fs.put("autosave_f.sav", 5, 10**30)
    assert w.poll() is None
    fs.now = 1
    assert w.poll() is None
    fs.now = 9
    assert w.poll() == p


def test_newest_only_marks_rest_done() -> None:
    fs = FS()
    w = fs.watcher()
    w.poll()
    fs.put("autosave_old.sav", 5, 1)
    new = fs.put("autosave_new.sav", 5, 2)
    assert w.poll() is None
    fs.now = 10
    assert w.poll() == new
    fs.now = 20
    assert w.poll() is None


def test_rearm_on_change_and_vanish() -> None:
    fs = FS()
    w = fs.watcher()
    w.poll()
    p = fs.put("autosave_x.sav", 5, 1)
    assert w.poll() is None
    fs.now = 8
    assert w.poll() == p
    fs.put("autosave_x.sav", 6, 2)
    fs.now = 9
    assert w.poll() is None
    fs.now = 9.5
    assert w.poll() is None
    fs.now = 17.5
    assert w.poll() == p
    del fs.files[p]
    fs.now = 30
    assert w.poll() is None
    fs.put("autosave_x.sav", 6, 2)  # reappears: new entry, re-armed
    fs.now = 31
    assert w.poll() is None
    fs.now = 39
    assert w.poll() == p


def test_ironman_and_patterns_and_oserror() -> None:
    fs = FS()
    w = fs.watcher()
    w.poll()
    fs.put("ironman.sav", 5, 1)
    fs.put("other.sav", 5, 1)
    assert w.poll() is None
    fs.now = 9
    assert w.poll() is None
    w2 = fs.watcher(include_ironman=True)
    w2.poll()
    fs.now = 20
    assert w2.poll() == ROOT / "c1" / "ironman.sav"
    bad = fs.watcher()
    bad._stat = lambda p: (_ for _ in ()).throw(OSError("x"))  # type: ignore[assignment]
    assert bad.poll() is None


def test_transient_stat_error_does_not_rearm() -> None:
    fs = FS()
    w = fs.watcher()
    w.poll()
    p = fs.put("autosave_t.sav", 5, 1)
    w.poll()
    fs.now = 8
    assert w.poll() == p
    real = fs.stat
    w._stat = lambda q: (_ for _ in ()).throw(OSError("busy"))  # type: ignore[assignment,return-value]
    fs.now = 20
    assert w.poll() is None
    w._stat = real
    fs.now = 40
    assert w.poll() is None


def test_listdir_failure_keeps_state() -> None:
    fs = FS()
    w = fs.watcher()
    w.poll()
    p = fs.put("autosave_l.sav", 5, 1)
    w.poll()
    fs.now = 8
    assert w.poll() == p
    real = w._listdir
    w._listdir = lambda q: (_ for _ in ()).throw(OSError("gone"))  # type: ignore[assignment,return-value]
    assert w.poll() is None
    w._listdir = real
    fs.now = 30
    assert w.poll() is None


def test_live_preexisting_ironman_not_baselined_but_later_change_is() -> None:
    fs = FS()
    auto = fs.put("autosave_a.sav", 10, 1)
    iron = fs.put("ironman.sav", 10, 5)  # newest, but pre-existing (C6 section 1.10: only modified after start)
    w = fs.watcher(include_ironman=True, baseline_ironman=False)
    assert w.poll() is None
    fs.now = 8.0
    assert w.poll() == auto
    fs.files[iron] = (11, 6)
    fs.now = 9.0
    assert w.poll() is None
    fs.now = 17.0
    assert w.poll() == iron


def test_release_baselines_ironman() -> None:
    fs = FS()
    fs.put("autosave_a.sav", 10, 1)
    iron = fs.put("ironman.sav", 10, 5)
    w = fs.watcher(include_ironman=True)
    w.poll()
    fs.now = 8.0
    assert w.poll() == iron
