"""Month-index helpers: ``mi = year*12 + month - 1`` (Stellaris months have 30 days; A-05 UNVERIFIED)."""

from __future__ import annotations


def mi_of(game_date: str) -> int:
    """Month index of a normalised ``YYYY.MM.DD`` game date."""
    year, month, _day = game_date.split(".")
    return int(year) * 12 + int(month) - 1


def date_of(mi: int) -> str:
    """Normalised game date for a month index (day 01). Raises ``ValueError`` if ``mi < 0``."""
    if mi < 0:
        raise ValueError(f"negative month index: {mi}")
    year, month0 = divmod(mi, 12)
    return f"{year:04d}.{month0 + 1:02d}.01"
