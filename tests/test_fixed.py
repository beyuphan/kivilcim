from datetime import date, timedelta

import pytest

from kivilcim.fixed import MIN_HISTORY_DAYS, RECURRING_SHARE, cell_of, find_fixed_cells

START = date(2026, 6, 1)


def day(n: int) -> date:
    return START + timedelta(days=n)


def daily(lat, lon, n_days, step=1):
    return [(lat, lon, day(i)) for i in range(0, n_days, step)]


def test_cell_of_groups_points_within_a_tenth_of_a_degree():
    assert cell_of(39.91, 32.81) == cell_of(39.93, 32.84)  # both round to cell (399, 328)
    assert cell_of(39.91, 32.84) != cell_of(40.05, 32.84)
    assert cell_of(39.94, 32.84) != cell_of(39.96, 32.84)  # the grid edge is at 39.95


def test_defaults():
    assert RECURRING_SHARE == 0.25
    assert MIN_HISTORY_DAYS == 60


def test_a_cell_that_detects_most_days_is_a_fixed_source():
    points = daily(36.7, 36.2, 100) + daily(40.0, 30.0, 100, step=50)  # 100 days vs 2 days
    assert find_fixed_cells(points) == {cell_of(36.7, 36.2)}


def test_a_cell_just_above_the_share_is_fixed_and_just_below_is_not():
    background = daily(40.0, 30.0, 100)  # makes the history 100 days long and is itself fixed
    exactly = daily(37.0, 33.0, 100, step=4)  # 25 days = exactly 25 percent
    below = daily(38.0, 34.0, 100, step=5)  # 20 days
    fixed = find_fixed_cells(background + exactly + below)
    assert cell_of(37.0, 33.0) in fixed
    assert cell_of(38.0, 34.0) not in fixed


def test_a_short_history_flags_nothing():
    points = daily(36.7, 36.2, 59)
    assert find_fixed_cells(points) == set()
    assert find_fixed_cells(daily(36.7, 36.2, 60)) == {cell_of(36.7, 36.2)}


def test_a_burst_of_several_detections_on_one_day_counts_as_one_day():
    burst = [(37.0, 33.0, day(0))] * 500
    assert find_fixed_cells(burst + daily(40.0, 30.0, 100)) == {cell_of(40.0, 30.0)}


def test_no_points_gives_no_cells():
    assert find_fixed_cells([]) == set()


@pytest.mark.parametrize("share", [0.1, 0.5])
def test_the_share_can_be_changed(share):
    points = daily(40.0, 30.0, 100) + daily(37.0, 33.0, 100, step=3)  # the second one has 34 days
    assert (cell_of(37.0, 33.0) in find_fixed_cells(points, recurring_share=share)) is (share <= 0.34)
