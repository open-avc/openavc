"""``device.error.<id>`` always says something.

The event carries the exception's own text, which the Programmer and a
trigger's ``$trigger.error`` read as it is. Some exceptions carry none: a
Python driver's ``send_and_wait`` raises a bare ``TimeoutError()`` when the
device stays silent, and a trigger that forwarded ``$trigger.error`` then sent
an empty message. Every place that publishes the event for an exception names
the exception's type when its text is empty (or only whitespace), and keeps
the text when there is some.

An invented device (Acme) throughout.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.device_errors import device_error_text
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.core.value_resolver import resolve_ref
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class


class _Silent(TimeoutError):
    """An exception whose text is only whitespace."""

    def __str__(self) -> str:
        return "  \n"


@pytest.mark.parametrize(("exc", "said"), [
    (TimeoutError(), "TimeoutError"),
    (ValueError(), "ValueError"),
    (_Silent(), "_Silent"),
    (ValueError("garbage reply"), "garbage reply"),
    (KeyError("power"), "'power'"),
], ids=["bare_timeout", "bare_value_error", "whitespace", "text", "key_error"])
def test_the_text_or_else_the_type(exc, said):
    assert device_error_text(exc) == said


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


def _heard(events: EventBus, device_id: str) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    events.on(f"device.error.{device_id}", lambda _name, payload: received.append(payload))
    return received


class _AcmeWidget(BaseDriver):
    """A Python driver whose command and poll raise what a test gives them."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_bare_error",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 9999},
        "commands": {"power_on": {"label": "Power On", "params": {}}},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.raises: Exception | None = None

    async def connect(self):
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self):
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None):
        raise self.raises

    async def poll(self):
        raise self.raises


async def test_a_command_that_timed_out_with_no_text_names_its_type(core):
    state, events = core
    dm = DeviceManager(state, events)
    driver = _AcmeWidget("w1", {}, state, events)
    await driver.connect()
    dm._devices["w1"] = driver
    heard = _heard(events, "w1")
    driver.raises = TimeoutError()

    try:
        with pytest.raises(TimeoutError):
            await dm.send_command("w1", "power_on")
    finally:
        await dm.disconnect_all()

    assert heard == [{"device_id": "w1", "error": "TimeoutError"}]
    # What a trigger on device.error.* forwards (its context is the payload).
    assert resolve_ref("$trigger.error", state=state, trigger_ctx=heard[0]) == "TimeoutError"


async def test_a_poll_that_failed_with_no_text_names_its_type(core):
    """A poll's TimeoutError is a missed poll, not a device.error, so the bare
    exception here is one the poll reports: a ValueError()."""
    state, events = core
    driver = _AcmeWidget("w2", {}, state, events)
    await driver.connect()
    heard = _heard(events, "w2")
    driver.raises = ValueError()

    await driver.start_polling(0.01)
    await asyncio.sleep(0.05)
    await driver.stop_polling()

    assert heard
    assert heard[0] == {"device_id": "w2", "error": "ValueError"}


class _RaisingHTTP:
    """An HTTP transport whose request raises what it was given."""

    def __init__(self, error: BaseException):
        self.connected = True
        self._error = error

    async def send_request(self, line):  # noqa: ARG002 - the transport's signature
        raise self._error


async def test_a_yaml_poll_line_that_failed_with_no_text_names_its_type(core):
    state, events = core
    cls = create_configurable_driver_class({
        "id": "acme_http_bare_error", "name": "Acme HTTP Box", "transport": "http",
        "commands": {}, "responses": [], "state_variables": {},
        "polling": {"queries": ["/status"]},
    })
    driver = cls("w3", {}, state, events)
    driver.transport = _RaisingHTTP(ValueError())
    heard = _heard(events, "w3")

    await driver.poll()
    await asyncio.sleep(0)

    assert heard == [{"device_id": "w3", "error": "ValueError"}]


class _AcmeSettings(_AcmeWidget):
    DRIVER_INFO: dict[str, Any] = {
        **_AcmeWidget.DRIVER_INFO,
        "id": "acme_settings_bare_error",
        "device_settings": {"label": {"type": "string", "label": "Label", "setup": False}},
    }

    async def set_device_setting(self, key, value):
        raise self.raises


async def test_a_queued_setting_that_failed_with_no_text_names_its_type(core):
    state, events = core
    dm = DeviceManager(state, events)
    driver = _AcmeSettings("w4", {}, state, events)
    await driver.connect()
    dm._devices["w4"] = driver
    dm._device_configs["w4"] = {"pending_settings": {"label": "Lobby"}}
    heard = _heard(events, "w4")
    driver.raises = TimeoutError()

    try:
        await dm._apply_pending_settings("w4")
    finally:
        await dm.disconnect_all()

    assert [p["error"] for p in heard] == [
        "Pending setting 'label' failed to apply: TimeoutError",
    ]
