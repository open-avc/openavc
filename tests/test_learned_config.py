"""Settings the device fills in: a config field's ``learned_from``.

Exercises ``core/learned_config.py`` through the real Engine, project file and
DeviceManager, with an INVENTED device (Acme), never a real product. The Acme
widget reports its model, channel count and MAC address while it connects,
before ``connected`` is true, which is the case that only the connect trigger
can catch.

Pinned here: the values land in the project file on disk in one save; the
running driver keeps running with its config updated (no teardown); open IDE
tabs are told; a simulated device, a value the field does not accept, a device
that is not connected and a running setup action all write nothing; a wrong
value picked offline is corrected; a report that changes later is saved too.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from openavc.core.device_config import apply_config_delta
from openavc.core.engine import Engine
from openavc.core.learned_config import accepted_value, learned_fields
from openavc.core.project_loader import DeviceConfig, load_project
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from tests.helpers import wait_for_condition

_DRIVER_ID = "acme_widget_learned_test"


class _AcmeWidget(BaseDriver):
    """Reports its identity while connecting, the way a driver that reads an
    About page in its post-connect step does."""

    DRIVER_INFO: dict[str, Any] = {
        "id": _DRIVER_ID,
        "name": "Acme Widget (learned config test)",
        "transport": "tcp",
        "default_config": {"port": 5000, "model": "", "channels": "2", "mac_address": ""},
        "config_schema": {
            "model": {
                "type": "enum", "label": "Model", "learned_from": "model",
                "values": [
                    {"value": "", "label": "Detect automatically"},
                    {"value": "W100", "label": "W100"},
                    {"value": "W200", "label": "W200"},
                ],
            },
            "channels": {
                "type": "enum", "label": "Channels", "learned_from": "channel_count",
                "values": [{"value": "2", "label": "2"}, {"value": "4", "label": "4"}],
            },
            "mac_address": {"type": "string", "label": "MAC Address", "learned_from": "mac_address"},
        },
        "state_variables": {
            "model": {"type": "string", "label": "Model"},
            "channel_count": {"type": "integer", "label": "Channels"},
            "mac_address": {"type": "string", "label": "MAC Address"},
        },
        "commands": {},
    }

    # What the next connect reports, set by each test.
    REPORT: dict[str, Any] = {}

    async def connect(self) -> None:
        for key, value in type(self).REPORT.items():
            self.set_state(key, value)
        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
def acme_widget():
    register_driver(_AcmeWidget)
    _AcmeWidget.REPORT = {"model": "W200", "channel_count": 4, "mac_address": "00:11:22:aa:bb:cc"}
    yield _AcmeWidget
    unregister_driver(_DRIVER_ID)


def _project_dict(devices=None) -> dict:
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": "P"},
        "variables": [],
        "macros": [],
        "devices": devices or [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": {},
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []}],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _engine(tmp_path) -> tuple[Engine, list[dict]]:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict()), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    sent: list[dict] = []

    async def record(msg, namespaces=None):
        sent.append(msg)

    eng.broadcast_ws = record
    eng.learned_config.start()
    return eng, sent


def _disk_config(eng, device_id="w1") -> dict:
    data = json.loads(eng.project_path.read_text(encoding="utf-8"))
    return next(d for d in data["devices"] if d["id"] == device_id)["config"]


async def _add(eng, config=None) -> int:
    project = eng.project.model_copy(deep=True)
    project.devices.append(DeviceConfig(
        id="w1", driver=_DRIVER_ID, name="Widget", config=config or {}, enabled=True,
    ))
    await asyncio.wait_for(eng.apply_project(project), timeout=10)
    # The apply's own save; anything learned is counted after it.
    applied = eng._revision_counter
    await wait_for_condition(
        lambda: eng.state.get("device.w1.connected") is True,
        message="the widget never connected",
    )
    return applied


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_reported_values_are_saved_in_one_write(tmp_path, acme_widget):
    eng, sent = _engine(tmp_path)
    after_add = await _add(eng)
    driver = eng.devices.get_driver("w1")
    await wait_for_condition(
        lambda: eng._revision_counter == after_add + 1,
        message="the learned values were never saved",
    )
    await _settle()
    # One save for the three fields, reported together while connecting.
    assert eng._revision_counter == after_add + 1
    on_disk = _disk_config(eng)
    assert on_disk["model"] == "W200"
    # A reported 4 is stored as the listed value "4", the type the field uses.
    assert on_disk["channels"] == "4"
    assert on_disk["mac_address"] == "00:11:22:aa:bb:cc"
    # The running driver kept running, with its config updated in place.
    assert eng.devices.get_driver("w1") is driver
    assert driver.config["model"] == "W200"
    assert eng.state.get("device.w1.connected") is True
    # Open IDE tabs were told.
    reloads = [m for m in sent if m.get("type") == "project.reloaded"]
    assert reloads and reloads[-1]["revision"] == eng._project_revision


@pytest.mark.asyncio
async def test_a_wrong_pick_is_corrected(tmp_path, acme_widget):
    eng, _ = _engine(tmp_path)
    after_add = await _add(eng, config={"model": "W100", "channels": "2"})
    await wait_for_condition(lambda: eng._revision_counter > after_add)
    on_disk = _disk_config(eng)
    assert on_disk["model"] == "W200"
    assert on_disk["channels"] == "4"


@pytest.mark.asyncio
async def test_a_matching_value_writes_nothing(tmp_path, acme_widget):
    acme_widget.REPORT = {"model": "W100", "channel_count": 2}
    eng, _ = _engine(tmp_path)
    after_add = await _add(eng, config={"model": "W100"})
    await _settle()
    assert eng._revision_counter == after_add


@pytest.mark.asyncio
async def test_a_value_the_field_does_not_accept_is_left(tmp_path, acme_widget, caplog):
    acme_widget.REPORT = {"model": "W999", "channel_count": 6}
    eng, _ = _engine(tmp_path)
    after_add = await _add(eng, config={"model": "W100"})
    await _settle()
    assert eng._revision_counter == after_add
    assert _disk_config(eng)["model"] == "W100"
    assert "does not accept" in caplog.text


@pytest.mark.asyncio
async def test_a_simulated_device_writes_nothing(tmp_path, acme_widget):
    eng, _ = _engine(tmp_path)
    eng.simulation.is_redirected = lambda device_id: True
    after_add = await _add(eng)
    await _settle()
    assert eng._revision_counter == after_add
    assert _disk_config(eng).get("model", "") == ""


@pytest.mark.asyncio
async def test_a_later_report_is_saved_and_a_disconnected_one_is_not(tmp_path, acme_widget):
    acme_widget.REPORT = {}
    eng, _ = _engine(tmp_path)
    after_add = await _add(eng)
    await _settle()
    assert eng._revision_counter == after_add
    driver = eng.devices.get_driver("w1")
    driver.set_state("model", "W100")
    await wait_for_condition(lambda: eng._revision_counter == after_add + 1)
    assert _disk_config(eng)["model"] == "W100"
    # Not connected: nothing is saved.
    driver.set_state("connected", False)
    driver.set_state("model", "W200")
    await _settle()
    assert eng._revision_counter == after_add + 1
    assert _disk_config(eng)["model"] == "W100"


@pytest.mark.asyncio
async def test_a_running_setup_action_writes_nothing(tmp_path, acme_widget):
    acme_widget.REPORT = {}
    eng, _ = _engine(tmp_path)
    after_add = await _add(eng)
    eng.setup_actions.is_running = lambda device_id: True
    eng.devices.get_driver("w1").set_state("model", "W200")
    await _settle()
    assert eng._revision_counter == after_add


def test_accepted_value():
    enum_str = {"type": "enum", "values": [{"value": "2"}, {"value": "4"}]}
    assert accepted_value(enum_str, 4) == (True, "4")
    assert accepted_value(enum_str, "4") == (True, "4")
    assert accepted_value(enum_str, 8) == (False, None)
    enum_int = {"type": "enum", "values": [2, 4, 8]}
    assert accepted_value(enum_int, 4) == (True, 4)
    assert accepted_value(enum_int, "8") == (True, 8)
    # A blank "detect automatically" entry is never what a report fills in.
    assert accepted_value({"type": "enum", "values": ["", "W1"]}, "") == (False, None)
    integer = {"type": "integer", "min": 2, "max": 4}
    assert accepted_value(integer, 4) == (True, 4)
    assert accepted_value(integer, "3") == (True, 3)
    assert accepted_value(integer, 5) == (False, None)
    assert accepted_value(integer, 2.5) == (False, None)
    assert accepted_value(integer, True) == (False, None)
    assert accepted_value({"type": "boolean"}, "1") == (True, True)
    assert accepted_value({"type": "boolean"}, False) == (True, False)
    assert accepted_value({"type": "string"}, " aa:bb ") == (True, "aa:bb")
    assert accepted_value({"type": "string"}, "") == (False, None)
    assert accepted_value({"type": "string"}, None) == (False, None)
    assert accepted_value({"type": "table"}, "x") == (False, None)


def test_learned_fields_skips_secrets():
    class _D:
        DRIVER_INFO = {"config_schema": {
            "a": {"learned_from": "x"},
            "b": {"learned_from": "y", "secret": True},
            "c": {"type": "string"},
        }}
    assert learned_fields(_D()) == {"a": "x"}


def test_apply_config_delta_splits_connection_fields():
    project = load_project_dict(_project_dict(devices=[{
        "id": "w1", "driver": _DRIVER_ID, "name": "W", "config": {"keep": 1}, "enabled": True,
    }]))
    assert apply_config_delta(project, "w1", {"port": 6000, "model": "W200"})
    assert project.connections["w1"] == {"port": 6000}
    dev = next(d for d in project.devices if d.id == "w1")
    assert dev.config == {"keep": 1, "model": "W200"}
    assert apply_config_delta(project, "nobody", {"model": "W1"}) is False


def load_project_dict(data: dict):
    from openavc.core.project_loader import ProjectConfig
    from openavc.core.project_migration import migrate_project

    migrated, _ = migrate_project(data)
    return ProjectConfig.model_validate(migrated)
