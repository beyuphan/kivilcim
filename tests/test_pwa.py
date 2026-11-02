import json
import struct
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kivilcim import __version__
from kivilcim.api import create_app
from kivilcim.db import init_db, make_engine, make_session_factory

ROOT = Path(__file__).parent.parent
STATIC = ROOT / "src" / "kivilcim" / "static"


@pytest.fixture
def client(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    init_db(engine)
    return TestClient(create_app(make_session_factory(engine)))


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_the_version_in_the_package_matches_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


def test_manifest_has_what_an_installable_app_needs(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["name"] == "Kıvılcım"
    assert manifest["start_url"] == "/" and manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] and manifest["background_color"]
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])
    assert "resmi" in manifest["description"].lower()  # the disclaimer travels with the app


def test_every_manifest_icon_is_served_and_the_size_matches(client):
    for icon in client.get("/manifest.webmanifest").json()["icons"]:
        response = client.get(icon["src"])
        assert response.status_code == 200, icon["src"]
        assert response.headers["content-type"] == "image/png"
        width, height = png_size(response.content)
        assert f"{width}x{height}" == icon["sizes"], icon["src"]


@pytest.mark.parametrize("name, size", [("apple-touch-icon.png", 180), ("icon-32.png", 32)])
def test_the_other_icons_have_their_sizes(name, size):
    assert png_size((STATIC / "icons" / name).read_bytes()) == (size, size)


def test_the_service_worker_is_served_from_the_root_with_the_version(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-cache"
    assert f'"{__version__}"' in response.text
    assert "__VERSION__" not in response.text


def test_the_service_worker_never_caches_live_data(client):
    script = client.get("/sw.js").text
    for path in ("/fires", "/risk", "/weather", "/status", "/events"):
        assert f'"{path}"' in script, path
    # Live paths are returned to the browser untouched, before any caching branch.
    assert script.index("if (isLive(url)) return;") < script.index("event.respondWith")


def test_every_precached_file_exists(client):
    script = client.get("/sw.js").text
    start = script.index("const SHELL = [")
    shell = json.loads(script[start + len("const SHELL = ") : script.index("];", start) + 1].replace(",\n]", "\n]"))
    for url in shell:
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("path", ["/", "/history"])
def test_pages_link_the_manifest_icons_and_worker_script(client, path):
    html = client.get(path).text
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'name="theme-color"' in html
    assert 'rel="apple-touch-icon"' in html
    assert '<script src="/static/pwa.js" defer></script>' in html


def test_the_offline_banner_script_is_served(client):
    script = client.get("/static/pwa.js").text
    assert "serviceWorker" in script and "offline" in script and "112" in script
