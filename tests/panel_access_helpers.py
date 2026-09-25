"""Shared helpers for the panel approval tests.

A claimed instance (a password is set, so nothing is admitted by the
dev-checkout rule), a real engine whose panel device store lives in the
test's own directory, the access mode set for one test and put back, and an
ASGI wrapper that makes the app see whatever socket peer the test names. The
two fixtures (``claimed_engine``, ``access_mode``) are registered in
``tests/conftest.py`` and delegate to the generators here.
"""

from __future__ import annotations

import json
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient

import openavc.api.auth as auth_mod
from openavc.api import rest, ws
from openavc.core.engine import Engine
from openavc.core.panel_devices import PanelDeviceStore
from openavc.core.project_loader import load_project
from openavc.main import app
from openavc.system_config import get_system_config

PASSWORD = "panel-approval-pw"
SPACE_NAME = "Executive Boardroom"

TEST_PROJECT = {
    "project": {"id": "panel_approval_test", "name": SPACE_NAME},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {
        "pages": [
            {
                "id": "main",
                "name": "Main",
                "grid": {"columns": 12, "rows": 8},
                "elements": [],
            }
        ],
        "settings": {"theme_id": "dark-default"},
    },
}


def make_engine(tmp_path) -> Engine:
    project_path = tmp_path / "project.avc"
    project_path.write_text(json.dumps(TEST_PROJECT), encoding="utf-8")
    engine = Engine(str(project_path))
    engine.project = load_project(str(project_path))
    engine._running = True
    engine.panel_devices = PanelDeviceStore(tmp_path / "panel_devices.json")
    return engine


def claimed_engine_fixture(tmp_path, monkeypatch):
    """A real engine wired into the app, on an instance with a password."""
    engine = make_engine(tmp_path)
    rest.set_engine(engine)
    ws.set_engine(engine)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: PASSWORD)
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    yield engine
    rest.set_engine(None)
    ws.set_engine(None)


def access_mode_fixture():
    """Set ``panels.access`` for one test and restore every layer after it,
    the file included, so a PATCH in one test cannot leak into the next."""
    cfg = get_system_config()
    saved_data = dict(cfg._data.get("panels", {}))
    saved_file_data = dict(cfg._file_data.get("panels", {}))
    saved_bytes = cfg.file_path.read_bytes() if cfg.file_path.exists() else None

    def _set(mode: str) -> None:
        cfg.set("panels", "access", mode)

    yield _set
    cfg._data["panels"] = saved_data
    cfg._file_data["panels"] = saved_file_data
    if saved_bytes is None:
        cfg.file_path.unlink(missing_ok=True)
    else:
        cfg.file_path.write_bytes(saved_bytes)


def peer_app(host: str):
    """The app as seen from a socket peer at ``host``."""

    async def _app(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = (host, 50000)
        await app(scope, receive, send)

    return _app


def lan_client(**kwargs) -> TestClient:
    return TestClient(app, **kwargs)


def loopback_client(**kwargs) -> TestClient:
    return TestClient(peer_app("127.0.0.1"), **kwargs)


TUNNEL_HEADERS = {"x-openavc-tunneled": "1"}


def set_cookie_of(resp) -> SimpleCookie | None:
    raw = resp.headers.get("set-cookie")
    if raw is None:
        return None
    jar: SimpleCookie = SimpleCookie()
    jar.load(raw)
    return jar


def cookie_header(engine: Engine, value: str) -> dict[str, str]:
    from openavc.api.panel_access import cookie_name

    return {"cookie": f"{cookie_name(engine.instance_id)}={value}"}


def approve_a_device(client: TestClient, engine: Engine, name: str = "Test panel") -> str:
    """Walk a device from first check-in to an approved cookie. Returns the
    approved cookie value the device holds afterwards."""
    from openavc.api.panel_access import cookie_name

    first = client.get("/api/panel/access")
    assert first.json()["status"] == "pending", first.json()
    pending_value = set_cookie_of(first)[cookie_name(engine.instance_id)].value
    device_id = pending_value.split(".", 1)[0]
    engine.panel_devices.approve(device_id, name, "test")
    second = client.get("/api/panel/access", headers=cookie_header(engine, pending_value))
    assert second.json()["status"] == "approved", second.json()
    return set_cookie_of(second)[cookie_name(engine.instance_id)].value
