from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    firms_map_key: str = ""
    database_url: str = "sqlite:///kivilcim.db"
    # Comma-separated FIRMS products to ingest. Three VIIRS satellites pass at different times, so
    # together they see a fire several times a day. MODIS adds coarser (1 km) observations.
    firms_sources: str = "VIIRS_SNPP_NRT,VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT,MODIS_NRT"
    # How many days back each run asks for. A window larger than the run interval
    # lets a missed run be recovered; deduplication makes the overlap harmless.
    firms_day_range: int = 2
    # Minutes between live ingests when the process runs its own timer (`python -m kivilcim`).
    # FIRMS allows 5,000 requests per 10 minutes, so this only has to keep up with how often the
    # satellites pass; 30 is plenty. 0 turns the timer off.
    ingest_interval_minutes: int = 30

    # Requests per minute per client; 0 turns a limit off. The weather limit is stricter because
    # those requests can make the server call Open-Meteo, whose free tier has a daily quota.
    rate_limit_per_minute: int = 120
    rate_limit_weather_per_minute: int = 30
    # Finding satellite images for an event downloads many tiles, so that is limited too.
    rate_limit_imagery_per_minute: int = 10
    # Behind a reverse proxy, take the client address from X-Forwarded-For. Only turn this on when
    # a proxy you control sets that header; otherwise a client could pick its own address.
    trust_proxy: bool = False
    # Browsers may call the read-only API from other sites. A comma-separated list, or * for any.
    cors_origins: str = "*"
    # The map. OpenStreetMap's public tile servers are meant for light use; for a busy public site
    # use a tile provider and set its URL template here (with its attribution text).
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "© OpenStreetMap"

    # Open-Meteo's free tier allows 10,000 calls per day. This is a call count, not money:
    # the service stops asking and serves cached weather before reaching that limit.
    open_meteo_daily_quota: int = 7000
    weather_cache_minutes: int = 45

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def source_list(self) -> list[str]:
        return [s.strip() for s in self.firms_sources.split(",") if s.strip()]


def get_settings() -> Settings:
    return Settings()
