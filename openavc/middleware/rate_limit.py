"""
Per-IP rate limiting middleware for the OpenAVC core server.

Four tiers based on request path:
- Open:     high limit for status/health endpoints (default 120/min)
- Standard: moderate limit for general API routes   (default 60/min)
- Control:  high limit for authenticated commissioning ops (default 120/min) —
            device commands/tests, discovery, driver install, project save.
            These are hot paths during normal commissioning from a remote
            Programmer (a volume ramp alone can fire many commands), so they
            must not share the strict security budget. Every route here
            requires programmer auth, and 401s feed the brute-force counter
            instead of the tier window, so the higher budget is only
            reachable with valid credentials.
- Strict:   low limit for security-sensitive ops     (default 10/min) —
            login, cloud pairing, backup restore.

Auth failures (401 responses) feed a dedicated brute-force counter that is
checked on every request at the strict rate, regardless of the endpoint's tier
— so credential probing is throttled even against a standard-tier protected
endpoint, and even when auth is opt-in.

Disabled entirely with OPENAVC_RATE_LIMIT_ENABLED=false.
"""

import json
import math
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from openavc import config
from openavc.utils.logger import get_logger
from openavc.utils.request_origin import LOOPBACK_HOSTS, is_tunneled_request

log = get_logger(__name__)

# Bucket key standing in for "arrived over the cloud remote-UI tunnel". Not a
# possible client IP, so it can never collide with a real one.
TUNNEL_BUCKET_KEY = "cloud-tunnel"

WINDOW_SECONDS = 60.0
CLEANUP_INTERVAL = 60.0
STALE_THRESHOLD = 300.0  # 5 minutes

# Paths to skip entirely (static files, WebSocket upgrades, docs).
# /api/push/ is device push traffic (webhooks / GENA NOTIFY): legitimate
# event bursts exceed any human-scale per-minute budget, and a 429 silently
# drops device state (Cisco codecs even deactivate a feedback slot after
# delivery failures). Same trusted-VLAN posture as UDP device control.
_SKIP_PREFIXES = ("/panel", "/programmer", "/docs", "/openapi.json", "/ws", "/isc/ws", "/api/push/")

# The panel's static render path, matched on whole leading SEGMENTS with one
# wildcard for the id in the middle. Exempt from rate limiting entirely, for the
# same reason /api/push/ is: the budget is spent by a machine doing something
# ordinary, and the 429 is silent.
#
# Measured on a real remote tablet (v1-readiness Q-101): a cold load of a
# realistic seven-page room panel — 46 distinct image assets, two custom
# controls — spends 59 of the standard tier's 60/min, one request short of a
# 429. Loopback is exempt, so a kiosk on the host never sees it and the dev box
# cannot reproduce it; a remote wall tablet is the only place it shows up, and
# the failure is a panel that draws half its tiles with nothing said. Raising
# the tier was the obvious answer and is the wrong one: open is only 120/min,
# and a panel that reaches 59 reaches 120 with a few more sources.
#
# GET (and HEAD) ONLY, which is the whole subtlety. The open serving route and
# an authenticated DELETE share the path shape --
# `/api/projects/{id}/assets/{filename}` is served open and deleted with a
# credential -- so exempting by path alone would hand the delete an unlimited
# budget. Nothing here mints or checks a credential; `/api/plugins/{id}/ext-token`
# is deliberately NOT in this list, and neither is `/api/themes/{id}`, whose
# authenticated `/export` sibling shares its prefix and which costs a panel one
# request per start anyway.
_STATIC_RENDER_FAMILIES = (
    ("api", "projects", "*", "assets"),
    ("api", "projects", "*", "ui"),
    ("api", "plugins", "*", "panel"),
    ("api", "plugins", "*", "files"),
)


def _is_static_render_path(path: str) -> bool:
    """True for a GET of a file under one of the render-path families.

    Requires at least one segment past the family root, so the collection
    endpoints (`/api/projects/X/assets`, which lists, and `/api/projects/X/ui`)
    keep their own budget -- only the per-file reads are exempt.
    """
    segments = [s for s in path.split("/") if s]
    for family in _STATIC_RENDER_FAMILIES:
        if len(segments) <= len(family):
            continue
        if all(pat == "*" or pat == seg for pat, seg in zip(family, segments)):
            return True
    return False

