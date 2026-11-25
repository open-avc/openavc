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

import asyncio
from typing import Any

import pytest

from openavc.core import device_manager, pending_settings
from openavc.core.device_manager import DeviceManager
from openavc.core.pending_settings import readback_confirms
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
    monkeypatch.setattr(pending_settings, "_CONFIRM_UNPOLLED_WINDOW", 0.05)
    monkeypatch.setattr(pending_settings, "_CONFIRM_POLL_STEP", 0.01)
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
    """The original failure: the send returns, the device never took the value,
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


# ── The instance a watcher holds ────────────────────────────────────────────


HELD = "acme_readback_held"


class _HeldDriver(_ReadBackDriver):
    """Built by the manager (registered): it echoes nothing and has reported
    ``brightness`` as 55 since its connect. Its first close waits for
    ``release`` once ``closing`` is set, and the first instance's sends wait
    for ``send_gate`` when one is set, so a test can act inside either."""

    DRIVER_INFO = {**_ReadBackDriver.DRIVER_INFO, "id": HELD}
    closing: asyncio.Event | None = None
    release: asyncio.Event | None = None
    send_gate: asyncio.Event | None = None
    sending: asyncio.Event | None = None
    instances: list = []

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.echo = False
        type(self).instances.append(self)

    async def connect(self):
        await super().connect()
        self.set_state("brightness", 55)

    async def disconnect(self):
        cls = type(self)
        if cls.closing is not None and not cls.closing.is_set():
            cls.closing.set()
            await cls.release.wait()
        await super().disconnect()

    async def set_device_setting(self, key, value):
        cls = type(self)
        if cls.send_gate is not None and self is cls.instances[0]:
            cls.sending.set()
            await cls.send_gate.wait()
        return await super().set_device_setting(key, value)


@pytest.fixture
def held(monkeypatch):
    from openavc.drivers.registry import _DRIVER_REGISTRY

    _HeldDriver.closing, _HeldDriver.release = asyncio.Event(), asyncio.Event()
    _HeldDriver.send_gate = _HeldDriver.sending = None
    _HeldDriver.instances = []
    # A step longer than the window: a watcher still running when its
    # device's close begins wakes past its deadline before it can see the
    # device go, so the test meets the judging it must not do.
    monkeypatch.setattr(pending_settings, "_CONFIRM_POLL_STEP", 0.2)
    _DRIVER_REGISTRY[HELD] = _HeldDriver
    yield _HeldDriver
    _DRIVER_REGISTRY.pop(HELD, None)


def _held_config() -> dict[str, Any]:
    return {
        "id": "dev", "driver": HELD, "name": "Dev", "config": {},
        "pending_settings": {"brightness": 70},
    }


async def _said_while_closing(dm: DeviceManager, retire) -> tuple[bool, list, list]:
    """Add ``dev`` with a queued brightness its watcher waits on, then run
    ``retire`` (a remove or an edit) and wait past the confirm window inside
    the old instance's close. Whether the watcher was still running when the
    close began, and the errors and clears said for ``dev`` by the window's
    end."""
    errors, applied = _errors(dm), _applied(dm)
    await dm.add_device(_held_config())
    watcher = dm._pending_confirm_tasks["dev"]
    await asyncio.sleep(0)  # its first step: the device is there and talking
    retiring = asyncio.create_task(retire())
    await asyncio.wait_for(_HeldDriver.closing.wait(), timeout=2.0)
    running_at_close = not (watcher.done() or watcher.cancelling())
    await asyncio.sleep(0.4)
    said = (running_at_close, list(errors), list(applied))
    _HeldDriver.release.set()
    await retiring
    await dm.disconnect_all()
    return said


async def test_a_removed_device_s_watcher_is_ended_before_its_close(dm, held):
    """The watcher used to be cancelled after the close was awaited, so in
    that close it could judge a device being removed: an error saying the
    setting stays queued, for a device that no longer exists."""
    assert await _said_while_closing(dm, lambda: dm.remove_device("dev")) == (False, [], [])


async def test_an_edited_device_s_watcher_is_ended_before_its_close(dm, held):
    """An edit retires the old instance the same way; the new one sends the
    queue again and watches it itself."""
    said = await _said_while_closing(dm, lambda: dm.update_device("dev", _held_config()))

    assert said == (False, [], [])


async def test_a_send_that_outlives_its_instance_leaves_the_new_watcher_alone(dm, held):
    """A connect's pass was still sending when the device was edited. Its
    instance has gone by the time it would start a watcher, so it starts
    none: the replacement's own watcher, on the replacement, is the one that
    stands."""
    held.closing = None
    held.send_gate, held.sending = asyncio.Event(), asyncio.Event()
    # A window longer than the test, so the replacement's watcher is still
    # waiting when it is looked at; the stop at the end cancels it.
    dm._confirm_window = lambda _driver: 30.0
    adding = asyncio.create_task(dm.add_device(_held_config()))
    await asyncio.wait_for(held.sending.wait(), timeout=2.0)

    await dm.update_device("dev", _held_config())
    replacement = dm._pending_confirm_tasks["dev"]
    held.send_gate.set()
    await adding

    assert dm._pending_confirm_tasks.get("dev") is replacement
    assert not (replacement.done() or replacement.cancelling())
    await dm.disconnect_all()


async def test_a_watcher_whose_device_went_judges_nothing_at_its_deadline(dm, held):
    """A watcher wakes past its deadline to find its device gone (removed,
    or replaced since): it leaves the setting queued, unjudged, as a device
    that drops mid-window does."""
    driver = await _device(dm)
    driver.echo = False
    driver.set_state("brightness", 55)
    errors, applied = _errors(dm), _applied(dm)
    await dm.store_pending_settings("dev", {"brightness": 70})
    await dm._apply_pending_settings("dev")
    watcher = dm._pending_confirm_tasks["dev"]
    await asyncio.sleep(0)

    dm._devices.pop("dev")
    await watcher

    assert (errors, applied) == ([], [])
    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}


# ── The window, and what counts as agreement ────────────────────────────────


class _OwnPace(BaseDriver):
    """Polls at an interval of its own, with none in its config."""

    DRIVER_INFO = {"id": "acme_pace", "name": "Acme Pace", "transport": "tcp"}

    async def poll(self) -> None:
        await asyncio.Event().wait()

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


async def test_window_is_two_poll_cycles_of_the_interval_the_device_is_polled_at(dm):
    """The interval the driver's poll loop runs at, not its config's: a
    driver that polls every 30 s with no poll_interval of its own waits two
    of its cycles, and one whose config names an interval it does not poll
    at waits as for a device nobody polls."""
    pace = _OwnPace("dev", {"host": "127.0.0.1", "port": 1}, StateStore(), EventBus())
    assert dm._confirm_window(pace) == pending_settings._CONFIRM_UNPOLLED_WINDOW
    await pace.start_polling(30)
    try:
        # Two cycles plus slack: the poll in flight when the write went out is
        # still carrying the old value.
        assert dm._confirm_window(pace) == 70.0
    finally:
        await pace.stop_polling()
    pace.config["poll_interval"] = 30
    assert dm._confirm_window(pace) == pending_settings._CONFIRM_UNPOLLED_WINDOW


async def test_window_is_capped():
    pace = _OwnPace("dev", {"host": "127.0.0.1", "port": 1}, StateStore(), EventBus())
    await pace.start_polling(100000)
    try:
        assert (
            device_manager.DeviceManager._confirm_window(None, pace)
            == pending_settings._CONFIRM_MAX_WINDOW
        )
    finally:
        await pace.stop_polling()


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
    assert readback_confirms("k", sdef, expected, actual) is agrees


# ── A live write waits by the same rule ─────────────────────────────────────


async def test_a_live_write_is_read_back_by_the_same_rule(dm):
    """``await_setting_readback`` is the rule above for a caller that wrote
    live (a device audit): agree in the device's own form, else say what the
    device still reports when the window closes."""
    driver = await _device(dm)
    await dm.set_device_setting("dev", "mode", "Auto")
    assert await dm.await_setting_readback("dev", "mode", "1") == (True, "1")

    driver.echo = False
    driver.set_state("brightness", 55)
    await dm.set_device_setting("dev", "brightness", 70)
    assert await dm.await_setting_readback("dev", "brightness", 70) == (False, 55)
    # No declared state variable to read it by: nothing to wait for.
    assert await dm.await_setting_readback("dev", "label", "x") == (False, None)


# ── The quieter paths ───────────────────────────────────────────────────────


def _failing_error_reports(dm: DeviceManager) -> list[str]:
    """Make every ``device.error.<id>`` emit raise; returns the errors tried."""
    tried: list[str] = []
    emit = dm.events.emit

    async def emit_or_fail(event: str, payload: Any = None) -> None:
        if event.startswith("device.error."):
            tried.append(payload["error"])
            raise RuntimeError("event bus down")
        await emit(event, payload)

    dm.events.emit = emit_or_fail
    return tried


async def test_a_queue_with_no_driver_to_send_it_is_left_alone(dm):
    dm._device_configs["dev"] = {"pending_settings": {"brightness": 70}}
    applied = _applied(dm)

    await dm._apply_pending_settings("dev")

    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert applied == []
    assert "dev" not in dm._pending_confirm_tasks


async def test_a_failure_that_cannot_be_reported_does_not_stop_the_rest(dm):
    class _RefusesBrightness(_ReadBackDriver):
        async def set_device_setting(self, key, value):
            if key == "brightness":
                raise ValueError("firmware refused it")
            return await super().set_device_setting(key, value)

    driver = _RefusesBrightness("dev", {}, dm.state, dm.events)
    await driver.connect()
    dm._devices["dev"] = driver
    dm._device_configs["dev"] = {"pending_settings": {"brightness": 70, "label": "Lobby"}}
    tried = _failing_error_reports(dm)
    applied = _applied(dm)

    await dm._apply_pending_settings("dev")

    assert tried == ["Pending setting 'brightness' failed to apply: firmware refused it"]
    assert driver.writes == [("label", "Lobby")]
    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert applied == [
        {"device_id": "dev", "applied": ["label"], "refused": [], "remaining": {"brightness": 70}},
    ]


async def test_a_watch_whose_device_is_already_gone_settles_nothing(dm):
    gone = _ReadBackDriver("dev", {}, dm.state, dm.events)
    await gone.connect()
    gone.set_state("brightness", 70)  # it even reads back; it is not the device now
    dm._device_configs["dev"] = {"pending_settings": {"brightness": 70}}
    applied = _applied(dm)

    await dm._confirm_pending_settings(
        "dev", gone, {"brightness": ("brightness", 70)}, {"brightness": 70},
    )

    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert applied == []


async def test_an_unconfirmed_write_that_cannot_be_reported_still_settles_the_rest(dm):
    driver = await _device(dm)
    driver.set_state("brightness", 55)
    tried = _failing_error_reports(dm)
    applied = _applied(dm)
    await dm.store_pending_settings("dev", {"brightness": 70, "mode": "1"})
    original = driver.set_device_setting

    async def echo_mode_only(key, value):  # mode reads back, brightness does not
        driver.echo = key == "mode"
        return await original(key, value)

    driver.set_device_setting = echo_mode_only

    await dm._apply_pending_settings("dev")
    await dm._pending_confirm_tasks["dev"]

    assert tried == [
        "Pending setting 'brightness' was sent but the device still reports 55 "
        "instead of 70. It stays queued and will be sent again on the next connection."
    ]
    assert dm._device_configs["dev"]["pending_settings"] == {"brightness": 70}
    assert applied == [
        {"device_id": "dev", "applied": ["mode"], "refused": [], "remaining": {"brightness": 70}},
    ]


async def test_an_orphaned_device_queues_settings_as_given(dm):
    dm._device_configs["dev"] = {}

    await dm.store_pending_settings("dev", {"no_such_setting": "anything", "brightness": "700"})

    assert dm._device_configs["dev"]["pending_settings"] == {
        "no_such_setting": "anything", "brightness": "700",
    }


async def test_a_value_queued_in_another_form_clears_once_confirmed(dm):
    """A hand-edited project can queue ``"70"`` for an integer setting: the
    send coerces it, and the confirmation still clears the entry as queued."""
    driver = await _device(dm)
    dm._device_configs["dev"] = {"pending_settings": {"brightness": "70"}}

    await dm._apply_pending_settings("dev")
    await dm._pending_confirm_tasks["dev"]

    assert driver.writes == [("brightness", 70)]
    assert "pending_settings" not in dm._device_configs["dev"]


async def test_a_live_write_stops_waiting_when_the_device_drops(dm):
    driver = await _device(dm)
    driver.echo = False
    driver.set_state("brightness", 55)
    dm._confirm_window = lambda _driver: 30.0
    await dm.set_device_setting("dev", "brightness", 70)
    await driver.disconnect()

    result = await asyncio.wait_for(
        dm.await_setting_readback("dev", "brightness", 70), timeout=2.0,
    )

    assert result == (False, 55)
