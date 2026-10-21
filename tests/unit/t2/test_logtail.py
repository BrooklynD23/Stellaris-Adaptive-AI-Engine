from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from sae.adapters import logtail
from sae.adapters.health import new_gate
from sae.adapters.logtail import MAX_PARTIAL, LogTailer, parse_lines
from sae.contracts.c5_bus import HealthKind

Events = list[tuple[HealthKind, str | None]]


def mk(path: Path) -> tuple[LogTailer, Events]:
    ev: Events = []
    return LogTailer(path, health=lambda k, d: ev.append((k, d))), ev


def append(path: Path, data: bytes) -> None:
    with open(path, "ab") as f:
        f.write(data)


PIECES = st.binary(max_size=40) | st.sampled_from(
    [b"\r\n", b"\n", b"\xe2\x80\xa8", b"SAE|v1|HB|2231.04.01|m=1|period=1|quiet=0"]
)


def _poll_chunks(path: Path, stream: bytes, cuts: list[int]) -> list[str]:
    t, _ = mk(path)
    got: list[str] = []
    pos = 0
    for c in sorted({min(c, len(stream)) for c in cuts}):
        append(path, stream[pos:c])
        pos = c
        got += t.poll()
    append(path, stream[pos:])
    return got + t.poll()


@given(st.lists(PIECES, max_size=30), st.lists(st.integers(min_value=0, max_value=2000), max_size=12))
@example([b"a\r\nb\n"], [2])  # cut between CR and LF
@example([b"\xe2\x80\xa8\n"], [1])  # cut inside U+2028
@example([b"x\xff\xfe\r\n\n"], [3, 4])
@settings(max_examples=80, deadline=None, derandomize=True)
def test_arbitrary_chunking_matches_oneshot(
    tmp_path_factory: pytest.TempPathFactory, pieces: list[bytes], cuts: list[int]
) -> None:
    stream = b"".join(pieces)
    d = tmp_path_factory.mktemp("c")
    expected = _poll_chunks(d / "one.log", stream, [])
    assert _poll_chunks(d / "many.log", stream, cuts) == expected


def test_bounded_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logtail, "MAX_READ", 5)
    p = tmp_path / "g.log"
    p.write_bytes(b"aaa\nbbb\nccc\n")
    t, _ = mk(p)
    got: list[str] = []
    polls = 0
    while len(got) < 3 and polls < 10:
        got += t.poll()
        polls += 1
    assert got == ["aaa", "bbb", "ccc"] and polls == 3


def test_truncation_resets(tmp_path: Path) -> None:
    p = tmp_path / "g.log"
    t, ev = mk(p)
    p.write_bytes(b"aaa\nbbb\n")
    assert t.poll() == ["aaa", "bbb"]
    p.write_bytes(b"c\n")
    assert t.poll() == ["c"]
    assert ev == [(HealthKind.RECONNECT, "truncated_or_replaced")]


def test_head_change_same_size_or_larger(tmp_path: Path) -> None:
    p = tmp_path / "g.log"
    t, ev = mk(p)
    p.write_bytes(b"old1\n")
    t.poll()
    p.write_bytes(b"new1\nnew2\nnew3\n")
    assert t.poll() == ["new1", "new2", "new3"]
    assert len(ev) == 1


def test_missing_then_reappears(tmp_path: Path) -> None:
    p = tmp_path / "g.log"
    t, ev = mk(p)
    assert t.poll() == [] and t.poll() == []
    assert ev == [(HealthKind.RECONNECT, "missing")]
    p.write_bytes(b"x\n")
    assert t.poll() == ["x"]
    p.unlink()
    t.poll()
    assert len(ev) == 2


def test_partial_line_completed_later(tmp_path: Path) -> None:
    p = tmp_path / "g.log"
    t, _ = mk(p)
    append(p, b"abc")
    assert t.poll() == []
    append(p, b"def\r\ng")
    assert t.poll() == ["abcdef"]
    append(p, b"\n")
    assert t.poll() == ["g"]


