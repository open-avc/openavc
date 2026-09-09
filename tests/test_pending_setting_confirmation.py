"""A queued device setting is cleared when the DEVICE confirms it, not when
the send returns.

A setting queued while a device is offline used to be cleared the moment
``set_device_setting`` returned without raising. On a datagram transport — or
on a socket being redirected under the driver — nothing raises and nothing
arrives: the log said "Applied", the queue emptied, and the value was gone with
no trace. The platform already had what it needed to notice, because a setting
with a ``state_key`` is reported back by the device.

So a setting whose value the device reports is held until the read-back agrees.
Uses an invented device (Acme).
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core import device_manager
from openavc.core.device_manager import DeviceManager, _readback_confirms
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _ReadBackDriver(BaseDriver):
    """Reports ``brightness`` back the way a real device does — but only when
    ``echo`` is on. With it off, the write goes nowhere and the device keeps
    reporting whatever it reported before, which is the failure being pinned."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_readback",
        "name": "Acme Read-back Widget",
        "transport": "tcp",
        "state_variables": {
            "brightness": {"type": "integer"},
            "mode": {"type": "string"},
        },
        "commands": {},
        "device_settings": {
            "brightness": {
                "type": "integer", "label": "Brightness", "state_key": "brightness",
                "min": 0, "max": 100, "default": 50, "setup": False,
            },
            "mode": {
                "type": "enum", "label": "Mode", "state_key": "mode",
                "values": [{"value": "1", "label": "Auto"}],
                "default": "1", "setup": False,
            },
            # No state_key target — the driver declares no such state variable,
            # so there is no read-back and the old clear-on-send rule applies.
            "label": {"type": "string", "label": "Label", "setup": False},
        },
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.writes: list[tuple[str, Any]] = []
        self.echo = True

    async def connect(self):
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self):
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command, params=None):
        return True

    async def set_device_setting(self, key, value):
        self.writes.append((key, value))
        if self.echo and key in ("brightness", "mode"):
            self.set_state(key, value)
        return True


@pytest.fixture
def dm(monkeypatch):
    """A manager whose confirmation window is short enough for a test."""
    monkeypatch.setattr(device_manager, "_CONFIRM_UNPOLLED_WINDOW", 0.05)
    monkeypatch.setattr(device_manager, "_CONFIRM_POLL_STEP", 0.01)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events)


async def _device(dm: DeviceManager, device_id: str = "dev") -> _ReadBackDriver:
    driver = _ReadBackDriver(device_id, {}, dm.state, dm.events)
    await driver.connect()
    dm._devices[device_id] = driver
    dm._device_configs[device_id] = {}
    return driver


def _errors(dm: DeviceManager, device_id: str = "dev") -> list[dict]:
    received: list[dict] = []
    dm.events.on(f"device.error.{device_id}", lambda n, p: received.append(p))
    return received


def _applied(dm: DeviceManager) -> list[dict]:
    received: list[dict] = []
    dm.events.on(
        "device.pending_settings_applied", lambda n, p: received.append(p)
    )
    return received


# ── The three outcomes ──────────────────────────────────────────────────────


async def test_readback_agreeing_clears_the_queue(dm):
    driver = await _device(dm)
    applied = _applied(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    # Still queued at this point — the send alone proves nothing.
    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}

    await dm._pending_confirm_tasks["dev"]

    assert driver.writes == [("brightness", 70)]
    assert "pending_settings" not in dm._device_configs["dev"]
    assert applied and applied[-1]["applied"] == ["brightness"]
    assert applied[-1]["remaining"] == {}


