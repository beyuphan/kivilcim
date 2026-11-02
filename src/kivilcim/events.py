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

from kivilcim.components import Components
from kivilcim.districts import INSIDE_TURKEY_KM, nearest_place
from kivilcim.fixed import MIN_HISTORY_DAYS, RECURRING_SHARE, cell_of, find_fixed_cells
from kivilcim.geo import haversine_km

# Provisional thresholds, chosen from a scan of June to November 2026 (see PLAN.md). The count and
# the summed heat power grow with the number of satellites that see a fire, so they are set per
# satellite and multiplied: three VIIRS satellites (SNPP, NOAA-20, NOAA-21) are ingested.
SATELLITES = 3
MIN_DETECTIONS = 15 * SATELLITES
MIN_TOTAL_FRP = 500.0 * SATELLITES  # MW, summed over the event's detections
MIN_MAX_FRP = 100.0  # MW, one detection this strong seeds an event (does not depend on the count)
MAX_DAYS = 14

# The VIIRS satellites went up one after another: Suomi NPP (data from 2012), NOAA-20 (April 2018) and
# NOAA-21 (January 2023). In earlier years fewer satellites saw a fire, so the count and the heat power
# a real fire reaches are smaller, and the two thresholds above are scaled to the satellites of the day.
NOAA20_FROM = date(2018, 4, 1)
NOAA21_FROM = date(2023, 1, 1)


def satellites_on(day: date) -> int:
    return 1 + (day >= NOAA20_FROM) + (day >= NOAA21_FROM)

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
    extent_km: float  # diagonal of the box around all detections; a very wide event is often crop burning
    district: str
    province: str
    in_turkey: bool


def _node(d) -> tuple[int, int, int]:
    return (*cell_of(d.latitude, d.longitude), d.acquired_at.date().toordinal())


def find_events(
    detections: Iterable,
    min_detections: int = MIN_DETECTIONS,
    min_total_frp: float = MIN_TOTAL_FRP,
    max_days: int = MAX_DAYS,
    min_max_frp: float = MIN_MAX_FRP,
    recurring_share: float = RECURRING_SHARE,
    min_history_days: int = MIN_HISTORY_DAYS,
    scale_by_satellites: bool = False,
) -> list[Event]:
    """Events, most recent first. Low-confidence detections are ignored. With `scale_by_satellites` the
    two thresholds that grow with the number of satellites are scaled to the satellites that existed on
    the event's days (for detections from before all three flew)."""
    rows = [d for d in detections if d.confidence_level != "low"]

    fixed = find_fixed_cells(
        ((d.latitude, d.longitude, d.acquired_at.date()) for d in rows), recurring_share, min_history_days
    )
    rows = [d for d in rows if cell_of(d.latitude, d.longitude) not in fixed]

    # 1. Strong detections become seeds and are linked into groups.
    seed_nodes = {_node(d) for d in rows if (d.frp or 0.0) >= min_max_frp}
    groups = Components()
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
        scale = 1.0
        if scale_by_satellites:
            scale = sum(satellites_on(day) for day in days) / len(days) / SATELLITES
        if len(members) < min_detections * scale or total < min_total_frp * scale or span > max_days:
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
                id="{:%Y%m%d}-{}_{}".format(days[0], *cell_of(strongest.latitude, strongest.longitude)),
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
                extent_km=round(
                    haversine_km(
                        min(m.latitude for m in members),
                        min(m.longitude for m in members),
                        max(m.latitude for m in members),
                        max(m.longitude for m in members),
                    ),
                    1,
                ),
                district=district,
                province=province,
                in_turkey=distance <= INSIDE_TURKEY_KM,
            )
        )
    events.sort(key=lambda e: (e.start, e.total_frp), reverse=True)
    return events
