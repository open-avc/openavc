"""A command's ``restarts_device_for`` is read once (``drivers/restart_window``),
and every reader reads it that way: the runtime's commanded-restart window,
the command the Device Audit lists, and the checker at the author's doors.

The reading is whole seconds from 1 to 600, or none: a usable number above 0,
rounded up and held to 600; 0 or less, a boolean, an infinity, NaN or a number
past a float is none. The table below is run through each reader in its own
words. The Programmer IDE's copy runs the same rows
(``restartWindow.test.ts``, from ``tests/fixtures/restart_seconds_cases.json``).

Invented devices ("acme_*") throughout; nothing here opens a socket.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import pytest

from openavc.audit.command_catalog import command_catalog
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.command_model import read_command
from openavc.drivers.restart_window import (
    RESTART_SECONDS_MAX,
    RESTART_SECONDS_MIN,
    restart_problem,
    restart_seconds,
)

_SHARED = json.loads(
    (Path(__file__).parent / "fixtures" / "restart_seconds_cases.json").read_text(encoding="utf-8")
)["cases"]

# The rows JSON cannot carry, beside the shared ones.
_PYTHON_ONLY = [
    {"name": "infinity", "value": math.inf, "seconds": None},
    {"name": "nan", "value": math.nan, "seconds": None},
    {"name": "past a float", "value": 10**400, "seconds": None},
    {"name": "text a float reads", "value": " 30 ", "seconds": 30},
    {"name": "text no float reads", "value": "soon", "seconds": None},
]
CASES = _SHARED + _PYTHON_ONLY


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [case["name"] for case in cases]


def test_the_range_is_the_contract_s():
    assert (RESTART_SECONDS_MIN, RESTART_SECONDS_MAX) == (1, 600)


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_reading(case):
    assert restart_seconds(case["value"]) == case["seconds"]


# --- the runtime -------------------------------------------------------------


class _AcmeDriver:
    """The smallest thing DeviceManager's send path accepts."""

    def __init__(self, restart: Any) -> None:
        self.DRIVER_INFO = {"id": "acme_widget", "commands": {"reboot": {"restarts_device_for": restart}}}
        self.config = {"host": "10.0.0.9", "port": 4001, "transport": "tcp"}
        self._state: dict[str, Any] = {"connected": True}
        self.transport = None
        self.last_transport_error = ""
        self.last_fault = None

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    async def send_command(self, command: str, params: dict) -> str:
        return "ok"


def _sent(restart: Any) -> DeviceManager:
    dm = DeviceManager(state=StateStore(), events=EventBus())
    driver = _AcmeDriver(restart)
    dm._devices["widget"] = driver
    dm._device_configs["widget"] = dict(driver.config)
    asyncio.run(dm.send_command("widget", "reboot", {}))
    return dm


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_runtime_opens_the_window_the_reading_says(case):
    # A NaN once opened a window no clock could end, and every later question
    # about it raised; a number past a float raised out of a send that had
    # already gone out.
    dm = _sent(case["value"])
    assert dm.restart_seconds_left("widget") == case["seconds"]
    assert dm.away_on_purpose("widget") is (case["seconds"] is not None)


# --- the Device Audit --------------------------------------------------------


class _AuditedDriver:
    def __init__(self, restart: Any) -> None:
        self.DRIVER_INFO = {
            "id": "acme_widget",
            "commands": {"reboot": {"label": "Reboot", "restarts_device_for": restart}},
        }


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_audit_lists_the_restart_the_runtime_opens(case):
    entry = command_catalog(_AuditedDriver(case["value"]))[0]
    assert entry["restarts_device_for"] == (case["seconds"] or 0)
    assert read_command("reboot", {"restarts_device_for": case["value"]}).restarts_device_for == case["seconds"]


# --- the author's doors --------------------------------------------------------


@pytest.mark.parametrize("value", [None, 1, 30, 600, 600.0])
def test_a_whole_number_in_the_range_or_none_is_no_problem(value):
    assert restart_problem(value) is None


@pytest.mark.parametrize(("value", "shown"), [
    (0, "0"), (-5, "-5"), (700, "700"), (601, "601"), (700.0, "700"), (1.5, "1.5"), (0.4, "0.4"),
    ("30", '"30"'), (True, "true"), (False, "false"), (math.inf, "inf"), ([30], "[30]"),
])
def test_anything_else_is_refused_naming_the_range(value, shown):
    assert restart_problem(value) == (
        f"must be a whole number of seconds from 1 to 600, not {shown}; "
        "leave it out when the command does not restart the device"
    )
