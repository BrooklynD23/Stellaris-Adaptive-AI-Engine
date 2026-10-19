"""Static checker for the ``sae_bridge`` mod (T1-U2).

Usage: ``uv run python3 mod/sae_bridge/tools/check_mod.py [--root mod/sae_bridge] [--write-unverified]``.
Checks braces/encoding, log templates (against C1), C2 variable names, C3 enum families, loc keys,
event ids and UNVERIFIED tags, and generates ``UNVERIFIED.md``. Stdlib plus ``sae.contracts`` only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sae.contracts.c1_log import LogLineError, parse_log_line
from sae.contracts.c2_ini import _COUNTRY_ORDER  # pyright: ignore[reportPrivateUsage]
from sae.contracts.common import EconPlan, Posture, ReasonKey, ShipSize, Stance

REPO = Path(__file__).resolve().parents[3]
BOM = "﻿"
TAG_RE = re.compile(
    r"# UNVERIFIED\((A-\d+), ((?:M0-\d+|M1-P|R-\d+)(?:/(?:M0-\d+|M1-P|R-\d+))*)\): (.+?) \| fallback: (.+)$"
)
LOG_RE = re.compile(r'\blog\s*=\s*"((?:[^"\\]|\\.)*)"')
TOKEN_RE = re.compile(r"\[([^\]]*)\]")
IDENT_RE = re.compile(r"\bsae_[a-z0-9_]+")
VALID_TEST_RE = re.compile(r"M0-(?:[1-9]|1[0-4])|M1-P|R-[1-9]")
_A = frozenset
#: (regex on comment-stripped code, A-ids any one of which must tag the construct)
RULES: list[tuple[re.Pattern[str], frozenset[str]]] = [
    (re.compile(p), _A(ids.split()))
    for p, ids in [
        (r"\blog\s*=", "A-02"),
        (r"\[GetDate\]", "A-05"),
        (r"\[This\.sae_", "A-06"),
        (r"\bGetName\b", "A-42 A-39"),
        (r"\bis_multiplayer\b", "A-13"),
        (r"\bglobal_event_country\b", "A-36"),
        (r"\b(?:modulo|subtract)_variable\b", "A-37"),
        (r"\b(?:set|change)_variable\s*=\s*\{[^}]*\bvalue\s*=\s*(?!-?\d)[^\s}]", "A-36 A-37 A-38"),
        (r"\bcheck_variable\b.*(?:<|>|value\s*=\s*-1)", "A-37 A-41"),
        (r"\bis_variable_set\b", "A-37 A-41"),
        (r"\bprev\.", "A-37 A-39"),
        (r"\b(?:has_communications|has_met)\b", "A-32"),
        (r"\b(?:add|remove)_opinion_modifier\b", "A-23 A-39"),
        (r"\btrigger:", "A-38 A-37"),
        (r"\bon_monthly_pulse\b|\bon_game_start\b", "A-40"),
        (r"\bon_game_load\b", "A-35"),
        (r"\bevent_target:", "A-36 A-39"),
        (
            r"\bis_country_type\b|\bis_ai\b|\b(?:every|any|random)_country\b|\bsave_event_target_as\b"
            r"|\bcountry_event\b|\bscopes\s*=|\bfrom\s*=|\[From\.",
            "A-39",
        ),
    ]
]
DIR_RULES: dict[tuple[str, ...], frozenset[str]] = {
    ("common", "ai_budget"): _A({"A-21"}),
    ("common", "script_values"): _A({"A-22"}),
}
ENTRY_RE = re.compile(r"\w+\s*=\s*\{")
SAMPLES = {
    "sae_sid": "3",
    "sae_seq_pending": "17",
    "sae_seq_applied": "18",
    "sae_g_period": "1",
    "sae_g_quiet": "0",
    "sae_g_mp": "0",
    "sae_g_m": "5",
    "sae_p_reason": "1",
    "sae_econ": "1",
    "sae_mil": "1",
    "sae_s_wars": "2",
    "sae_s_contact": "1",
}
STAT_VARS = {"sae_s_fleet", "sae_s_navcap_used", "sae_s_navcap"}
ACK_PAIRS = {
    ("APPLIED", "OK"),
    ("REJECTED", "RANGE"),
    ("REJECTED", "NOT_MANAGED"),
    ("REJECTED", "BAD_TARGET"),
    ("REJECTED", "MULTIPLAYER"),
    ("STALE", "EXPIRED"),
    ("DUPLICATE", "SEQ_OLD"),
    ("SCHEMA_MISMATCH", "SCHEMA"),
}
BASE = {
    "HELLO": {"mod", "schema", "period", "quiet", "mp"},
    "HB": {"m", "period", "quiet"},
    "SNAP": {"sid", "seq", "fleet", "navcap_used", "navcap", "wars", "econ", "mil", "contact", "name"},
    "PLYR": {"sid", "fleet", "navcap_used", "navcap", "wars", "name"},
    "ACK": {"sid", "seq", "status", "code"},
}


@dataclass(frozen=True)
class Failure:
    """One checker failure."""

    file: str
    line: int
    check: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.check}: {self.message}"


@dataclass(frozen=True)
class Tag:
    """One parsed UNVERIFIED tag."""

    file: str
    line: int
    aid: str
    tests: str
    claim: str
    fallback: str


def strip_comment(line: str) -> str:
    """Return ``line`` without its ``#`` comment (``#`` inside a double-quoted string is kept)."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i]
    return line


