"""A command that restarts the device reports a window, not a fault.

The rule under test, in one sentence: for as long as the driver said its
restart would last, the device is absent WITHOUT `offline_reason` being set —
because `offline_reason` is what the cloud's error tally, every alert rule and
every automation condition read, and none of them should hear about an absence
somebody asked for.

Everything here uses an invented device (`acme_widget`), per the core-tests
rule: no real product names and no reads of openavc-drivers.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from openavc.api.error_messages import friendly_error
from openavc.core.connection_fault import (
    AUTH_FAILED,
    UNREACHABLE,
    ConnectionFaultError,
    restarting_message,
)
from openavc.core.device_manager import DeviceManager, device_restarting
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore

RESTART_SECONDS = 45


class _AcmeDriver:
    """The smallest thing DeviceManager's command and classify paths accept."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "transport": "tcp",
        "commands": {
            "reboot": {"restarts_device_for": RESTART_SECONDS},
            "set_level": {},
            "wake": {"available_offline": True},
            "absurd_reboot": {"restarts_device_for": 99_999},
        },
    }

    def __init__(self) -> None:
        self.config = {"host": "10.0.0.9", "port": 4001, "transport": "tcp"}
        self._state: dict[str, Any] = {"connected": True}
        self.sent: list[tuple[str, dict]] = []
        self.transport = None
        self.last_transport_error = ""
        self.last_fault = None
        self.fail_next = False

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    async def send_command(self, command: str, params: dict) -> str:
        if self.fail_next:
            raise RuntimeError("the send itself failed")
        self.sent.append((command, params))
        return "ok"


def _manager() -> tuple[DeviceManager, _AcmeDriver, StateStore]:
    state = StateStore()
    dm = DeviceManager(state=state, events=EventBus())
    driver = _AcmeDriver()
    dm._devices["widget"] = driver
    dm._device_configs["widget"] = dict(driver.config)
    return dm, driver, state


def _drop(dm: DeviceManager, driver: _AcmeDriver) -> str:
    """Classify a plain unreachable failure, the way the reconnect loop does."""
    driver._state["connected"] = False
    return dm._set_offline_reason(
        "widget", driver, exc=ConnectionFaultError("", code=UNREACHABLE)
    )


# --- arming ----------------------------------------------------------------


def test_a_command_that_declares_a_restart_arms_a_window():
    dm, driver, _ = _manager()
    assert dm.restart_seconds_left("widget") is None
    asyncio.run(dm.send_command("widget", "reboot", {}))
    assert dm.restart_seconds_left("widget") == RESTART_SECONDS


def test_an_ordinary_command_arms_nothing():
    dm, driver, _ = _manager()
    asyncio.run(dm.send_command("widget", "set_level", {"value": 3}))
    assert dm.restart_seconds_left("widget") is None


def test_arming_writes_no_state_until_the_device_actually_goes():
    # The window is a latent permission, not a state. A driver that
    # over-declares the field on a command the device shrugs off costs nothing
    # visible, which is the right way round for a number an author estimates.
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    assert state.get("device.widget.restarting") is None
    assert state.get("device.widget.offline_detail") is None


def test_a_send_that_raises_arms_nothing():
    # A command that never left the building has not restarted anything, and
    # arming on the attempt would suppress the very fault that stopped it.
    dm, driver, _ = _manager()
    driver.fail_next = True
    with pytest.raises(RuntimeError):
        asyncio.run(dm.send_command("widget", "reboot", {}))
    assert dm.restart_seconds_left("widget") is None


def test_a_declared_window_is_capped():
    # The contract caps this too; a driver dropped straight into driver_repo/
    # skips that validation, so the platform is the second gate.
    dm, driver, _ = _manager()
    asyncio.run(dm.send_command("widget", "absurd_reboot", {}))
    assert dm.restart_seconds_left("widget") == 600


# --- what gets published ---------------------------------------------------


