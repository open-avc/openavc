"""Shared API error helpers.

The error contract, in one place so it stays one contract:

* ``detail`` is **always** a JSON string — a sentence a person can read. Anything
  a client needs to branch on rides in sibling top-level keys, via
  :class:`StructuredApiError`, so the readable half never has to be dug out of
  an object.
* A 500 never carries raw exception text. Route it through :func:`api_error`,
  which logs the real exception server-side and hands the caller a stable
  sentence instead of a stack-trace fragment or a filesystem path.
* A 4xx says what the caller got wrong, in their words. Passing an exception's
  message straight through is fine there when our own validation code raised it
  with that message written for the user.
"""

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from openavc.utils.logger import get_logger

log = get_logger(__name__)


def api_error(status_code: int, message: str, exc: Exception | None = None) -> HTTPException:
    """Build an HTTPException with a safe user-facing message, logging the full exception."""
    if exc is not None:
        log.error(f"API error ({status_code}): {message} — {type(exc).__name__}: {exc}")
    return HTTPException(status_code=status_code, detail=message)


class StructuredApiError(HTTPException):
    """An error that carries machine-readable fields *beside* the string detail.

    A few failures need more than a sentence — the theme-import collision the UI
    turns into an "overwrite?" prompt, the driver-definition save that has a list
    of validation errors to show. The temptation is to make ``detail`` an object
    and hide the sentence inside it, which is what the API used to do; then every
    error extractor needs a special case, and the ones that don't have it show
    the user raw JSON.

    So the extra data goes alongside instead::

        raise StructuredApiError(409, "A theme named 'Midnight' already exists.",
                                 code="theme_exists", theme_id="midnight")
        → 409 {"detail": "A theme named 'Midnight' already exists.",
               "code": "theme_exists", "theme_id": "midnight"}

    Every client still finds a readable string at ``detail``; the ones that care
    read the sibling keys. Rendered by ``structured_api_error_handler``,
    registered once on the app in ``main.py``.
    """

    def __init__(self, status_code: int, detail: str, **fields: Any) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.fields = fields


async def structured_api_error_handler(request: Request, exc: StructuredApiError) -> JSONResponse:
    """Render a :class:`StructuredApiError` as ``{"detail": <str>, **fields}``."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, **exc.fields},
        headers=exc.headers,
    )


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
