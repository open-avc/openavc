"""Which failures are allowed to say ``Device 'x' not found`` — and which are not.

``DeviceManager`` raised a plain ValueError for "no such device", and every door
above it mapped *any* ValueError to a 404 naming the device. So a fault that had
nothing to do with the device's existence — a Python driver's handler failing to
parse a response, a helper deep inside a protocol, a setting key the author
typo'd — was reported as a missing device while that device was connected and
rendering its own page. It is the single most misdirecting answer available: it
sends an integrator to check cabling that is fine.

This had already been patched twice at the symptom. ``UnknownCommandError``
exists because an undeclared command name answered this way, and the non-finite
parameter guard exists because ``int(float("nan"))`` raises ValueError and did
too. Both fixes narrowed one caller; the mapping itself stayed.

So the fault is named instead: ``DeviceNotFoundError`` for "no device has this
id", ``UnknownDeviceSettingError`` for "this device declares no such setting".
Both halves are pinned here — a build that has stopped saying "not found" when
the device really is missing fails just as loudly as one that says it when the
device is right there.

Uses an invented device (Acme) and synthetic payloads throughout: this is the
platform's error contract, not any product's.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.core import device_manager as device_manager_module
from openavc.core.device_manager import DeviceManager, DeviceNotFoundError
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import (
    BaseDriver,
    DeviceSettingValueError,
    UnknownDeviceSettingError,
)
from openavc.main import app

_MISSING = "no_such_device"


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {"brightness": {"type": "integer"}},
        "commands": {"power_on": {"label": "Power On", "params": {}}},
        "actions": [{"id": "power_on", "kind": "command", "icon": "power"}],
        "device_settings": {
            "brightness": {
                "type": "integer", "label": "Brightness", "state_key": "brightness",
                "min": 0, "max": 100, "default": 50, "setup": False,
            },
        },
    }

    async def connect(self) -> None:
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return True

    async def set_device_setting(self, key: str, value: Any) -> Any:
        return True


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


# ── Half one: the platform names the fault ─────────────────────────────────
#
# Every entry point that can be handed an id nobody holds. Parametrised rather
# than written out because the point is coverage of the whole set: a twelfth
# method added later with a bare ValueError puts the bug back at whichever door
# calls it, and a list is the only thing that notices.


async def _call(dm: DeviceManager, name: str) -> None:
    calls = {
        "update_device": lambda: dm.update_device(_MISSING, {"id": _MISSING}),
        "send_command": lambda: dm.send_command(_MISSING, "power_on", {}),
        "set_device_setting": lambda: dm.set_device_setting(_MISSING, "brightness", 50),
        "store_pending_settings": lambda: dm.store_pending_settings(_MISSING, {}),
        "reconnect_device": lambda: dm.reconnect_device(_MISSING),
        "begin_setup": lambda: dm.begin_setup(_MISSING),
        "reconnect_in_place": lambda: dm.reconnect_in_place(_MISSING),
        "pause_device": lambda: dm.pause_device(_MISSING),
        "resume_device": lambda: dm.resume_device(_MISSING),
        "retry_orphaned_device": lambda: dm.retry_orphaned_device(_MISSING),
    }
    await calls[name]()


@pytest.mark.parametrize(
    "method",
    [
        "update_device",
        "send_command",
        "set_device_setting",
        "store_pending_settings",
        "reconnect_device",
        "begin_setup",
        "reconnect_in_place",
        "pause_device",
        "resume_device",
        "retry_orphaned_device",
    ],
)
async def test_every_no_such_device_answer_is_typed(core, method):
    state, events = core
    dm = DeviceManager(state, events)
    with pytest.raises(DeviceNotFoundError) as caught:
        await _call(dm, method)
    assert _MISSING in str(caught.value)


@pytest.mark.parametrize("method", ["get_device_info", "get_device_settings"])
def test_the_synchronous_readers_are_typed_too(core, method):
    state, events = core
    dm = DeviceManager(state, events)
    with pytest.raises(DeviceNotFoundError):
        getattr(dm, method)(_MISSING)


async def test_a_device_that_is_simply_not_an_orphan_is_not_reported_missing(core):
    """The retry door used to answer "not found or not orphaned" and leave the
    reader to guess which. They are different facts about different devices."""
    state, events = core
    dm = DeviceManager(state, events)
    driver = _AcmeDriver("dev1", {}, state, events)
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {"id": "dev1", "driver": "acme_widget"}

    with pytest.raises(ValueError) as caught:
        await dm.retry_orphaned_device("dev1")
    assert not isinstance(caught.value, DeviceNotFoundError)
    assert "is not orphaned" in str(caught.value)


async def test_an_undeclared_setting_key_names_the_setting_not_the_device(core):
    """Both settings doors, because they used to disagree: the direct write
    raised a bare ValueError (answered "device or setting not found") while the
    pending write already raised a typed one."""
    state, events = core
    dm = DeviceManager(state, events)
    driver = _AcmeDriver("dev1", {}, state, events)
    await driver.connect()
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {}

    for call in (
        dm.set_device_setting("dev1", "brightnes", 50),
        dm.store_pending_settings("dev1", {"brightnes": 50}),
    ):
        with pytest.raises(UnknownDeviceSettingError) as caught:
            await call
        assert "brightnes" in str(caught.value)
        # Still a refused settings write for anyone who only branches on that.
        assert isinstance(caught.value, DeviceSettingValueError)


def test_no_bare_value_error_in_the_device_manager_still_claims_a_device_is_missing():
    """The guard the two symptom fixes did not have.

    Naming the fault only helps while every raise site uses the name; one new
    ``raise ValueError(f"Device '{did}' not found")`` restores the bug at
    whichever door happens to call that method, silently and for good.
    """
    source = Path(device_manager_module.__file__).read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        if getattr(node.exc.func, "id", "") != "ValueError" or not node.exc.args:
            continue
        text = ast.get_source_segment(source, node.exc.args[0]) or ""
        if "not found" in text:
            offenders.append(f"line {node.lineno}: {text}")
    assert offenders == [], (
        "raise DeviceNotFoundError for these, or the door above answers "
        "'not found' about a device that is present: " + "; ".join(offenders)
    )


def test_no_door_answers_a_bare_value_error_with_a_missing_device():
    """The other half of the same guard, on the REST side.

    A door may still catch ValueError — it just may not answer it by claiming
    the device is absent, because by then it no longer knows that.
    """
    from openavc.api.routes import devices as devices_routes

    source = Path(devices_routes.__file__).read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if getattr(node.type, "id", "") != "ValueError":
            continue
        body = "\n".join(
            ast.get_source_segment(source, stmt) or "" for stmt in node.body
        )
        if "not found" in body:
            offenders.append(f"line {node.lineno}")
    assert offenders == [], (
        "catch DeviceNotFoundError instead at " + ", ".join(offenders)
    )


# ── Half two: what each door answers ───────────────────────────────────────


@pytest.fixture
def client():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = _AcmeDriver("dev1", {}, state, events)
    driver.set_state("connected", True)

    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine._running = True
    engine.devices = MagicMock()
    engine.devices.get_driver = MagicMock(return_value=driver)
    engine.devices.send_command = AsyncMock(return_value=True)

    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        # The point of several of these is what a door does with a fault it has
        # no branch for, so the exception has to become a response rather than
        # be re-raised into the test.
        yield TestClient(app, raise_server_exceptions=False), engine
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


_DRIVER_FAULT = ValueError("Unparseable response from the widget: 'ERR7'")


def test_a_drivers_own_value_error_is_not_a_missing_device(client):
    """The finding, at the door it was found on."""
    c, engine = client
    engine.devices.send_command = AsyncMock(side_effect=_DRIVER_FAULT)
    resp = c.post("/api/devices/dev1/command", json={"command": "power_on"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "not found" not in detail.lower()
    assert detail == "Failed to send command 'power_on' to device 'dev1'"
    # A 500 names what failed; the driver's own words stay in the server log.
    assert "ERR7" not in detail


def test_the_action_door_does_not_call_it_a_missing_device_either(client):
    c, engine = client
    engine.devices.send_command = AsyncMock(side_effect=_DRIVER_FAULT)
    resp = c.post("/api/devices/dev1/actions/power_on", json={"params": {}})
    assert resp.status_code == 500
    assert "not found" not in resp.json()["detail"].lower()


def test_a_driver_fault_reading_a_device_is_not_a_missing_device(client):
    c, engine = client
    engine.devices.get_device_info = MagicMock(side_effect=_DRIVER_FAULT)
    resp = c.get("/api/devices/dev1")
    assert resp.status_code != 404


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/devices/dev1/command", {"command": "power_on"}),
        ("post", "/api/devices/dev1/actions/power_on", {"params": {}}),
    ],
)
def test_the_command_doors_still_say_not_found_when_it_is(client, method, path, body):
    """The neighbouring message must not have moved."""
    c, engine = client
    engine.devices.send_command = AsyncMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    resp = getattr(c, method)(path, json=body)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device 'dev1' not found"


def test_get_device_still_says_not_found_when_it_is(client):
    c, engine = client
    engine.devices.get_device_info = MagicMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    resp = c.get("/api/devices/dev1")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device 'dev1' not found"


def test_reading_settings_still_says_not_found_when_it_is(client):
    c, engine = client
    engine.devices.get_device_settings = MagicMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    resp = c.get("/api/devices/dev1/settings")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device 'dev1' not found"


def test_an_undeclared_setting_key_reads_as_the_setting_missing(client):
    """404 (the key is a path segment), naming the setting rather than the
    device — it used to answer "Device 'dev1' or setting 'brightnes' not
    found", which blames the device for the author's typo."""
    c, engine = client
    engine.devices.set_device_setting = AsyncMock(
        side_effect=UnknownDeviceSettingError(
            "Unknown device setting 'brightnes' for device 'dev1'"
        )
    )
    resp = c.put("/api/devices/dev1/settings/brightnes", json={"value": 50})
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail == "Unknown device setting 'brightnes' for device 'dev1'"
    assert "Device 'dev1' not found" not in detail


