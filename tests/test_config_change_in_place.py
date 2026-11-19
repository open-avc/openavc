"""A setting changed while the device runs reaches what its driver built from it.

Two doors change a running device's settings without re-adding it: a value
the device fills in (a config field's ``learned_from``) and a setup action's
``request_config_update``. Both end in ``DeviceManager.merge_live_config``,
which moves the driver's config and then tells the driver which settings
changed. What the driver built from its config when it was set up follows:
a YAML driver's reply patterns and derived values are worked out again, and
a declared child roster is rebuilt, for a Python driver as for a YAML one.

A reply table nothing in the change reaches is left as it is, so a throttle
window open on it keeps running; one the change reaches is compiled again,
and a write its throttle held is written then, never left on a timer the
connection's close no longer reaches.

Driven with INVENTED devices (Acme): through the real Engine, its learned
settings and its setup-action door for the doors, and on the driver and the
device manager for the rest.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig, ProjectConfig, load_project, save_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.setup_actions import SetupActionContext, SetupLease
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import register_driver, unregister_driver
from tests.helpers import wait_for_condition

_YAML_ID = "acme_unit_widget_in_place"


def _definition() -> dict[str, Any]:
    """An Acme widget addressed by a unit letter it also reports: its replies
    carry the letter, and a derived tag built from it."""
    return {
        "id": _YAML_ID,
        "name": "Acme Unit Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 23, "unit": "A", "outputs": 2, "poll_interval": 0},
        "config_schema": {
            "unit": {"type": "string", "label": "Unit", "learned_from": "unit_id"},
            "outputs": {"type": "integer", "label": "Outputs", "learned_from": "output_count"},
        },
        "config_derived": {"tag": "{unit}-01"},
        "state_variables": {
            "unit_id": {"type": "string", "label": "Unit"},
            "output_count": {"type": "integer", "label": "Output Count"},
            "power": {"type": "string", "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
            "meter": {"type": "integer", "label": "Meter"},
            "note": {"type": "string", "label": "Note"},
        },
        "child_entity_types": {
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "instances": {"count_from": "outputs"},
                "state_variables": {"level": {"type": "integer", "label": "Level"}},
            },
        },
        "commands": {"ping": {"label": "Ping", "send": "{tag} PING\r"}},
        "responses": [
            {"match": r"^{unit}PWR (\w+)$", "set": {"power": "$1"}},
            {"match": r"^{tag} LVL (\d+)$", "set": {"level": "$1"}},
            {"match": r"^{unit}MTR (\d+)$", "set": {"meter": "$1"}, "throttle": 30},
            {"match": r"^NOTE (\w+)$", "set": {"note": "$1"}, "throttle": 30},
        ],
    }


class _Capture:
    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def wait_idle(self) -> None:
        return None

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _yaml_class() -> type:
    definition = _definition()
    assert validate_driver_definition(definition) == []
    return create_configurable_driver_class(definition)


def _yaml_driver(config: dict[str, Any] | None = None) -> Any:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = _yaml_class()(
        "w1", {"host": "127.0.0.1", "port": 9000, "unit": "A", "outputs": 2,
               "poll_interval": 0, **(config or {})},
        state, events,
    )
    driver._register_declared_children()
    driver.transport = _Capture()
    driver._pace_lines = False
    return driver


async def _frames(driver: Any, *lines: str) -> None:
    for line in lines:
        await driver.on_data_received(line.encode())


def _manager_with(driver: Any) -> DeviceManager:
    """A device manager holding ``driver`` as device w1, the config it was
    handed kept beside it."""
    manager = DeviceManager(driver.state, driver.events)
    manager._devices["w1"] = driver
    manager._device_configs["w1"] = {
        "id": "w1", "driver": _YAML_ID, "name": "Widget", "config": dict(driver.config),
    }
    return manager


# ── the learned-setting door, through the real engine ──


class _ConnectsAndReports:
    """A connect that reports the unit and output count the way a driver
    that reads the device's identity while it connects does."""

    REPORT: dict[str, Any] = {}

    async def connect(self) -> None:
        for key, value in type(self).REPORT.items():
            self.set_state(key, value)
        self._connected = True
        self.set_state("connected", True)
        self.transport = _Capture()
        self._pace_lines = False
        await self.events.emit(f"device.connected.{self.device_id}")

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)


