"""CI-2 placeholder."""

import pytest


def test_ci2_not_applicable() -> None:
    """CI-2 (opportunistic queue) is cut; write it if the queue returns."""
    pytest.skip(reason="opportunistic queue cut (signoff_fable optional cuts, C7 D-13)")