def test_a_bad_setting_value_is_still_a_400(client):
    """The branch above it must not have swallowed this one."""
    c, engine = client
    engine.devices.set_device_setting = AsyncMock(
        side_effect=DeviceSettingValueError("'brightness' must be at most 100, got 999")
    )
    resp = c.put("/api/devices/dev1/settings/brightness", json={"value": 999})
    assert resp.status_code == 400
    assert "at most 100" in resp.json()["detail"]


def test_writing_a_setting_on_a_missing_device_says_so(client):
    c, engine = client
    engine.devices.set_device_setting = AsyncMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    resp = c.put("/api/devices/dev1/settings/brightness", json={"value": 50})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device 'dev1' not found"


def test_a_pending_write_answers_both_halves_in_the_body(client):
    """Key and value arrive together in the body here, so both are a 400."""
    c, engine = client
    engine.devices.store_pending_settings = AsyncMock(
        side_effect=UnknownDeviceSettingError(
            "Unknown device setting 'brightnes' for device 'dev1'"
        )
    )
    resp = c.post(
        "/api/devices/dev1/settings/pending", json={"settings": {"brightnes": 50}}
    )
    assert resp.status_code == 400
    assert "brightnes" in resp.json()["detail"]

    engine.devices.store_pending_settings = AsyncMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    resp = c.post(
        "/api/devices/dev1/settings/pending", json={"settings": {"brightness": 50}}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device 'dev1' not found"


def test_retry_separates_a_missing_device_from_a_healthy_one(client):
    """One 404 became two answers, because it was answering two questions."""
    c, engine = client
    engine.devices.retry_orphaned_device = AsyncMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    resp = c.post("/api/devices/dev1/retry")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Device 'dev1' not found"

    engine.devices.retry_orphaned_device = AsyncMock(
        side_effect=ValueError("Device 'dev1' is not orphaned")
    )
    resp = c.post("/api/devices/dev1/retry")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail == "Device 'dev1' is not orphaned"
    assert "not found" not in detail


# ── The AI tool doors, which answer in prose rather than status codes ───────


async def test_the_ai_setting_tool_says_what_was_actually_wrong():
    """It reported "Device 'x' or setting 'y' not found" for a bad *value* —
    so the generator's next attempt had no idea which of three things to fix."""
    from openavc.cloud.tools.device_tools import DeviceToolsMixin

    class _Handler(DeviceToolsMixin):
        def __init__(self, engine):
            self._engine = engine

        def _get_engine(self):
            return self._engine

    engine = MagicMock()
    engine.devices = MagicMock()
    handler = _Handler(engine)
    base = {"device_id": "dev1", "setting_key": "brightness"}

    engine.devices.set_device_setting = AsyncMock(
        side_effect=DeviceSettingValueError("'brightness' must be at most 100, got 999")
    )
    result = await handler._set_device_setting({**base, "value": 999})
    assert result["error"] == "'brightness' must be at most 100, got 999"

    engine.devices.set_device_setting = AsyncMock(
        side_effect=UnknownDeviceSettingError(
            "Unknown device setting 'brightnes' for device 'dev1'"
        )
    )
    result = await handler._set_device_setting({**base, "setting_key": "brightnes", "value": 50})
    assert result["error"] == "Unknown device setting 'brightnes' for device 'dev1'"

    engine.devices.set_device_setting = AsyncMock(
        side_effect=DeviceNotFoundError("Device 'dev1' not found")
    )
    result = await handler._set_device_setting({**base, "value": 50})
    assert result["error"] == "Device 'dev1' not found"
