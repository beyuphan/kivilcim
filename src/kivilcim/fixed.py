"""Cells where satellites see a heat source on a large share of all days.

Factories, refineries, gas flares and landfills burn all the time, so a detection there says
little about a fire. This finds such cells from the detection history. They are flagged, never
silently dropped: a wildfire can start next to a factory, so the data stays visible.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

CELL_DEGREES = 0.1

# A cell is a fixed source when it detects on at least this share of all days with data. The rule
# needs enough history to mean anything, so it does nothing for short data sets.
RECURRING_SHARE = 0.25
MIN_HISTORY_DAYS = 60

Cell = tuple[int, int]


def cell_of(lat: float, lon: float) -> Cell:
    """The ~0.1 degree (about 11 km) cell a point falls in."""
    return round(lat / CELL_DEGREES), round(lon / CELL_DEGREES)


def find_fixed_cells(
    points: Iterable[tuple[float, float, date]],
    recurring_share: float = RECURRING_SHARE,
    min_history_days: int = MIN_HISTORY_DAYS,
) -> set[Cell]:
    """Cells that detect on at least `recurring_share` of all days that have any detection.

    `points` are (latitude, longitude, day) tuples. Returns an empty set when the history is
    shorter than `min_history_days`.
    """
    days_by_cell: dict[Cell, set[date]] = defaultdict(set)
    all_days: set[date] = set()
    for lat, lon, day in points:
        days_by_cell[cell_of(lat, lon)].add(day)
        all_days.add(day)
    if len(all_days) < min_history_days:
        return set()
    needed = recurring_share * len(all_days)
    return {cell for cell, days in days_by_cell.items() if len(days) >= needed}
