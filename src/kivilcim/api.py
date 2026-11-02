"""REST API: /health, /status and /fires.

Start with: uvicorn kivilcim.api:create_app --factory
"""

import json
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from kivilcim import __version__
from kivilcim.alerts import AlertService, BotPoller, Telegram
from kivilcim.cards import render_card
from kivilcim.config import Settings, get_settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.events import Event, find_events
from kivilcim.fixed import cell_of, find_fixed_cells
from kivilcim.geo import bounding_box, haversine_km
from kivilcim.imagery import find_before_after
from kivilcim.models import EventImagery, FireDetection, IngestRun, WeatherSnapshot
from kivilcim.passes import (
    MAX_ELEMENTS_AGE_DAYS,
    coverage_summary,
    elements_age,
    find_passes,
    load_elements,
    refresh_elements,
)
from kivilcim.ratelimit import RateLimitMiddleware
from kivilcim.risk import compute_risk
from kivilcim.scheduler import IngestScheduler, make_ingest_job
from kivilcim.security import SecurityHeadersMiddleware, build_csp
from kivilcim.seo import apply_head, head_tags, province_by_slug, provinces
from kivilcim.weather import (
    ATTRIBUTION,
    CacheStatus,
    WeatherUnavailable,
    calls_today,
    get_weather,
    get_weather_many,
    round_coordinate,
)
from kivilcim.zones import find_zones

log = logging.getLogger("kivilcim.api")

STATIC_DIR = Path(__file__).parent / "static"

DISCLAIMER = (
    "Kıvılcım is not an official warning system. Satellite detections can be delayed "
    "and can include false positives. In an emergency call 112 and contact AFAD."
)

# Provisional freshness thresholds, applied to the last successful ingest (see PLAN.md).
FRESH_HOURS = 3
WARNING_HOURS = 6

MAX_RADIUS_KM = 500
MAX_SINCE_HOURS = 168

# Weather and risk queries are limited to a loose box around Turkey, so nobody can use the
# service to scan the world and use up the weather API's call quota.
TURKEY_LAT = (35.5, 42.5)
TURKEY_LON = (25.5, 45.5)

Freshness = Literal["fresh", "warning", "stale", "unknown"]


class DataAsOf(BaseModel):
    last_ingest_at: datetime | None
    latest_observation_at: datetime | None
    freshness: Freshness


class SourceStatus(BaseModel):
    source: str
    last_success_at: datetime | None
    last_run_at: datetime
    last_run_status: str
    last_error: str | None
    delay_minutes: float | None
    latest_observation_at: datetime | None
    detection_count: int


class StatusResponse(BaseModel):
    data_as_of: DataAsOf
    sources: list[SourceStatus]
    ingest_interval_minutes: int | None  # None when this process does not run its own timer
    weather_calls_today: int  # calls made to the weather provider today (UTC), counted per point
    weather_daily_quota: int  # the self-imposed daily limit; at the limit only cached weather is served
    disclaimer: str


class FireOut(BaseModel):
    source: str
    satellite: str
    instrument: str
    latitude: float
    longitude: float
    acquired_at: datetime
    age_minutes: float
    confidence_level: str
    confidence_raw: str
    distance_km: float
    brightness: float | None
    frp: float | None


class FiresQuery(BaseModel):
    lat: float
    lon: float
    radius_km: float
    since_hours: int


class FiresResponse(BaseModel):
    query: FiresQuery
    count: int
    fires: list[FireOut]  # nominal and high confidence
    low_confidence: list[FireOut]  # shown separately, never mixed in
    # Detections in a cell that detects on a large share of all days: a factory, refinery, flare
    # or landfill is likely. Flagged and listed apart, never dropped.
    recurring_sources: list[FireOut]
    data_as_of: DataAsOf
    disclaimer: str


class WeatherOut(BaseModel):
    latitude: float  # the rounded point the weather is for
    longitude: float
    observed_at: datetime
    fetched_at: datetime
    temperature_c: float
    humidity_pct: float
    wind_speed_kmh: float
    wind_direction_deg: float | None
    precip_72h_mm: float
    cache_status: CacheStatus


class WeatherResponse(BaseModel):
    weather: WeatherOut
    attribution: str
    disclaimer: str


class RiskOut(BaseModel):
    score: float
    level: str
    drivers: list[str]
    contributions: dict[str, float]
    index_version: str
    experimental: bool
    note: str


class RiskResponse(BaseModel):
    risk: RiskOut
    weather: WeatherOut
    attribution: str
    disclaimer: str


EVENTS_NOTE = (
    "Events are satellite detections grouped automatically by strength, place and time. "
    "They are not confirmed incidents, and some may be crop burning or other heat sources "
    "rather than forest fires."
)


class EventOut(BaseModel):
    id: str
    start: date
    end: date
    peak_day: date
    days: int
    detections: int
    total_frp_mw: float
    max_frp_mw: float
    center_lat: float
    center_lon: float
    bbox: list[float]  # min_lat, min_lon, max_lat, max_lon
    extent_km: float  # diagonal of the bbox
    district: str  # nearest district center, approximate
    province: str
    in_turkey: bool


class EventsResponse(BaseModel):
    count: int
    events: list[EventOut]
    note: str
    disclaimer: str


class EventDetection(BaseModel):
    latitude: float
    longitude: float
    acquired_at: datetime
    frp: float | None
    confidence_level: str
    source: str


class EventDay(BaseModel):
    date: date
    detections: int
    total_frp_mw: float


class EventDetailResponse(BaseModel):
    event: EventOut
    window_start: date
    window_end: date
    days: list[EventDay]
    detections: list[EventDetection]
    note: str
    disclaimer: str


class ImageOut(BaseModel):
    label: str  # "Öncesi" or "Sonrası"
    date: date
    source: str  # Sentinel-2, Landsat or VIIRS
    resolution_m: int
    quality: str  # clear, partly_cloudy, cloudy or partial
    cloud: float  # the share of the measured tile that looks like cloud, 0 to 1
    tile_url: str  # a map tile URL template with {z}, {y} and {x} left in
    max_native_zoom: int


class EventImageryResponse(BaseModel):
    event_id: str
    before: ImageOut | None
    after: ImageOut | None
    low_resolution: bool  # at least one side is the coarse 250 m daily image
    before_options: list[ImageOut] = []  # other usable days to choose from, in date order
    after_options: list[ImageOut] = []
    waiting: bool = False  # the after image cannot exist yet: too soon after the fire
    attribution: str
    note: str
    disclaimer: str