# Open tier paths (high limit, no auth needed). Every entry here is also on the
# unauthenticated open router — tests/test_rate_limit_tiers.py holds that line,
# because an open-tier path that *does* require auth would be handing an
# anonymous caller the larger budget for nothing.
_OPEN_EXACT = {
    "/api/status",
    "/api/health",
    "/api/cloud/status",
    "/api/startup-status",
    "/api/auth/required",
    "/api/setup/status",
    "/api/certificate",
}
_OPEN_PREFIXES = ()

# Control-tier request families, matched on whole leading path SEGMENTS.
#
# Two things this buys over the substring list it replaces. First, a tier can
# no longer depend on a value *inside* the path: a device action named
# "run_test" used to land in a different bucket than one named "discover",
# purely because the classifier looked for "/test" anywhere in the string.
# Second, a new sibling route inherits the right tier the day it is added —
# `/api/drivers/upload-bundle` spent its whole life in the wrong bucket only
# because it was added after the list naming its two siblings.
#
# Everything under these roots is a commissioning operation: firing a device,
# testing a connection, installing or editing a driver, running a scan. They
# are hot during setup from a remote Programmer, they all require programmer
# auth, and their 401s feed the brute-force counter rather than this window —
# so the higher budget is only ever reachable with valid credentials.
_CONTROL_ROOTS = (
    "/api/devices",             # command, test, send-raw, ir-emit, ir-import,
                                # actions, settings, children, lifecycle
    "/api/discovery",
    "/api/drivers",             # install, upload, upload-bundle, update
    "/api/driver-definitions",
    "/api/python-drivers",
    "/api/isc",                 # send, broadcast, command
)


