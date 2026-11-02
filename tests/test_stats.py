import math

import pytest

from kivilcim.stats import auc, bootstrap_interval, partial_spearman, pearson, ranks, spearman


def test_ranks_are_one_based_and_ties_share_the_average():
    assert ranks([10, 30, 20]) == [1, 3, 2]
    assert ranks([5, 5, 1, 9]) == [2.5, 2.5, 1, 4]
    assert ranks([7, 7, 7]) == [2, 2, 2]
    assert ranks([]) == []


def test_pearson_known_values():
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1)
    assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1)
    assert pearson([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(0.8)
    assert pearson([1, 1, 1], [1, 2, 3]) == 0  # no spread in one series


def test_pearson_needs_matching_series():
    with pytest.raises(ValueError):
        pearson([1], [1])
    with pytest.raises(ValueError):
        pearson([1, 2], [1, 2, 3])


def test_spearman_ignores_the_shape_of_a_monotonic_relation():
    x = [1, 2, 3, 4, 5, 6]
    assert spearman(x, [v**3 for v in x]) == pytest.approx(1)
    assert spearman(x, [-math.exp(v) for v in x]) == pytest.approx(-1)


def test_spearman_of_unrelated_series_is_near_zero():
    x = list(range(20))
    y = [7, 2, 19, 11, 0, 15, 4, 17, 9, 13, 1, 18, 6, 12, 3, 16, 8, 14, 5, 10]
    assert abs(spearman(x, y)) < 0.2


def test_spearman_with_ties():
    assert spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1)


def test_partial_spearman_removes_a_shared_cause():
    # Both x and y simply follow z, so once z is taken out nothing is left.
    z = list(range(30))
    x = [v + (i % 3) * 0.1 for i, v in enumerate(z)]
    y = [2 * v + (i % 5) * 0.1 for i, v in enumerate(z)]
    assert spearman(x, y) > 0.95
    assert abs(partial_spearman(x, y, z)) < 0.35


def test_partial_spearman_keeps_a_real_relation():
    z = [(i * 7) % 30 for i in range(30)]
    x = list(range(30))
    y = [v + 0.01 * zz for v, zz in zip(x, z, strict=True)]  # y follows x, and z is unrelated to both
    assert partial_spearman(x, y, z) > 0.95


def test_auc_perfect_none_and_reversed():
    assert auc([5, 6, 7], [1, 2, 3]) == 1
    assert auc([1, 2, 3], [5, 6, 7]) == 0
    assert auc([1, 2, 3], [1, 2, 3]) == pytest.approx(0.5)


def test_auc_counts_ties_as_half():
    assert auc([2, 2], [2, 2]) == 0.5
    assert auc([3, 1], [2]) == pytest.approx(0.5)


def test_auc_needs_both_groups():
    with pytest.raises(ValueError):
        auc([], [1])


def test_bootstrap_interval_is_reproducible_and_brackets_the_estimate():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    def mean(indices):
        return sum(values[i] for i in indices) / len(indices)

    first = bootstrap_interval(mean, len(values), rounds=500, seed=3)
    again = bootstrap_interval(mean, len(values), rounds=500, seed=3)
    assert first == again
    assert first[0] < 5.5 < first[1]
    other = bootstrap_interval(mean, len(values), rounds=500, seed=4)
    assert other != first


def test_a_wider_sample_gives_a_narrower_interval():
    def width(n):
        values = [float(i % 10) for i in range(n)]
        low, high = bootstrap_interval(lambda idx: sum(values[i] for i in idx) / len(idx), n, rounds=400)
        return high - low

    assert width(400) < width(25)


def test_bootstrap_skips_unusable_resamples():
    calls = []

    def sometimes_fails(indices):
        calls.append(1)
        if len(calls) % 2:
            raise ValueError("a group is missing")
        return 1.0

    assert bootstrap_interval(sometimes_fails, 5, rounds=10) == (1.0, 1.0)
    with pytest.raises(ValueError):
        bootstrap_interval(lambda idx: (_ for _ in ()).throw(ValueError("never")), 5, rounds=5)
