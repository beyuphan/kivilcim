"""An installed copy (a wheel, a container image) must carry every file the site serves."""

import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).parent.parent
STATIC = ROOT / "src" / "kivilcim" / "static"


def test_every_static_file_is_listed_in_the_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["kivilcim"]
    missing = [
        str(path.relative_to(STATIC.parent))
        for path in STATIC.rglob("*")
        if path.is_file() and not any(fnmatch(path.relative_to(STATIC.parent).as_posix(), p) for p in patterns)
    ]
    assert missing == [], f"not packaged: {missing}"


def test_the_pages_and_icons_the_site_needs_exist():
    for name in ("index.html", "history.html", "methodology.html", "sw.js", "pwa.js", "manifest.webmanifest", "ilceler.json"):
        assert (STATIC / name).is_file(), name
    assert len(list((STATIC / "icons").glob("*.png"))) >= 5