def _under(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` itself or sits beneath it.

    Segment-bounded on purpose: ``/api/drivers`` must not match a future
    ``/api/drivers-export``.
    """
    return path == root or path.startswith(root + "/")

# Registered prefixes outside /api/ that classify as the standard tier.
# Plugin guest aliases (api/plugin_ext.py) register here when mounted so
# their traffic is rate-limited and their 401s feed the brute-force counter
# — every other non-/api path is skipped by design.
_extra_standard_prefixes: set[str] = set()


def register_standard_prefix(prefix: str) -> None:
    """Classify paths under ``prefix`` (segment-bounded) as the standard tier."""
    _extra_standard_prefixes.add(prefix)


def unregister_standard_prefix(prefix: str) -> None:
    _extra_standard_prefixes.discard(prefix)


def _classify(method: str, path: str) -> str:
    """Classify a request into a rate-limit tier."""
    if any(path == p or path.startswith(p + "/") for p in _extra_standard_prefixes):
        return "standard"
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return "skip"
    if method in ("GET", "HEAD") and _is_static_render_path(path):
        return "skip"
    if not path.startswith("/api/"):
        return "skip"

    # Open tier
    if path in _OPEN_EXACT:
        return "open"
    if any(path.startswith(p) for p in _OPEN_PREFIXES):
        return "open"

    # Strict tier: security-sensitive operations
    if method == "POST":
        if path == "/api/auth/session":
            return "strict"
        if path.startswith("/api/cloud/"):
            return "strict"
        if path.startswith("/api/backups/") and "/restore" in path:
            return "strict"

    # Control tier: authenticated commissioning operations. Hot during normal
    # setup from a remote Programmer, so they get their own high budget
    # instead of draining (or being 429'd by) the strict security window.
    # Reads are left on the standard budget — it is the writes that come in
    # bursts (a volume ramp, a driver test loop), and none of these families
    # is polled over HTTP (scan progress arrives over the WebSocket).
    if method != "GET":
        if path == "/api/project" and method == "PUT":
            return "control"
        if any(_under(path, root) for root in _CONTROL_ROOTS):
            return "control"

    # Everything else on /api/ is standard
    return "standard"


def _get_client_ip(request: Request) -> str:
    # Cloud-tunnel traffic first: it arrives from loopback, so without this it
    # would take the exemption below and get no limit and no brute-force
    # counter at all — an unthrottled channel for guessing the very password
    # it is being asked for. One key for the whole tunnel, not one per caller:
    # everything the cloud forwards is attacker-supplied (including any
    # X-Forwarded-For), so a per-caller key would just be a knob for spreading
    # guesses across buckets. The budget is per-channel by design. The tunnel
    # is one operator driving one browser, and it lands on the same standard
    # and control tiers a remote Programmer on the LAN already lives with, so
    # this is parity with direct remote access rather than a new ceiling.
    if is_tunneled_request(request):
        return TUNNEL_BUCKET_KEY
    # Only trust X-Forwarded-For when explicitly configured to sit behind a
    # known reverse proxy. Otherwise a client could set the header to spoof its
    # source IP — dodging per-IP limits AND the 127.0.0.1 rate-limit exemption
    # below, which would defeat the 401 brute-force counter. Default to the
    # real TCP peer.
    if config.TRUST_FORWARDED_FOR:
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
    __slots__ = ("open", "standard", "control", "strict", "auth_fail", "last_seen")

    def __init__(self) -> None:
        self.open = _SlidingWindow(config.RATE_LIMIT_OPEN_PER_MINUTE)
        self.standard = _SlidingWindow(config.RATE_LIMIT_STANDARD_PER_MINUTE)
        self.control = _SlidingWindow(config.RATE_LIMIT_CONTROL_PER_MINUTE)
        self.strict = _SlidingWindow(config.RATE_LIMIT_STRICT_PER_MINUTE)
        # 401 brute-force counter: the strict rate, but checked on EVERY request
        # regardless of tier. Kept separate from the strict-tier endpoint window
        # so legitimate sensitive-op traffic (which fills ``strict``) doesn't
        # feed the brute-force limit and vice versa.
        self.auth_fail = _SlidingWindow(config.RATE_LIMIT_STRICT_PER_MINUTE)
        self.last_seen = time.monotonic()

    def get_window(self, tier: str) -> _SlidingWindow:
        if tier == "open":
            return self.open
        if tier == "control":
            return self.control
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
    if len(_warn_dedup) > 10000:
        oldest = sorted(_warn_dedup, key=_warn_dedup.get)[:5000]  # type: ignore[arg-type]
        for k in oldest:
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

        # Exempt localhost from rate limiting — primary deployment is single-user
        # local. Tunneled traffic never lands here: _get_client_ip has already
        # given it its own key.
        if client_ip in LOOPBACK_HOSTS:
            return await call_next(request)
        buckets = _ip_buckets.get(client_ip)
        if buckets is None:
            buckets = _IPBuckets()
            _ip_buckets[client_ip] = buckets
        buckets.last_seen = now

        window = buckets.get_window(tier)

        # Brute-force gate: an IP that has piled up 401s is throttled at the
        # strict rate on EVERY tier. Credential probing usually targets a
        # standard-tier protected endpoint, whose own (higher) window is far too
        # loose to slow guessing — this dedicated counter closes that gap.
        if buckets.auth_fail.is_exceeded(now):
            retry = buckets.auth_fail.time_until_open(now)
            _log_limited(client_ip, "auth_fail", request.url.path, now)
            return _make_429(retry)

        # Then the request's own tier window.
        if window.is_exceeded(now):
            retry = window.time_until_open(now)
            _log_limited(client_ip, tier, request.url.path, now)
            return _make_429(retry)

        # Process the request
        response = await call_next(request)

        # Record in the appropriate bucket
        now_after = time.monotonic()
        if response.status_code == 401:
            # Auth failure feeds the brute-force counter, not the tier window.
            buckets.auth_fail.record(now_after)
        else:
            window.record(now_after)

        return response


def _log_limited(ip: str, tier: str, path: str, now: float) -> None:
    key = (ip, tier)
    last = _warn_dedup.get(key, 0.0)
    if now - last > 30.0:
        log.warning("Rate limited %s on %s tier (path: %s)", ip, tier, path)
        _warn_dedup[key] = now
