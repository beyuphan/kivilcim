"""Security headers for every response, and a Content-Security-Policy for the web pages."""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Satellite image tiles for the before and after view.
IMAGERY_HOST = "https://gibs.earthdata.nasa.gov"
# The generated API documentation loads its own assets, so it gets no page policy.
NO_PAGE_POLICY = ("/docs", "/redoc")
# The embeddable map is the only page that may be shown inside other sites.
EMBED_PATH = "/embed"

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=()",
}


def tile_origin(tile_url: str) -> str:
    """The origin of a map tile URL template, with a {s} subdomain turned into a wildcard."""
    parts = urlsplit(tile_url)
    host = (parts.netloc or "").replace("{s}", "*")
    return f"{parts.scheme}://{host}" if parts.scheme and host else ""


def build_csp(tile_url: str) -> str:
    """Scripts and the map library come from this site only. Styles may be inline (the map library
    sets some), images may also come from the map tile host and the satellite imagery host."""
    tiles = tile_origin(tile_url)
    img = " ".join(filter(None, ["'self'", "data:", "blob:", IMAGERY_HOST, tiles]))
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            f"img-src {img}",
            "connect-src 'self'",
            "worker-src 'self'",
            "manifest-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, csp: str) -> None:
        super().__init__(app)
        self._csp = csp

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in BASE_HEADERS.items():
            response.headers.setdefault(name, value)
        is_page = response.headers.get("content-type", "").startswith("text/html")
        embedded = request.url.path == EMBED_PATH
        if embedded:  # the one page other sites may show in a frame
            del response.headers["X-Frame-Options"]
        if is_page and not request.url.path.startswith(NO_PAGE_POLICY):
            csp = self._csp.replace("frame-ancestors 'none'", "frame-ancestors *") if embedded else self._csp
            response.headers.setdefault("Content-Security-Policy", csp)
        return response
