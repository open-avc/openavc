"""Which responses the browser must revalidate before reusing.

The Programmer and Simulator pages went out with no Cache-Control at all, so a
browser reused one it had fetched days earlier: a Simulator window opened
after an update ran the previous build. Their hashed assets may be cached;
the HTML page that names them may not.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openavc.main import app, revalidate_header


@pytest.mark.parametrize("path, content_type", [
    ("/programmer/", "text/html; charset=utf-8"),
    ("/programmer/index.html", "text/html; charset=utf-8"),
    ("/simulator/", "text/html; charset=utf-8"),
    ("/panel", "text/html; charset=utf-8"),
    ("/panel/panel.js", "text/javascript; charset=utf-8"),
])
def test_pages_are_revalidated(path, content_type):
    assert revalidate_header(path, content_type) == "no-cache"


@pytest.mark.parametrize("path, content_type", [
    ("/programmer/assets/index-acme1234.js", "text/javascript; charset=utf-8"),
    ("/programmer/assets/index-acme1234.css", "text/css; charset=utf-8"),
    ("/simulator/assets/index-acme1234.js", "text/javascript; charset=utf-8"),
    ("/simulator/api/devices", "application/json"),
    ("/api/status", "application/json"),
])
def test_hashed_assets_and_api_answers_are_left_alone(path, content_type):
    assert revalidate_header(path, content_type) is None


def test_the_header_reaches_a_real_response():
    response = TestClient(app).get("/panel/panel.css")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
