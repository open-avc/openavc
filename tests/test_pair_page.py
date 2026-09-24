"""The /pair landing page sends each scanner to the app route that works for it."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest
from openavc.api.routes.pair import (
    _APP_PAGE_URL,
    _APP_STORE_URL,
    _PLAY_STORE_URL,
    app_download_url,
)
from openavc.main import app

ANDROID_PHONE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)
FIRE_TABLET = (
    "Mozilla/5.0 (Linux; Android 9; KFTRWI) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Silk/128.1.1 like Chrome/128.0.0.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)
IPAD_MOBILE_SITE = (
    "Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)
# iPadOS Safari asks for the desktop site by default and says Macintosh.
IPAD_DESKTOP_SITE = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Safari/605.1.15"
)
WINDOWS_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (ANDROID_PHONE, _PLAY_STORE_URL),
        (IPHONE, _APP_STORE_URL),
        (IPAD_MOBILE_SITE, _APP_STORE_URL),
        (FIRE_TABLET, _APP_PAGE_URL),
        (IPAD_DESKTOP_SITE, _APP_PAGE_URL),
        (WINDOWS_DESKTOP, _APP_PAGE_URL),
        ("", _APP_PAGE_URL),
    ],
)
def test_app_link_matches_the_scanning_device(user_agent, expected):
    assert app_download_url(user_agent) == expected


@pytest.fixture
def client():
    engine = MagicMock()
    engine.get_status.return_value = {"project_name": "Acme Hall", "version": "0.0.0-test"}
    rest.set_engine(engine)
    yield TestClient(app)
    rest.set_engine(None)


def test_pair_page_links_the_store_for_the_request(client):
    android = client.get("/pair", headers={"User-Agent": ANDROID_PHONE})
    assert android.status_code == 200
    assert f'href="{_PLAY_STORE_URL}"' in android.text
    assert "Acme Hall" in android.text

    desktop = client.get("/pair", headers={"User-Agent": WINDOWS_DESKTOP})
    assert f'href="{_APP_PAGE_URL}"' in desktop.text
