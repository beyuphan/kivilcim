"""Checks the weather index against past satellite detections.

Run it as `python -m kivilcim.validate`. It needs the detection history in the database and calls
the Open-Meteo history API (free for non-commercial use), so it is a one-off tool, not part of
the server. The results go to `static/validation.json` and appear on the methodology page.

The analysis is fixed before the results are seen, and the index is not tuned on this data.

Two questions are asked, both over a period inside the dry season (by default June to October)
so that the plain difference between summer and winter cannot do the work:

A. At an active fire, does a higher index go with the fire continuing or growing the next day?
B. Does the index tell places with fire from places without, on the same day?

Both are observational. They show whether the index is related to what satellites see, not
whether it predicts how a fire will spread.
"""

import argparse
import json
import logging
import math
import random
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from kivilcim import __version__
from kivilcim.config import get_settings
from kivilcim.db import make_engine, make_session_factory
from kivilcim.districts import district_centers
from kivilcim.fixed import cell_of, find_fixed_cells
from kivilcim.geo import haversine_km
from kivilcim.models import FireDetection
from kivilcim.risk import INDEX_VERSION, compute_risk
from kivilcim.stats import auc, bootstrap_interval, partial_spearman, spearman
from kivilcim.zones import find_zones

log = logging.getLogger("kivilcim.validate")

MIN_ZONE_DETECTIONS = 5  # smaller zones are mostly single pixels and say little about a fire
REGION_PAD_DEGREES = 0.15  # the area around a zone in which "the same fire" is looked for the next day
MIN_GAP_DAYS = 4  # one sample per place per this many days, since the same fire on consecutive days is not independent
GAP_CELL_DEGREES = 0.3
CONTROL_EXCLUDE_KM = 25  # a control place has no detection within this distance that day
READING_HOUR_UTC = 12  # 15:00 local time, when conditions are at their driest and hottest
ROUNDS = 1000
MIN_PER_MONTH = 20  # a month needs this many samples to enter the within-month average

OUT_PATH = Path(__file__).parent / "static" / "validation.json"


# ---- samples ----------------------------------------------------------------


@dataclass(frozen=True)
class FireSample:
    day: date
    lat: float
    lon: float
    detections: int
    today_frp: float
    next_frp: float
    next_count: int

    @property
    def log_change(self) -> float:
        """How the fire's heat power changed by the next day: 0 unchanged, below 0 weaker, above 0 stronger."""
        return math.log((self.next_frp + 1) / (self.today_frp + 1))


@dataclass(frozen=True)
class ControlSample:
    day: date
    lat: float
    lon: float


def _in_region(d, box: tuple[float, float, float, float]) -> bool:
    min_lat, max_lat, min_lon, max_lon = box
    return min_lat <= d.latitude <= max_lat and min_lon <= d.longitude <= max_lon


def fire_samples(
    rows: Sequence,
    fixed_cells: set,
    start: date,
    end: date,
    min_detections: int = MIN_ZONE_DETECTIONS,
    pad: float = REGION_PAD_DEGREES,
) -> list[FireSample]:
    """Active fire zones on each day from `start` to `end`, each with what happened there the next day.

    Low-confidence detections and those in always-on cells are left out, as in the live zones. `end`
    must leave a full day of data after it, since the next day is needed.
    """
    by_day: dict[date, list] = defaultdict(list)
    for d in rows:
        if d.confidence_level != "low" and cell_of(d.latitude, d.longitude) not in fixed_cells:
            by_day[d.acquired_at.date()].append(d)

    samples = []
    day = start
    while day <= end:
        today = by_day.get(day, [])
        tomorrow = by_day.get(day + timedelta(days=1), [])
        for zone in find_zones(today, frozenset(), max_zones=100_000):
            if zone.detections < min_detections:
                continue
            box = (zone.min_lat - pad, zone.max_lat + pad, zone.min_lon - pad, zone.max_lon + pad)
            today_here = [d for d in today if _in_region(d, box)]
            next_here = [d for d in tomorrow if _in_region(d, box)]
            samples.append(
                FireSample(
                    day=day,
                    lat=zone.center_lat,
                    lon=zone.center_lon,
                    detections=zone.detections,
                    today_frp=sum(d.frp or 0.0 for d in today_here),
                    next_frp=sum(d.frp or 0.0 for d in next_here),
                    next_count=len(next_here),
                )
            )
        day += timedelta(days=1)
    return samples


