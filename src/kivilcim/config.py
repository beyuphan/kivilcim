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

    # Open-Meteo's free tier allows 10,000 calls per day. This is a call count, not money:
    # the service stops asking and serves cached weather before reaching that limit.
    open_meteo_daily_quota: int = 7000
    weather_cache_minutes: int = 45

    @property
    def source_list(self) -> list[str]:
        return [s.strip() for s in self.firms_sources.split(",") if s.strip()]


def get_settings() -> Settings:
    return Settings()
