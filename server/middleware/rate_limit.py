"""
Per-IP rate limiting middleware for the OpenAVC core server.

Three tiers based on request path:
- Open:     high limit for status/health endpoints (default 120/min)
- Standard: moderate limit for general API routes   (default 60/min)
- Strict:   low limit for expensive/sensitive ops    (default 10/min)

Auth failures (401 responses) are retroactively counted against the strict
tier, providing brute-force protection even when auth is opt-in.

Disabled entirely with OPENAVC_RATE_LIMIT_ENABLED=false.
"""

import json
import math
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from server import config
from server.utils.logger import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 60.0
CLEANUP_INTERVAL = 60.0
STALE_THRESHOLD = 300.0  # 5 minutes

# Paths to skip entirely (static files, WebSocket upgrades, docs)
_SKIP_PREFIXES = ("/panel", "/programmer", "/docs", "/openapi.json", "/ws", "/isc/ws")

# Open tier paths (high limit, no auth needed)
_OPEN_EXACT = {"/api/status", "/api/health", "/api/cloud/status"}
_OPEN_PREFIXES = ("/api/library",)


def _classify(method: str, path: str) -> str:
    """Classify a request into a rate-limit tier."""
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return "skip"
    if not path.startswith("/api/"):
        return "skip"

    # Open tier
    if path in _OPEN_EXACT:
        return "open"
    if any(path.startswith(p) for p in _OPEN_PREFIXES):
        return "open"

    # Strict tier: expensive or security-sensitive operations
    if method == "POST":
        if path.startswith("/api/discovery/"):
            return "strict"
        if path.startswith("/api/devices/") and ("/command" in path or "/test" in path):
            return "strict"
        if path.endswith("/test-command"):
            return "strict"
        if path in ("/api/drivers/install", "/api/drivers/upload"):
            return "strict"
        if path.startswith("/api/cloud/"):
            return "strict"
        if path.startswith("/api/backups/") and "/restore" in path:
            return "strict"
    if method == "PUT" and path == "/api/project":
        return "strict"

    # Everything else on /api/ is standard
    return "standard"


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class _SlidingWindow:
    __slots__ = ("_timestamps", "_max_count", "_window")

    def __init__(self, max_count: int, window: float = WINDOW_SECONDS) -> None:
        self._timestamps: deque[float] = deque()
        self._max_count = max_count
        self._window = window

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def is_exceeded(self, now: float) -> bool:
        self._prune(now)
        return len(self._timestamps) >= self._max_count

    def record(self, now: float) -> None:
        self._timestamps.append(now)

    def time_until_open(self, now: float) -> float:
        self._prune(now)
        if len(self._timestamps) < self._max_count:
            return 0.0
        return self._timestamps[0] + self._window - now


class _IPBuckets:
    __slots__ = ("open", "standard", "strict", "last_seen")

    def __init__(self) -> None:
        self.open = _SlidingWindow(config.RATE_LIMIT_OPEN_PER_MINUTE)
        self.standard = _SlidingWindow(config.RATE_LIMIT_STANDARD_PER_MINUTE)
        self.strict = _SlidingWindow(config.RATE_LIMIT_STRICT_PER_MINUTE)
        self.last_seen = time.monotonic()

    def get_window(self, tier: str) -> _SlidingWindow:
        if tier == "open":
            return self.open
        if tier == "strict":
            return self.strict
        return self.standard


# Module-level state
_ip_buckets: dict[str, _IPBuckets] = {}
_last_cleanup = time.monotonic()
# Dedup log warnings: (ip, tier) -> last_warned_at
_warn_dedup: dict[tuple[str, str], float] = {}


def _cleanup(now: float) -> None:
    global _last_cleanup
    if now - _last_cleanup < CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    stale = [ip for ip, b in _ip_buckets.items() if now - b.last_seen > STALE_THRESHOLD]
    for ip in stale:
        del _ip_buckets[ip]
    stale_warns = [k for k, t in _warn_dedup.items() if now - t > STALE_THRESHOLD]
    for k in stale_warns:
        del _warn_dedup[k]


def _make_429(retry_after: float) -> Response:
    seconds = max(1, int(math.ceil(retry_after)))
    return Response(
        content=json.dumps({"detail": "Too many requests. Try again in a few seconds.", "retry_after": seconds}),
        status_code=429,
        media_type="application/json",
        headers={"Retry-After": str(seconds)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not config.RATE_LIMIT_ENABLED:
            return await call_next(request)

        # Don't rate-limit CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        tier = _classify(request.method, request.url.path)
        if tier == "skip":
            return await call_next(request)

        now = time.monotonic()
        _cleanup(now)

        client_ip = _get_client_ip(request)

        # Exempt localhost from rate limiting — primary deployment is single-user local
        if client_ip in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)
        buckets = _ip_buckets.get(client_ip)
        if buckets is None:
            buckets = _IPBuckets()
            _ip_buckets[client_ip] = buckets
        buckets.last_seen = now

        # If strict tier is exceeded, block all requests from this IP
        if buckets.strict.is_exceeded(now):
            retry = buckets.strict.time_until_open(now)
            _log_limited(client_ip, "strict", request.url.path, now)
            return _make_429(retry)

        # Check the tier-specific window
        window = buckets.get_window(tier)
        if window.is_exceeded(now):
            retry = window.time_until_open(now)
            _log_limited(client_ip, tier, request.url.path, now)
            return _make_429(retry)

        # Process the request
        response = await call_next(request)

        # Record in the appropriate bucket
        now_after = time.monotonic()
        if response.status_code == 401:
            # Auth failure counts toward strict tier
            buckets.strict.record(now_after)
        else:
            window.record(now_after)

        return response


def _log_limited(ip: str, tier: str, path: str, now: float) -> None:
    key = (ip, tier)
    last = _warn_dedup.get(key, 0.0)
    if now - last > 30.0:
        log.warning("Rate limited %s on %s tier (path: %s)", ip, tier, path)
        _warn_dedup[key] = now