def test_partial_cap(tmp_path: Path) -> None:
    p = tmp_path / "g.log"
    t, ev = mk(p)
    append(p, b"x" * (MAX_PARTIAL + 1))
    assert t.poll() == []
    assert [k for k, _ in ev] == [HealthKind.LOG_PARSE_ERROR]
    assert (ev[0][1] or "").startswith("too_long")
    append(p, b"yy\nok\n")
    assert t.poll() == ["ok"]


def test_opener_oserror_never_raises(tmp_path: Path) -> None:
    ev: Events = []

    def bad(path: Path, mode: str) -> object:
        raise PermissionError("locked")

    t = LogTailer(tmp_path / "g.log", opener=bad, health=lambda k, d: ev.append((k, d)))  # type: ignore[arg-type]
    assert t.poll() == []
    assert ev[0][0] is HealthKind.RECONNECT


def test_parse_lines_routing() -> None:
    ev: Events = []
    h = lambda k, d: ev.append((k, d))  # noqa: E731
    lines = [
        "noise",
        "SAE|v1|HB|2231.04.01|m=375|period=1|quiet=0",
        "SAE|v2|HB|2231.04.01|m=1",
        "SAE|v2|HB|2231.04.02|m=2",
        "SAE|v1|HB|2231.04.01|m=oops",
        "SAE|v1|HB|2231.04.01|m=oops2",
        "SAE|v1|BOGUS|2231.04.01",
    ]
    recs = parse_lines(lines, h, 375, new_gate())
    assert len(recs) == 1
    kinds = [k for k, _ in ev]
    assert kinds.count(HealthKind.VERSION_SKEW) == 1
    assert kinds.count(HealthKind.LOG_PARSE_ERROR) == 2  # bad_value once per month, bad_header once
    assert all(len(d or "") <= 200 for _, d in ev)


def test_plyr_parse_error_detail_is_redacted(tmp_path: Path) -> None:
    from sae.adapters.health import new_gate
    from sae.adapters.logtail import parse_lines

    ev: list[str | None] = []
    parse_lines(
        ["x SAE|v1|PLYR|2231.01.01|fleet=777777|navcap=notanumber|name=P"],
        lambda k, d: ev.append(d), "k", new_gate(),
    )
    assert ev and "777777" not in (ev[0] or "") and "SAE|v1|PLYR|2231.01.01" in (ev[0] or "")


def test_rotation_with_same_head_detected_by_tail_fingerprint(tmp_path: Path) -> None:
    from sae.adapters.logtail import LogTailer

    p = tmp_path / "g.log"
    head = b"banner " + b"x" * 600 + b"\n"
    p.write_bytes(head + b"".join(b"SAE|v1|HB|2200.01.01|m=%d|period=1|quiet=0\n" % i for i in range(5)))
    t = LogTailer(p, health=lambda k, d: None)
    t.poll()
    p.write_bytes(head + b"".join(b"SAE|v1|HB|2200.02.01|m=%d|period=1|quiet=0\n" % i for i in range(1, 9)))
    out = t.poll()
    assert out[0].startswith("banner") and len(out) == 9


@pytest.mark.parametrize(
    ("line", "want"),
    [
        ("x SAE|v1|PLYR|2231.01.01|fleet=777777|name=P", "SAE|v1|PLYR|2231.01.01"),
        ("SAE|v1|PLYR|2231.1.1|fleet=777777", "SAE|v1|PLYR"),
        ("SAE|v1|SNAP|2231.01.01|sid=3|name=PLYR fleet=777777", "SAE|v1|PLYR|2231.01.01"),
        ("SAE|v1|PLYR", "SAE|v1|PLYR"),
        ("SAE|v1|HB|2231.01.01|m=x", "SAE|v1|HB|2231.01.01|m=x"),
    ],
)
def test_redact_c1_rule(line: str, want: str) -> None:
    from sae.adapters.logtail import _redact

    assert _redact(line) == want


def test_version_skew_plyr_detail_has_no_line_bytes() -> None:
    from sae.adapters import health as H
    from sae.adapters.logtail import parse_lines

    got: list[str | None] = []
    parse_lines(["SAE|v2|PLYR|2231.01.01|fleet=777777"], lambda k, d: got.append(d), None, H.new_gate())
    assert got == ["version != v1"]