def script_files(root: Path) -> list[Path]:
    """All ``.txt``/``.mod`` files, sorted."""
    return sorted(
        p for p in root.rglob("*") if p.suffix in {".txt", ".mod"} and "tests" not in p.relative_to(root).parts
    )


def loc_files(root: Path) -> list[Path]:
    """Localisation ``.yml`` files, sorted."""
    return sorted((root / "localisation").rglob("*.yml"))


def rel(root: Path, p: Path) -> str:
    """Path relative to the mod root, POSIX style."""
    return p.relative_to(root).as_posix()


def code_lines(p: Path) -> list[tuple[int, str]]:
    """``(lineno, code-without-comment)`` for a script file."""
    return [(n, strip_comment(t)) for n, t in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)]


def check_braces(root: Path) -> list[Failure]:
    """Check 1: balanced braces, loc BOM/header, ASCII-only scripts."""
    out: list[Failure] = []
    for p in script_files(root):
        name = rel(root, p)
        raw = p.read_bytes()
        if not raw.isascii():
            out.append(Failure(name, 1, "braces", "non-ASCII byte in script file"))
        stack: list[int] = []
        for n, code in code_lines(p):
            quoted = re.sub(r'"[^"]*"', '""', code)
            for ch in quoted:
                if ch == "{":
                    stack.append(n)
                elif ch == "}":
                    if not stack:
                        out.append(Failure(name, n, "braces", "unmatched '}'"))
                        break
                    stack.pop()
        out.extend(Failure(name, n, "braces", "unmatched '{'") for n in stack)
    for p in loc_files(root):
        name = rel(root, p)
        text = p.read_bytes().decode("utf-8")
        if not text.startswith(BOM):
            out.append(Failure(name, 1, "braces", "localisation file must start with a UTF-8 BOM"))
        keys = [
            (n, t)
            for n, t in enumerate(text.lstrip(BOM).splitlines(), 1)
            if t.strip() and not t.lstrip().startswith("#")
        ]
        if not keys or keys[0][1].strip() != "l_english:":
            out.append(Failure(name, keys[0][0] if keys else 1, "braces", "first key line must be 'l_english:'"))
    return out


