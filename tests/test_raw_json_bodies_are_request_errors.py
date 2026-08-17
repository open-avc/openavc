"""A bad body is the caller's mistake, and the reply should say which field.

`POST /api/project/open-from-library` was found answering a missing field with a
500 that named nothing, and it turned out to be four routes sharing one shape:
read the raw JSON, construct the model by hand, let pydantic's ValidationError
escape. Those four were fixed; these are the rest of the same shape, found the
same way -- `POST /api/cloud/pair` with `pairing_token` instead of `token` cost
another read of another traceback at a bench.

The half a narrower fix keeps missing is a body that is not an object at all:
`Model(**body)` raises TypeError there rather than ValidationError, so it stays
a 500 even after the missing-field half is fixed. `isc_broadcast` had a third
version of it again -- an explicit 422 for the missing field, and `body.get` on
a list for everything else.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client():
    from openavc.api.routes import cloud as cloud_routes
    from openavc.api.routes import isc as isc_routes

    app = FastAPI()
    app.include_router(cloud_routes.router, prefix="/api")
    app.include_router(isc_routes.router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize(
    "path,body,missing",
    [
        ("/api/cloud/pair", {"pairing_token": "x"}, "token"),
        ("/api/isc/send", {"event": "e"}, "instance_id"),
        ("/api/isc/broadcast", {"payload": {}}, "event"),
        ("/api/isc/command", {"instance_id": "i", "command": "on"}, "device_id"),
    ],
)
def test_a_missing_field_is_422_and_is_named(path, body, missing):
    response = _client().post(path, json=body)
    assert response.status_code == 422, response.text
    assert missing in response.text


@pytest.mark.parametrize(
    "path",
    ["/api/cloud/pair", "/api/isc/send", "/api/isc/broadcast", "/api/isc/command"],
)
def test_a_body_that_is_not_an_object_is_also_422(path):
    response = _client().post(path, json=["nope"])
    assert response.status_code == 422, response.text


def test_a_good_body_gets_past_validation():
    """The guard above is worthless if the routes now reject everything.

    ISC is off in this bare app, so a well-formed body reaches the handler and
    meets its own 503 -- which is the handler answering, not the request being
    refused.
    """
    response = _client().post(
        "/api/isc/send",
        json={"instance_id": "peer", "event": "hello", "payload": {}},
    )
    assert response.status_code == 503, response.text
