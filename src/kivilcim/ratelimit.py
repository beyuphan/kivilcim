"""Per-client request limits, so one visitor cannot use up the weather API's daily call quota.

A fixed one-minute window per client and bucket, kept in memory. That is enough for one process;
behind several processes each would count on its own.
"""

import threading
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

WINDOW_SECONDS = 60
PRUNE_ABOVE = 10_000  # remember at most about this many clients before dropping expired windows

# Anything that can make the server call the weather provider counts against the stricter bucket.
WEATHER_PATHS = ("/weather", "/risk")
# Pages, static files and the health check are never limited.
EXEMPT_PATHS = ("/", "/history", "/methodology", "/health", "/sw.js", "/manifest.webmanifest")
EXEMPT_PREFIXES = ("/static/",)


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, client: str, bucket: str, limit: int) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds until the window resets)."""
        now = self._clock()
        key = (client, bucket)
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= WINDOW_SECONDS:
                start, count = now, 0
            count += 1
            self._windows[key] = (start, count)
            if len(self._windows) > PRUNE_ABOVE:
                self._windows = {k: v for k, v in self._windows.items() if now - v[0] < WINDOW_SECONDS}
            retry_after = max(1, int(WINDOW_SECONDS - (now - start)) + 1)
            return count <= limit, retry_after


def bucket_for(path: str) -> str | None:
    """'weather', 'imagery', 'general', or None when the path is exempt."""
    if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
        return None
    if path == "/imagery/place" or (path.startswith("/events/") and path.endswith("/imagery")):
        return "imagery"
    if any(path == p or path.startswith(p + "/") for p in WEATHER_PATHS):
        return "weather"
    return "general"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        general_per_minute: int,
        weather_per_minute: int,
        trust_proxy: bool = False,
        limiter: RateLimiter | None = None,
        imagery_per_minute: int | None = None,
    ) -> None:
        super().__init__(app)
        self._limits = {
            "general": general_per_minute,
            "weather": weather_per_minute,
            "imagery": general_per_minute if imagery_per_minute is None else imagery_per_minute,
        }
        self._trust_proxy = trust_proxy
        self._limiter = limiter or RateLimiter()

    def _client(self, request: Request) -> str:
        if self._trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded.strip():
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        bucket = bucket_for(request.url.path)
        limit = self._limits.get(bucket, 0) if bucket else 0
        if bucket and limit > 0:
            allowed, retry_after = self._limiter.check(self._client(request), bucket, limit)
            if not allowed:
                return JSONResponse(
                    {"detail": "Too many requests. Please slow down and try again shortly."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)