def _project_dict() -> dict[str, Any]:
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": "P"},
        "variables": [], "macros": [], "devices": [], "device_groups": [],
        "connections": {}, "scripts": [], "plugins": {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


@pytest.fixture
def reporting_widget():
    cls = type("AcmeReportingUnitWidget", (_ConnectsAndReports, _yaml_class()), {})
    cls.REPORT = {"unit_id": "B", "output_count": 4}
    register_driver(cls)
    yield cls
    unregister_driver(_YAML_ID)


async def test_a_learned_setting_reaches_the_replies_and_the_derived_values(tmp_path, reporting_widget):
    """The widget reports unit B where the project said A. Once B is saved,
    a reply addressed to B is read, one addressed to A is not, the derived
    tag says B, and the roster sized from the saved output count follows."""
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict()), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True

    async def quiet(msg, namespaces=None):
        return None

    eng.broadcast_ws = quiet
    eng.learned_config.start()
    project = eng.project.model_copy(deep=True)
    project.devices.append(DeviceConfig(
        id="w1", driver=_YAML_ID, name="Widget", config={"unit": "A", "outputs": 2}, enabled=True,
    ))
    await asyncio.wait_for(eng.apply_project(project), timeout=10)
    driver = eng.devices.get_driver("w1")
    await wait_for_condition(
        lambda: driver.config.get("unit") == "B" and driver.config.get("outputs") == 4,
        message="the reported unit and output count were never taken",
    )

    await _frames(driver, "BPWR on")
    await _frames(driver, "APWR off")
    await _frames(driver, "B-01 LVL 7")
    assert eng.state.get("device.w1.power") == "on"
    assert eng.state.get("device.w1.level") == 7
    assert driver.build_wire("ping") == b"B-01 PING\r"
    assert sorted(driver.list_children("output")) == [1, 2, 3, 4]
    assert eng.devices.get_driver("w1") is driver
    eng.learned_config.stop()
    await eng.devices.disconnect_all()


# ── the setup-action door, on a Python driver ──


_ZONED_ID = "acme_zoned_setup_in_place"


class _AcmeZoned(BaseDriver):
    """A Python driver whose zone roster is built from a setting its setup
    action writes."""

    DRIVER_INFO: dict[str, Any] = {
        "id": _ZONED_ID,
        "name": "Acme Zoned Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "default_config": {"zones": "A,B"},
        "config_schema": {"zones": {"type": "string", "label": "Zones"}},
        "state_variables": {},
        "child_entity_types": {
            "zone": {
                "label": "Zone",
                "id_format": {"type": "string"},
                "instances": {"ids_from": "zones"},
            },
        },
        "commands": {},
        "actions": [
            {"id": "rezone", "kind": "setup", "label": "Re-zone", "availability": "offline"},
        ],
    }
    refreshed: list[str] = []

    async def connect(self) -> None:
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def refresh_children(self) -> Any:
        type(self).refreshed.append(str(self.config.get("zones")))
        return {}


@pytest.fixture
async def zoned_engine(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data))
    register_driver(_AcmeZoned)
    _AcmeZoned.refreshed = []
    eng = Engine(str(tmp_path / "project.avc"))
    eng.project = ProjectConfig(**{
        "openavc_version": CURRENT_VERSION,
        "project": {"id": "hall", "name": "Hall"},
        "devices": [{"id": "z1", "name": "Zoned", "driver": _ZONED_ID, "config": {"zones": "A,B"}}],
        "connections": {"z1": {"host": "10.0.0.1", "port": 23}},
    })
    save_project(eng.project_path, eng.project)

    async def quiet(message, *, client_type=None):
        await asyncio.sleep(0)

    eng.broadcast_ws = quiet
    eng._running = True
    monkeypatch.setattr(eng, "_record_push_ports", MagicMock())
    monkeypatch.setattr(eng, "_schedule_device_bringup", MagicMock())
    with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
        await eng.devices.add_device(eng.resolved_device_config(eng.project.devices[0]), defer_connect=True)
        try:
            yield eng
        finally:
            await eng.devices._cancel_reconnect("z1")
            await eng.devices.disconnect_all()
            unregister_driver(_ZONED_ID)