class ScanOut(BaseModel):
    satellite: str
    instrument: str
    at: datetime
    minutes: float  # minutes ago for the last scan, minutes from now for the next


class CoverageResponse(BaseModel):
    available: bool  # false when no orbit data could be fetched yet
    reliable: bool  # false when the orbit data is more than two weeks old
    scans_24h: int  # how many times a satellite scanned the place in the last 24 hours
    last: ScanOut | None
    next: ScanOut | None
    longest_gap_hours: float | None  # the longest stretch with no scan in the window (-24 to +24 h)
    orbit_data_age_days: float | None
    note: str


class FireSuggestion(BaseModel):
    found: bool
    start: date | None = None
    end: date | None = None
    detections: int = 0
    max_frp_mw: float = 0.0


TURKEY_MIN_LAT, TURKEY_MAX_LAT = 35.5, 42.5
TURKEY_MIN_LON, TURKEY_MAX_LON = 25.5, 45.0
FIRST_IMAGE_DAY = date(2016, 1, 1)  # the 30 m archive reaches back to about 2015
MAX_CHECK_DAYS = 120

IMAGERY_NOTE = (
    "The nearest clear satellite images before the first and after the last detection. They are from "
    "the day shown, not from the moment of the fire; a burn scar can be faint, and cloud can hide it."
)
# A choice that found a clear image on both sides is final. Anything less is looked at again later,
# because a new image may have been published since.
IMAGERY_FINAL_SECONDS = 400 * 86400
IMAGERY_RETRY_SECONDS = 6 * 3600
IMAGERY_WAITING_SECONDS = 3 * 3600
IMAGERY_VERSION = 3  # stored choices of an older version are looked for again
IMAGERY_FAILED_SECONDS = 15 * 60

MAX_EVENT_DETECTIONS = 5000


def event_out(event: Event) -> EventOut:
    return EventOut(
        id=event.id,
        start=event.start,
        end=event.end,
        peak_day=event.peak_day,
        days=event.days,
        detections=event.detections,
        total_frp_mw=event.total_frp,
        max_frp_mw=event.max_frp,
        center_lat=event.center_lat,
        center_lon=event.center_lon,
        bbox=[event.min_lat, event.min_lon, event.max_lat, event.max_lon],
        extent_km=event.extent_km,
        district=event.district,
        province=event.province,
        in_turkey=event.in_turkey,
    )


class ZoneOut(BaseModel):
    id: str
    latitude: float
    longitude: float
    detections: int
    total_frp_mw: float
    max_frp_mw: float
    latest_observation_at: datetime
    age_minutes: float
    bbox: list[float]  # min_lat, min_lon, max_lat, max_lon
    radius_km: float  # for drawing
    district: str  # nearest district center, approximate
    province: str
    risk: RiskOut | None  # None when the weather could not be obtained
    weather: WeatherOut | None


class ZonesResponse(BaseModel):
    since_hours: int
    count: int
    zones: list[ZoneOut]
    data_as_of: DataAsOf
    attribution: str
    disclaimer: str


RISK_NOTE = (
    "Experimental fire danger level from current weather. It shows how favorable the weather "
    "is for fires, not the probability that a fire will happen."
)


def weather_out(snapshot: WeatherSnapshot, status: CacheStatus) -> WeatherOut:
    return WeatherOut(
        latitude=snapshot.latitude,
        longitude=snapshot.longitude,
        observed_at=snapshot.observed_at,
        fetched_at=snapshot.fetched_at,
        temperature_c=snapshot.temperature,
        humidity_pct=snapshot.humidity,
        wind_speed_kmh=snapshot.wind_speed,
        wind_direction_deg=snapshot.wind_direction,
        precip_72h_mm=snapshot.precip_recent,
        cache_status=status,
    )


def freshness_of(last_ingest_at: datetime | None, now: datetime) -> Freshness:
    if last_ingest_at is None:
        return "unknown"
    age = now - last_ingest_at
    if age < timedelta(hours=FRESH_HOURS):
        return "fresh"
    if age < timedelta(hours=WARNING_HOURS):
        return "warning"
    return "stale"


def source_statuses(session: Session, now: datetime) -> list[SourceStatus]:
    statuses = []
    sources = session.scalars(select(IngestRun.source).distinct().order_by(IngestRun.source))
    for source in sources.all():
        last_run = session.scalars(
            select(IngestRun).where(IngestRun.source == source).order_by(IngestRun.id.desc()).limit(1)
        ).one()
        last_success = session.scalar(
            select(func.max(IngestRun.finished_at)).where(
                IngestRun.source == source, IngestRun.status == "success"
            )
        )
        latest_obs, count = session.execute(
            select(func.max(FireDetection.acquired_at), func.count(FireDetection.id)).where(
                FireDetection.source == source
            )
        ).one()
        statuses.append(
            SourceStatus(
                source=source,
                last_success_at=last_success,
                last_run_at=last_run.started_at,
                last_run_status=last_run.status,
                last_error=last_run.error,
                delay_minutes=(
                    round((now - last_success).total_seconds() / 60, 1) if last_success else None
                ),
                latest_observation_at=latest_obs,
                detection_count=count,
            )
        )
    return statuses


def data_as_of(session: Session, now: datetime) -> DataAsOf:
    """Overall freshness is judged by the stalest source, so one stuck source is never hidden."""
    statuses = source_statuses(session, now)
    successes = [s.last_success_at for s in statuses]
    last_ingest = min(successes) if successes and all(successes) else None
    latest_obs = session.scalar(select(func.max(FireDetection.acquired_at)))
    return DataAsOf(
        last_ingest_at=last_ingest,
        latest_observation_at=latest_obs,
        freshness=freshness_of(last_ingest, now),
    )


