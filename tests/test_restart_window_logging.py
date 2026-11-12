"""A device inside a restart its own command declared logs its silence as info.

A command declaring ``restarts_device_for`` tells the platform the device will
go quiet; the card says it is restarting. The polls and liveness probes that go
unanswered inside that window, and the drop they lead to, are expected then, so
they are logged at info and say the device is restarting. Outside a window, or
once it has run out, they are warnings as before.

Uses an invented device (``acme_widget``), per the core-tests rule.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.polling import _PollWatch
from openavc.drivers.registry import register_driver, unregister_driver

LOGGER = "openavc.drivers.base"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_restart_log",
        "name": "Acme Widget",
        "category": "test",
        "transport": "tcp",
        "state_variables": {},
        "commands": {"reboot": {"restarts_device_for": 45}},
    }

    HEALTH_INTERVAL_S = 0.01
    HEALTH_TIMEOUT_S = 0.05
    HEALTH_MAX_FAILURES = 2

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        raise ConnectionError("No widget answered")

    async def _liveness_probe(self) -> None:
        raise ConnectionError()


class _FakeTransport:
    connected = True
    last_error = ""

    async def send(self, data: bytes) -> None:
        pass

    async def close(self) -> None:
        self.connected = False


def _widget(restart_left: int | None) -> _AcmeWidget:
    driver = _AcmeWidget("widget", {}, StateStore(), EventBus())
    driver.transport = _FakeTransport()
    driver._connected = True
    driver.set_state("connected", True)
    driver._restart_window = lambda: restart_left
    return driver


def _lines(caplog, level: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelname == level]


async def _miss_polls(driver: _AcmeWidget) -> None:
    watch = _PollWatch(max_missed=2, watching=False, reading=False)
    for _ in range(2):
        await driver._poll_once(watch, ())
    driver._drop_for_failed_polls(watch)


async def _miss_probes(driver: _AcmeWidget) -> None:
    driver._start_health_loop()
    await asyncio.sleep(0.3)


@pytest.mark.parametrize("miss", [_miss_polls, _miss_probes], ids=["poll", "probe"])
async def test_inside_a_declared_restart_the_silence_is_info(miss, caplog):
    driver = _widget(restart_left=45)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await miss(driver)
    assert _lines(caplog, "WARNING") == []
    info = _lines(caplog, "INFO")
    assert len(info) >= 3
    assert all(line.endswith("; the device is restarting (45 s left)") for line in info), info


@pytest.mark.parametrize("miss", [_miss_polls, _miss_probes], ids=["poll", "probe"])
async def test_outside_a_restart_the_silence_is_still_a_warning(miss, caplog):
    driver = _widget(restart_left=None)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await miss(driver)
    warnings = _lines(caplog, "WARNING")
    assert len(warnings) >= 3
    assert not any("restarting" in line for line in warnings)


async def test_the_missed_poll_line_names_the_failure_and_the_restart(caplog):
    driver = _widget(restart_left=12)
    watch = _PollWatch(max_missed=2, watching=False, reading=False)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await driver._poll_once(watch, ())
    assert _lines(caplog, "INFO") == [
        "[widget] Poll failed (connection): No widget answered; the device is restarting (12 s left)",
    ]


async def test_the_device_manager_hands_each_driver_its_own_window():
    register_driver(_AcmeWidget)
    try:
        dm = DeviceManager(state=StateStore(), events=EventBus())
        await dm.add_device(
            {"id": "widget", "driver": "acme_widget_restart_log", "name": "Widget", "config": {}},
            defer_connect=True,
        )
        driver = dm.get_driver("widget")
        assert driver._restart_window() is None
        dm._arm_restart_window("widget", driver, "reboot")
        assert driver._restart_window() == 45
        dm._end_restart_window("widget")
        assert driver._restart_window() is None
    finally:
        unregister_driver("acme_widget_restart_log")
