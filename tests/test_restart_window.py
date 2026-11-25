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


# --- the checker: refused at the author's doors, named where a driver loads ---

from openavc.drivers.avcdriver_semantic import (  # noqa: E402
    check_driver_definition,
    validate_driver_definition,
)
from openavc.drivers.driver_loader import read_driver_file  # noqa: E402
from openavc.drivers.python_info import (  # noqa: E402
    UNEVALUATED,
    check_python_driver_info,
    python_driver_refusals,
)

PLACE = "commands.reboot.restarts_device_for"


def _range(shown: str) -> str:
    return (
        f"{PLACE}: must be a whole number of seconds from 1 to 600, not {shown}; "
        "leave it out when the command does not restart the device"
    )


def _yaml_unit(**reboot: Any) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 23},
        "commands": {"reboot": {"label": "Reboot", "send": "REBOOT\r", **reboot}},
    }


def _python_info(**reboot: Any) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"reboot": {"label": "Reboot", **reboot}},
    }


_REFUSED = [(0, "0"), (-5, "-5"), (700, "700"), (700.0, "700"), (1.5, "1.5"), ("30", '"30"'), (True, "true")]


@pytest.mark.parametrize(("value", "shown"), _REFUSED)
def test_a_yaml_driver_s_value_outside_the_range_is_refused_at_the_authors_doors_once(value, shown):
    # Catalog CI's schema refused these; the platform's own checker passed
    # -5, 0 and 700. One sentence at the place, the shape walk's own about
    # the same value left out.
    errors = validate_driver_definition(_yaml_unit(restarts_device_for=value), strict=True)
    said = [e for e in errors if e.startswith(PLACE)]
    assert said == [_range(shown)], errors


@pytest.mark.parametrize(("value", "shown"), _REFUSED)
def test_an_installed_yaml_driver_keeps_loading_and_names_it(value, shown, tmp_path):
    verdict = check_driver_definition(_yaml_unit(restarts_device_for=value))
    assert not [e for e in verdict.errors if PLACE in e], verdict.errors
    assert [n for n in verdict.notices if n.startswith(PLACE)] == [_range(shown)], verdict.notices

    path = tmp_path / "acme_widget.avcdriver"
    import yaml

    path.write_text(yaml.safe_dump(_yaml_unit(restarts_device_for=value)), encoding="utf-8")
    read = read_driver_file(path)
    assert read.definition is not None, read.reasons
    assert [n for n in read.notices if n.startswith(PLACE)] == [_range(shown)], read.notices


@pytest.mark.parametrize("value", [None, 1, 90, 600])
def test_a_yaml_driver_in_the_range_or_without_one_is_not(value):
    reboot = {} if value is None else {"restarts_device_for": value}
    assert not [e for e in validate_driver_definition(_yaml_unit(**reboot), strict=True) if PLACE in e]


@pytest.mark.parametrize(("value", "shown"), _REFUSED)
def test_a_python_driver_s_value_outside_the_range_is_refused_at_the_authors_doors(value, shown):
    # A DRIVER_INFO's value was refused by catalog CI's schema alone:
    # drivers.check and the Code view's save passed 1.5 and 700.
    assert _range(shown) in python_driver_refusals(_python_info(restarts_device_for=value))
    verdict = check_python_driver_info(_python_info(restarts_device_for=value))
    assert _range(shown) not in verdict.errors
    assert _range(shown) in verdict.notices


def test_a_python_value_built_in_code_is_not_judged():
    info = _python_info(restarts_device_for=UNEVALUATED)
    assert not [e for e in python_driver_refusals(info) if PLACE in e]


async def test_an_installed_python_driver_keeps_loading_and_names_it(tmp_path, monkeypatch):
    from openavc.drivers import driver_loader, refused_files, registry

    monkeypatch.setattr(
        registry, "_DRIVER_REGISTRY",
        {k: v for k, v in registry._DRIVER_REGISTRY.items() if not k.startswith("acme")},
    )
    monkeypatch.setattr(refused_files, "_refused", {})
    monkeypatch.setattr(refused_files, "_noticed", {})
    (tmp_path / "acme_widget.py").write_text(
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        "        'id': 'acme_widget',\n"
        "        'name': 'Acme Widget',\n"
        "        'transport': 'tcp',\n"
        "        'commands': {'reboot': {'label': 'Reboot', 'restarts_device_for': 700}},\n"
        "    }\n",
        encoding="utf-8",
    )
    try:
        assert driver_loader.load_all_drivers([tmp_path]) == 1
        assert registry.is_driver_registered("acme_widget")
        notices = refused_files.load_notices(tmp_path / "acme_widget.py")
        assert _range("700") in notices
    finally:
        import sys

        sys.modules.pop("acme_widget", None)


# --- the sleep rule: a wake command's window --------------------------------------


def _sleeping(wake_restart: Any) -> dict[str, Any]:
    unit = _yaml_unit()
    unit["commands"] = {
        "power_off": {"label": "Off", "send": "OFF\r", "sleeps_device": "power_on"},
        "power_on": {"label": "On", "send": "ON\r", "available_offline": True, "restarts_device_for": wake_restart},
    }
    return unit


def test_a_wake_command_with_no_window_written_is_refused_at_every_door():
    unit = _sleeping(None)
    assert any("declares no restarts_device_for" in e for e in check_driver_definition(unit).errors)


@pytest.mark.parametrize("value", [-5, 0])
def test_a_wake_command_s_window_outside_the_range_is_the_range_rule_s_to_say(value):
    # Said once, by the range rule, at the author's doors; a copy installed
    # with it keeps loading, as before for -5.
    unit = _sleeping(value)
    errors = validate_driver_definition(unit, strict=True)
    assert not any("declares no restarts_device_for" in e for e in errors), errors
    assert f"commands.power_on.restarts_device_for: must be a whole number of seconds from 1 to 600, not {value}; " \
        "leave it out when the command does not restart the device" in errors
    assert not check_driver_definition(unit).errors
