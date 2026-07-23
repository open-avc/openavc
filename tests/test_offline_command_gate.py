"""Offline-capable commands: the send_command connected-gate skip.

The platform blocks commands to a disconnected device — a live connection is
the whole point of most commands. The exception is a command whose handler
needs no connection at all (the canonical case is a Wake-on-LAN power_on that
sends a magic packet). Such a command declares ``available_offline`` and the
gate lets it through while offline, so a macro, panel button, or schedule can
wake a device that has gone fully off the network.

Two halves are exercised here, both with an invented device (Acme):

  - DeviceManager.send_command skips the connected-gate for an
    available_offline command but still blocks a normal one (and still runs
    param validation on the offline command);
  - resolve_device_actions maps the flag onto a promoted button's
    availability ("always") for both promotion styles, so the Quick Action
    stays visible/enabled while the device is offline.
"""

from __future__ import annotations

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.actions import resolve_device_actions
from server.drivers.base import BaseDriver, CommandParamError


class _AcmeDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {
            # Runs with no live connection — the offline-capable case.
            "wake": {"label": "Wake", "available_offline": True},
            # A normal command: needs the device connected.
            "beep": {"label": "Beep"},
            # Offline-capable but with a validated param, to prove the param
            # gate still runs on the offline path.
            "set_channel": {
                "label": "Set Channel",
                "available_offline": True,
                "params": {"n": {"type": "integer", "min": 1, "max": 8}},
            },
        },
        "quick_actions": ["wake"],
        "actions": [
            {"id": "beep", "kind": "command"},
            {"id": "wake_btn", "kind": "command", "command": "wake"},
        ],
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.calls: list[tuple[str, dict | None]] = []

    async def connect(self):
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self._connected = False

    async def send_command(self, command, params=None):
        self.calls.append((command, params))
        return True


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


def _offline_driver(core):
    state, events = core
    dm = DeviceManager(state, events)
    driver = _AcmeDriver("acme1", {}, state, events)
    # Deliberately NOT connected: no connect() call, connected state is falsy.
    dm._devices["acme1"] = driver
    return dm, driver


# ── send_command gate ────────────────────────────────────────────────────────


async def test_available_offline_command_runs_while_offline(core):
    dm, driver = _offline_driver(core)
    assert not driver.get_state("connected")
    result = await dm.send_command("acme1", "wake")
    assert result is True
    assert driver.calls == [("wake", None)]


async def test_normal_command_still_blocked_while_offline(core):
    dm, driver = _offline_driver(core)
    with pytest.raises(ConnectionError):
        await dm.send_command("acme1", "beep")
    assert driver.calls == []  # nothing reached the driver


async def test_offline_command_still_validates_params(core):
    dm, driver = _offline_driver(core)
    # In range: passes the gate and the param check, reaches the driver.
    await dm.send_command("acme1", "set_channel", {"n": 3})
    assert driver.calls == [("set_channel", {"n": 3})]
    # Out of range: the param gate rejects it even though the command is offline-capable.
    with pytest.raises(CommandParamError):
        await dm.send_command("acme1", "set_channel", {"n": 99})


async def test_both_commands_run_while_connected(core):
    state, events = core
    dm = DeviceManager(state, events)
    driver = _AcmeDriver("acme2", {}, state, events)
    await driver.connect()
    dm._devices["acme2"] = driver
    await dm.send_command("acme2", "wake")
    await dm.send_command("acme2", "beep")
    assert driver.calls == [("wake", None), ("beep", None)]


# ── resolve_device_actions availability mapping ──────────────────────────────


def test_quick_action_sugar_maps_available_offline_to_always():
    resolved = resolve_device_actions(_AcmeDriver.DRIVER_INFO)
    by_id = {a["id"]: a for a in resolved}
    # `wake` promoted via quick_actions sugar → always-visible.
    assert by_id["wake"]["availability"] == "always"


def test_explicit_command_action_inherits_available_offline():
    resolved = resolve_device_actions(_AcmeDriver.DRIVER_INFO)
    by_id = {a["id"]: a for a in resolved}
    # `wake_btn` explicitly promotes the `wake` command with no availability of
    # its own → inherits always from the command's available_offline flag.
    assert by_id["wake_btn"]["availability"] == "always"
    # A normal promoted command stays online-gated (hidden while offline).
    assert by_id["beep"]["availability"] == "online"


def test_explicit_availability_still_wins_over_the_flag():
    info = {
        "commands": {"wake": {"label": "Wake", "available_offline": True}},
        "actions": [
            {"id": "wake", "kind": "command", "availability": "offline"},
        ],
    }
    resolved = resolve_device_actions(info)
    assert resolved[0]["availability"] == "offline"
