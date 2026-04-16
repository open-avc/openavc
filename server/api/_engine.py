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


# --- Rate limiting for expensive test endpoints ---

_test_endpoint_last_call: dict[str, float] = {}
_TEST_RATE_LIMIT_SECONDS = 2.0


def _rate_limit_test(endpoint_key: str) -> None:
    """Raise 429 if the same test endpoint was called within the rate limit window."""
    now = _time_mod.monotonic()
    last = _test_endpoint_last_call.get(endpoint_key, 0.0)
    if now - last < _TEST_RATE_LIMIT_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests — wait {_TEST_RATE_LIMIT_SECONDS:.0f}s between test calls",
        )
    _test_endpoint_last_call[endpoint_key] = now