def thin(samples: Sequence[FireSample], min_gap_days: int = MIN_GAP_DAYS) -> list[FireSample]:
    """Keep one sample per place per `min_gap_days`, the strongest, so one long fire is not counted
    as many independent cases."""
    kept: list[FireSample] = []
    last_kept: dict[tuple[int, int], date] = {}
    for sample in sorted(samples, key=lambda s: (s.day, -s.today_frp)):
        place = (round(sample.lat / GAP_CELL_DEGREES), round(sample.lon / GAP_CELL_DEGREES))
        previous = last_kept.get(place)
        if previous is None or (sample.day - previous).days >= min_gap_days:
            kept.append(sample)
            last_kept[place] = sample.day
    return kept


def stratified_by_month(samples: Sequence[FireSample], limit: int, seed: int) -> list[FireSample]:
    """At most `limit` samples, spread evenly over the months present so no month dominates."""
    if len(samples) <= limit:
        return list(samples)
    months: dict[tuple[int, int], list[FireSample]] = defaultdict(list)
    for s in samples:
        months[(s.day.year, s.day.month)].append(s)
    rng = random.Random(seed)
    per_month = max(1, limit // len(months))
    chosen: list[FireSample] = []
    leftovers: list[FireSample] = []
    for _, group in sorted(months.items()):
        rng.shuffle(group)
        chosen += group[:per_month]
        leftovers += group[per_month:]
    rng.shuffle(leftovers)
    chosen += leftovers[: max(0, limit - len(chosen))]
    return sorted(chosen, key=lambda s: s.day)


def control_samples(
    rows: Sequence,
    days: Sequence[date],
    places: Sequence[tuple[float, float]],
    seed: int,
    exclude_km: float = CONTROL_EXCLUDE_KM,
    tries: int = 60,
) -> list[ControlSample]:
    """For each day in `days`, one random place that had no detection of any kind within `exclude_km`."""
    by_day: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for d in rows:
        by_day[d.acquired_at.date()].append((d.latitude, d.longitude))
    rng = random.Random(seed)
    controls = []
    for day in days:
        seen = by_day.get(day, [])
        for _ in range(tries):
            lat, lon = rng.choice(places)
            if all(haversine_km(lat, lon, a, b) > exclude_km for a, b in seen):
                controls.append(ControlSample(day, lat, lon))
                break
    return controls


# ---- weather history --------------------------------------------------------


class HistoryUnavailable(Exception):
    """The history service could not be reached after several attempts."""


@dataclass(frozen=True)
class Reading:
    temperature: float
    humidity: float
    wind: float
    rain72: float

    def score(self) -> float:
        return compute_risk(self.temperature, self.humidity, self.wind, self.rain72).score


def _parse_reading(item: dict, day: date, hour: int) -> Reading | None:
    """The conditions at `hour` UTC on `day` and the rain over the 72 hours before it."""
    try:
        hourly = item["hourly"]
        times = hourly["time"]
        at = times.index(f"{day.isoformat()}T{hour:02d}:00")
        temperature = hourly["temperature_2m"][at]
        humidity = hourly["relative_humidity_2m"][at]
        wind = hourly["wind_speed_10m"][at]
        rain = [v for v in hourly["precipitation"][max(0, at - 71) : at + 1] if v is not None]
    except (KeyError, ValueError, IndexError, TypeError):
        return None
    if None in (temperature, humidity, wind) or not rain:
        return None
    return Reading(float(temperature), float(humidity), float(wind), round(sum(rain), 2))


class OpenMeteoHistory:
    """Past weather from Open-Meteo, in batches per day, with retries and a disk cache.

    The cache means an interrupted run resumes where it stopped and a repeat run asks for nothing.
    """

    URLS = {
        "archive": "https://archive-api.open-meteo.com/v1/archive",
        "forecast": "https://api.open-meteo.com/v1/forecast",  # recent days only (about three months)
    }

    def __init__(
        self,
        client: httpx.Client,
        provider: str = "archive",
        cache_path: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        batch_size: int = 50,
        pause_seconds: float = 8.0,
        max_attempts: int = 6,
        hour: int = READING_HOUR_UTC,
    ) -> None:
        if provider not in self.URLS:
            raise ValueError(f"provider must be one of {sorted(self.URLS)}")
        self._client, self._provider, self._sleep = client, provider, sleep
        self._batch, self._pause, self._attempts, self._hour = batch_size, pause_seconds, max_attempts, hour
        self._cache_path = cache_path
        self._cache: dict[str, list[float] | None] = {}
        if cache_path and cache_path.exists():
            self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.requests_made = 0

    def _key(self, day: date, lat: float, lon: float) -> str:
        return f"{self._provider}|{day.isoformat()}|{lat:.1f}|{lon:.1f}"

    def _save(self) -> None:
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache), encoding="utf-8")

    def _request(self, day: date, points: list[tuple[float, float]]) -> list[dict]:
        params = {
            "latitude": ",".join(f"{lat:.1f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.1f}" for _, lon in points),
            "start_date": (day - timedelta(days=3)).isoformat(),
            "end_date": day.isoformat(),
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
            "wind_speed_unit": "kmh",
            "timezone": "UTC",
        }
        last_problem = "unknown"
        for attempt in range(self._attempts):
            if attempt:
                self._sleep(min(120.0, 5.0 * 2 ** (attempt - 1)))
            try:
                response = self._client.get(self.URLS[self._provider], params=params)
            except httpx.HTTPError as exc:
                last_problem = type(exc).__name__
                continue
            self.requests_made += 1
            if response.status_code == 200:
                payload = response.json()
                items = payload if isinstance(payload, list) else [payload]
                if len(items) != len(points):
                    raise HistoryUnavailable(f"asked for {len(points)} places, got {len(items)}")
                return items
            last_problem = f"HTTP {response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                raise HistoryUnavailable(f"the history service answered {last_problem}")
        raise HistoryUnavailable(f"the history service did not answer after {self._attempts} attempts ({last_problem})")

    def readings(
        self, wanted: dict[date, list[tuple[float, float]]], progress: Callable[[str], None] | None = None
    ) -> dict[tuple[date, float, float], Reading | None]:
        """Readings for every (day, place) asked for. Places are rounded to 0.1 degree."""
        result: dict[tuple[date, float, float], Reading | None] = {}
        for day in sorted(wanted):
            unique = list(dict.fromkeys((round(lat, 1), round(lon, 1)) for lat, lon in wanted[day]))
            missing = []
            for lat, lon in unique:
                cached = self._cache.get(self._key(day, lat, lon), "absent")
                if cached == "absent":
                    missing.append((lat, lon))
                else:
                    result[(day, lat, lon)] = Reading(*cached) if cached else None
            for start in range(0, len(missing), self._batch):
                chunk = missing[start : start + self._batch]
                items = self._request(day, chunk)
                for (lat, lon), item in zip(chunk, items, strict=True):
                    reading = _parse_reading(item, day, self._hour)
                    self._cache[self._key(day, lat, lon)] = (
                        [reading.temperature, reading.humidity, reading.wind, reading.rain72] if reading else None
                    )
                    result[(day, lat, lon)] = reading
                self._save()
                self._sleep(self._pause)  # stay well under the provider's per-minute limit
            if progress:
                progress(day.isoformat())
        return result


