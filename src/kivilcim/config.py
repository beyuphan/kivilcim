from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    firms_map_key: str = ""
    database_url: str = "sqlite:///kivilcim.db"


def get_settings() -> Settings:
    return Settings()