def descriptor_version(root: Path) -> str:
    """``version`` from descriptor.mod ('' if absent)."""
    m = re.search(r'^version\s*=\s*"([^"]*)"', (root / "descriptor.mod").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def render(template: str, name: str, line: int, fixed: bool = False) -> tuple[str, list[Failure]]:
    """Substitute bracket tokens with samples; fail on anything not allowed."""
    fails: list[Failure] = []

    def sub(m: re.Match[str]) -> str:
        tok = m.group(1)
        if tok == "GetDate":
            return "2231.04.01"
        if tok == "This.GetName":
            return "Kel-Azaan | Hegemony"
        var = tok.removeprefix("This.")
        if tok.startswith("This.sae_"):
            if var in SAMPLES:
                return SAMPLES[var] + (".000" if fixed else "")
            if var in STAT_VARS:
                return "412.500" if fixed else "412.5"
        fails.append(Failure(name, line, "templates", f"disallowed or unsampled bracket token [{tok}]"))
        return "0"

    return TOKEN_RE.sub(sub, template), fails


def expected_keys(rtype: str, status: str, code: str) -> set[str]:
    """Expected exact key set for a record type (ACK depends on status/code)."""
    keys = set(BASE[rtype])
    if rtype == "ACK":
        if (status, code) == ("APPLIED", "OK"):
            keys.add("reason")
        if status == "DUPLICATE":
            keys.add("applied")
    return keys


def check_templates(root: Path) -> tuple[list[Failure], int]:
    """Check 2: render and parse every ``log = "..."`` template. Returns (failures, template count)."""
    out: list[Failure] = []
    counts: dict[str, int] = defaultdict(int)
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    n_tpl = 0
    for p in script_files(root):
        name = rel(root, p)
        for n, code in code_lines(p):
            for m in LOG_RE.finditer(code):
                n_tpl += 1
                out.extend(_check_one(root, name, n, m.group(1), counts, pairs))
    for rtype in ("HELLO", "HB", "SNAP", "PLYR"):
        if counts[rtype] != 1:
            out.append(
                Failure("common", 0, "templates", f"expected exactly one {rtype} template, found {counts[rtype]}")
            )
    for pair in sorted(ACK_PAIRS):
        if pairs[pair] != 1:
            out.append(Failure("common", 0, "templates", f"ACK pair {pair} appears {pairs[pair]} times, expected 1"))
    return out, n_tpl


def _check_one(
    root: Path, name: str, n: int, tpl: str, counts: dict[str, int], pairs: dict[tuple[str, str], int]
) -> list[Failure]:
    text, out = render(tpl, name, n)
    fields = tpl.split("|")
    rtype = fields[2] if len(fields) > 2 else "?"
    kv = dict(f.split("=", 1) for f in fields[4:] if "=" in f)
    counts[rtype] += 1
    if rtype == "ACK":
        pairs[(kv.get("status", ""), kv.get("code", ""))] += 1
    if out:
        return out
    fixed_text, _ = render(tpl, name, n, fixed=True)
    for label, line in (("plain", text), ("fixed-point", fixed_text)):
        try:
            rec = parse_log_line(line)
        except LogLineError as exc:
            return [Failure(name, n, "templates", f"{label} rendering fails C1 parse: {exc}")]
    if rec is None or rec.type != rtype or rtype not in BASE:
        return [Failure(name, n, "templates", f"rendered line did not parse as {rtype}")]
    want = expected_keys(rtype, kv.get("status", ""), kv.get("code", ""))
    if set(kv) != want:
        return [Failure(name, n, "templates", f"{rtype} keys {sorted(set(kv))} != expected {sorted(want)}")]
    if rtype == "HELLO" and kv.get("mod") != descriptor_version(root):
        return [Failure(name, n, "templates", f"HELLO mod={kv.get('mod')} != descriptor {descriptor_version(root)}")]
    return []


def referenced(root: Path) -> dict[str, tuple[str, int]]:
    """``sae_*`` identifiers in common/** and events/** (code only), with first location."""
    seen: dict[str, tuple[str, int]] = {}
    for p in script_files(root):
        parts = p.relative_to(root).parts
        if parts[0] not in {"common", "events"}:
            continue
        for n, code in code_lines(p):
            for ident in IDENT_RE.findall(re.sub(r'"[^"]*"', '""', code)):
                seen.setdefault(ident, (rel(root, p), n))
    return seen


def defined_names(root: Path) -> set[str]:
    """Top-level names defined as scripted effects/triggers/values/budgets (not variables)."""
    names: set[str] = set()
    for p in script_files(root):
        for _, code in code_lines(p):
            m = re.match(r"(?:@)?(sae_\w+)\s*=\s*\{", code)
            if m:
                names.add(m.group(1))
    return names


def check_calls(root: Path) -> list[Failure]:
    """Every ``sae_x = yes|no`` call must name exactly one top-level scripted effect/trigger."""
    out: list[Failure] = []
    defs: dict[str, str] = {}
    for p in script_files(root):
        if p.relative_to(root).parts[:2] not in {("common", "scripted_effects"), ("common", "scripted_triggers")}:
            continue
        for n, code in code_lines(p):
            m = re.match(r"(sae_\w+)\s*=\s*\{", code)
            if m and m.group(1) in defs:
                out.append(Failure(rel(root, p), n, "calls", f"{m.group(1)} is already defined in {defs[m.group(1)]}"))
            elif m:
                defs[m.group(1)] = f"{rel(root, p)}:{n}"
    for p in script_files(root):
        for n, code in code_lines(p):
            out.extend(
                Failure(rel(root, p), n, "calls", f"call to undefined effect/trigger {name}")
                for name in re.findall(r"\b(sae_\w+)\s*=\s*(?:yes|no)\b", code)
                if name not in defs
            )
    return out


def check_c2vars(root: Path) -> list[Failure]:
    """Checks 3 and cross refs: C2 vars, enum families, loc reason keys, event ids."""
    out: list[Failure] = []
    ref = referenced(root)
    ids = {k: v for k, v in ref.items() if k not in defined_names(root)}
    c2 = set(_COUNTRY_ORDER)
    got = {k for k in ids if k.startswith("sae_p_") or k == "sae_seq_pending"}
    for k in sorted(c2 - got):
        out.append(Failure("common", 0, "c2vars", f"C2 variable {k} is never referenced"))
    for k in sorted(got - c2):
        out.append(Failure(*ids[k], "c2vars", f"unknown C2-style variable {k}"))
    for k in ("sae_emit_period", "sae_quiet"):
        if k not in ref:
            out.append(Failure("common", 0, "c2vars", f"knob variable {k} is never referenced"))
    fam = [
        ("sae_econ_", {e.value for e in EconPlan}),
        ("sae_mil_", {e.value for e in Posture}),
        ("sae_dip_", {e.value for e in Stance}),
        ("sae_ship_", {e.value for e in ShipSize}),
    ]
    for prefix, want in fam:
        found = {k[len(prefix) :] for k in ids if k.startswith(prefix)} - {"target"}
        for v in sorted(want - found):
            out.append(Failure("common", 0, "c2vars", f"{prefix}{v} (C3 enum member) is never referenced"))
        for v in sorted(found - want):
            out.append(Failure(*ids[prefix + v], "c2vars", f"{prefix}{v} is not a C3 enum member"))
    out.extend(_check_loc_events(root))
    out.extend(_check_managed_cap(root))
    out.extend(_check_integrality(root))
    return out


#: C6 section 2: the mod caps the managed set with its own scripted variable.
MAX_MANAGED_DEF = re.compile(r"^@sae_max_managed\s*=\s*16\s*$", re.M)
MAX_MANAGED_USE = re.compile(r"check_variable\s*=\s*\{\s*which\s*=\s*sae_g_managed\s+value\s*<\s*@sae_max_managed\s*\}")


def _check_managed_cap(root: Path) -> list[Failure]:
    text = "\n".join("\n".join(c for _, c in code_lines(p)) for p in script_files(root))
    out: list[Failure] = []
    if not MAX_MANAGED_DEF.search(text):
        out.append(Failure("common", 0, "c2vars", "@sae_max_managed = 16 is not defined (C6 section 2)"))
    if not MAX_MANAGED_USE.search(text):
        out.append(Failure("common", 0, "c2vars", "managed cap check on sae_g_managed < @sae_max_managed is missing"))
    return out


#: C2 section 3 "integers only": every staged value except sae_p_schema is tested with x mod 1 = 0.
_FRAC_RE = re.compile(r"set_variable\s*=\s*\{\s*which\s*=\s*sae_t_frac\s+value\s*=\s*(sae_\w+)\s*\}")


def _check_integrality(root: Path) -> list[Failure]:
    text = "\n".join("\n".join(c for _, c in code_lines(p)) for p in script_files(root))
    got = set(_FRAC_RE.findall(text))
    want = set(_COUNTRY_ORDER) - {"sae_p_schema"}
    out = [Failure("common", 0, "c2vars", f"no integrality check for {k}") for k in sorted(want - got)]
    if "modulo_variable = { which = sae_t_frac value = 1 }" not in text:
        out.append(Failure("common", 0, "c2vars", "integrality modulo on sae_t_frac is missing"))
    if "check_variable = { which = sae_t_nonint value = 0 }" not in text:
        out.append(Failure("common", 0, "c2vars", "range check does not require sae_t_nonint = 0"))
    return out


def _check_loc_events(root: Path) -> list[Failure]:
    out: list[Failure] = []
    loc = "".join(p.read_text(encoding="utf-8") for p in loc_files(root))
    for r in ReasonKey:
        if not re.search(rf"^\s*sae_reason_{r.value}:", loc, re.M):
            out.append(Failure("localisation", 0, "c2vars", f"missing loc key sae_reason_{r.value}"))
    defs: set[str] = set()
    def_pos: set[tuple[str, int]] = set()
    for p in script_files(root):
        if p.relative_to(root).parts[0] == "events":
            for n, e in event_defs(p):
                defs.add(e)
                def_pos.add((rel(root, p), n))
    out.extend(
        Failure(f, n, "c2vars", f"event {e} is referenced but not defined")
        for p in script_files(root)
        if p.relative_to(root).parts[0] in {"common", "events"}
        for f, n, code in [(rel(root, p), 0, "")]
        for n, code in code_lines(p)
        if (f, n) not in def_pos
        for e in re.findall(r"\b(sae\.\d+)\b(?!\.)", code)
        if e not in defs
    )
    return out


def event_defs(p: Path) -> list[tuple[int, str]]:
    """``(line, id)`` for ``id = sae.N`` at brace depth 1 inside a top-level ``event``/``country_event`` block."""
    defs: list[tuple[int, str]] = []
    depth, top = 0, ""
    for n, code in code_lines(p):
        flat = re.sub(r'"[^"]*"', '""', code)
        if depth == 0:
            m = re.match(r"\s*(\w+)\s*=\s*\{", flat)
            top = m.group(1) if m else ""
        for m in re.finditer(r"\bid\s*=\s*(sae\.\d+)\b", flat):
            before = flat[: m.start()]
            if depth + before.count("{") - before.count("}") == 1 and top in {"event", "country_event"}:
                defs.append((n, m.group(1)))
        depth += flat.count("{") - flat.count("}")
    return defs


def load_registry() -> dict[str, tuple[str, str]]:
    """Parse README §5 and proposal 006 §1 tables into ``{A-id: (assumption, M0 tests)}``."""
    reg: dict[str, tuple[str, str]] = {}
    files = [REPO / "contracts" / "README.md", *sorted((REPO / "contracts" / "proposals").glob("006-T1-*.md"))]
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\| (A-\d+) \| (.*) \| ([^|]+) \| ([^|]+) \|$", line)
            if m:
                reg[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
    return reg


def collect_tags(root: Path) -> tuple[list[Tag], list[Failure]]:
    """Check 4a: parse tags in every script and loc file."""
    reg, tags, out = load_registry(), [], []
    for p in [*script_files(root), *loc_files(root)]:
        name = rel(root, p)
        for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            if "UNVERIFIED" not in line:
                continue
            m = TAG_RE.search(line)
            if not m:
                out.append(Failure(name, n, "tags", "malformed UNVERIFIED tag"))
                continue
            if m.group(1) not in reg:
                out.append(Failure(name, n, "tags", f"unknown assumption id {m.group(1)}"))
            allowed = set(re.split(r"[,/\s]+", reg.get(m.group(1), ("", ""))[1]))
            for tid in m.group(2).split("/"):
                if not VALID_TEST_RE.fullmatch(tid):
                    out.append(Failure(name, n, "tags", f"invalid test id {tid}"))
                elif m.group(1) in reg and tid not in allowed:
                    out.append(Failure(name, n, "tags", f"test id {tid} not in registry row for {m.group(1)}"))
            if not m.group(4).strip():
                out.append(Failure(name, n, "tags", "empty fallback"))
            tags.append(Tag(name, n, m.group(1), m.group(2), m.group(3), m.group(4).strip()))
    return tags, out


def check_risky(root: Path) -> list[Failure]:
    """Check 4b: each risky construct needs a tag with an allowed A-id on its line or the comment block above."""
    out: list[Failure] = []
    for p in [*script_files(root), *loc_files(root)]:
        name = rel(root, p)
        lines = p.read_text(encoding="utf-8-sig").splitlines()
        dir_rule = DIR_RULES.get(p.relative_to(root).parts[:2]) if p.suffix == ".txt" else None
        for i, line in enumerate(lines):
            code = strip_comment(line)
            needs = [ids for rx, ids in RULES if rx.search(code)]
            if dir_rule and ENTRY_RE.match(code):
                needs.append(dir_rule)
            have = tag_ids(lines, i) if needs else set()
            for ids in needs:
                if not ids & have:
                    want = "/".join(sorted(ids))
                    out.append(Failure(name, i + 1, "tags", f"risky construct needs a {want} tag: {code.strip()[:60]}"))
    return out


def tag_ids(lines: list[str], i: int) -> set[str]:
    """A-ids of tags on line ``i`` and in the contiguous comment block directly above it."""
    found = {m.group(1) for m in [TAG_RE.search(lines[i])] if m}
    j = i - 1
    while j >= 0 and lines[j].lstrip().startswith("#"):
        found |= {m.group(1) for m in [TAG_RE.search(lines[j])] if m}
        j -= 1
    return found


def generate_md(tags: list[Tag]) -> str:
    """Check 5: the deterministic UNVERIFIED.md body."""
    reg = load_registry()
    by: dict[str, list[Tag]] = defaultdict(list)
    for t in tags:
        by[t.aid].append(t)
    rows = [
        "# UNVERIFIED assumptions in sae_bridge",
        "",
        "Generated by `mod/sae_bridge/tools/check_mod.py --write-unverified`; do not edit by hand.",
        "Sources: the `# UNVERIFIED(...)` tags in the mod files, `contracts/README.md` section 5 and",
        "`contracts/proposals/006-T1-mod-semantics-and-assumptions.md` section 1.",
        "T8 merges this file into the M0 checklist.",
        "",
        "| Id | Assumption | M0 test | Constructs | Fallback(s) |",
        "|---|---|---|---|---|",
    ]
    for aid in sorted(by, key=lambda a: int(a[2:])):
        ts = sorted(by[aid], key=lambda t: (t.file, t.line))
        cons = "<br>".join(f"`{t.file}:{t.line}` ({t.tests}) {_esc(t.claim)}" for t in ts)
        fbs = "<br>".join(sorted({_esc(t.fallback) for t in ts}))
        text, tests = reg.get(aid, ("?", "?"))
        rows.append(f"| {aid} | {_esc(text)} | {tests} | {cons} | {fbs} |")
    return "\n".join(rows) + "\n"


def _esc(s: str) -> str:
    return s.replace("|", "\\|")


def run(root: Path, write: bool = False) -> tuple[list[Failure], str]:
    """Run every check. Returns (failures, summary line)."""
    tags, tag_fails = collect_tags(root)
    tpl_fails, n_tpl = check_templates(root)
    fails = [*check_braces(root), *tpl_fails, *check_c2vars(root), *check_calls(root), *tag_fails, *check_risky(root)]
    md, target = generate_md(tags), root / "UNVERIFIED.md"
    if write:
        target.write_text(md, encoding="utf-8", newline="\n")
    elif not target.exists() or target.read_bytes().decode("utf-8").replace("\r\n", "\n") != md:
        fails.append(Failure("UNVERIFIED.md", 0, "unverified_md", "missing or stale; run with --write-unverified"))
    n_files = len(script_files(root)) + len(loc_files(root))
    return fails, f"check_mod: {n_files} files, {n_tpl} templates, {len(tags)} tags, {len(fails)} failures"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=REPO / "mod" / "sae_bridge")
    ap.add_argument("--write-unverified", action="store_true")
    args = ap.parse_args(argv)
    fails, summary = run(args.root, args.write_unverified)
    for f in fails:
        print(f)
    print(summary)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
