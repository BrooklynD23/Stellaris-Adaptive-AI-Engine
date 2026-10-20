"""Campaign fold, run scan and update planning."""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime
from pathlib import Path

from sae.contracts.c10_profile import CampaignEvent
from sae.profile import index
from sae.profile.index import Segment, campaign_state, new_campaign_id, plan_update, scan_runs

from ._fixtures import T, live_key, make_run, month, run_id, synth_key

AT = datetime(2026, 10, 1, tzinfo=UTC)
K1, K2 = live_key(1), live_key(2)


def ev(seq: int, cid: str, kind: str, **kw: object) -> CampaignEvent:
    return CampaignEvent.model_validate({"seq": seq, "at": AT, "campaign_id": cid, "event": kind, **kw})


def seen(
    seq: int, cid: str, start: str, last: str, runs: tuple[str, ...], key: str = K1, **kw: object
) -> CampaignEvent:
    return ev(seq, cid, "seen", folder_key=key, start_date=start, last_date=last, run_ids=runs, **kw)


def cid_n(n: int) -> str:
    return f"c-{n:016x}"


def seg(rid: str, first: str, last: str, key: str = K1, n: int = 0) -> Segment:
    return Segment(rid, T(2026, 10, 1 + n), key, first, last, ("v4.4.4",), ())


def no_ask(cid: str) -> None:
    return None


# --- fold ----------------------------------------------------------------------------------------------------


def test_campaign_id_formula() -> None:
    assert new_campaign_id(K1, "r") == "c-" + hashlib.sha256(f"{K1}|r".encode()).hexdigest()[:16]


def test_seen_merge_union_minmax_first_folder() -> None:
    a, b = run_id(1), run_id(2)
    rows = [
        seen(0, cid_n(1), "2200.03.01", "2200.06.01", (a,), game_versions=("v1",), policy_sha256=("aa" * 32,)),
        seen(1, cid_n(1), "2200.01.01", "2200.09.01", (a, b), key=K2, game_versions=("v1", "v2")),
    ]
    s = campaign_state(rows)[cid_n(1)]
    assert (s.start_date, s.last_date, s.run_ids, s.folder_key) == ("2200.01.01", "2200.09.01", (a, b), K1)
    assert s.game_versions == ("v1", "v2") and s.policy_sha256 == ("aa" * 32,) and s.status == "active"


def test_status_closed_reopen_excluded_deleted() -> None:
    c = cid_n(1)
    base = [seen(0, c, "2200.01.01", "2200.06.01", (run_id(1),))]
    assert campaign_state(base)[c].status == "active"
    closed = [*base, ev(1, c, "closed")]
    assert campaign_state(closed)[c].status == "closed"
    same = [*closed, seen(2, c, "2200.01.01", "2200.06.01", (run_id(2),))]
    assert campaign_state(same)[c].status == "closed"  # not later: stays closed
    later = [*closed, seen(2, c, "2200.01.01", "2200.07.01", (run_id(2),))]
    assert campaign_state(later)[c].status == "active"
    excl = [*closed, ev(2, c, "excluded", reason="mods")]
    assert campaign_state(excl)[c].status == "excluded"
    assert campaign_state([*excl, ev(3, c, "closed")])[c].status == "excluded"
    assert campaign_state([*excl, seen(3, c, "2200.01.01", "2201.01.01", (run_id(3),))])[c].status == "excluded"
    dele = [*excl, ev(3, c, "deleted")]
    assert campaign_state(dele)[c].status == "deleted"
    assert campaign_state([*dele, ev(4, c, "excluded")])[c].status == "deleted"
    assert campaign_state([*dele, seen(4, c, "2200.01.01", "2300.01.01", (run_id(3),))])[c].last_date == "2200.06.01"


def test_row_for_unknown_campaign_ignored() -> None:
    assert campaign_state([ev(0, cid_n(9), "closed")]) == {}


def _labelled(n: int) -> list[CampaignEvent]:
    rows: list[CampaignEvent] = []
    for i in range(1, n + 1):
        rows.append(seen(len(rows), cid_n(i), "2200.01.01", "2201.01.01", (run_id(i),), key=live_key(i)))
    for i in range(1, n + 1):
        rows.append(ev(len(rows), cid_n(i), "labelled", label_sha256="cd" * 32))
    return rows


