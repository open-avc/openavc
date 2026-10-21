"""What Send Command and a device action answer when the driver raises.

A device that says no ("Encoder 001 is offline") reached the Programmer as
"Failed to send command 'flash_led' to device 'widget_1'": the reason only in
the server log, the device by its id. A driver now raises
``CommandRefusedError`` for a refusal, and both doors answer with its
sentence. Any other exception a driver raises keeps its text out of the answer
(it is not written for the reader and may hold what should stay private): the
answer names the device and the command, and the exception goes to the
device's own log, where the answer says it is.

An invented device (Acme) throughout.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, CommandRefusedError
from openavc.main import app

PRIVATE = "private: /firmware/secrets/token"


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"flash_led": {"label": "Flash LED", "params": {}}},
        "actions": [{"id": "flash_led", "kind": "command", "icon": "bolt"}],
    }

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
def client():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = _AcmeDriver("widget_1", {}, state, events)
    driver.set_state("connected", True)
    state.set("device.widget_1.name", "Lobby Widget")

    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine.project = None
    engine._running = True
    engine.devices = MagicMock()
    engine.devices.get_driver = MagicMock(return_value=driver)

    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app, raise_server_exceptions=False), engine
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


DOORS = [
    ("/api/devices/widget_1/command", {"command": "flash_led"}),
    ("/api/devices/widget_1/actions/flash_led", {"params": {}}),
]


@pytest.mark.parametrize("path,body", DOORS)
def test_a_refusal_is_answered_in_the_devices_words(client, path, body):
    c, engine = client
    engine.devices.send_command = AsyncMock(
        side_effect=CommandRefusedError("Encoder 001 is offline")
    )
    resp = c.post(path, json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Encoder 001 is offline"


@pytest.mark.parametrize("path,body", DOORS)
def test_any_other_failure_names_the_device_and_keeps_its_text_out(client, path, body, caplog):
    c, engine = client
    engine.devices.send_command = AsyncMock(side_effect=RuntimeError(PRIVATE))
    with caplog.at_level(logging.ERROR):
        resp = c.post(path, json=body)
    assert resp.status_code == 500
    assert resp.json()["detail"] == (
        "Lobby Widget couldn't run Flash LED. The Device Log on its page has the details."
    )
    # The line the answer points at: under the device's id, so its page shows it.
    assert any(
        r.getMessage().startswith("[widget_1] Command 'flash_led' failed: RuntimeError: " + PRIVATE)
        for r in caplog.records
    )


def test_a_timeout_names_the_device(client):
    c, engine = client
    engine.devices.send_command = AsyncMock(side_effect=TimeoutError("[widget_1] No response"))
    resp = c.post("/api/devices/widget_1/command", json={"command": "flash_led"})
    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("Lobby Widget did not respond in time.")
