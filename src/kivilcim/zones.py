"""Active fire zones: current detections grouped by place.

A zone is a set of recent detections in neighboring ~0.1 degree cells. It is what the weather
index is computed for: the index describes the weather at an active fire, so it needs a place
that has one. These are grouped satellite detections, not confirmed incidents.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from kivilcim.components import Components
from kivilcim.districts import INSIDE_TURKEY_KM, nearest_place
from kivilcim.fixed import Cell, cell_of
from kivilcim.geo import haversine_km

MAX_ZONES = 60  # keeps the weather lookups for one request bounded
MIN_RADIUS_KM = 2.0
MAX_RADIUS_KM = 40.0


@dataclass(frozen=True)
class Zone:
    id: str
    center_lat: float  # weighted by heat power
    center_lon: float
    detections: int
    total_frp: float
    max_frp: float
    latest_observation: datetime
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    radius_km: float  # for drawing: half the diagonal of the box around the detections
    district: str
    province: str


def find_zones(
    detections: Iterable,
    fixed_cells: set[Cell] | frozenset[Cell] = frozenset(),
    max_zones: int = MAX_ZONES,
) -> list[Zone]:
    """Zones of recent detections, strongest first, inside Turkey.

    Low-confidence detections and those in `fixed_cells` (likely factories and flares) are left
    out: the weather index would say nothing useful about them. At most `max_zones` are returned.
    """
    rows = [
        d
        for d in detections
        if d.confidence_level != "low" and cell_of(d.latitude, d.longitude) not in fixed_cells
    ]

    groups = Components()
    cells = {cell_of(d.latitude, d.longitude) for d in rows}
    for cell in cells:
        groups.add(cell)
    for ci, cj in cells:
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if (ci + di, cj + dj) in cells and (di or dj):
                    groups.union((ci, cj), (ci + di, cj + dj))

    members_by_group = defaultdict(list)
    for d in rows:
        members_by_group[groups.find(cell_of(d.latitude, d.longitude))].append(d)

    zones = []
    for members in members_by_group.values():
        total = sum(m.frp or 0.0 for m in members)
        if total:
            lat = sum(m.latitude * (m.frp or 0.0) for m in members) / total
            lon = sum(m.longitude * (m.frp or 0.0) for m in members) / total
        else:
            lat = sum(m.latitude for m in members) / len(members)
            lon = sum(m.longitude for m in members) / len(members)

        district, province, distance = nearest_place(lat, lon)
        if distance > INSIDE_TURKEY_KM:
            continue

        strongest = max(members, key=lambda m: m.frp or 0.0)
        min_lat, max_lat = min(m.latitude for m in members), max(m.latitude for m in members)
        min_lon, max_lon = min(m.longitude for m in members), max(m.longitude for m in members)
        diagonal = haversine_km(min_lat, min_lon, max_lat, max_lon)
        ci, cj = cell_of(strongest.latitude, strongest.longitude)
        zones.append(
            Zone(
                id=f"{ci}_{cj}",
                center_lat=round(lat, 4),
                center_lon=round(lon, 4),
                detections=len(members),
                total_frp=round(total, 1),
                max_frp=round(strongest.frp or 0.0, 1),
                latest_observation=max(m.acquired_at for m in members),
                min_lat=min_lat,
                max_lat=max_lat,
                min_lon=min_lon,
                max_lon=max_lon,
                radius_km=round(min(MAX_RADIUS_KM, max(MIN_RADIUS_KM, diagonal / 2)), 1),
                district=district,
                province=province,
            )
        )
    zones.sort(key=lambda z: z.total_frp, reverse=True)
    return zones[:max_zones]
