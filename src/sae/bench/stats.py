"""Small statistics helpers: seeded percentile bootstrap, percentile, OLS slope."""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence

BOOTSTRAP_RESAMPLES = 10_000


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile ``q`` (0-100) of a non-empty sequence."""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= q <= 100:
        raise ValueError(f"q out of range: {q}")
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def bootstrap_slowdown_ci(
    base: Sequence[float],
    treated: Sequence[float],
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
    q: float = 95.0,
) -> tuple[float, float]:
    """Percentile-bootstrap ``(lo, hi)`` (hi = ``q`` percentile) of ``mean(treated) / mean(base) - 1``.

    Each group is resampled independently with replacement; deterministic per ``seed``.
    """
    if not base or not treated:
        raise ValueError("bootstrap needs non-empty groups")
    rng = random.Random(seed)
    nb, nt = len(base), len(treated)
    out: list[float] = []
    for _ in range(resamples):
        mb = sum(rng.choices(base, k=nb)) / nb
        mt = sum(rng.choices(treated, k=nt)) / nt
        out.append(mt / mb - 1.0)
    return percentile(out, 100.0 - q), percentile(out, q)


def ols_slope(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Ordinary least squares ``(slope, intercept)``; needs >= 2 distinct x values."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("ols needs >= 2 paired points")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("ols needs distinct x values")
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    return slope, my - slope * mx