async def test_a_write_that_vanished_stays_queued_and_says_so(dm):
    """The Q-192 failure: the send returns, the device never took the value,
    and the old code logged 'Applied' and threw the queue away."""
    driver = await _device(dm)
    driver.echo = False
    # The device last reported 55 — a poll landed before the write.
    driver.set_state("brightness", 55)
    errors = _errors(dm)
    applied = _applied(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    await dm._pending_confirm_tasks["dev"]

    # Held for the next connect rather than lost.
    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert applied == []
    assert errors and "brightness" in errors[0]["error"]
    assert errors[0]["source"] == "pending_settings"


async def test_a_setting_a_talking_device_never_reports_clears(dm):
    """One real driver declares 28 settings its hardware never reads back.
    While the device is reporting its OTHER readings, a state variable still
    at ``None`` means "this device doesn't report that", and holding the
    write forever would re-send it on every reconnect for nothing."""
    driver = await _device(dm)
    driver.echo = False
    driver.set_state("mode", "1")            # the device is answering
    assert driver.get_state("brightness") is None
    errors = _errors(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    await dm._pending_confirm_tasks["dev"]

    assert "pending_settings" not in dm._device_configs["dev"]
    assert errors == []


async def test_a_setting_a_silent_device_never_reports_stays_queued(dm):
    """The shape the write vanished in: the device is connected, the datagram
    went nowhere, and NOTHING has been reported back. Silence is not
    agreement — clearing here is the loss this whole check exists to stop."""
    driver = await _device(dm)
    driver.echo = False
    assert not dm._device_is_reporting(driver)
    errors = _errors(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    await dm._pending_confirm_tasks["dev"]

    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert errors and "reported nothing back" in errors[0]["error"]


async def test_a_setting_with_no_read_back_clears_on_send(dm):
    """``label`` names no declared state variable, so no watcher is started
    and the clear happens inline exactly as it always did."""
    await _device(dm)
    await dm.store_pending_settings("dev", {"label": "Rack A"})

    await dm._apply_pending_settings("dev")

    assert "pending_settings" not in dm._device_configs["dev"]
    assert "dev" not in dm._pending_confirm_tasks


# ── Which value gets cleared ────────────────────────────────────────────────


async def test_a_newer_queued_value_survives_an_older_confirmation(dm):
    """The confirmation lands a poll cycle after the write. If something
    queued a newer value in between, clearing the key would throw away a
    write nobody has made yet."""
    driver = await _device(dm)
    driver.echo = False
    driver.set_state("mode", "1")
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    # A newer value is queued while the watcher waits...
    await dm.store_pending_settings("dev", {"brightness": 40})
    # ...and only then does the device confirm the FIRST write.
    driver.set_state("brightness", 70)
    await dm._pending_confirm_tasks["dev"]

    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 40}


async def test_a_live_write_drops_a_stale_queued_value(dm):
    """A setting written directly settles the question the queue was holding.
    Otherwise an unconfirmed value sits in the project and overwrites this
    one on the next connect, days later, for no visible reason."""
    driver = await _device(dm)
    applied = _applied(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm.set_device_setting("dev", "brightness", 40)

    assert driver.writes == [("brightness", 40)]
    assert "pending_settings" not in dm._device_configs["dev"]
    assert applied and applied[-1]["applied"] == ["brightness"]


# ── Lifecycle ───────────────────────────────────────────────────────────────


async def test_a_drop_mid_window_leaves_the_queue_for_the_reconnect(dm):
    driver = await _device(dm)
    driver.echo = False
    driver.set_state("brightness", 55)
    errors = _errors(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    await driver.disconnect()
    await dm._pending_confirm_tasks["dev"]

    # Queued for the reconnect, and no error: the disconnect is the story,
    # and the reconnect re-applies it.
    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert errors == []


async def test_removing_the_device_cancels_the_watcher(dm):
    driver = await _device(dm)
    driver.echo = False
    driver.set_state("brightness", 55)
    await dm.store_pending_settings("dev", {"brightness": 70})

    await dm._apply_pending_settings("dev")
    task = dm._pending_confirm_tasks["dev"]
    await dm.remove_device("dev")

    assert task.cancelled() or task.cancelling()
    assert "dev" not in dm._pending_confirm_tasks


# ── The window, and what counts as agreement ────────────────────────────────


def test_window_is_two_poll_cycles_for_a_polled_device(dm):
    class _Cfg:
        config = {"poll_interval": 30}

    class _NoPoll:
        config = {"poll_interval": 0}

    class _Junk:
        config = {"poll_interval": "soon"}

    # Two cycles plus slack: the poll in flight when the write went out is
    # still carrying the old value.
    assert dm._confirm_window(_Cfg()) == 70.0
    assert dm._confirm_window(_NoPoll()) == device_manager._CONFIRM_UNPOLLED_WINDOW
    assert dm._confirm_window(_Junk()) == device_manager._CONFIRM_UNPOLLED_WINDOW


def test_window_is_capped():
    assert (
        device_manager.DeviceManager._confirm_window(
            None, type("D", (), {"config": {"poll_interval": 100000}})()
        )
        == device_manager._CONFIRM_MAX_WINDOW
    )


@pytest.mark.parametrize(
    "sdef,expected,actual,agrees",
    [
        ({"type": "integer"}, 70, 70, True),
        # The device reports what it stores, in its own form.
        ({"type": "integer"}, 70, "70", True),
        ({"type": "boolean"}, True, "on", True),
        ({"type": "enum", "values": [{"value": "1", "label": "Auto"}]},
         "1", "Auto", True),
        ({"type": "integer"}, 70, 55, False),
        # "Declared, never reported" is never a confirmation.
        ({"type": "integer"}, 70, None, False),
        # A value the schema refuses falls back to a plain text comparison
        # rather than counting as a mismatch on a technicality.
        ({"type": "integer", "min": 0, "max": 100}, 70, "700", False),
        (None, "x", "x", True),
    ],
)
def test_readback_agreement(sdef, expected, actual, agrees):
    assert _readback_confirms("k", sdef, expected, actual) is agrees