def test_label_split_rules() -> None:
    st = campaign_state(_labelled(15))
    split = {st[cid_n(i)].label_order: st[cid_n(i)].split for i in range(1, 16)}
    assert [split[i] for i in range(1, 6)] == ["tune"] * 5
    assert split[6] == "sealed" and split[10] == "sealed" and split[14] == "sealed"
    assert split[7] == "tune" and split[8] == "tune" and split[9] == "tune" and split[11] == "tune"


def test_label_order_is_first_label_and_relabel_ignored() -> None:
    rows = [*_labelled(2), ev(4, cid_n(1), "labelled", label_sha256="ee" * 32)]
    st = campaign_state(rows)
    assert st[cid_n(1)].label_order == 1 and st[cid_n(2)].label_order == 2
    assert campaign_state(rows[:2])[cid_n(1)].split is None


def test_sealed_retirement_needs_two_newer_and_ten_queries() -> None:
    rows = _labelled(14)  # sealed: #6, #10, #14
    c6 = cid_n(6)
    assert campaign_state(rows, {c6: 10})[c6].split == "tune"
    assert campaign_state(rows, {c6: 9})[c6].split == "sealed"
    assert campaign_state(_labelled(10), {c6: 99})[c6].split == "sealed"  # one newer only
    assert campaign_state(rows, {cid_n(14): 99})[cid_n(14)].split == "sealed"  # newest never retires


def test_fold_is_deterministic_under_prefix_property() -> None:
    rnd = random.Random(7)
    rows = _labelled(6)
    for _ in range(20):
        k = rnd.randrange(len(rows) + 1)
        assert campaign_state(rows[:k]) == campaign_state(rows[:k])
        st = campaign_state(rows[:k])
        assert list(st) == [e.campaign_id for e in rows[:k] if e.event == "seen"]


# --- scan ----------------------------------------------------------------------------------------------------


def test_scan_modes_and_synth_flag(tmp_path: Path) -> None:
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 2), K1, [month("2200.01.01")])
    make_run(tmp_path, run_id(2, "release"), "release", T(2026, 10, 1), K2, [month("2200.01.01")])
    make_run(tmp_path, run_id(3, "synth"), "synth", T(2026, 10, 3), synth_key(3), [month("2200.01.01")])
    make_run(tmp_path, run_id(4, "replay"), "replay", T(2026, 10, 4), K1, [month("2200.01.01")])
    plain = scan_runs(tmp_path, False)
    assert [s.run_id for s in plain] == [run_id(2, "release"), run_id(1)]  # by run t_wall
    withs = scan_runs(tmp_path, True)
    assert len(withs) == 4 and withs[-1].run_id == run_id(4, "replay")


def test_scan_two_folder_keys_two_segments_and_null_skipped(tmp_path: Path) -> None:
    months = [month("2200.01.01"), month("2200.02.01"), month("2200.03.01"), month("2200.04.01"), month("2200.05.01")]
    make_run(tmp_path, run_id(1), "live", T(2026, 10, 1), None, months, keys=[K1, K1, None, K2, K2])
    segs = scan_runs(tmp_path, False)
    assert [(s.folder_key, s.first_date, s.last_date) for s in segs] == [
        (K1, "2200.01.01", "2200.02.01"), (K2, "2200.04.01", "2200.05.01")
    ]
    assert segs[0].game_versions == ("v4.4.4",) and len(segs[0].policy_sha256) == 1


def test_scan_ignores_unusable_runs(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "bus.jsonl").write_text("garbage\n")
    (tmp_path / "nobus").mkdir()
    assert scan_runs(tmp_path, True) == []


# --- plan ----------------------------------------------------------------------------------------------------


def test_new_campaign_then_idempotent_and_growing(tmp_path: Path) -> None:
    s1 = seg(run_id(1), "2200.01.01", "2200.06.01")
    rows, pend = plan_update({}, [s1], no_ask)
    assert pend == [] and len(rows) == 1 and rows[0].campaign_id == new_campaign_id(K1, run_id(1))
    st = campaign_state(rows)
    assert plan_update(st, [s1], no_ask) == ([], [])
    grown = seg(run_id(1), "2200.01.01", "2200.09.01")
    more, _ = plan_update(st, [grown], no_ask)
    assert len(more) == 1 and more[0].last_date == "2200.09.01" and more[0].campaign_id == rows[0].campaign_id


def test_second_segment_of_same_run_other_folder_is_new_campaign() -> None:
    segs = [seg(run_id(1), "2200.01.01", "2200.02.01", K1), seg(run_id(1), "2300.01.01", "2300.02.01", K2)]
    rows, _ = plan_update({}, segs, no_ask)
    assert len(rows) == 2 and rows[0].campaign_id != rows[1].campaign_id


