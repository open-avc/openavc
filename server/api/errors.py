"""Shared API error helpers."""

from fastapi import HTTPException

from server.utils.logger import get_logger

log = get_logger(__name__)


def api_error(status_code: int, message: str, exc: Exception | None = None) -> HTTPException:
    """Build an HTTPException with a safe user-facing message, logging the full exception."""
    if exc is not None:
        log.error(f"API error ({status_code}): {message} — {type(exc).__name__}: {exc}")
    return HTTPException(status_code=status_code, detail=message)
