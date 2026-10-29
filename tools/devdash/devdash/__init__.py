"""SAE dev dashboard tooling. Stdlib-only and never imported by ``src/sae``.

Hooks import this package under a bare ``python3``, so nothing here may depend on third-party packages.
``REPO`` is the repository root and ``dev_dir()`` the git-ignored ``.sae-dev/`` state directory.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["DEV_DIR_ENV", "DEV_DIR_NAME", "REPO", "dev_dir"]

REPO: Path = Path(__file__).resolve().parents[3]
"""Repository root: this file sits at ``tools/devdash/devdash/__init__.py``."""

DEV_DIR_ENV = "SAE_DEVDASH_DIR"
"""Environment variable that overrides the state directory (used by the tests)."""

DEV_DIR_NAME = ".sae-dev"


def dev_dir(repo: Path | None = None) -> Path:
    """Return the dev-state directory.

    ``$SAE_DEVDASH_DIR`` wins when it is set and non-empty; otherwise the result is ``<repo>/.sae-dev`` where
    ``repo`` defaults to :data:`REPO`. The directory is not created here.
    """
    override = os.environ.get(DEV_DIR_ENV, "")
    if override:
        return Path(override)
    return (repo if repo is not None else REPO) / DEV_DIR_NAME
