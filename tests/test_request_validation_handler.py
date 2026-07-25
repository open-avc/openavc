"""Request-validation (422) errors are returned as a canonical string detail.

FastAPI's default 422 body is a list of ``{loc, msg, type}`` dicts, which the
frontend error extractor can't parse — it reaches the integrator as raw Pydantic
JSON. The app registers ``request_validation_exception_handler`` to flatten that
into the platform's ``{"detail": "<field>: <msg>"}`` shape. These tests cover the
pure formatter and the handler end-to-end.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from server.api.errors import (
    format_request_validation_errors,
    request_validation_exception_handler,
)


# ---------------------------------------------------------------------------
# Pure formatter
# ---------------------------------------------------------------------------

def test_formatter_strips_location_marker_and_joins() -> None:
    errors = [
        {"loc": ("body", "name"), "msg": "Field required", "type": "missing"},
        {"loc": ("body", "count"), "msg": "Input should be a valid integer", "type": "int_parsing"},
    ]
    assert format_request_validation_errors(errors) == (
        "name: Field required; count: Input should be a valid integer"
    )


def test_formatter_keeps_nested_path() -> None:
    errors = [{"loc": ("body", "config", "port"), "msg": "Input should be a valid integer", "type": "int_parsing"}]
    assert format_request_validation_errors(errors) == "config.port: Input should be a valid integer"


def test_formatter_query_marker_stripped() -> None:
    errors = [{"loc": ("query", "limit"), "msg": "Field required", "type": "missing"}]
    assert format_request_validation_errors(errors) == "limit: Field required"


def test_formatter_body_only_loc_becomes_request() -> None:
    errors = [{"loc": ("body",), "msg": "Input should be a valid dictionary", "type": "dict_type"}]
    assert format_request_validation_errors(errors) == "request: Input should be a valid dictionary"


def test_formatter_empty_returns_generic() -> None:
    assert format_request_validation_errors([]) == "Invalid request"


# ---------------------------------------------------------------------------
# Handler end-to-end (minimal app with a typed endpoint)
# ---------------------------------------------------------------------------

class _Body(BaseModel):
    name: str
    count: int


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

    @app.post("/thing")
    async def _make_thing(body: _Body) -> dict:
        return {"ok": True}

    return app


def test_missing_fields_return_string_detail() -> None:
    resp = TestClient(_app()).post("/thing", json={})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # The whole point: a string, never the raw list of Pydantic dicts.
    assert isinstance(detail, str)
    assert "name" in detail and "count" in detail


def test_wrong_type_returns_string_detail() -> None:
    resp = TestClient(_app()).post("/thing", json={"name": "x", "count": "not-an-int"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "count" in detail


def test_malformed_json_returns_string_detail() -> None:
    resp = TestClient(_app()).post(
        "/thing", content="{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
