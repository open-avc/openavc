"""
Shared engine state for REST API route modules.

Holds the engine reference and common helpers used across multiple
route files. Extracted to avoid circular imports between rest.py
and the domain-specific route modules.
"""

import time as _time_mod

from fastapi import HTTPException

from server.utils.logger import get_logger

log = get_logger(__name__)

# The engine is injected by main.py after creation
_engine = None


def set_engine(engine) -> None:
    """Set the engine reference (called by main.py at startup)."""
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not started")
    return _engine


def get_engine_optional():
    """Return the engine, or None if not started yet, without raising.

    For non-HTTP callers (the WebSocket handler) that need to decide their
    own not-ready behavior — e.g. close the socket with a status code rather
    than surface an HTTPException. Lets ws.py share this single engine slot
    instead of holding its own, so main.py can't set one and miss the other.
    """
    return _engine


# --- Runaway protection for manual/operator "fire this now" endpoints ---
#
# A short per-key debounce shared by every path that fires a macro or trigger
# on demand — the REST test/run endpoints AND the AI tools — so rapid manual
# or agent-driven firing of the *same* macro/trigger is throttled uniformly,
# no matter which surface it comes through. (Runtime automation — panel button
# presses, triggers, scripts — does not go through these paths.) Keep the
# transport-neutral primitive (`_test_call_retry_after`) separate from the HTTP
# wrapper (`_rate_limit_test`) so the cloud tools can share the window without
# importing HTTP concerns or raising HTTPException.

_test_endpoint_last_call: dict[str, float] = {}
_TEST_RATE_LIMIT_SECONDS = 2.0


def _test_call_retry_after(endpoint_key: str) -> float:
    """Transport-neutral debounce for on-demand macro/trigger firing.

    Returns 0.0 if a call for ``endpoint_key`` is allowed now (and records it),
    or the number of seconds still to wait if it fell inside the window. The
    recorded timestamp is the last *allowed* call, so a burst is throttled
    against the last one that got through, not the last attempt.
    """
    now = _time_mod.monotonic()
    last = _test_endpoint_last_call.get(endpoint_key, 0.0)
    remaining = _TEST_RATE_LIMIT_SECONDS - (now - last)
    if remaining > 0:
        return remaining
    _test_endpoint_last_call[endpoint_key] = now
    return 0.0


def _rate_limit_test(endpoint_key: str) -> None:
    """Raise 429 if the same test/run endpoint was called within the window."""
    if _test_call_retry_after(endpoint_key) > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests — wait {_TEST_RATE_LIMIT_SECONDS:.0f}s between test calls",
        )