def test_inside_the_window_no_offline_reason_is_published():
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    code = _drop(dm, driver)

    # The classified code still comes back: the reconnect policy hinges on
    # THIS failure, not on what was published about it.
    assert code == UNREACHABLE
    # But nothing downstream is told a fault happened.
    assert state.get("device.widget.offline_reason") is None
    assert state.get("device.widget.restarting") is True
    assert state.get("device.widget.offline_detail") == restarting_message(
        RESTART_SECONDS
    )


def test_without_a_window_the_fault_is_published_as_before():
    dm, driver, state = _manager()
    _drop(dm, driver)
    assert state.get("device.widget.offline_reason") == UNREACHABLE
    assert state.get("device.widget.restarting") is None
    assert "Can't reach" in (state.get("device.widget.offline_detail") or "")


def test_the_detail_counts_down_rather_than_repeating_the_declared_figure():
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    dm._restart_windows["widget"] = time.monotonic() + 10
    _drop(dm, driver)
    assert state.get("device.widget.offline_detail") == restarting_message(10)


# --- how the window ends ---------------------------------------------------


def test_reconnecting_ends_the_window():
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    _drop(dm, driver)
    dm._clear_offline_reason("widget")
    assert dm.restart_seconds_left("widget") is None
    assert state.get("device.widget.restarting") is None
    assert state.get("device.widget.offline_detail") is None


def test_expiry_publishes_the_real_fault():
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    _drop(dm, driver)
    assert state.get("device.widget.offline_reason") is None

    dm._restart_windows["widget"] = time.monotonic() - 0.01  # window runs out
    _drop(dm, driver)
    assert state.get("device.widget.offline_reason") == UNREACHABLE
    assert state.get("device.widget.restarting") is None


def test_a_permanent_fault_breaks_the_window_immediately():
    # auth_failed means the device is REACHABLE and refusing us, which is
    # positive evidence it is not still booting.
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    driver._state["connected"] = False
    code = dm._set_offline_reason(
        "widget", driver, exc=ConnectionFaultError("", code=AUTH_FAILED)
    )
    assert code == AUTH_FAILED
    assert state.get("device.widget.offline_reason") == AUTH_FAILED
    assert state.get("device.widget.restarting") is None
    assert dm.restart_seconds_left("widget") is None


def test_removing_the_device_drops_the_window():
    dm, driver, _ = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    asyncio.run(dm.remove_device("widget"))
    assert dm.restart_seconds_left("widget") is None


# --- what a press gets told ------------------------------------------------


def test_a_press_during_the_window_is_refused_with_the_waiting_sentence():
    dm, driver, _ = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    driver._state["connected"] = False

    with pytest.raises(ConnectionError) as caught:
        asyncio.run(dm.send_command("widget", "set_level", {"value": 3}))
    assert getattr(caught.value, "restart_seconds", None) == RESTART_SECONDS
    assert (
        friendly_error(caught.value, "Lobby Display")
        == "Lobby Display is restarting. It should be back in about 45 seconds."
    )


def test_a_press_with_no_window_still_says_not_connected():
    dm, driver, _ = _manager()
    driver._state["connected"] = False
    with pytest.raises(ConnectionError) as caught:
        asyncio.run(dm.send_command("widget", "set_level", {"value": 3}))
    assert getattr(caught.value, "restart_seconds", None) is None
    assert friendly_error(caught.value, "Lobby Display") == (
        "Lobby Display is not connected."
    )


def test_an_available_offline_command_still_runs_during_a_window():
    # Wake-on-LAN is exactly the command somebody presses while the device is
    # away; a window must not become a second gate in front of it.
    dm, driver, _ = _manager()
    asyncio.run(dm.send_command("widget", "reboot", {}))
    driver._state["connected"] = False
    assert asyncio.run(dm.send_command("widget", "wake", {})) == "ok"


def test_the_sentence_singularises_its_last_second():
    assert restarting_message(1) == "Restarting. It should be back in about 1 second."
    assert friendly_error(device_restarting("w", 1), "Lobby Display") == (
        "Lobby Display is restarting. It should be back in about 1 second."
    )
