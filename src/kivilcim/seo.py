"""What search engines and chat apps see of a page: its title, description, preview picture, and for a
province a short text. The map itself is drawn in the browser, so without this a link to a fire or a
province would show nothing useful when shared or searched for."""

import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path

STATIC_JSON = Path(__file__).parent / "static" / "ilceler.json"
_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇâîû", "iisSgGuUoOcCaiu")


def slugify(name: str) -> str:
    """'Şanlıurfa' -> 'sanliurfa', 'Kahramanmaraş' -> 'kahramanmaras'."""
    return re.sub(r"[^a-z0-9]+", "-", name.translate(_FOLD).lower()).strip("-")


@lru_cache(maxsize=1)
def provinces() -> list[dict]:
    """The 81 provinces with their slug, center and district names."""
    data = json.loads(STATIC_JSON.read_text(encoding="utf-8"))
    return [
        {
            "name": p["name"],
            "slug": slugify(p["name"]),
            "lat": p["lat"],
            "lon": p["lon"],
            "districts": [{"name": d["name"], "lat": d["lat"], "lon": d["lon"]} for d in p["districts"]],
        }
        for p in data["provinces"]
    ]


def province_by_slug(slug: str) -> dict | None:
    return next((p for p in provinces() if p["slug"] == slug), None)


def head_tags(base: str, path: str, title: str, description: str, image: str | None = None) -> str:
    """The preview tags (Open Graph and Twitter) and the canonical address, escaped for HTML."""
    url = f"{base}{path}"
    tags = [
        f'<link rel="canonical" href="{escape(url)}">',
        '<meta property="og:site_name" content="Kıvılcım">',
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="tr_TR">',
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:description" content="{escape(description)}">',
        f'<meta property="og:url" content="{escape(url)}">',
        f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">',
        f'<meta name="twitter:title" content="{escape(title)}">',
        f'<meta name="twitter:description" content="{escape(description)}">',
    ]
    if image:
        tags += [
            f'<meta property="og:image" content="{escape(image)}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta name="twitter:image" content="{escape(image)}">',
        ]
    else:
        icon = f"{base}/static/icons/icon-512.png"
        tags += [f'<meta property="og:image" content="{escape(icon)}">', f'<meta name="twitter:image" content="{escape(icon)}">']
    return "\n".join(tags)


def apply_head(html: str, title: str, description: str, tags: str) -> str:
    """Put a page's own title, description and preview tags into the shared HTML."""
    html = re.sub(r"<title>.*?</title>", lambda _: f"<title>{escape(title)}</title>", html, count=1, flags=re.S)
    html = re.sub(
        r'<meta name="description" content=".*?">',
        lambda _: f'<meta name="description" content="{escape(description)}">',
        html,
        count=1,
        flags=re.S,
    )
    return html.replace("</head>", f"{tags}\n</head>", 1)
