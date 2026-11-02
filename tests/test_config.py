from kivilcim.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.firms_map_key == ""
    assert settings.database_url == "sqlite:///kivilcim.db"


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", "abc123")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///other.db")
    settings = Settings(_env_file=None)
    assert settings.firms_map_key == "abc123"
    assert settings.database_url == "sqlite:///other.db"