def _two_campaign_state() -> tuple[dict[str, index.CampaignState], str, str]:
    """Old campaign (run 1) closed by a 'new' split, then current campaign (run 2); same folder."""
    old, new = new_campaign_id(K1, run_id(1)), new_campaign_id(K1, run_id(2))
    rows = [
        seen(0, old, "2200.01.01", "2280.01.01", (run_id(1),)),
        ev(1, old, "closed"),
        seen(2, new, "2200.01.01", "2210.01.01", (run_id(2),)),
    ]
    return campaign_state(rows), old, new


def test_lookup_a_run_in_older_campaign_goes_there() -> None:
    st, old, _new = _two_campaign_state()
    rows, _ = plan_update(st, [seg(run_id(1), "2200.01.01", "2290.01.01")], no_ask)
    assert [r.campaign_id for r in rows] == [old]  # reopened by a later last_date, not the current campaign


def test_lookup_b_deleted_campaign_skipped() -> None:
    st, old, _new = _two_campaign_state()
    st = campaign_state([seen(0, old, "2200.01.01", "2280.01.01", (run_id(1),)), ev(1, old, "deleted")])
    assert plan_update(st, [seg(run_id(1), "2200.01.01", "2290.01.01")], no_ask) == ([], [])


def test_lookup_c_new_run_goes_to_latest_by_first_seen() -> None:
    st, _old, new = _two_campaign_state()
    rows, _ = plan_update(st, [seg(run_id(3), "2209.01.01", "2212.01.01")], no_ask)
    assert [r.campaign_id for r in rows] == [new]


def test_lookup_c_skips_deleted_current() -> None:
    old, new = new_campaign_id(K1, run_id(1)), new_campaign_id(K1, run_id(2))
    st = campaign_state([
        seen(0, old, "2200.01.01", "2250.01.01", (run_id(1),)),
        seen(1, new, "2200.01.01", "2210.01.01", (run_id(2),)),
        ev(2, new, "deleted"),
    ])
    rows, _ = plan_update(st, [seg(run_id(3), "2249.01.01", "2250.06.01")], no_ask)
    assert [r.campaign_id for r in rows] == [old]


def test_reused_folder_split_new_reload_none() -> None:
    cur = new_campaign_id(K1, run_id(1))
    st = campaign_state([seen(0, cur, "2200.01.01", "2260.01.01", (run_id(1),))])
    s2 = seg(run_id(2), "2200.01.01", "2205.01.01", n=1)
    asked: list[str] = []

    def ask(answer: str | None):  # type: ignore[no-untyped-def]
        def f(cid: str):  # type: ignore[no-untyped-def]
            asked.append(cid)
            return answer

        return f

    rows, pend = plan_update(st, [s2], ask(None))
    assert rows == [] and pend == [s2] and asked == [cur]
    rows, pend = plan_update(st, [s2], ask("reload"))
    assert [(r.campaign_id, r.event) for r in rows] == [(cur, "seen")] and pend == []
    rows, pend = plan_update(st, [s2], ask("new"))
    new = new_campaign_id(K1, run_id(2))
    assert [(r.campaign_id, r.event) for r in rows] == [(cur, "closed"), (new, "seen")]
    # folded rows: the next segment of run 2 lands on the new campaign
    st2 = campaign_state([seen(0, cur, "2200.01.01", "2260.01.01", (run_id(1),)), *rows])
    assert plan_update(st2, [s2], no_ask) == ([], [])


def test_split_new_when_current_already_closed_emits_no_extra_closed() -> None:
    cur = new_campaign_id(K1, run_id(1))
    st = campaign_state([seen(0, cur, "2200.01.01", "2260.01.01", (run_id(1),)), ev(1, cur, "closed")])
    rows, _ = plan_update(st, [seg(run_id(2), "2200.01.01", "2205.01.01")], lambda _c: "new")
    assert [r.event for r in rows] == ["seen"]


def test_exactly_five_years_is_not_a_split() -> None:
    cur = new_campaign_id(K1, run_id(1))
    st = campaign_state([seen(0, cur, "2200.01.01", "2260.01.01", (run_id(1),))])
    rows, pend = plan_update(st, [seg(run_id(2), "2255.01.01", "2256.01.01")], no_ask)
    assert len(rows) == 1 and rows[0].campaign_id == cur and not pend
