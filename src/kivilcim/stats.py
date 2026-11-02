"""Small statistics helpers, in plain Python so no numerical library is needed."""

import math
import random
from collections.abc import Callable, Sequence


def ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks, with tied values sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            result[order[k]] = average
        i = j + 1
    return result


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    if n < 2 or n != len(y):
        raise ValueError("need two series of the same length, at least 2 long")
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Rank correlation: -1 to 1, 0 when the two series are unrelated."""
    return pearson(ranks(x), ranks(y))


def partial_spearman(x: Sequence[float], y: Sequence[float], control: Sequence[float]) -> float:
    """Rank correlation of x and y with the (rank) influence of `control` taken out of both."""
    rx, ry, rz = ranks(x), ranks(y), ranks(control)
    n = len(rz)
    mz = sum(rz) / n
    szz = sum((z - mz) ** 2 for z in rz)

    def residuals(r: list[float]) -> list[float]:
        if szz == 0:
            return r
        mr = sum(r) / n
        slope = sum((z - mz) * (v - mr) for z, v in zip(rz, r, strict=True)) / szz
        return [v - (mr + slope * (z - mz)) for z, v in zip(rz, r, strict=True)]

    return pearson(residuals(rx), residuals(ry))


def auc(positive: Sequence[float], negative: Sequence[float]) -> float:
    """The chance that a random positive scores higher than a random negative (ties count half).

    0.5 means the score does not separate the two groups, 1 means it separates them perfectly.
    """
    if not positive or not negative:
        raise ValueError("need at least one positive and one negative")
    combined = ranks(list(positive) + list(negative))
    n_pos, n_neg = len(positive), len(negative)
    rank_sum = sum(combined[:n_pos])
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def bootstrap_interval(
    statistic: Callable[[list[int]], float],
    n_items: int,
    rounds: int = 1000,
    seed: int = 1,
    level: float = 0.95,
) -> tuple[float, float]:
    """A percentile confidence interval from resampling item indices with replacement.

    `statistic` receives a list of indices (with repeats) and returns the statistic for that resample.
    """
    rng = random.Random(seed)
    values = []
    for _ in range(rounds):
        sample = [rng.randrange(n_items) for _ in range(n_items)]
        try:
            values.append(statistic(sample))
        except ValueError:  # a resample that lacks one of the groups
            continue
    if not values:
        raise ValueError("no usable resamples")
    values.sort()
    low = values[int((1 - level) / 2 * (len(values) - 1))]
    high = values[int((1 + level) / 2 * (len(values) - 1))]
    return low, high