# ---- analysis ---------------------------------------------------------------


def _terciles(pairs: list[tuple[float, FireSample]]) -> list[list[tuple[float, FireSample]]]:
    ordered = sorted(pairs, key=lambda p: p[0])
    n = len(ordered)
    return [ordered[: n // 3], ordered[n // 3 : 2 * n // 3], ordered[2 * n // 3 :]]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def describe_correlation(rho: float, low: float, high: float) -> str:
    if low <= 0 <= high:
        return "anlamlı bir ilişki bulunamadı"
    size = abs(rho)
    word = "çok zayıf" if size < 0.1 else "zayıf" if size < 0.3 else "orta" if size < 0.5 else "güçlü"
    return f"{word} {'pozitif' if rho > 0 else 'negatif'} ilişki"


def describe_auc(value: float, low: float, high: float) -> str:
    if low <= 0.5 <= high:
        return "yangın olan ve olmayan yerleri ayırdığına dair kanıt yok"
    if value < 0.5:
        return "ters yönde ayrım (yangın olan yerlerde indeks daha düşük)"
    word = "çok zayıf" if value < 0.6 else "zayıf" if value < 0.7 else "orta" if value < 0.8 else "iyi"
    return f"{word} ayrım gücü"


def _tr(value: float, digits: int = 2, sign: bool = False) -> str:
    text = f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"
    return text.replace(".", ",")


def analyze(
    fires: Sequence[FireSample],
    controls: Sequence[ControlSample],
    readings: dict[tuple[date, float, float], Reading | None],
    seed: int = 1,
    rounds: int = ROUNDS,
) -> dict:
    """The two tests, with confidence intervals, as a dictionary ready to be written as JSON."""

    def lookup(day: date, lat: float, lon: float) -> Reading | None:
        return readings.get((day, round(lat, 1), round(lon, 1)))

    scored = [(r.score(), s) for s in fires if (r := lookup(s.day, s.lat, s.lon))]
    control_scores = [r.score() for c in controls if (r := lookup(c.day, c.lat, c.lon))]
    tests: list[dict] = []
    details: dict = {"fire_cases": len(scored), "control_cases": len(control_scores)}

    if len(scored) >= 30:
        x = [p[0] for p in scored]
        change = [p[1].log_change for p in scored]
        today = [p[1].today_frp for p in scored]
        n = len(scored)

        rho = spearman(x, change)
        low, high = bootstrap_interval(
            lambda idx: spearman([x[i] for i in idx], [change[i] for i in idx]), n, rounds, seed
        )
        details["rho"] = {"value": rho, "low": low, "high": high}
        tests.append(
            {
                "name": "A. Aktif yangın bölgesinde indeks ile ertesi günkü yangın gücü değişimi",
                "result": f"Spearman ρ = {_tr(rho, sign=True)} (%95 aralık {_tr(low, sign=True)} ile {_tr(high, sign=True)})",
                "n": n,
                "comment": describe_correlation(rho, low, high) + ". Değişim: ertesi günün toplam ısı gücünün bugününe oranının logaritması.",
            }
        )

        partial = partial_spearman(x, change, today)
        plow, phigh = bootstrap_interval(
            lambda idx: partial_spearman([x[i] for i in idx], [change[i] for i in idx], [today[i] for i in idx]),
            n, rounds, seed,
        )
        details["partial_rho"] = {"value": partial, "low": plow, "high": phigh}
        tests.append(
            {
                "name": "A2. Aynısı, bölgenin o günkü yangın gücünün etkisi çıkarıldıktan sonra",
                "result": f"kısmi ρ = {_tr(partial, sign=True)} (%95 aralık {_tr(plow, sign=True)} ile {_tr(phigh, sign=True)})",
                "n": n,
                "comment": describe_correlation(partial, plow, phigh)
                + ". Güçlü yangınlar ertesi gün sıradan olarak zayıflar; bu etkiyi ayırmadan bakmak yanıltabilir.",
            }
        )

        months: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (_, sample) in enumerate(scored):
            months[(sample.day.year, sample.day.month)].append(i)
        weighted, total = 0.0, 0
        for idx in months.values():
            if len(idx) >= MIN_PER_MONTH:
                weighted += spearman([x[i] for i in idx], [change[i] for i in idx]) * len(idx)
                total += len(idx)
        if total:
            within = weighted / total
            details["within_month_rho"] = {"value": within, "n": total}
            tests.append(
                {
                    "name": "A3. Aynı ay içinde karşılaştırma (mevsim etkisini ayırmak için)",
                    "result": f"ortalama ρ = {_tr(within, sign=True)}",
                    "n": total,
                    "comment": f"En az {MIN_PER_MONTH} örneği olan aylarda ayrı hesaplanıp örnek sayısıyla ağırlıklandırıldı.",
                }
            )

        groups = _terciles(scored)
        names = ["en düşük üçte bir", "orta üçte bir", "en yüksek üçte bir"]
        bands = []
        for name, group in zip(names, groups, strict=True):
            if not group:
                continue
            persisted = sum(1 for _, s in group if s.next_count > 0) / len(group)
            band = {
                "name": name,
                "index_range": [group[0][0], group[-1][0]],
                "n": len(group),
                "persisted": persisted,
                "median_change": _median([s.log_change for _, s in group]),
            }
            bands.append(band)
            tests.append(
                {
                    "name": f"A4. İndeksin {name}",
                    "result": f"indeks {_tr(band['index_range'][0], 0)}-{_tr(band['index_range'][1], 0)}: ertesi gün sürme %{_tr(persisted * 100, 0)}, "
                    f"medyan değişim {_tr(band['median_change'], sign=True)}",
                    "n": len(group),
                    "comment": "Sürme: bölgede ertesi gün en az bir tespit var. Değişim: ısı gücü oranının logaritması (0: aynı, eksi: zayıfladı).",
                }
            )
        details["bands"] = bands
    else:
        tests.append(
            {"name": "A. Aktif yangın bölgesinde indeks ile ertesi günkü değişim", "result": "ölçülemedi", "n": len(scored),
             "comment": "Hava verisi olan örnek sayısı çok az (en az 30 gerekir)."}
        )

    if len(scored) >= 30 and len(control_scores) >= 30:
        fire_scores = [p[0] for p in scored]
        value = auc(fire_scores, control_scores)
        low, high = bootstrap_interval(
            lambda idx: auc([fire_scores[i] for i in idx if i < len(fire_scores)],
                            [control_scores[i - len(fire_scores)] for i in idx if i >= len(fire_scores)]),
            len(fire_scores) + len(control_scores), rounds, seed,
        )
        details["auc"] = {"value": value, "low": low, "high": high,
                          "fire_mean": sum(fire_scores) / len(fire_scores),
                          "control_mean": sum(control_scores) / len(control_scores)}
        tests.append(
            {
                "name": "B. Yangın olan yerler ile aynı gün yangın olmayan yerlerin ayrımı",
                "result": f"AUC = {_tr(value)} (%95 aralık {_tr(low)} ile {_tr(high)}); ortalama indeks {_tr(details['auc']['fire_mean'], 0)} "
                f"(yangın) ve {_tr(details['auc']['control_mean'], 0)} (kontrol)",
                "n": len(fire_scores) + len(control_scores),
                "comment": describe_auc(value, low, high) + ". 0,5 hiç ayırmama, 1 mükemmel ayırma demektir. Kontrol: aynı gün 25 km içinde hiç tespit olmayan rastgele ilçe merkezleri.",
            }
        )

    return {"tests": tests, "details": details}


def usage_note(details: dict) -> str:
    """How the index may be presented, given what the measurement found. It never claims more than
    the numbers support: a relation with next-day behavior has to hold within a month too, or it
    may only be the season."""
    rho, within = details.get("rho"), details.get("within_month_rho")
    if rho is None:
        return "Ölçüm yapılamadığı için indeks yalnızca deneysel bir gösterge olarak sunulur."
    if rho["low"] > 0 and within is not None and within["value"] > 0.1:
        return (
            "Bu ölçümde indeks ile yangının ertesi gün sürmesi arasında bir ilişki görüldü. Yine de bu bir tahmin "
            "değildir ve tahmin olarak sunulmaz."
        )
    return (
        "Bu ölçümde indeksin yangının ertesi gün sürmesiyle ilişkili olduğuna dair kanıt bulunamadı. Bu yüzden indeks "
        "bir yayılma ya da süreklilik tahmini olarak sunulmaz; yalnızca havanın yangın için ne kadar elverişli olduğunu "
        "anlatan deneysel bir gösterge olarak sunulur."
    )


def build_report(analysis: dict, period: tuple[date, date], provider: str, generated: datetime | None = None) -> dict:
    """The file the methodology page reads."""
    details = analysis["details"]
    parts = []
    rho = details.get("rho")
    if rho:
        parts.append(
            f"Aktif yangın bölgelerinde indeks ile ertesi günkü yangın gücü değişimi arasında {describe_correlation(rho['value'], rho['low'], rho['high'])} bulundu."
        )
    auc_result = details.get("auc")
    if auc_result:
        parts.append(
            f"İndeks, aynı gün yangın olan ve olmayan yerleri ayırmada: {describe_auc(auc_result['value'], auc_result['low'], auc_result['high'])}."
        )
    if not parts:
        parts.append("Yeterli örnek ya da hava verisi olmadığı için ölçüm yapılamadı.")
    when = generated or datetime.now(timezone.utc)
    return {
        "summary": " ".join(parts),
        "usage_note": usage_note(details),
        "tests": analysis["tests"],
        "caveats": [
            f"Dönem: {period[0].isoformat()} ile {period[1].isoformat()}. Yalnızca VIIRS tespitleri ve tek bir yılın tek bir mevsimi kullanıldı.",
            "Gözlemsel ölçümdür: indeksin bir yangının nasıl yayılacağını önceden bildirdiğini değil, uydunun gördükleriyle ilişkisini gösterir.",
            "Ertesi günkü ısı gücü gürültülüdür: uydu geçişleri, bulut ve duman sonucu değiştirir. Aynı yangın art arda günlerde bağımsız örnek sayılmasın diye aynı yerden 4 günde en fazla bir örnek alındı; yine de güven aralıkları iyimser kalabilir.",
            f"Geçmiş hava, Open-Meteo'nun {'yeniden analiz arşivinden' if provider == 'archive' else 'yakın dönem tahmin modelinden'} alındı; canlı sitedeki değerlerle çözünürlük farkı olabilir. Her gün için 15:00 (İstanbul) koşulları kullanıldı.",
            "İndeksin ağırlıkları bu veriye göre ayarlanmadı; ölçüm, sade ve akıl yürütmeyle seçilmiş formülün olduğu gibi sınanmasıdır.",
            "Bu ölçüm bildirilen gerçek yangın kayıtlarıyla değil, uydu verisiyle yapıldı.",
        ],
        "generated": when.strftime("%Y-%m-%d"),
        "index_version": INDEX_VERSION,
        "software_version": __version__,
        "provider": provider,
        "details": details,
    }


# ---- command line -----------------------------------------------------------


def run(
    rows: Sequence,
    history: OpenMeteoHistory,
    start: date,
    end: date,
    max_fire: int = 400,
    seed: int = 1,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Everything from detections to the report dictionary."""
    viirs = [d for d in rows if d.instrument == "VIIRS" and d.confidence_level != "low"]
    fixed = find_fixed_cells((d.latitude, d.longitude, d.acquired_at.date()) for d in viirs)
    candidates = thin(fire_samples(viirs, fixed, start, end))
    fires = stratified_by_month(candidates, max_fire, seed)
    controls = control_samples(rows, [s.day for s in fires], district_centers(), seed)

    wanted: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for s in fires:
        wanted[s.day].append((s.lat, s.lon))
    for c in controls:
        wanted[c.day].append((c.lat, c.lon))
    readings = history.readings(wanted, progress)
    return build_report(analyze(fires, controls, readings, seed), (start, end), history._provider)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kivilcim.validate", description=__doc__.strip().splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 27),
                        help="last day; the archive lags by about five days, so it must be older than that")
    parser.add_argument("--provider", choices=["archive", "forecast"], default="archive")
    parser.add_argument("--max-fire", type=int, default=400, help="how many fire cases to use at most")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--pause", type=float, default=3.0, help="seconds to wait between requests")
    parser.add_argument("--cache", type=Path, default=Path("data") / "validation-cache.json")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        rows = session.scalars(select(FireDetection)).all()
    log.info("%d detections loaded", len(rows))

    with httpx.Client(timeout=60) as client:
        history = OpenMeteoHistory(client, args.provider, args.cache, pause_seconds=args.pause)
        try:
            report = run(rows, history, args.start, args.end, args.max_fire, args.seed, lambda d: log.info("weather for %s", d))
        except HistoryUnavailable as exc:
            print(f"Stopped: {exc}. What was fetched is saved in {args.cache}; run again later to continue.", file=sys.stderr)
            return 1
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['summary']}\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
