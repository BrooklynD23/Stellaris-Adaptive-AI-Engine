"""Campaign-folder keys and the install salt (C6 section 2 v1.3, proposal 020; privacy: A-46).

The raw save-folder name never leaves this module: callers get ``f-<16 hex>`` (HMAC-SHA256 under the
per-install salt) or, for replay and synth runs, ``f-synth-<8 hex>`` (sha256 of the run id).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

SALT_BYTES = 32
SALT_NAME = "salt"
_RETRIES = 3
_RETRY_S = 0.01


class SaltError(Exception):
    """The install salt could not be created or read, or is not exactly 32 bytes."""


def folder_key(salt: bytes, name: str) -> str:
    """``"f-" + hmac(salt, name UTF-8, sha256).hexdigest()[:16]`` (C6 section 2)."""
    return "f-" + hmac.new(salt, name.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def synth_key(run_id: str) -> str:
    """``"f-synth-" + sha256(run_id UTF-8).hexdigest()[:8]``: unique per run, so replay/synth runs never merge."""
    return "f-synth-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8]


def load_salt(sae_dir: Path) -> bytes:
    """Read ``<sae_dir>/salt``, creating it first with ``O_CREAT|O_EXCL`` if absent (C6 section 2).

    An existing salt is only read (never written). The loser of a creation race re-reads the winner's file
    with a short retry, since the winner may still be mid-write. Raises :class:`SaltError` when the file is
    not exactly 32 bytes (never rewritten) or on I/O failure.

    A-46 UNVERIFIED: M0-11 checks that the save-folder name used as the key input is stable on rename.
    """
    path = sae_dir / SALT_NAME
    try:
        sae_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass  # lost the race; fall through to the retry re-read
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(secrets.token_bytes(SALT_BYTES))
                    f.flush()
                    os.fsync(f.fileno())
        data = path.read_bytes()
        for _ in range(_RETRIES):
            if len(data) == SALT_BYTES:
                break
            time.sleep(_RETRY_S)  # a racing writer may still be mid-write
            data = path.read_bytes()
    except OSError as exc:
        raise SaltError(f"salt {path.name}: {type(exc).__name__}") from exc
    if len(data) != SALT_BYTES:
        raise SaltError(f"salt must be {SALT_BYTES} bytes, found {len(data)}")
    return data
