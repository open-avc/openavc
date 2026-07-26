"""Errors that carry machine-readable fields keep ``detail`` a readable string.

A few failures need more than a sentence: the theme-import collision the UI turns
into an "overwrite?" prompt, the driver-definition save with a list of validation
errors. Those used to make ``detail`` an object and bury the sentence inside it,
so every error extractor needed a special case and the ones without it showed the
user raw JSON. ``StructuredApiError`` puts the extra fields *beside* ``detail``
instead. These tests pin that rendering and the two live endpoints that use it.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.errors import StructuredApiError, structured_api_error_handler


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(StructuredApiError, structured_api_error_handler)

    @app.get("/collide")
    async def collide():
        raise StructuredApiError(
            409, "A custom theme with id 'midnight' already exists.",
            code="theme_exists", theme_id="midnight", name="Midnight",
        )

    @app.get("/plain")
    async def plain():
        raise StructuredApiError(422, "Nothing structured here")

    return app


def test_fields_ride_beside_a_string_detail() -> None:
    resp = TestClient(_app()).get("/collide")
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "A custom theme with id 'midnight' already exists."
    assert isinstance(body["detail"], str)
    assert body["code"] == "theme_exists"
    assert body["theme_id"] == "midnight"
    assert body["name"] == "Midnight"


def test_reads_like_any_other_error_with_no_extra_fields() -> None:
    body = TestClient(_app()).get("/plain").json()
    assert body == {"detail": "Nothing structured here"}


# --- The live endpoints that use it ---


@pytest.fixture
def client():
    from server.api import rest, ws
    from server.main import app
    from tests.test_api_endpoints import _make_mock_engine

    engine = _make_mock_engine()
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app)
    rest.set_engine(None)
    ws.set_engine(None)


def test_invalid_driver_definition_detail_lists_every_error(client) -> None:
    """The detail alone has to be enough — a client that only reads ``detail``
    still shows the author every problem, and ``errors`` is there for anything
    that wants to render them one at a time."""
    # Passes the request model, fails the driver-definition validator: a driver
    # with no commands and a nonsense transport.
    resp = client.post(
        "/api/driver-definitions",
        json={"id": "Acme Widget!", "name": "Acme Widget", "transport": "carrier_pigeon"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], str)
    assert body["errors"], "the machine-readable half should carry the same errors"
    for message in body["errors"]:
        assert message in body["detail"]
