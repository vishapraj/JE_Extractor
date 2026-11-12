"""Statistics for measured accuracy."""

import math
from typing import Tuple

Z95 = 1.959963984540054  # two-sided 95%


def wilson(k: int, n: int, z: float = Z95) -> Tuple[float, float]:
    """95% Wilson score interval for k right out of n. Unlike k/n alone, it shows how little a small
    sample proves: 19/20 right is 95%, but the interval is 76%-99%."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value for two models scored on the same items: b items only the first
    got right, c only the second. Small (below 0.05): the difference is unlikely to be chance."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)
