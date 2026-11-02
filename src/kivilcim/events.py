"""Group satellite detections into fire events.

An event grows from strong detections and then gathers the weaker ones around them. These
are automatically grouped satellite detections, not confirmed incidents.

Why strong detections first: crop residue burning and fixed heat sources (factories, flares,
landfills) produce many weak detections that last for weeks. If detections were chained by
simple nearness, a real fire next to them would merge into a months-long blob and be lost.
Seeding with strong detections (at least `min_max_frp` MW, which forest fires reach and crop
burning rarely does) keeps the weak ones from acting as bridges.

Cells that detect on a large share of all days are removed first as fixed sources.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from kivilcim.districts import INSIDE_TURKEY_KM, nearest_place

CELL_DEGREES = 0.1

# Provisional thresholds, chosen from a scan of June to November 2026 (see PLAN.md).
MIN_DETECTIONS = 15
MIN_TOTAL_FRP = 500.0  # MW, summed over the event's detections
MIN_MAX_FRP = 100.0  # MW, a detection this strong seeds an event
MAX_DAYS = 14
# A cell that detects on at least this share of all days with data is a fixed source. The rule
# needs enough history to mean anything, so it is skipped for short data sets.
RECURRING_SHARE = 0.25
MIN_HISTORY_DAYS = 60

# Seeds this close in space (cells) and time (days) belong to the same event.
LINK_CELLS = 2
LINK_DAYS = 2
# A weaker detection joins an event if it is this close to one of its seeds.
ATTACH_CELLS = 2
ATTACH_DAYS_BEFORE = 2
ATTACH_DAYS_AFTER = 3


@dataclass(frozen=True)
class Event:
    id: str
    start: date
    end: date
    peak_day: date  # the day with the most detections
    days: int  # calendar days from start to end, inclusive
    detections: int
    total_frp: float
    max_frp: float
    center_lat: float  # FRP-weighted
    center_lon: float
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    district: str
    province: str
    in_turkey: bool


class _Components:
    """Union-find over hashable nodes."""

    def __init__(self) -> None:
        self.parent: dict = {}

    def add(self, node) -> None:
        self.parent.setdefault(node, node)

    def find(self, node):
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != root:
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _cell(value: float) -> int:
    return round(value / CELL_DEGREES)


def _node(d) -> tuple[int, int, int]:
    return _cell(d.latitude), _cell(d.longitude), d.acquired_at.date().toordinal()


def find_events(
    detections: Iterable,
    min_detections: int = MIN_DETECTIONS,
    min_total_frp: float = MIN_TOTAL_FRP,
    max_days: int = MAX_DAYS,
    min_max_frp: float = MIN_MAX_FRP,
    recurring_share: float = RECURRING_SHARE,
    min_history_days: int = MIN_HISTORY_DAYS,
) -> list[Event]:
    """Events, most recent first. Low-confidence detections are ignored."""
    rows = [d for d in detections if d.confidence_level != "low"]

    history_days = {d.acquired_at.date() for d in rows}
    if len(history_days) >= min_history_days:
        days_by_cell = defaultdict(set)
        for d in rows:
            days_by_cell[_node(d)[:2]].add(d.acquired_at.date())
        fixed = {c for c, ds in days_by_cell.items() if len(ds) >= recurring_share * len(history_days)}
        rows = [d for d in rows if _node(d)[:2] not in fixed]

    # 1. Strong detections become seeds and are linked into groups.
    seed_nodes = {_node(d) for d in rows if (d.frp or 0.0) >= min_max_frp}
    groups = _Components()
    for node in seed_nodes:
        groups.add(node)
    for ci, cj, day in seed_nodes:
        for other in seed_nodes:
            if (
                other != (ci, cj, day)
                and abs(other[0] - ci) <= LINK_CELLS
                and abs(other[1] - cj) <= LINK_CELLS
                and abs(other[2] - day) <= LINK_DAYS
            ):
                groups.union((ci, cj, day), other)

    # 2. Every cell-day near a seed is claimed by that seed's group.
    claimed: dict[tuple[int, int, int], tuple] = {}
    for node in sorted(seed_nodes):
        root = groups.find(node)
        ci, cj, day = node
        for di in range(-ATTACH_CELLS, ATTACH_CELLS + 1):
            for dj in range(-ATTACH_CELLS, ATTACH_CELLS + 1):
                for dd in range(-ATTACH_DAYS_BEFORE, ATTACH_DAYS_AFTER + 1):
                    claimed.setdefault((ci + di, cj + dj, day + dd), root)

    members_by_group = defaultdict(list)
    for d in rows:
        root = claimed.get(_node(d))
        if root is not None:
            members_by_group[root].append(d)

    events = []
    for members in members_by_group.values():
        days = sorted({m.acquired_at.date() for m in members})
        span = (days[-1] - days[0]).days + 1
        total = sum(m.frp or 0.0 for m in members)
        if len(members) < min_detections or total < min_total_frp or span > max_days:
            continue

        if total:
            lat = sum(m.latitude * (m.frp or 0.0) for m in members) / total
            lon = sum(m.longitude * (m.frp or 0.0) for m in members) / total
        else:
            lat = sum(m.latitude for m in members) / len(members)
            lon = sum(m.longitude for m in members) / len(members)
        per_day = defaultdict(int)
        for m in members:
            per_day[m.acquired_at.date()] += 1
        peak_day = max(per_day, key=lambda day: (per_day[day], -day.toordinal()))
        strongest = max(members, key=lambda m: m.frp or 0.0)
        district, province, distance = nearest_place(lat, lon)

        events.append(
            Event(
                id=f"{days[0]:%Y%m%d}-{_cell(strongest.latitude)}_{_cell(strongest.longitude)}",
                start=days[0],
                end=days[-1],
                peak_day=peak_day,
                days=span,
                detections=len(members),
                total_frp=round(total, 1),
                max_frp=round(strongest.frp or 0.0, 1),
                center_lat=round(lat, 4),
                center_lon=round(lon, 4),
                min_lat=min(m.latitude for m in members),
                max_lat=max(m.latitude for m in members),
                min_lon=min(m.longitude for m in members),
                max_lon=max(m.longitude for m in members),
                district=district,
                province=province,
                in_turkey=distance <= INSIDE_TURKEY_KM,
            )
        )
    events.sort(key=lambda e: (e.start, e.total_frp), reverse=True)
    return events
