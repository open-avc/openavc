"""The AI tools and device logins: nothing they answer holds one, and they take none.

What is pinned here:

- ``update_device``, ``add_device`` and ``set_device_setting`` refuse a login
  in any form: a value, ``***`` (what a masked read hands back), the marker,
  or null (a removal). Nothing is changed or added, and the saved login stays.
  A login is the connection password and every setting the driver marks
  secret, by the one rule; for a driver that is not installed, the name rule
  too. A username is an ordinary setting.
- A login the driver requires and nothing fills is left waiting, never
  refused: the device is added with the marker over nothing, waits under
  Needs login, and the answer says where to enter it. Changing a device onto
  such a driver is the same.
- ``get_device_info`` for a device whose driver is not installed shows a saved
  login as the marker and masks one held in the clear. A link's URL never has
  a login filled in.
- The state reads (``get_project_state``, ``get_state_value``,
  ``get_state_history``, ``get_device_info``'s state, ``simulate_ui_action``'s
  changes) hide the password of a ``user:password@`` address.

An invented device throughout.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.api import rest
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.device_logins import SAVED_LOGIN, DeviceLogins
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver

DRIVER = "acme_ai_login"
OPEN_DRIVER = "acme_ai_open"
KEY_DRIVER = "acme_ai_keyed"
SAVED = "Hunter2-Acme"


class _AcmeWidget(BaseDriver):
    """Requires a password (no default) and declares two more secrets."""

    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5500},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "username": {"type": "string", "label": "Username"},
            "password": {"type": "string", "secret": True, "required": True, "label": "Password"},
            "api_key": {"type": "string", "secret": True, "label": "API Key"},
            "site_code": {"type": "password", "label": "Site Code"},
            "mode": {"type": "string", "label": "Mode"},
        },
        "device_settings": {
            "brightness": {"type": "integer", "label": "Brightness"},
            "admin_pin": {"type": "string", "secret": True, "label": "Admin PIN"},
        },
        "actions": [
            {"id": "web", "kind": "link", "label": "Open", "url": "http://{host}/login?u={username}&p={password}"},
        ],
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def set_device_setting(self, key: str, value: Any) -> None:
        self.set_state(key, value)


class _AcmeOpen(_AcmeWidget):
    """A password the device may not need: nothing waits when it is left out."""

    DRIVER_INFO: dict[str, Any] = {
        **_AcmeWidget.DRIVER_INFO,
        "id": OPEN_DRIVER,
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "password": {"type": "string", "secret": True, "label": "Password"},
        },
    }


class _AcmeKeyed(_AcmeWidget):
    """Its login is a key, required, under another name."""

    DRIVER_INFO: dict[str, Any] = {
        **_AcmeWidget.DRIVER_INFO,
        "id": KEY_DRIVER,
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "psk": {"type": "string", "secret": True, "required": True, "label": "Pre-Shared Key"},
        },
    }


@pytest.fixture(autouse=True)
def _drivers(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    for cls in (_AcmeWidget, _AcmeOpen, _AcmeKeyed):
        registry.register_driver(cls)


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    return folder


def _project(*devices: DeviceConfig) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="p1", name="Hall"),
        devices=list(devices),
        connections={
            d.id: {"host": "192.0.2.10", "username": "admin", "password": SAVED_LOGIN}
            for d in devices
        },
    )


@pytest.fixture
async def engine(data, tmp_path):
    from openavc.core.engine import Engine

    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    eng.device_logins.save("proj1", DRIVER, {"password": SAVED})
    project = _project(DeviceConfig(id="proj1", driver=DRIVER, name="Hall Projector", config={"mode": "eco"}))
    eng.project_path.write_text(json.dumps(project.model_dump(mode="json")), encoding="utf-8")
    rest.set_engine(eng)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await eng.reload_project()
            yield eng
    finally:
        rest.set_engine(None)
        await eng.devices.disconnect_all()


@pytest.fixture
def tools(engine):
    agent = MagicMock()
    agent.state = engine.state
    return AIToolHandler(agent, engine.devices, engine.events, project_path=engine.project_path)


def _file(engine) -> str:
    return engine.project_path.read_text(encoding="utf-8")


def _device(engine, device_id: str) -> DeviceConfig:
    return next(d for d in engine.project.devices if d.id == device_id)


# --- the device tools take no login ------------------------------------------------


@pytest.mark.parametrize("sent", ["New-Password-1", "***", SAVED_LOGIN, None])
async def test_update_device_refuses_a_password_in_any_form(engine, tools, data, sent):
    before = _file(engine)

    answer = await tools._update_device({"device_id": "proj1", "config": {"password": sent, "mode": "cool"}})

    assert answer["login_settings"] == ["password"]
    assert answer["error"] == (
        "Password is a login. Enter it on the device's page in the Programmer, under Edit. "
        "Nothing was changed."
    )
    assert _file(engine) == before
    assert _device(engine, "proj1").config["mode"] == "eco"
    assert DeviceLogins(data).login("proj1", DRIVER, "password") == SAVED


async def test_update_device_refuses_every_setting_the_driver_marks_secret(engine, tools):
    answer = await tools._update_device(
        {"device_id": "proj1", "config": {"api_key": "k-123456", "site_code": "4711", "mode": "cool"}},
    )

    assert answer["login_settings"] == ["api_key", "site_code"]
    assert answer["error"].startswith("API Key and Site Code are logins. Enter them on the device's page")
    assert "k-123456" not in _file(engine)


async def test_update_device_still_changes_the_address_username_and_settings(engine, tools, data):
    answer = await tools._update_device(
        {"device_id": "proj1", "config": {"host": "192.0.2.11", "username": "ops", "mode": "cool"}},
    )

    assert answer == {"status": "updated", "device_id": "proj1"}
    on_disk = json.loads(_file(engine))
    assert on_disk["connections"]["proj1"] == {
        "host": "192.0.2.11", "username": "ops", "password": SAVED_LOGIN,
    }
    assert on_disk["devices"][0]["config"]["mode"] == "cool"
    assert DeviceLogins(data).login("proj1", DRIVER, "password") == SAVED


async def test_add_device_refuses_a_login_and_adds_nothing(engine, tools, data):
    answer = await tools._add_device({
        "id": "proj2", "driver": DRIVER, "name": "Lobby Projector",
        "config": {"host": "192.0.2.12", "password": "Typed-In-Chat"},
    })

    assert answer["login_settings"] == ["password"]
    assert answer["error"] == (
        "Password is a login. Add the device without it, then enter it on the device's page "
        "in the Programmer, under Edit. Nothing was added."
    )
    assert [d.id for d in engine.project.devices] == ["proj1"]
    assert "Typed-In-Chat" not in _file(engine)
    assert DeviceLogins(data).login("proj2", DRIVER, "password") is None


async def test_a_driver_not_installed_has_its_logins_found_by_name(engine, tools):
    answer = await tools._add_device({
        "id": "amp1", "driver": "acme_not_installed", "name": "Amp",
        "config": {"host": "192.0.2.13", "username": "admin", "api_key": "k-999999"},
    })

    assert answer["login_settings"] == ["api_key"]
    assert [d.id for d in engine.project.devices] == ["proj1"]


async def test_set_device_setting_refuses_a_secret_setting(engine, tools):
    driver = engine.devices.get_driver("proj1")
    driver.set_device_setting = AsyncMock()

    answer = await tools._set_device_setting({"device_id": "proj1", "setting_key": "admin_pin", "value": "2468"})

    assert answer == {
        "error": "Admin PIN is a login. Enter it on the device's page in the Programmer. Nothing was changed.",
        "login_settings": ["admin_pin"],
    }
    driver.set_device_setting.assert_not_called()


# --- a login the driver requires waits for the device page ----------------------------


async def test_add_device_leaves_a_required_login_waiting(engine, tools, data):
    answer = await tools._add_device({
        "id": "proj2", "driver": DRIVER, "name": "Lobby Projector", "config": {"host": "192.0.2.12"},
    })

    assert answer == {
        "status": "created", "id": "proj2",
        "needs_login": ["password"],
        "needs_login_note": (
            "Needs login: enter Password on this device's page in the Programmer. "
            "It does not connect until then."
        ),
    }
    on_disk = json.loads(_file(engine))
    assert on_disk["connections"]["proj2"] == {"host": "192.0.2.12", "password": SAVED_LOGIN}
    assert DeviceLogins(data).login("proj2", DRIVER, "password") is None
    assert {"id": "proj2", "name": "Lobby Projector"} in engine.logins_needed()
    assert engine.devices.get_device_info("proj2")["needs_login"] == ["password"]


async def test_add_device_still_refuses_any_other_required_field(engine, tools):
    answer = await tools._add_device({"id": "proj2", "driver": DRIVER, "name": "Lobby Projector", "config": {}})

    assert answer == {"error": "IP Address is required.", "missing_config": ["host"]}
    assert [d.id for d in engine.project.devices] == ["proj1"]


async def test_a_login_the_device_may_not_need_is_left_out(engine, tools):
    answer = await tools._add_device({
        "id": "disp1", "driver": OPEN_DRIVER, "name": "Display", "config": {"host": "192.0.2.14"},
    })

    assert answer == {"status": "created", "id": "disp1"}
    assert "password" not in json.loads(_file(engine))["connections"]["disp1"]


async def test_changing_the_driver_leaves_its_required_login_waiting(engine, tools, data):
    answer = await tools._update_device({"device_id": "proj1", "driver": KEY_DRIVER})

    assert answer["status"] == "updated"
    assert answer["needs_login"] == ["password", "psk"]
    assert answer["needs_login_note"].startswith("Needs login: enter Password and Pre-Shared Key")
    assert _device(engine, "proj1").config["psk"] == SAVED_LOGIN
    # Filed for the driver it was saved for, never handed to another.
    assert DeviceLogins(data).login("proj1", DRIVER, "password") == SAVED


# --- what the read tools answer --------------------------------------------------------


async def test_a_device_whose_driver_is_missing_shows_the_marker_and_masks_the_rest(engine, tools):
    project = engine.project.model_copy(deep=True)
    project.devices.append(DeviceConfig(
        id="amp1", driver="acme_not_installed", name="Amp", config={"api_key": "k-999999"},
    ))
    project.connections["amp1"] = {"host": "192.0.2.13", "password": SAVED_LOGIN}
    engine.project_path.write_text(json.dumps(project.model_dump(mode="json")), encoding="utf-8")
    with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
        await engine.reload_project()

    info = await tools._get_device_info({"device_id": "amp1"})

    assert info["config"]["password"] == SAVED_LOGIN
    assert info["config"]["api_key"] == "***"
    assert "k-999999" not in json.dumps(info)


async def test_a_link_never_has_a_login_filled_in(engine, tools):
    info = await tools._get_device_info({"device_id": "proj1"})

    link = next(a for a in info["actions"] if a["id"] == "web")
    assert link["url"] == "http://192.0.2.10/login?u=admin&p={password}"
    assert SAVED not in json.dumps(info)


async def test_the_state_reads_hide_a_stream_address_password(engine, tools):
    url = "rtsp://admin:S3cret-Cam@192.0.2.20/live"
    shown = "rtsp://admin:***@192.0.2.20/live"
    engine.state.set("device.proj1.stream_url", url, source="device")

    snapshot = await tools._get_project_state({})
    one = await tools._get_state_value({"key": "device.proj1.stream_url"})
    history = await tools._get_state_history({"count": 1000})
    info = await tools._get_device_info({"device_id": "proj1"})

    assert snapshot["device.proj1.stream_url"] == shown
    assert one == {"key": "device.proj1.stream_url", "value": shown}
    assert {"key": "device.proj1.stream_url", "old_value": None, "new_value": shown} == {
        k: v for k, v in history[-1].items() if k in ("key", "old_value", "new_value")
    }
    assert info["state"]["stream_url"] == shown
    for answer in (snapshot, one, history, info):
        assert "S3cret-Cam" not in json.dumps(answer)
    # On this system the value is untouched: the panel plays the stream.
    assert engine.state.get("device.proj1.stream_url") == url


async def test_a_simulated_press_reports_its_changes_as_the_state_reads_do(engine, tools, monkeypatch):
    async def press(action, element_id, *args, **kwargs):
        engine.state.set("device.proj1.stream_url", "rtsp://admin:S3cret-Cam@192.0.2.20/live", source="device")
        engine.state.set("isc.peer1.mode", "on", source="device")
        return [{"action": "device.command", "device": "proj1", "command": "stream", "params": {}, "sent": True}]

    monkeypatch.setattr(engine, "handle_ui_event", press)

    answer = await tools._simulate_ui_action({"action": "press", "element_id": "btn_stream"})

    assert answer["state_changes"] == [{
        "key": "device.proj1.stream_url", "old_value": None,
        "new_value": "rtsp://admin:***@192.0.2.20/live",
    }]


def test_a_state_value_leaves_for_the_cloud_without_a_url_password():
    from openavc.cloud.state_relay import cloud_state_value

    assert cloud_state_value("http://ops:pa55w0rd@192.0.2.5:8080/x") == "http://ops:***@192.0.2.5:8080/x"
    assert cloud_state_value("rtsp://192.0.2.5/live") == "rtsp://192.0.2.5/live"
    assert cloud_state_value("on") == "on"
    assert cloud_state_value(42) == 42
    assert cloud_state_value(None) is None
