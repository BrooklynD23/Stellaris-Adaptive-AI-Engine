"""SAE interface contracts C1-C9 as typed code (owner: architect). See ``contracts/README.md``.

These models ARE the contract: JSON Schemas in ``contracts/schemas/`` are exported from them with
``uv run python -m sae.contracts.export`` and every example in ``contracts/C*.md`` is validated
against them by ``tests/contract``.
"""

#: Contract id -> frozen version. Bumped only through the change protocol (contracts/README.md).
CONTRACT_VERSIONS: dict[str, int] = {f"C{i}": 1 for i in range(1, 10)}
