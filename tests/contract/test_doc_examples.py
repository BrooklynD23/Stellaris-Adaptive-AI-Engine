"""Validate every fenced example in contracts/C*.md against the contract code.

Fence info strings (see contracts/README.md, "Examples"):
  sae-log [invalid]              each non-blank line parses (or fails) under C1
  sae-ini [invalid]              the block parses (or fails) under C2; valid blocks round-trip exactly
  sae-ini compiled-from=<id>     equals render_ini(compile_batch(<json block with id=<id>>))
  json model=<Name> [invalid] [id=<id>]
  jsonl model=<Name> [invalid]   each non-blank line
  toml model=SaeConfig [invalid] resolved through resolve_config
Any other fence (text, python, ...) is prose and ignored.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from sae.contracts.c1_log import LogLineError, parse_log_line
from sae.contracts.c2_ini import IniError, parse_ini, render_ini
from sae.contracts.c3_intent import IntentBatch, compile_batch
from sae.contracts.c8_config import resolve_config
from sae.contracts.registry import MODELS

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
_FENCE = re.compile(r"^```([^\n`]*)\n(.*?)^```\s*$", re.M | re.S)


@dataclass(frozen=True)
class Block:
    """One fenced example."""

    doc: str
    line: int
    lang: str
    attrs: dict[str, str]
    invalid: bool
    body: str

    @property
    def ident(self) -> str:
        return f"{self.doc}:{self.line}:{self.lang}"


def _blocks() -> list[Block]:
    found: list[Block] = []
    for path in sorted(CONTRACTS.glob("C[0-9]_*.md")):
        text = path.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            words = match.group(1).split()
            if not words or words[0] not in {"sae-log", "sae-ini", "json", "jsonl", "toml"}:
                continue
            attrs = dict(w.split("=", 1) for w in words[1:] if "=" in w)
            if words[0] in {"json", "jsonl", "toml"} and "model" not in attrs:
                continue
            found.append(
                Block(
                    doc=path.name,
                    line=text.count("\n", 0, match.start()) + 1,
                    lang=words[0],
                    attrs=attrs,
                    invalid="invalid" in words[1:],
                    body=match.group(2),
                )
            )
    return found


BLOCKS = _blocks()
BY_ID = {b.attrs["id"]: b for b in BLOCKS if "id" in b.attrs}


def _lines(body: str) -> list[str]:
    return [ln for ln in body.splitlines() if ln.strip()]


def _check_one(block: Block, payload: str) -> None:
    if block.lang == "sae-log":
        if parse_log_line(payload) is None:
            raise LogLineError("no_marker", payload)
    elif block.lang == "sae-ini":
        parsed = parse_ini(payload)
        if not block.invalid and "compiled-from" not in block.attrs:
            assert render_ini(parsed) == payload, "valid sae-ini examples must round-trip byte for byte"
    elif block.lang == "toml":
        resolve_config(tomllib.loads(payload))
    else:
        MODELS[block.attrs["model"]].validate_python(json.loads(payload))


def _payloads(block: Block) -> list[str]:
    if block.lang in {"sae-log", "jsonl"}:
        return _lines(block.body)
    return [block.body]


def test_every_contract_has_examples() -> None:
    docs = {b.doc for b in BLOCKS}
    expected = {p.name for p in CONTRACTS.glob("C[0-9]_*.md")}
    assert len(expected) == 9, sorted(expected)
    assert docs == expected, f"contracts without testable examples: {sorted(expected - docs)}"


@pytest.mark.parametrize("block", BLOCKS, ids=[b.ident for b in BLOCKS])
def test_example(block: Block) -> None:
    if block.lang in {"json", "jsonl", "toml"}:
        assert block.attrs["model"] in MODELS, block.attrs
    for payload in _payloads(block):
        if block.invalid:
            with pytest.raises((LogLineError, IniError, ValidationError, ValueError, tomllib.TOMLDecodeError)):
                _check_one(block, payload)
        else:
            _check_one(block, payload)
    if "compiled-from" in block.attrs:
        source = BY_ID[block.attrs["compiled-from"]]
        batch = IntentBatch.model_validate(json.loads(source.body))
        assert render_ini(compile_batch(batch)) == block.body


def test_example_ids_are_unique() -> None:
    ids = [b.attrs["id"] for b in BLOCKS if "id" in b.attrs]
    assert len(ids) == len(set(ids))