async def test_a_setup_action_that_changes_a_roster_setting_rebuilds_the_roster(zoned_engine):
    driver = zoned_engine.devices.get_driver("z1")
    await driver.connect()
    context = SetupActionContext(zoned_engine, SetupLease("z1", driver))
    await context.apply_config_update({"zones": "A,B,C"})
    await wait_for_condition(
        lambda: _AcmeZoned.refreshed == ["A,B,C"],
        message="the zone roster was not rebuilt from the setting the setup action saved",
    )
    assert zoned_engine.devices.get_driver("z1") is driver


async def test_a_setup_action_that_changes_another_setting_leaves_the_roster(zoned_engine):
    driver = zoned_engine.devices.get_driver("z1")
    await driver.connect()
    context = SetupActionContext(zoned_engine, SetupLease("z1", driver))
    await context.apply_config_update({"port": 9999})
    for _ in range(10):
        await asyncio.sleep(0)
    assert _AcmeZoned.refreshed == []


async def test_a_roster_setting_saved_while_offline_is_left_to_the_next_connect(zoned_engine):
    """Refresh from Device reads the device, so it is not run while the
    device is offline; the driver reads its children when it connects."""
    driver = zoned_engine.devices.get_driver("z1")
    context = SetupActionContext(zoned_engine, SetupLease("z1", driver))
    await context.apply_config_update({"zones": "A,B,C"})
    for _ in range(10):
        await asyncio.sleep(0)
    assert _AcmeZoned.refreshed == []
    assert driver.config["zones"] == "A,B,C"


# ── what the driver re-derives, and what it leaves ──


async def test_a_derived_value_and_a_pattern_follow_the_change_at_once() -> None:
    driver = _yaml_driver()
    manager = _manager_with(driver)
    manager.merge_live_config("w1", {"unit": "B"})

    assert driver.config["tag"] == "B-01"
    await _frames(driver, "BPWR on", "APWR off", "A-01 LVL 3", "B-01 LVL 9")
    assert driver.get_state("power") == "on"
    assert driver.get_state("level") == 9
    assert manager.get_device_config("w1")["config"]["unit"] == "B"


async def test_a_yaml_roster_follows_its_setting_at_once() -> None:
    driver = _yaml_driver()
    _manager_with(driver).merge_live_config("w1", {"outputs": 5})
    assert sorted(driver.list_children("output")) == [1, 2, 3, 4, 5]


async def test_a_change_no_reply_reads_leaves_the_tables_and_their_windows() -> None:
    """A field no reply pattern or derived value reads: the tables are the
    same ones, so a throttle window open on them still holds the next
    reading for its end."""
    driver = _yaml_driver()
    tables = driver._compiled_responses
    await _frames(driver, "NOTE first")
    _manager_with(driver).merge_live_config("w1", {"outputs": 3})
    await _frames(driver, "NOTE second")

    assert driver._compiled_responses is tables
    assert driver.get_state("note") == "first"
    driver._drop_all_held()


async def test_a_held_reading_is_written_when_its_table_is_compiled_again() -> None:
    """A reading the meter's throttle held when the unit changed is written
    then, and no timer of the old table is left to write after the close."""
    driver = _yaml_driver()
    await _frames(driver, "AMTR 1", "AMTR 2")
    assert driver.get_state("meter") == 1
    old_states = [entry[3] for entry in driver._compiled_responses if entry[3]]

    _manager_with(driver).merge_live_config("w1", {"unit": "B"})

    assert driver.get_state("meter") == 2
    assert all(not state.get("timers") and not state.get("pending") for state in old_states)
    await _frames(driver, "BMTR 3")
    assert driver.get_state("meter") == 3
    driver._drop_all_held()


async def test_a_setting_given_its_own_value_tells_the_driver_nothing() -> None:
    driver = _yaml_driver()
    tables = driver._compiled_responses
    told: list[set[str]] = []
    driver._config_changed = told.append
    _manager_with(driver).merge_live_config("w1", {"unit": "A", "outputs": 2})
    assert told == []
    assert driver._compiled_responses is tables


async def test_a_driver_that_fails_to_follow_is_logged_and_the_change_still_lands(caplog) -> None:
    driver = _yaml_driver()
    manager = _manager_with(driver)

    def refuse(changed: set[str]) -> None:
        raise RuntimeError("cannot follow")

    driver._config_changed = refuse
    with caplog.at_level(logging.ERROR):
        manager.merge_live_config("w1", {"unit": "B"})

    assert driver.config["unit"] == "B"
    assert manager.get_device_config("w1")["config"]["unit"] == "B"
    assert any("cannot follow" in (r.exc_text or "") for r in caplog.records)
