"""Tests for tools/check_mod.py: the real mod passes, and each check fails on a mutated copy."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

MOD = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_mod", MOD / "tools" / "check_mod.py")
assert SPEC is not None and SPEC.loader is not None
cm: ModuleType = importlib.util.module_from_spec(SPEC)
sys.modules["check_mod"] = cm
SPEC.loader.exec_module(cm)

EFFECTS = "common/scripted_effects/sae_effects.txt"


@pytest.fixture()
def copy(tmp_path: Path) -> Path:
    """Fresh copy of the mod with a regenerated UNVERIFIED.md."""
    dst = tmp_path / "sae_bridge"
    shutil.copytree(MOD, dst, ignore=shutil.ignore_patterns("tests", "tools", "__pycache__"))
    cm.run(dst, write=True)
    return dst


def edit(root: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, old
    p.write_text(text.replace(old, new, count), encoding="utf-8", newline="")


def checks(root: Path, write: bool = False) -> set[str]:
    fails, _ = cm.run(root, write)
    return {f.check for f in fails}


def messages(root: Path) -> str:
    fails, _ = cm.run(root)
    return "\n".join(str(f) for f in fails)


def test_real_mod_passes() -> None:
    fails, summary = cm.run(MOD)
    assert fails == [], "\n".join(map(str, fails))
    assert summary.endswith("0 failures")
    assert cm.main(["--root", str(MOD)]) == 0


def test_clean_copy_passes(copy: Path) -> None:
    assert messages(copy) == ""


def test_unbalanced_brace(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "  events = { sae.3 }\n", "  events = { sae.3 \n")
    assert "unmatched '{'" in messages(copy)


def test_extra_close_brace(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "  events = { sae.3 }\n", "  events = { sae.3 } }\n")
    assert "unmatched '}'" in messages(copy)


def test_loc_bom_missing(copy: Path) -> None:
    p = copy / "localisation/english/sae_l_english.yml"
    p.write_bytes(p.read_bytes().removeprefix(b"\xef\xbb\xbf"))
    assert "BOM" in messages(copy)


def test_bad_bracket_token(copy: Path) -> None:
    edit(
        copy,
        EFFECTS,
        '|period=[This.sae_g_period]|quiet=[This.sae_g_quiet]"',
        '|period=[Root.GetName]|quiet=[This.sae_g_quiet]"',
    )
    assert "disallowed" in messages(copy)


def test_template_missing_key(copy: Path) -> None:
    edit(copy, EFFECTS, '|mp=[This.sae_g_mp]"', '"')
    assert "templates" in checks(copy)


def test_duplicated_ack_pair(copy: Path) -> None:
    edit(copy, EFFECTS, "status=REJECTED|code=RANGE", "status=REJECTED|code=BAD_TARGET")
    msg = messages(copy)
    assert "('REJECTED', 'BAD_TARGET') appears 2 times" in msg
    assert "('REJECTED', 'RANGE') appears 0 times" in msg


def test_hello_version_mismatch(copy: Path) -> None:
    edit(copy, "descriptor.mod", 'version="0.1.0"', 'version="0.2.0"')
    assert "descriptor" in messages(copy)


def test_unknown_p_var(copy: Path) -> None:
    edit(copy, EFFECTS, "sae_p_expires", "sae_p_bogus", count=100)
    msg = messages(copy)
    assert "unknown C2-style variable sae_p_bogus" in msg


def test_enum_family_mismatch(copy: Path) -> None:
    edit(copy, EFFECTS, "sae_econ_research", "sae_econ_bogus", count=100)
    msg = messages(copy)
    assert "sae_econ_bogus is not a C3 enum member" in msg


def test_missing_loc_reason_key(copy: Path) -> None:
    edit(copy, "localisation/english/sae_l_english.yml", " sae_reason_baseline:0", " sae_reason_basel1ne:0")
    assert "missing loc key sae_reason_baseline" in messages(copy)


def test_undefined_event_ref(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "sae.3", "sae.99")
    assert "sae.99 is referenced but not defined" in messages(copy)


def test_untagged_risky_token(copy: Path) -> None:
    edit(
        copy,
        "common/on_actions/sae_on_actions.txt",
        " # UNVERIFIED(A-40, M0-3): on_game_start fires once at game start"
        " | fallback: sae.0 prints HELLO on its first run",
        "",
    )
    assert "needs a A-40 tag: on_game_start" in messages(copy)


def test_unknown_a_id(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "UNVERIFIED(A-40,", "UNVERIFIED(A-99,")
    assert "unknown assumption id A-99" in messages(copy)


def test_empty_fallback(copy: Path) -> None:
    edit(
        copy,
        "common/on_actions/sae_on_actions.txt",
        "fallback: sae.0 prints HELLO on its first run",
        "fallback:",
    )
    assert "empty fallback" in messages(copy) or "malformed" in messages(copy)


def test_stale_unverified_md(copy: Path) -> None:
    (copy / "UNVERIFIED.md").write_text("old\n", encoding="utf-8")
    assert "unverified_md" in checks(copy)
    assert "unverified_md" not in checks(copy, write=True)
    assert "unverified_md" not in checks(copy)


def test_missing_unverified_md(copy: Path) -> None:
    (copy / "UNVERIFIED.md").unlink()
    assert "unverified_md" in checks(copy)


@pytest.mark.parametrize("text", ["", "a # b", 'x = "a#b" # c', "{ }"])
def test_strip_comment_idempotent(text: str) -> None:
    once = cm.strip_comment(text)
    assert cm.strip_comment(once) == once
    assert "#" not in once.replace('"a#b"', "")


def test_missing_enum_and_c2_vars_everywhere(copy: Path) -> None:
    for p in copy.rglob("*.txt"):
        text = p.read_text(encoding="utf-8")
        p.write_text(
            text.replace("sae_p_expires", "sae_p_gone").replace("sae_econ_research", "sae_econ_gone"), newline=""
        )
    msg = messages(copy)
    assert "C2 variable sae_p_expires is never referenced" in msg
    assert "sae_econ_research (C3 enum member) is never referenced" in msg


def drop_text(root: Path, rel: str, text: str) -> None:
    edit(root, rel, text, "")


def test_removing_a37_tag_fails_despite_a36_above(copy: Path) -> None:
    tag = " # UNVERIFIED(A-37, M0-5): modulo_variable accepts a variable operand | fallback: per-country countdown"
    drop_text(copy, EFFECTS, tag)
    assert "needs a A-37 tag: modulo_variable" in messages(copy)


def test_invalid_test_id(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "A-40, M0-3", "A-40, M0-99")
    assert "invalid test id M0-99" in messages(copy)


def test_test_id_not_in_registry_row(copy: Path) -> None:
    edit(copy, "common/ai_budget/sae_budgets.txt", "A-21, M0-8", "A-21, M0-3")
    assert "test id M0-3 not in registry row for A-21" in messages(copy)


def test_family_typo(copy: Path) -> None:
    edit(copy, EFFECTS, "sae_econ_research", "sae_econ_tech_rsh")
    assert "sae_econ_tech_rsh is not a C3 enum member" in messages(copy)


def test_undefined_country_event_in_effect(copy: Path) -> None:
    tag = "# UNVERIFIED(A-39, M0-3): country_event from a global event | fallback: none"
    edit(copy, EFFECTS, "sae_emit_plyr = {\n", f"sae_emit_plyr = {{\n  {tag}\n  country_event = {{ id = sae.99 }}\n")
    assert "event sae.99 is referenced but not defined" in messages(copy)


def test_id_nested_in_event_is_not_a_definition(copy: Path) -> None:
    tag = "# UNVERIFIED(A-39, M0-3): country_event from a global event | fallback: none"
    edit(
        copy,
        "events/sae_bridge_events.txt",
        "  id = sae.0\n",
        f"  id = sae.0\n  {tag}\n  immediate = {{ id = sae.77 }}\n",
    )
    edit(copy, "common/on_actions/sae_on_actions.txt", "sae.3", "sae.77")
    assert "event sae.77 is referenced but not defined" in messages(copy)


def test_country_order_is_c2_order() -> None:
    assert cm._COUNTRY_ORDER == [
        "sae_p_schema", "sae_p_expires", "sae_p_reason", "sae_p_econ", "sae_p_mil", "sae_p_mil_target",
        "sae_p_dip", "sae_p_dip_target", "sae_p_ship_corvette", "sae_p_ship_frigate", "sae_p_ship_destroyer",
        "sae_p_ship_cruiser", "sae_p_ship_battleship", "sae_seq_pending",
    ]  # fmt: skip


def test_non_ascii_script(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "# sae_bridge hooks", "# sae_bridge h\u00f3oks")
    assert "non-ASCII" in messages(copy)


def test_missing_l_english(copy: Path) -> None:
    edit(copy, "localisation/english/sae_l_english.yml", "l_english:", "l_french:")
    assert "l_english:" in messages(copy)


def test_malformed_tag(copy: Path) -> None:
    edit(copy, "common/on_actions/sae_on_actions.txt", "A-40, M0-3): on_game_start", "A-40 M0-3): on_game_start")
    assert "malformed UNVERIFIED tag" in messages(copy)


def test_untagged_ai_budget_entry(copy: Path) -> None:
    tag = (
        "# UNVERIFIED(A-21, M0-8): flag-gated additive ai_budget entry changes AI spending | "
        "fallback: gate StarNet economic subplans by flag instead\n"
    )
    edit(copy, "common/ai_budget/sae_budgets.txt", tag, "", count=1)
    assert "needs a A-21 tag" in messages(copy)


def test_missing_hb_template(copy: Path) -> None:
    edit(copy, EFFECTS, "SAE|v1|HB|", "SAE|v1|HX|")
    assert "expected exactly one HB template, found 0" in messages(copy)


def test_wrong_plyr_keys(copy: Path) -> None:
    edit(copy, EFFECTS, '|wars=[This.sae_s_wars]|name=[This.GetName]"', '|name=[This.GetName]"', count=100)
    assert "PLYR keys" in messages(copy)


def test_undefined_effect_call(copy: Path) -> None:
    edit(copy, EFFECTS, "sae_emit_plyr = yes", "sae_emit_plyrr = yes")
    assert "call to undefined effect/trigger sae_emit_plyrr" in messages(copy)


def test_duplicate_definition(copy: Path) -> None:
    p = copy / "common/scripted_triggers/sae_triggers.txt"
    p.write_text(p.read_text(encoding="utf-8") + "\nsae_emit_plyr = { always = yes }\n", encoding="utf-8")
    assert "sae_emit_plyr is already defined in" in messages(copy)


def test_untagged_this_sae_template(copy: Path) -> None:
    p = copy / EFFECTS
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    out = [ln for ln in lines if not ("UNVERIFIED(A-06" in ln and ln.lstrip().startswith("#"))]
    p.write_text("".join(out), encoding="utf-8")
    assert "needs a A-06 tag: log" in messages(copy)


def test_fixed_point_rendering_parses() -> None:
    text, fails = cm.render(
        "SAE|v1|HB|[GetDate]|m=[This.sae_g_m]|period=[This.sae_g_period]|quiet=[This.sae_g_quiet]", "x", 1, fixed=True
    )
    assert fails == [] and "m=5.000" in text
    assert cm.parse_log_line(text) is not None


def test_snap_and_plyr_key_sets() -> None:
    assert cm.BASE["SNAP"] == {"sid", "seq", "fleet", "navcap_used", "navcap", "wars", "econ", "mil", "contact", "name"}
    assert cm.BASE["PLYR"] == {"sid", "fleet", "navcap_used", "navcap", "wars", "name"}


def test_managed_cap_constant_missing(copy: Path) -> None:
    edit(copy, "common/scripted_variables/sae_variables.txt", "@sae_max_managed = 16", "@sae_max_managed = 17")
    assert "@sae_max_managed = 16 is not defined" in messages(copy)


def test_managed_cap_check_missing(copy: Path) -> None:
    edit(copy, EFFECTS, "value < @sae_max_managed", "value < 99")
    assert "managed cap check" in messages(copy)


def test_integrality_check_missing_for_var(copy: Path) -> None:
    edit(copy, EFFECTS, "set_variable = { which = sae_t_frac value = sae_p_econ }", "")
    assert "no integrality check for sae_p_econ" in messages(copy)


def test_range_ignores_integrality(copy: Path) -> None:
    edit(
        copy,
        "common/scripted_triggers/sae_triggers.txt",
        "which = sae_t_nonint value = 0",
        "which = sae_t_nonint value = 1",
    )
    assert "range check does not require sae_t_nonint = 0" in messages(copy)
