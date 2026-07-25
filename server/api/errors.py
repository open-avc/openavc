"""Shared API error helpers."""

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from server.utils.logger import get_logger

log = get_logger(__name__)


def api_error(status_code: int, message: str, exc: Exception | None = None) -> HTTPException:
    """Build an HTTPException with a safe user-facing message, logging the full exception."""
    if exc is not None:
        log.error(f"API error ({status_code}): {message} — {type(exc).__name__}: {exc}")
    return HTTPException(status_code=status_code, detail=message)


def format_request_validation_errors(errors: list) -> str:
    """Flatten FastAPI/Pydantic request-validation errors into one readable string.

    The raw 422 body is a list of ``{"loc", "msg", "type"}`` dicts; this turns it
    into the platform's canonical ``"<field>: <msg>"`` shape (multiple errors
    joined by ``"; "``) so a validation failure reads the same as any other
    ``HTTPException(status, "message")`` — a single string ``detail`` the
    frontend error extractor already understands.
    """
    parts: list[str] = []
    for err in errors:
        loc = list(err.get("loc", ()))
        # Drop the leading location marker (body/query/path/header/cookie) so
        # the field reads as the user named it; keep any nested path after it.
        if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]
        field = ".".join(str(part) for part in loc) if loc else "request"
        message = err.get("msg") or "invalid value"
        parts.append(f"{field}: {message}")
    return "; ".join(parts) if parts else "Invalid request"


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return request-validation failures in the canonical ``{"detail": <str>}``
    shape instead of FastAPI's default list-of-dicts 422 body.

    Without this, a malformed body on a typed endpoint reaches the integrator as
    a wall of raw Pydantic JSON — the one error class beginners hit most has the
    worst presentation. Registered once on the app in ``main.py``.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": format_request_validation_errors(exc.errors())},
    )