class AssetFiles(StaticFiles):
    """Static files with cache headers: the map library and icons never change under the same name, so
    they are kept for a week; everything else is checked with the server each time (a short 304
    answer when unchanged), so a new release shows at once."""

    LONG_LIVED = ("vendor/", "icons/")

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            long_lived = path.replace("\\", "/").startswith(self.LONG_LIVED)
            response.headers["Cache-Control"] = "public, max-age=604800" if long_lived else "no-cache"
        return response


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    now: Callable[[], datetime] | None = None,
    http_client: httpx.Client | None = None,
    settings: Settings | None = None,
    run_scheduler: bool = False,
) -> FastAPI:
    """Build the app. `run_scheduler` also starts the timer that keeps the data fresh; it is off by
    default so that tests and tools that only read never reach out to the network."""
    settings = settings or get_settings()
    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    clock = now or (lambda: datetime.now(timezone.utc))
    owns_client = http_client is None
    weather_client = http_client or httpx.Client(timeout=15)

    ingest_every = (
        settings.ingest_interval_minutes if run_scheduler and settings.ingest_interval_minutes > 0 else None
    )
    if ingest_every and not settings.firms_map_key:
        log.warning("FIRMS_MAP_KEY is not set, so the automatic ingest is off; the site serves what is stored")
        ingest_every = None

    # Telegram alerts: only when a bot token is set, and the bot listens only when the app runs its own timer.
    alert_service = poller = telegram_client = None
    bot = {"username": settings.telegram_bot_username}
    if settings.telegram_bot_token:
        telegram_client = httpx.Client(timeout=40)
        telegram = Telegram(settings.telegram_bot_token, telegram_client)

        def weather_for(lat: float, lon: float):
            try:
                with session_factory() as session:
                    w = get_weather(session, weather_client, lat, lon, clock(), settings.weather_cache_minutes, settings.open_meteo_daily_quota)
                return w.wind_direction_deg, w.wind_speed_kmh
            except Exception:
                return None

        def fixed_for(session: Session) -> set:
            return fixed_cells(session)

        alert_service = AlertService(
            session_factory, telegram, clock,
            lambda: (settings.public_url or "").rstrip("/"), weather_for, fixed_for,
            settings.telegram_channel, settings.telegram_channel_min_frp,
        )
        if run_scheduler:
            poller = BotPoller(alert_service, telegram, session_factory)
            if not bot["username"]:
                bot["username"] = telegram.me() or ""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        scheduler = ingest_client = None
        if ingest_every:
            ingest_client = httpx.Client(timeout=60)
            scheduler = IngestScheduler(
                make_ingest_job(session_factory, settings, ingest_client, after=after_ingest), ingest_every * 60
            )
            scheduler.start()
            log.info("automatic ingest started: every %d minutes", ingest_every)
        if poller:
            poller.start()
            log.info("the Telegram bot is listening")
        yield
        if poller:
            poller.stop()
            telegram_client.close()
        if scheduler:
            scheduler.stop()
            ingest_client.close()
        if owns_client:
            weather_client.close()

    app = FastAPI(
        title="Kıvılcım",
        version=__version__,
        description=(
            "Wildfire detections from NASA FIRMS satellite data, with the weather at active fires. "
            f"{DISCLAIMER} Read-only and free; the weather values come from Open-Meteo.com (CC BY 4.0), "
            "which is free for non-commercial use only."
        ),
        lifespan=lifespan,
    )
    # Added innermost first: a rejected request still gets the CORS and security headers.
    app.add_middleware(
        RateLimitMiddleware,
        general_per_minute=settings.rate_limit_per_minute,
        weather_per_minute=settings.rate_limit_weather_per_minute,
        imagery_per_minute=settings.rate_limit_imagery_per_minute,
        trust_proxy=settings.trust_proxy,
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["GET"], allow_headers=[])
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware, csp=build_csp(settings.tile_url))

    def current_weather(session: Session, lat: float, lon: float):
        try:
            return get_weather(
                session,
                weather_client,
                lat,
                lon,
                clock(),
                settings.weather_cache_minutes,
                settings.open_meteo_daily_quota,
            )
        except WeatherUnavailable:
            # `from None`: the cause (which can name the provider's URL) is not passed on to the client.
            raise HTTPException(status_code=503, detail="Weather data is temporarily unavailable.") from None

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def active_zones(session: Session, current: datetime, since_hours: int) -> list:
        rows = session.scalars(
            select(FireDetection).where(FireDetection.acquired_at >= current - timedelta(hours=since_hours))
        ).all()
        return find_zones(rows, fixed_cells(session))

    def warm_weather() -> None:
        """Fetch the weather for the current zones ahead of time, so the first visitor after an update
        does not wait for it. Readings older than 20 minutes are refreshed, which keeps them fresh
        across a 30 minute update cycle."""
        with session_factory() as session:
            current = clock()
            zones = active_zones(session, current, 24)
            get_weather_many(
                session, weather_client, [(z.center_lat, z.center_lon) for z in zones], current, 20,
                settings.open_meteo_daily_quota,
            )

    def render_page(name: str) -> HTMLResponse:
        """A page from the static folder with the site's settings filled in.

        The settings go into a JSON block that the page's script reads (a block of type
        application/json is data, not script, so the content security policy does not mind it).
        "</" is escaped so no value can close the element, whatever it contains.
        """
        return HTMLResponse(page_html(name), headers={"Cache-Control": "no-cache"})

    def page_html(name: str) -> str:
        config = json.dumps(
            {"tileUrl": settings.tile_url, "tileAttribution": settings.tile_attribution, "version": __version__}
        ).replace("</", "<\\/")
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        return html.replace("__CONFIG__", config).replace("__VERSION__", __version__)

    def base_url(request: Request) -> str:
        return (settings.public_url or str(request.base_url)).rstrip("/")

    def shell(request: Request, title: str, description: str, image: str | None = None, text: str | None = None) -> HTMLResponse:
        """The app page with its own title, description and link preview, and optionally a text that
        search engines read in place of the loading skeleton (the script replaces it)."""
        base = base_url(request)
        query = f"?{request.url.query}" if request.url.query else ""
        html = apply_head(page_html("index.html"), title, description, head_tags(base, request.url.path + query, title, description, image))
        if text is not None:
            html = re.sub(r"<!--SKELETON-->.*?<!--/SKELETON-->", lambda _: text, html, count=1, flags=re.S)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    DEFAULT_TITLE = "Kıvılcım · Türkiye'de uydudan yangın izleme"
    DEFAULT_DESCRIPTION = (
        "Türkiye'de NASA uydularının son saatlerde gördüğü yangın sıcaklıkları, uydunun yeri ne zaman taradığı, "
        "rüzgar ve geçmiş yangınların öncesi-sonrası uydu görüntüleri. Resmi bir uyarı sistemi değildir."
    )

    # One page, several addresses: the map, the past fires and one fire all open the same app, which
    # reads the address itself. Moving between them in the page never reloads the map.
    @app.get("/", include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        q = request.query_params
        if "lat" in q and "lon" in q:
            name = q.get("name", "").strip()[:60] or "Seçilen yer"
            return shell(
                request, f"{name} · yakındaki yangınlar · Kıvılcım",
                f"{name} çevresinde son 24 saatte uydunun gördüğü yangın sıcaklıkları, uydunun yeri en son ne zaman taradığı ve rüzgar.",
            )
        return shell(request, DEFAULT_TITLE, DEFAULT_DESCRIPTION)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> Response:
        # Served from the root so it can control the whole site. The version changes the cache
        # name, so a new release replaces the stored copies.
        script = (STATIC_DIR / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", __version__)
        return Response(
            script,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/check", include_in_schema=False)
    def check_page(request: Request) -> HTMLResponse:
        q = request.query_params
        title, description, image = "Yerim yandı mı? · Kıvılcım", "Bir yerin yangından önceki ve sonraki uydu görüntüsünü yan yana görün.", None
        if all(k in q for k in ("lat", "lon", "start")):
            name = q.get("name", "").strip()[:60]
            title = f"{name or 'Yerim'} yandı mı? Öncesi ve sonrası uydu görüntüsü · Kıvılcım"
            params = {k: q[k] for k in ("lat", "lon", "start", "end") if k in q}
            if name:
                params["name"] = name
            image = f"{base_url(request)}/imagery/place/card.jpg?{urlencode(params)}"
        return shell(request, title, description, image)

    @app.get("/history", include_in_schema=False)
    def history_page(request: Request) -> HTMLResponse:
        return shell(
            request, "Geçmiş yangınlar · Kıvılcım",
            "Uydunun gördüğü büyük yangınlar: her biri için yangından önceki ve sonraki uydu görüntüsü ve günlük yayılma haritası.",
        )

    @app.get("/history/{event_id}", include_in_schema=False)
    def event_page(request: Request, event_id: str, session: Session = Depends(get_session)) -> HTMLResponse:
        event = next((e for e in all_events(session) if e.id == event_id), None)
        if event is None:
            return shell(request, "Geçmiş yangın · Kıvılcım", "Uydunun gördüğü bir yangının öncesi ve sonrası.")
        place = f"{event.district}, {event.province}"
        return shell(
            request,
            f"{place} yangını: öncesi ve sonrası uydu görüntüsü · Kıvılcım",
            f"{place} çevresinde {event.start.day}.{event.start.month}.{event.start.year} tarihinde başlayan yangın: "
            f"{event.detections} uydu tespiti, {event.days} gün. Yangından önceki ve sonraki uydu görüntüleri.",
            f"{base_url(request)}/events/{event.id}/card.jpg",
        )

    @app.get("/il/{slug}", include_in_schema=False)
    def province_page(request: Request, slug: str, session: Session = Depends(get_session)) -> HTMLResponse:
        province = province_by_slug(slug)
        if province is None:
            raise HTTPException(status_code=404, detail="Province not found.")
        current = clock()
        zones = [z for z in active_zones(session, current, 24) if z.province == province["name"]]
        count, detections = len(zones), sum(z.detections for z in zones)
        name = escape(province["name"])
        if zones:
            summary = (
                f"Son 24 saatte {name} sınırları içinde uydular {count} bölgede toplam {detections} yangın sıcaklığı tespiti gördü."
            )
        else:
            summary = (
                f"Son 24 saatte {name} sınırları içinde uyduların gördüğü bir yangın sıcaklığı yok. Bu, yangın olmadığı anlamına "
                "gelmez: uydular günde birkaç kez geçer ve küçük ya da kısa süreli ateşleri kaçırabilir."
            )
        districts = "".join(
            f'<li><a href="/?lat={d["lat"]:.4f}&amp;lon={d["lon"]:.4f}&amp;r=25&amp;name={quote(d["name"] + ", " + province["name"])}">{escape(d["name"])}</a></li>'
            for d in province["districts"]
        )
        text = (
            f'<article class="seo"><h1>{name}: yangın tespitleri</h1><p>{summary}</p>'
            "<p>Veriler NASA FIRMS uydu gözlemlerinden gelir ve saatler geç olabilir. Kıvılcım resmi bir uyarı sistemi değildir; "
            "acil durumda 112'yi arayın.</p>"
            f'<p><a href="/?lat={province["lat"]:.4f}&amp;lon={province["lon"]:.4f}&amp;r=100&amp;name={quote(province["name"])}">'
            f"{name} haritada, son 24 saat</a> · <a href=\"/history\">Geçmiş yangınlar</a> · <a href=\"/methodology\">Yöntem</a></p>"
            f"<h2>{name} ilçeleri</h2><ul>{districts}</ul></article>"
        )
        return shell(
            request,
            f"{province['name']} yangın tespitleri (son 24 saat) · Kıvılcım",
            re.sub(r"<[^>]+>", "", summary.replace("&amp;", "&")),
            None,
            text,
        )

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request, session: Session = Depends(get_session)) -> Response:
        base = base_url(request)
        paths = ["/", "/history", "/check", "/methodology"]
        paths += [f"/il/{p['slug']}" for p in provinces()]
        paths += [f"/history/{e.id}" for e in all_events(session) if e.in_turkey][:500]
        body = "".join(f"<url><loc>{escape(base + p)}</loc></url>" for p in paths)
        xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
        return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/robots.txt", include_in_schema=False)
    def robots(request: Request) -> Response:
        lines = [
            "User-agent: *",
            "Allow: /",
            "Allow: /events/*/card.jpg",
            "Disallow: /events/",
            "Disallow: /fires",
            "Disallow: /risk",
            "Disallow: /weather",
            "Disallow: /passes",
            "Disallow: /imagery/",
            f"Sitemap: {base_url(request)}/sitemap.xml",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain")

    @app.get("/report", include_in_schema=False)
    def report() -> HTMLResponse:
        return render_page("report.html")

    @app.get("/embed", include_in_schema=False)
    def embed() -> HTMLResponse:
        return render_page("embed.html")

    @app.get("/methodology", include_in_schema=False)
    def methodology() -> HTMLResponse:
        return render_page("methodology.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", response_model=StatusResponse)
    def status(session: Session = Depends(get_session)) -> StatusResponse:
        current = clock()
        return StatusResponse(
            data_as_of=data_as_of(session, current),
            sources=source_statuses(session, current),
            ingest_interval_minutes=ingest_every,
            weather_calls_today=calls_today(session, current),
            weather_daily_quota=settings.open_meteo_daily_quota,
            disclaimer=DISCLAIMER,
        )

    @app.get("/fires", response_model=FiresResponse)
    def fires(
        lat: float = Query(ge=-90, le=90),
        lon: float = Query(ge=-180, le=180),
        radius_km: float = Query(default=25, gt=0, le=MAX_RADIUS_KM),
        since_hours: int = Query(default=24, ge=1, le=MAX_SINCE_HOURS),
        session: Session = Depends(get_session),
    ) -> FiresResponse:
        current = clock()
        min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_km)
        rows = session.scalars(
            select(FireDetection).where(
                FireDetection.latitude.between(min_lat, max_lat),
                FireDetection.longitude.between(min_lon, max_lon),
                FireDetection.acquired_at >= current - timedelta(hours=since_hours),
            )
        ).all()

        found = []
        for row in rows:
            distance = haversine_km(lat, lon, row.latitude, row.longitude)
            if distance <= radius_km:
                found.append(
                    FireOut(
                        source=row.source,
                        satellite=row.satellite,
                        instrument=row.instrument,
                        latitude=row.latitude,
                        longitude=row.longitude,
                        acquired_at=row.acquired_at,
                        age_minutes=round((current - row.acquired_at).total_seconds() / 60, 1),
                        confidence_level=row.confidence_level,
                        confidence_raw=row.confidence_raw,
                        distance_km=round(distance, 2),
                        brightness=row.brightness,
                        frp=row.frp,
                    )
                )
        found.sort(key=lambda f: f.distance_km)

        fixed = fixed_cells(session)

        def is_fixed(f: FireOut) -> bool:
            return cell_of(f.latitude, f.longitude) in fixed

        return FiresResponse(
            query=FiresQuery(lat=lat, lon=lon, radius_km=radius_km, since_hours=since_hours),
            count=len(found),
            fires=[f for f in found if f.confidence_level != "low" and not is_fixed(f)],
            low_confidence=[f for f in found if f.confidence_level == "low"],
            recurring_sources=[f for f in found if f.confidence_level != "low" and is_fixed(f)],
            data_as_of=data_as_of(session, current),
            disclaimer=DISCLAIMER,
        )

    @app.get("/weather", response_model=WeatherResponse)
    def weather(
        lat: float = Query(ge=TURKEY_LAT[0], le=TURKEY_LAT[1]),
        lon: float = Query(ge=TURKEY_LON[0], le=TURKEY_LON[1]),
        session: Session = Depends(get_session),
    ) -> WeatherResponse:
        result = current_weather(session, lat, lon)
        return WeatherResponse(
            weather=weather_out(result.snapshot, result.status),
            attribution=ATTRIBUTION,
            disclaimer=DISCLAIMER,
        )

    @app.get("/risk/zones", response_model=ZonesResponse)
    def risk_zones(
        response: Response,
        since_hours: int = Query(default=24, ge=1, le=72),
        weather: bool = Query(default=True, description="Add the weather index. false answers at once from stored detections."),
        session: Session = Depends(get_session),
    ) -> ZonesResponse:
        """Active fire zones across Turkey, each with the weather index at its center.

        Weather for all zones is fetched in one request (and cached), strongest zones first, so
        if the daily call quota runs short it is the weakest zones that go without a value. With
        `weather=false` nothing is fetched and every zone has `risk` and `weather` empty; a page can
        draw the zones from that at once and add the colors when the full answer arrives.
        """
        response.headers["Cache-Control"] = "public, max-age=60"  # one minute, so shared caches can help
        current = clock()
        zones = active_zones(session, current, since_hours)
        weather_by_point = (
            get_weather_many(
                session,
                weather_client,
                [(z.center_lat, z.center_lon) for z in zones],
                current,
                settings.weather_cache_minutes,
                settings.open_meteo_daily_quota,
            )
            if weather
            else {}
        )

        out = []
        for zone in zones:
            result = weather_by_point.get((round_coordinate(zone.center_lat), round_coordinate(zone.center_lon)))
            risk = weather = None
            if result is not None:
                snap = result.snapshot
                scored = compute_risk(snap.temperature, snap.humidity, snap.wind_speed, snap.precip_recent)
                weather = weather_out(snap, result.status)
                risk = RiskOut(
                    score=scored.score,
                    level=scored.level,
                    drivers=scored.drivers,
                    contributions=scored.contributions,
                    index_version=scored.index_version,
                    experimental=True,
                    note=RISK_NOTE,
                )
            out.append(
                ZoneOut(
                    id=zone.id,
                    latitude=zone.center_lat,
                    longitude=zone.center_lon,
                    detections=zone.detections,
                    total_frp_mw=zone.total_frp,
                    max_frp_mw=zone.max_frp,
                    latest_observation_at=zone.latest_observation,
                    age_minutes=round((current - zone.latest_observation).total_seconds() / 60, 1),
                    bbox=[zone.min_lat, zone.min_lon, zone.max_lat, zone.max_lon],
                    radius_km=zone.radius_km,
                    district=zone.district,
                    province=zone.province,
                    risk=risk,
                    weather=weather,
                )
            )
        return ZonesResponse(
            since_hours=since_hours,
            count=len(out),
            zones=out,
            data_as_of=data_as_of(session, current),
            attribution=ATTRIBUTION,
            disclaimer=DISCLAIMER,
        )

    @app.get("/risk", response_model=RiskResponse)
    def risk(
        lat: float = Query(ge=TURKEY_LAT[0], le=TURKEY_LAT[1]),
        lon: float = Query(ge=TURKEY_LON[0], le=TURKEY_LON[1]),
        session: Session = Depends(get_session),
    ) -> RiskResponse:
        result = current_weather(session, lat, lon)
        snap = result.snapshot
        scored = compute_risk(snap.temperature, snap.humidity, snap.wind_speed, snap.precip_recent)
        return RiskResponse(
            risk=RiskOut(
                score=scored.score,
                level=scored.level,
                drivers=scored.drivers,
                contributions=scored.contributions,
                index_version=scored.index_version,
                experimental=True,
                note=RISK_NOTE,
            ),
            weather=weather_out(snap, result.status),
            attribution=ATTRIBUTION,
            disclaimer=DISCLAIMER,
        )

    fixed_lock = threading.Lock()
    fixed_cache: dict = {"key": None, "cells": set()}

    def fixed_cells(session: Session) -> set:
        """Cells that look like fixed heat sources, found from the VIIRS history. Cached until a new
        detection is stored (see all_events for why a count and max id are a safe key)."""
        condition = (FireDetection.instrument == "VIIRS", FireDetection.confidence_level != "low")
        key = tuple(session.execute(select(func.count(FireDetection.id), func.max(FireDetection.id)).where(*condition)).one())
        with fixed_lock:
            if fixed_cache["key"] != key:
                rows = session.execute(
                    select(FireDetection.latitude, FireDetection.longitude, FireDetection.acquired_at).where(*condition)
                ).all()
                fixed_cache.update(
                    key=key, cells=find_fixed_cells((lat, lon, when.date()) for lat, lon, when in rows)
                )
            return fixed_cache["cells"]

    events_lock = threading.Lock()
    events_cache: dict = {"key": None, "events": []}

    def all_events(session: Session) -> list[Event]:
        """Events from VIIRS detections only: MODIS pixels are 1 km, so their heat power is on a
        different scale and would distort the strength thresholds. The result is cached until a
        new detection is stored (rows only ever get added, so a count and max id are a safe key)."""
        key = tuple(
            session.execute(
                select(func.count(FireDetection.id), func.max(FireDetection.id)).where(
                    FireDetection.instrument == "VIIRS"
                )
            ).one()
        )
        with events_lock:
            if events_cache["key"] != key:
                rows = session.scalars(select(FireDetection).where(FireDetection.instrument == "VIIRS")).all()
                events_cache.update(key=key, events=find_events(rows))
            return events_cache["events"]

    @app.get("/events", response_model=EventsResponse)
    def events(
        response: Response,
        turkey_only: bool = True,
        since: date | None = None,
        until: date | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
    ) -> EventsResponse:
        response.headers["Cache-Control"] = "public, max-age=60"
        found = [
            e
            for e in all_events(session)
            if (e.in_turkey or not turkey_only)
            and (since is None or e.end >= since)
            and (until is None or e.start <= until)
        ][:limit]
        return EventsResponse(
            count=len(found), events=[event_out(e) for e in found], note=EVENTS_NOTE, disclaimer=DISCLAIMER
        )

    @app.get("/events/{event_id}", response_model=EventDetailResponse)
    def event_detail(
        event_id: str,
        days_before: int = Query(default=5, ge=0, le=30),
        days_after: int = Query(default=10, ge=0, le=60),
        padding_deg: float = Query(default=0.3, ge=0, le=2),
        session: Session = Depends(get_session),
    ) -> EventDetailResponse:
        event = next((e for e in all_events(session) if e.id == event_id), None)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")

        window_start = event.start - timedelta(days=days_before)
        window_end = event.end + timedelta(days=days_after)
        rows = session.scalars(
            select(FireDetection)
            .where(
                FireDetection.latitude.between(event.min_lat - padding_deg, event.max_lat + padding_deg),
                FireDetection.longitude.between(event.min_lon - padding_deg, event.max_lon + padding_deg),
                FireDetection.acquired_at >= datetime.combine(window_start, datetime.min.time(), timezone.utc),
                FireDetection.acquired_at
                < datetime.combine(window_end + timedelta(days=1), datetime.min.time(), timezone.utc),
            )
            .order_by(FireDetection.acquired_at)
            .limit(MAX_EVENT_DETECTIONS)
        ).all()

        per_day: dict[date, list[float]] = {}
        for row in rows:
            per_day.setdefault(row.acquired_at.date(), []).append(row.frp or 0.0)
        return EventDetailResponse(
            event=event_out(event),
            window_start=window_start,
            window_end=window_end,
            days=[
                EventDay(date=day, detections=len(values), total_frp_mw=round(sum(values), 1))
                for day, values in sorted(per_day.items())
            ],
            detections=[
                EventDetection(
                    latitude=r.latitude,
                    longitude=r.longitude,
                    acquired_at=r.acquired_at,
                    frp=r.frp,
                    confidence_level=r.confidence_level,
                    source=r.source,
                )
                for r in rows
            ],
            note=EVENTS_NOTE,
            disclaimer=DISCLAIMER,
        )

    imagery_lock = threading.Lock()

    def last_activity(session: Session, lat: float, lon: float, start: date, end: date) -> date:
        """The last day a fire of some strength was seen near a place. Fires often go on after an event
        as it was grouped (its length is capped), and an "after" image should be taken once they are
        out. Only the area the image shows counts, only detections of at least 5 MW (the weakest are
        crop burning and noise), and only up to two weeks past `end`, so a place that burns all
        season still gets an image."""
        first = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        limit = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=15)
        last = session.scalar(
            select(func.max(FireDetection.acquired_at)).where(
                FireDetection.latitude.between(lat - 0.12, lat + 0.12),
                FireDetection.longitude.between(lon - 0.15, lon + 0.15),
                FireDetection.acquired_at >= first,
                FireDetection.acquired_at < limit,
                FireDetection.confidence_level != "low",
                FireDetection.frp >= 5,
            )
        )
        return max(end, last.date()) if last else end

    def imagery_for(session: Session, key: str, lat: float, lon: float, start: date, end: date) -> dict:
        """The stored image choice for a place and dates, searched and stored first if there is none yet."""
        now = clock()

        def stored() -> dict | None:
            row = session.get(EventImagery, key)
            if row and (now - row.computed_at).total_seconds() < row.ttl_seconds:
                payload = json.loads(row.payload)
                if payload.get("v") == IMAGERY_VERSION:
                    return payload
            return None

        payload = stored()
        if payload is not None:
            return payload
        with imagery_lock:  # one search at a time: it is heavy, and two would repeat each other
            session.expire_all()
            payload = stored()
            if payload is not None:
                return payload
            payload = find_before_after(
                lat, lon, start, end, now.date(), quiet_from=last_activity(session, lat, lon, start, end)
            )
            payload["v"] = IMAGERY_VERSION
            both_clear = all(payload[side] and payload[side]["quality"] == "clear" for side in ("before", "after"))
            nothing = payload["before"] is None and payload["after"] is None
            if payload["incomplete"] or nothing:
                ttl = IMAGERY_FAILED_SECONDS
            elif payload["waiting"]:
                ttl = IMAGERY_WAITING_SECONDS
            elif both_clear and not payload["low_resolution"]:
                ttl = IMAGERY_FINAL_SECONDS
            else:
                ttl = IMAGERY_RETRY_SECONDS
            if key.startswith("p"):  # searches for places people typed in are kept for a month only
                session.execute(
                    delete(EventImagery).where(
                        EventImagery.event_id.like("p%"), EventImagery.computed_at < now - timedelta(days=30)
                    )
                )
            session.merge(EventImagery(event_id=key, computed_at=now, ttl_seconds=ttl, payload=json.dumps(payload)))
            session.commit()
            return payload

    def imagery_response(key: str, payload: dict, response: Response) -> EventImageryResponse:
        response.headers["Cache-Control"] = "public, max-age=300"
        return EventImageryResponse(
            event_id=key,
            before=payload["before"],
            after=payload["after"],
            low_resolution=payload["low_resolution"],
            before_options=payload.get("before_options", []),
            after_options=payload.get("after_options", []),
            waiting=payload.get("waiting", False),
            attribution=payload["attribution"],
            note=IMAGERY_NOTE,
            disclaimer=DISCLAIMER,
        )

    @app.get("/events/{event_id}/imagery", response_model=EventImageryResponse)
    def event_imagery(
        event_id: str,
        response: Response,
        session: Session = Depends(get_session),
    ) -> EventImageryResponse:
        """The satellite images to compare for an event: the nearest clear one before it began and
        one taken a few days after the last fire there went out, with other usable days to choose
        from. The search downloads tiles, so the choice is stored and reused."""
        event = next((e for e in all_events(session) if e.id == event_id), None)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        payload = imagery_for(session, event.id, event.center_lat, event.center_lon, event.start, event.end)
        return imagery_response(event_id, payload, response)

    coverage_cache: dict = {}
    coverage_lock = threading.Lock()

    @app.get("/passes", response_model=CoverageResponse)
    def passes(
        response: Response,
        lat: float = Query(ge=TURKEY_MIN_LAT, le=TURKEY_MAX_LAT),
        lon: float = Query(ge=TURKEY_MIN_LON, le=TURKEY_MAX_LON),
        session: Session = Depends(get_session),
    ) -> CoverageResponse:
        """When the fire-detecting satellites last scanned a place and when they will next. Without it
        "no detection" says nothing: a satellite may not have passed at all. Predicted from public orbit
        data; a scan is when the place lay inside the satellite's swath."""
        current = clock()
        with coverage_lock:
            rows = load_elements(session)
            if len(rows) < 5:
                refresh_elements(session, weather_client, current)
                rows = load_elements(session)
        response.headers["Cache-Control"] = "public, max-age=300"
        note = (
            "Predicted from public orbit data. A scan is when the place was inside the satellite's swath; "
            "small fires and the edges of a swath can still be missed, and the data reaches this site about "
            "three hours after a scan."
        )
        if not rows:
            return CoverageResponse(
                available=False, reliable=False, scans_24h=0, last=None, next=None, longest_gap_hours=None,
                orbit_data_age_days=None, note=note,
            )
        key = (round(lat, 1), round(lon, 1), int(current.timestamp() // 300))
        found = coverage_cache.get(key)
        if found is None:
            found = find_passes(rows, lat, lon, current - timedelta(hours=24), current + timedelta(hours=24))
            coverage_cache.clear()  # one five-minute slot at a time is enough
            coverage_cache[key] = found
        summary = coverage_summary(found, current)
        age = elements_age(rows, current)

        def scan(p) -> ScanOut | None:
            if p is None:
                return None
            return ScanOut(
                satellite=p.satellite, instrument=p.instrument, at=p.closest,
                minutes=abs((p.closest - current).total_seconds()) / 60,
            )

        return CoverageResponse(
            available=True,
            reliable=age is not None and age < timedelta(days=MAX_ELEMENTS_AGE_DAYS),
            scans_24h=sum(1 for p in found if p.closest <= current),
            last=scan(summary["last"]),
            next=scan(summary["next"]),
            longest_gap_hours=round(summary["longest_gap_minutes"] / 60, 1) if summary["longest_gap_minutes"] else None,
            orbit_data_age_days=round(age.total_seconds() / 86400, 1) if age else None,
            note=note,
        )

    card_cache: dict = {}
    card_lock = threading.Lock()

    def make_card(key: str, imagery: dict, lat: float, lon: float, title: str, subtitle: str, extent_km: float) -> Response:
        """A shareable before/after picture, kept for a few hours since building it fetches tiles."""
        if not imagery["before"] and not imagery["after"]:
            raise HTTPException(status_code=404, detail="There are no usable satellite images for this yet.")
        stamp = clock().timestamp()
        with card_lock:
            hit = card_cache.get(key)
            if hit and stamp - hit[0] < 6 * 3600:
                body = hit[1]
            else:
                def fetch(url: str) -> bytes | None:
                    try:
                        r = weather_client.get(url, timeout=15)
                    except httpx.HTTPError:
                        return None
                    return r.content if r.status_code == 200 else None

                body = render_card(fetch, imagery, lat, lon, title, subtitle, extent_km)
                if len(card_cache) >= 48:
                    card_cache.pop(next(iter(card_cache)))
                card_cache[key] = (stamp, body)
        return Response(content=body, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/events/{event_id}/card.jpg", include_in_schema=True)
    def event_card(event_id: str, session: Session = Depends(get_session)) -> Response:
        """A picture of a past fire with the satellite before and after, for sharing."""
        event = next((e for e in all_events(session) if e.id == event_id), None)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        imagery = imagery_for(session, event.id, event.center_lat, event.center_lon, event.start, event.end)
        months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        when = f"{event.start.day} {months[event.start.month - 1]}" if event.start == event.end else (
            f"{event.start.day}-{event.end.day} {months[event.end.month - 1]} {event.end.year}"
            if event.start.month == event.end.month
            else f"{event.start.day} {months[event.start.month - 1]} - {event.end.day} {months[event.end.month - 1]} {event.end.year}"
        )
        return make_card(
            f"e:{event.id}:{imagery['before'] and imagery['before']['date']}:{imagery['after'] and imagery['after']['date']}",
            imagery, event.center_lat, event.center_lon, f"{event.district}, {event.province}",
            f"{when} · {event.days} gün · {event.detections} uydu tespiti", event.extent_km,
        )

    @app.get("/imagery/place/card.jpg")
    def place_card(
        lat: float = Query(ge=TURKEY_MIN_LAT, le=TURKEY_MAX_LAT),
        lon: float = Query(ge=TURKEY_MIN_LON, le=TURKEY_MAX_LON),
        start: date = Query(),
        end: date | None = Query(default=None),
        name: str = Query(default="", max_length=60),
        session: Session = Depends(get_session),
    ) -> Response:
        """The same picture for a place and dates chosen with the "did it burn" check."""
        end = end or start
        if start < FIRST_IMAGE_DAY or end < start or end > clock().date() or (end - start).days > MAX_CHECK_DAYS:
            raise HTTPException(status_code=422, detail="The dates must be between 2016 and today, start before end, at most 120 days apart.")
        lat, lon = round(lat, 2), round(lon, 2)
        imagery = imagery_for(session, f"p{lat}_{lon}_{start}_{end}", lat, lon, start, end)
        title = name.strip() or "Yangın öncesi ve sonrası"
        subtitle = f"{start.day}.{start.month}.{start.year}" + ("" if end == start else f" - {end.day}.{end.month}.{end.year}")
        return make_card(
            f"p:{lat}:{lon}:{start}:{end}:{imagery['before'] and imagery['before']['date']}:{imagery['after'] and imagery['after']['date']}",
            imagery, lat, lon, title, f"Yangın tarihi {subtitle}", 10.0,
        )

    @app.get("/imagery/place", response_model=EventImageryResponse)
    def place_imagery(
        response: Response,
        lat: float = Query(ge=TURKEY_MIN_LAT, le=TURKEY_MAX_LAT),
        lon: float = Query(ge=TURKEY_MIN_LON, le=TURKEY_MAX_LON),
        start: date = Query(description="First day of the fire (or the day it was noticed)"),
        end: date | None = Query(default=None, description="Last day of the fire; the start day if omitted"),
        session: Session = Depends(get_session),
    ) -> EventImageryResponse:
        """Satellite images from before and after a fire at any place in Turkey, for checking whether
        a house, field or forest burned. Taken the same way as for the past fires on the map."""
        end = end or start
        today = clock().date()
        if start < FIRST_IMAGE_DAY or end < start or end > today or (end - start).days > MAX_CHECK_DAYS:
            raise HTTPException(status_code=422, detail="The dates must be between 2016 and today, start before end, at most 120 days apart.")
        lat, lon = round(lat, 2), round(lon, 2)  # about 1 km: keeps stored searches shared and places vague
        key = f"p{lat}_{lon}_{start}_{end}"
        payload = imagery_for(session, key, lat, lon, start, end)
        return imagery_response(key, payload, response)

    @app.get("/imagery/suggest", response_model=FireSuggestion)
    def suggest_dates(
        lat: float = Query(ge=TURKEY_MIN_LAT, le=TURKEY_MAX_LAT),
        lon: float = Query(ge=TURKEY_MIN_LON, le=TURKEY_MAX_LON),
        session: Session = Depends(get_session),
    ) -> FireSuggestion:
        """The dates of the latest fire the satellites saw near a place (within about 15 km, strong
        detections only, last 120 days), to fill in the dates of a check. A gap of five quiet days
        starts a new fire."""
        since = clock() - timedelta(days=MAX_CHECK_DAYS)
        rows = session.execute(
            select(FireDetection.acquired_at, FireDetection.frp).where(
                FireDetection.latitude.between(lat - 0.14, lat + 0.14),
                FireDetection.longitude.between(lon - 0.18, lon + 0.18),
                FireDetection.acquired_at >= since,
                FireDetection.confidence_level != "low",
                FireDetection.frp >= 5,
            )
        ).all()
        days = sorted({r.acquired_at.date() for r in rows})
        if not days:
            return FireSuggestion(found=False)
        start = end = days[-1]
        for day in reversed(days[:-1]):
            if (start - day).days > 5:
                break
            start = day
        inside = [r for r in rows if start <= r.acquired_at.date() <= end]
        return FireSuggestion(
            found=True, start=start, end=end, detections=len(inside), max_frp_mw=max(r.frp for r in inside)
        )

    warm_state = {"running": False}

    def warm_imagery(limit: int = 40) -> None:
        """Search the images of the biggest fires ahead of time, one after another, so the history
        pages open at once. Runs in the background after each update."""
        if warm_state["running"]:
            return
        warm_state["running"] = True
        try:
            with session_factory() as session:
                big = sorted((e for e in all_events(session) if e.in_turkey), key=lambda e: -e.total_frp)[:limit]
                for event in big:
                    try:
                        imagery_for(session, event.id, event.center_lat, event.center_lon, event.start, event.end)
                    except Exception:
                        log.exception("could not search the images of %s", event.id)
        finally:
            warm_state["running"] = False

    def after_ingest() -> None:
        warm_weather()
        try:
            with session_factory() as session:
                refresh_elements(session, weather_client, clock())
        except Exception:
            log.exception("could not refresh the satellite orbit data")
        if alert_service:
            try:
                alert_service.dispatch()
            except Exception:
                log.exception("could not send the alerts")
        threading.Thread(target=warm_imagery, name="warm-imagery", daemon=True).start()

    class BotInfo(BaseModel):
        enabled: bool
        username: str | None = None

    @app.get("/alerts/bot", response_model=BotInfo)
    def alert_bot(response: Response) -> BotInfo:
        """Whether the Telegram bot is set up, and its username for the "tell me on Telegram" links."""
        response.headers["Cache-Control"] = "public, max-age=300"
        return BotInfo(enabled=bool(alert_service and bot["username"]), username=bot["username"] or None)

    app.state.alerts = alert_service
    app.state.warm_imagery = warm_imagery

    app.state.warm_weather = warm_weather  # also lets tests and tools call it
    app.mount("/static", AssetFiles(directory=STATIC_DIR), name="static")
    return app


def create_server_app() -> FastAPI:
    """The app with its own ingest timer, for `uvicorn kivilcim.api:create_server_app --factory`."""
    return create_app(run_scheduler=True)
