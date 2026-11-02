"""Security headers for every response, and a Content-Security-Policy for the web pages."""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# The map library is loaded from this host (with a pinned version and an integrity hash).
SCRIPT_HOST = "https://unpkg.com"
# Satellite image tiles for the before and after view.
IMAGERY_HOST = "https://gibs.earthdata.nasa.gov"
# The generated API documentation loads its own assets, so it gets no page policy.
NO_PAGE_POLICY = ("/docs", "/redoc")

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
    """The pages use small inline scripts and styles, so those are allowed, but every other
    source is limited to this site, the map library host and the map tile host."""
    tiles = tile_origin(tile_url)
    img = " ".join(filter(None, ["'self'", "data:", "blob:", SCRIPT_HOST, IMAGERY_HOST, tiles]))
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'unsafe-inline' {SCRIPT_HOST}",
            f"style-src 'self' 'unsafe-inline' {SCRIPT_HOST}",
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
        if is_page and not request.url.path.startswith(NO_PAGE_POLICY):
            response.headers.setdefault("Content-Security-Policy", self._csp)
        return response
