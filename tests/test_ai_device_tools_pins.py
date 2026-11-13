"""The AI's device and device-group tools, pinned where nothing else looked.

Each passes on the code as it is, so a restructure that changes an answer, a
write or a log line fails here:

- ``update_device``: no project, an unknown device, a driver that is not
  installed (warned and saved), product hardware selected under another id
  (refused with a connection, reassigned without one), every connection field
  sent as null (the connections row goes), the required-field check asked only
  when the settings or the driver change and then only for a gap the edit
  makes (an older device's blank field rides back on the answer), and an edit
  that leaves the device's other settings and its connections row alone.
- ``add_device``: no project, no id, an id already in use, a driver that is
  not installed, the name defaulting to the id, the address landing in the
  connections table, nothing written there for a device sent no address.
- The three group tools: every refusal, the name defaulting to the id, a
  rename alone, a membership change alone.
- ``delete_device``: the device's connections row, monitors and group
  memberships go with it, and what still points at it rides back.
- The settings tools, the connection test and the command tool's answers.

An invented device throughout.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    DeviceConfig,
    DeviceGroup,
    MonitorConfig,
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)
from openavc.drivers.base import BaseDriver, CommandParamError
from openavc.drivers.registry import register_driver, unregister_driver


class _AcmeWidget(BaseDriver):
    """Connects without a socket; one ordinary setting, no required field."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 4000},
        "config_schema": {
            "host": {"type": "string", "label": "IP Address"},
            "mode": {"type": "string", "label": "Mode"},
        },
        "state_variables": {},
        "commands": {"power_on": {"label": "Power On", "params": {}}},
        "device_settings": {
            "brightness": {"type": "integer", "label": "Brightness"},
            "admin_pin": {"type": "string", "secret": True, "label": "Admin PIN"},
        },
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _AcmeStrict(_AcmeWidget):
    """The same, with a required setting that has no default."""

    DRIVER_INFO: dict[str, Any] = {
        **_AcmeWidget.DRIVER_INFO,
        "id": "acme_strict",
        "config_schema": {
            "host": {"type": "string", "label": "IP Address"},
            "zone": {"type": "string", "required": True, "label": "Zone"},
        },
    }


class _AcmeKeyed(_AcmeWidget):
    """The same, with a key its driver marks secret."""

    DRIVER_INFO: dict[str, Any] = {
        **_AcmeWidget.DRIVER_INFO,
        "id": "acme_keyed",
        "config_schema": {
            "host": {"type": "string", "label": "IP Address"},
            "api_key": {"type": "string", "secret": True, "label": "API Key"},
        },
    }


@pytest.fixture
async def setup(tmp_path):
    register_driver(_AcmeWidget)
    register_driver(_AcmeStrict)
    register_driver(_AcmeKeyed)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        devices=[
            DeviceConfig(id="dev1", driver="acme_widget", name="Dev 1", config={"mode": "eco"}),
            DeviceConfig(id="dev2", driver="acme_widget", name="Dev 2", config={}),
            # Saved before its driver required a zone: an older device.
            DeviceConfig(id="old1", driver="acme_strict", name="Old 1", config={}),
            DeviceConfig(id="key1", driver="acme_keyed", name="Key 1", config={}),
        ],
        connections={
            "dev1": {"host": "192.0.2.10", "port": 4000},
            "dev2": {"host": "192.0.2.11"},
        },
        device_groups=[DeviceGroup(id="front", name="Front", device_ids=["dev1", "dev2"])],
        monitors=[
            MonitorConfig(key="device.dev1.power"),
            MonitorConfig(key="device.dev2.power"),
        ],
    )
    save_project(project_path, engine.project)
    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))

    rest.set_engine(engine)
    ws.set_engine(engine)
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    try:
        yield handler, engine
    finally:
        await engine.devices.disconnect_all()
        rest.set_engine(None)
        ws.set_engine(None)
        unregister_driver("acme_widget")
        unregister_driver("acme_strict")
        unregister_driver("acme_keyed")


def _on_disk(engine) -> ProjectConfig:
    return load_project(str(engine.project_path))


def _device(project, device_id: str) -> DeviceConfig:
    return next(d for d in project.devices if d.id == device_id)


def _no_engine(engine: Any) -> AIToolHandler:
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    handler._get_engine = lambda: engine
    return handler


# --- update_device ----------------------------------------------------------------


@pytest.mark.parametrize("engine", [None, SimpleNamespace(project=None)])
async def test_update_device_without_a_project(engine):
    answer = await _no_engine(engine)._update_device({"device_id": "dev1", "name": "X"})
    assert answer == {"error": "No project loaded"}


async def test_update_device_names_an_unknown_device(setup):
    handler, engine = setup
    before = engine.project_path.read_text(encoding="utf-8")

    answer = await handler._update_device({"device_id": "ghost", "name": "X"})

    assert answer == {"error": "Device 'ghost' not found"}
    assert engine.project_path.read_text(encoding="utf-8") == before


async def test_update_device_onto_a_driver_not_installed_warns_and_saves(setup, caplog):
    handler, engine = setup
    caplog.set_level(logging.WARNING)

    answer = await handler._update_device({"device_id": "dev2", "driver": "acme_absent"})

    assert answer == {"status": "updated", "device_id": "dev2"}
    assert _device(_on_disk(engine), "dev2").driver == "acme_absent"
    assert [r.getMessage() for r in caplog.records if "not in registry" in r.getMessage()] == [
        "update_device: driver 'acme_absent' not in registry (may not be loaded yet)",
    ]


async def test_update_device_onto_the_same_driver_does_not_warn(setup, caplog):
    handler, _engine = setup
    caplog.set_level(logging.WARNING)

    await handler._update_device({"device_id": "dev2", "driver": "acme_widget", "name": "Dev Two"})

    assert not [r for r in caplog.records if "not in registry" in r.getMessage()]


async def test_update_device_with_every_connection_field_null_drops_the_row(setup):
    handler, engine = setup

    answer = await handler._update_device({"device_id": "dev1", "config": {"host": None, "port": None}})

    assert answer == {"status": "updated", "device_id": "dev1"}
    on_disk = _on_disk(engine)
    assert "dev1" not in on_disk.connections
    assert _device(on_disk, "dev1").config == {"mode": "eco"}
    assert "dev1" not in engine.project.connections


async def test_update_device_config_lands_each_half_where_it_belongs(setup):
    handler, engine = setup

    answer = await handler._update_device(
        {"device_id": "dev2", "config": {"host": "192.0.2.20", "mode": "cool", "port": 4100}},
    )

    assert answer == {"status": "updated", "device_id": "dev2"}
    on_disk = _on_disk(engine)
    assert on_disk.connections["dev2"] == {"host": "192.0.2.20", "port": 4100}
    assert _device(on_disk, "dev2").config == {"mode": "cool"}
    # Another device's row and settings are not touched.
    assert on_disk.connections["dev1"] == {"host": "192.0.2.10", "port": 4000}
    assert _device(on_disk, "dev1").config == {"mode": "eco"}


async def test_update_device_with_an_empty_config_keeps_everything(setup):
    handler, engine = setup

    answer = await handler._update_device({"device_id": "dev1", "config": {}})

    assert answer == {"status": "updated", "device_id": "dev1"}
    on_disk = _on_disk(engine)
    assert on_disk.connections["dev1"] == {"host": "192.0.2.10", "port": 4000}
    assert _device(on_disk, "dev1").config == {"mode": "eco"}


async def test_update_device_with_a_null_config_keeps_everything(setup):
    handler, engine = setup

    answer = await handler._update_device({"device_id": "dev1", "config": None})

    assert answer == {"status": "updated", "device_id": "dev1"}
    on_disk = _on_disk(engine)
    assert on_disk.connections["dev1"] == {"host": "192.0.2.10", "port": 4000}
    assert _device(on_disk, "dev1").config == {"mode": "eco"}


_ZONE_STILL_BLANK = {
    "missing_config": ["zone"],
    "missing_config_note": "Still blank: Zone, which its driver requires. Ask for it and send it in config.",
}


async def test_update_device_rename_alone_never_asks_for_a_required_field(setup):
    handler, engine = setup

    answer = await handler._update_device({"device_id": "old1", "name": "Old One"})

    assert answer == {"status": "updated", "device_id": "old1", **_ZONE_STILL_BLANK}
    assert _device(_on_disk(engine), "old1").name == "Old One"


async def test_update_device_settings_on_an_older_device_save_as_edit_device_saves_them(setup):
    """PATCH /api/devices/{id} refuses only a gap its own edit makes; so does this."""
    handler, engine = setup

    answer = await handler._update_device({"device_id": "old1", "config": {"host": "192.0.2.30"}})

    assert answer == {"status": "updated", "device_id": "old1", **_ZONE_STILL_BLANK}
    on_disk = _on_disk(engine)
    assert on_disk.connections["old1"] == {"host": "192.0.2.30"}
    assert _device(on_disk, "old1").config == {}


async def test_update_device_onto_the_same_driver_on_an_older_device_saves(setup):
    handler, engine = setup

    answer = await handler._update_device({"device_id": "old1", "driver": "acme_strict", "name": "Old One"})

    assert answer == {"status": "updated", "device_id": "old1", **_ZONE_STILL_BLANK}
    assert _device(_on_disk(engine), "old1").name == "Old One"


async def test_update_device_blanking_a_required_field_is_refused(setup):
    handler, engine = setup
    assert (await handler._add_device(
        {"id": "zoned", "driver": "acme_strict", "config": {"zone": "north"}},
    ))["status"] == "created"
    before = engine.project_path.read_text(encoding="utf-8")

    answer = await handler._update_device({"device_id": "zoned", "config": {"zone": None, "host": "192.0.2.31"}})

    assert answer == {"error": "Zone is required.", "missing_config": ["zone"]}
    assert engine.project_path.read_text(encoding="utf-8") == before


async def test_update_device_with_every_required_field_filled_says_nothing_more(setup):
    handler, _engine = setup

    answer = await handler._update_device({"device_id": "old1", "config": {"zone": "south"}})

    assert answer == {"status": "updated", "device_id": "old1"}


async def test_update_device_onto_a_stricter_driver_asks_for_its_required_field(setup):
    handler, engine = setup

    answer = await handler._update_device({"device_id": "dev2", "driver": "acme_strict"})

    assert answer == {"error": "Zone is required.", "missing_config": ["zone"]}
    assert _device(_on_disk(engine), "dev2").driver == "acme_widget"


async def test_update_device_name_and_enabled_default_to_the_record(setup):
    handler, engine = setup

    await handler._update_device({"device_id": "dev1", "config": {"mode": "cool"}})

    device = _device(_on_disk(engine), "dev1")
    assert (device.name, device.enabled, device.driver) == ("Dev 1", True, "acme_widget")


class _Policy:
    """Product hardware: one manufacturer driver, owned by one device id.
    Everything else is the engine's own policy (which has no manifest)."""

    manifest = {"driver": "acme_fw"}
    driver_id = "acme_fw"

    def __init__(self, owner: str, real: Any = None) -> None:
        self.owner = owner
        self._real = real

    def owns(self, device_id: str) -> bool:
        return device_id == self.owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


async def test_update_device_onto_product_hardware_with_a_connection_is_refused(setup, monkeypatch):
    handler, engine = setup
    monkeypatch.setattr(engine, "installed_equipment", _Policy("built_in", engine.installed_equipment))
    reassign = AsyncMock()
    monkeypatch.setattr(engine, "reassign_installed_equipment", reassign)

    answer = await handler._update_device(
        {"device_id": "dev2", "driver": "acme_fw", "config": {"host": "192.0.2.40"}},
    )

    assert answer == {
        "error": "Product hardware uses its manufacturer connection. Select it without connection overrides.",
    }
    reassign.assert_not_awaited()


async def test_update_device_onto_product_hardware_reassigns_it(setup, monkeypatch):
    handler, engine = setup
    monkeypatch.setattr(engine, "installed_equipment", _Policy("built_in", engine.installed_equipment))
    reassign = AsyncMock()
    monkeypatch.setattr(engine, "reassign_installed_equipment", reassign)

    answer = await handler._update_device(
        {"device_id": "dev2", "driver": "acme_fw", "name": "Main Unit", "config": {}},
    )

    assert answer == {"status": "updated", "device_id": "dev2"}
    reassign.assert_awaited_once_with("dev2", name="Main Unit", enabled=None)
    assert _device(_on_disk(engine), "dev2").driver == "acme_widget"


async def test_update_device_onto_product_hardware_answers_its_refusal(setup, monkeypatch):
    handler, engine = setup
    monkeypatch.setattr(engine, "installed_equipment", _Policy("built_in", engine.installed_equipment))
    monkeypatch.setattr(
        engine, "reassign_installed_equipment",
        AsyncMock(side_effect=ValueError("Product hardware is already in use.")),
    )

    answer = await handler._update_device({"device_id": "dev2", "driver": "acme_fw", "enabled": False})

    assert answer == {"error": "Product hardware is already in use."}


async def test_update_device_by_the_hardware_owner_is_an_ordinary_edit(setup, monkeypatch):
    handler, engine = setup
    monkeypatch.setattr(engine, "installed_equipment", _Policy("dev2", engine.installed_equipment))
    reassign = AsyncMock()
    monkeypatch.setattr(engine, "reassign_installed_equipment", reassign)

    await handler._update_device({"device_id": "dev2", "driver": "acme_fw", "name": "Main Unit"})

    reassign.assert_not_awaited()
    assert _device(_on_disk(engine), "dev2").driver == "acme_fw"


async def test_update_device_without_a_manifest_is_an_ordinary_edit(setup, monkeypatch):
    handler, engine = setup
    policy = _Policy("built_in", engine.installed_equipment)
    policy.manifest = None
    monkeypatch.setattr(engine, "installed_equipment", policy)
    reassign = AsyncMock()
    monkeypatch.setattr(engine, "reassign_installed_equipment", reassign)

    await handler._update_device({"device_id": "dev2", "driver": "acme_fw"})

    reassign.assert_not_awaited()
    assert _device(_on_disk(engine), "dev2").driver == "acme_fw"


# --- add_device -------------------------------------------------------------------


@pytest.mark.parametrize("engine", [None, SimpleNamespace(project=None)])
async def test_add_device_without_a_project(engine):
    answer = await _no_engine(engine)._add_device({"id": "dev9", "driver": "acme_widget"})
    assert answer == {"error": "No project loaded"}


async def test_add_device_needs_an_id(setup):
    handler, _engine = setup
    assert await handler._add_device({"driver": "acme_widget"}) == {"error": "Device ID is required"}


async def test_add_device_refuses_an_id_in_use(setup):
    handler, _engine = setup
    answer = await handler._add_device({"id": "dev1", "driver": "acme_widget"})
    assert answer == {"error": "Device 'dev1' already exists"}


async def test_add_device_lands_the_address_in_the_connections_table(setup):
    handler, engine = setup

    answer = await handler._add_device({
        "id": "dev9", "driver": "acme_widget",
        "config": {"host": "192.0.2.90", "port": 4100, "mode": "eco"},
    })

    assert answer == {"status": "created", "id": "dev9"}
    on_disk = _on_disk(engine)
    added = _device(on_disk, "dev9")
    assert (added.name, added.enabled, added.config) == ("dev9", True, {"mode": "eco"})
    assert on_disk.connections["dev9"] == {"host": "192.0.2.90", "port": 4100}


async def test_add_device_without_an_address_writes_no_connections_row(setup):
    handler, engine = setup

    answer = await handler._add_device(
        {"id": "dev9", "driver": "acme_widget", "name": "Nine", "enabled": False, "config": {"mode": "eco"}},
    )

    assert answer == {"status": "created", "id": "dev9"}
    on_disk = _on_disk(engine)
    added = _device(on_disk, "dev9")
    assert (added.name, added.enabled) == ("Nine", False)
    assert "dev9" not in on_disk.connections


async def test_add_device_with_a_driver_not_installed_warns_and_adds(setup, caplog):
    handler, engine = setup
    caplog.set_level(logging.WARNING)

    answer = await handler._add_device({"id": "dev9", "driver": "acme_absent"})

    assert answer == {"status": "created", "id": "dev9"}
    assert _device(_on_disk(engine), "dev9").driver == "acme_absent"
    assert [r.getMessage() for r in caplog.records if "not in registry" in r.getMessage()] == [
        "add_device: driver 'acme_absent' not in registry (may not be loaded yet)",
    ]


async def test_add_device_with_no_driver_does_not_warn(setup, caplog):
    handler, _engine = setup
    caplog.set_level(logging.WARNING)

    await handler._add_device({"id": "dev9"})

    assert not [r for r in caplog.records if "not in registry" in r.getMessage()]


async def test_add_device_refuses_a_required_field_left_blank(setup):
    handler, engine = setup

    answer = await handler._add_device({"id": "dev9", "driver": "acme_strict"})

    assert answer == {"error": "Zone is required.", "missing_config": ["zone"]}
    assert all(d.id != "dev9" for d in _on_disk(engine).devices)


# --- delete_device ----------------------------------------------------------------


@pytest.mark.parametrize("engine", [None, SimpleNamespace(project=None)])
async def test_delete_device_without_a_project(engine):
    assert await _no_engine(engine)._delete_device({"device_id": "dev1"}) == {"error": "No project loaded"}


async def test_delete_device_takes_its_row_monitors_and_memberships(setup):
    handler, engine = setup

    answer = await handler._delete_device({"device_id": "dev1"})

    assert answer["status"] == "deleted"
    assert answer["device_id"] == "dev1"
    on_disk = _on_disk(engine)
    assert [d.id for d in on_disk.devices] == ["dev2", "old1", "key1"]
    assert "dev1" not in on_disk.connections
    assert [m.key for m in on_disk.monitors] == ["device.dev2.power"]
    assert on_disk.device_groups[0].device_ids == ["dev2"]


async def test_delete_device_names_an_unknown_device(setup):
    handler, _engine = setup
    assert await handler._delete_device({"device_id": "ghost"}) == {"error": "Device 'ghost' not found"}


async def test_delete_device_with_nothing_pointing_at_it_carries_no_impact(setup, monkeypatch):
    handler, _engine = setup
    monkeypatch.setattr(handler, "_find_references", lambda kind, ref: {})

    answer = await handler._delete_device({"device_id": "dev2"})

    assert answer == {"status": "deleted", "device_id": "dev2"}


async def test_delete_device_hands_back_what_pointed_at_it(setup, monkeypatch):
    handler, _engine = setup
    asked: list[tuple[str, str]] = []

    def references(kind: str, ref: str) -> dict:
        asked.append((kind, ref))
        return {"macros": [{"macro_id": "m1"}]}

    monkeypatch.setattr(handler, "_find_references", references)

    answer = await handler._delete_device({"device_id": "dev2"})

    assert asked == [("device", "dev2")]
    assert answer == {"status": "deleted", "device_id": "dev2", "impact": {"macros": [{"macro_id": "m1"}]}}


async def test_delete_device_answers_the_hardware_refusal(setup, monkeypatch):
    handler, engine = setup

    class _Refusing:
        def refuse_removal(self, device_id: str) -> None:
            raise ValueError("Product hardware cannot be removed.")

    monkeypatch.setattr(engine, "installed_equipment", _Refusing())

    assert await handler._delete_device({"device_id": "dev1"}) == {"error": "Product hardware cannot be removed."}
    assert any(d.id == "dev1" for d in _on_disk(engine).devices)


# --- device groups ----------------------------------------------------------------


@pytest.mark.parametrize("tool", ["_add_device_group", "_update_device_group", "_delete_device_group"])
@pytest.mark.parametrize("engine", [None, SimpleNamespace(project=None)])
async def test_group_tools_without_a_project(engine, tool):
    answer = await getattr(_no_engine(engine), tool)({"id": "g", "device_ids": []})
    assert answer == {"error": "No project loaded"}


async def test_add_device_group_needs_an_id(setup):
    handler, _engine = setup
    assert await handler._add_device_group({"name": "Rear"}) == {"error": "Group ID is required"}


async def test_add_device_group_refuses_an_id_in_use(setup):
    handler, _engine = setup
    answer = await handler._add_device_group({"id": "front", "device_ids": []})
    assert answer == {"error": "Group 'front' already exists"}


async def test_add_device_group_names_every_unknown_member(setup):
    handler, engine = setup

    answer = await handler._add_device_group({"id": "rear", "device_ids": ["dev1", "ghost", "spook"]})

    assert answer == {"error": "Device(s) not found in project: ghost, spook"}
    assert [g.id for g in _on_disk(engine).device_groups] == ["front"]


async def test_add_device_group_names_itself_after_its_id(setup):
    handler, engine = setup

    answer = await handler._add_device_group({"id": "rear", "device_ids": ["dev2"]})

    assert answer == {"status": "created", "id": "rear"}
    group = _on_disk(engine).device_groups[1]
    assert (group.id, group.name, group.device_ids) == ("rear", "rear", ["dev2"])


async def test_add_device_group_with_no_members(setup):
    handler, engine = setup

    answer = await handler._add_device_group({"id": "rear", "name": "Rear"})

    assert answer == {"status": "created", "id": "rear"}
    group = _on_disk(engine).device_groups[1]
    assert (group.name, group.device_ids) == ("Rear", [])


async def test_update_device_group_names_an_unknown_group(setup):
    handler, _engine = setup
    assert await handler._update_device_group({"id": "rear", "name": "Rear"}) == {"error": "Group 'rear' not found"}


async def test_update_device_group_names_every_unknown_member(setup):
    handler, engine = setup

    answer = await handler._update_device_group({"id": "front", "name": "Stage", "device_ids": ["ghost"]})

    assert answer == {"error": "Device(s) not found in project: ghost"}
    group = _on_disk(engine).device_groups[0]
    assert (group.name, group.device_ids) == ("Front", ["dev1", "dev2"])


async def test_update_device_group_renames_alone(setup):
    handler, engine = setup

    answer = await handler._update_device_group({"id": "front", "name": "Stage"})

    assert answer == {"status": "updated", "id": "front"}
    group = _on_disk(engine).device_groups[0]
    assert (group.name, group.device_ids) == ("Stage", ["dev1", "dev2"])


async def test_update_device_group_changes_members_alone(setup):
    handler, engine = setup

    answer = await handler._update_device_group({"id": "front", "device_ids": ["dev2"]})

    assert answer == {"status": "updated", "id": "front"}
    group = _on_disk(engine).device_groups[0]
    assert (group.name, group.device_ids) == ("Front", ["dev2"])


async def test_delete_device_group(setup):
    handler, engine = setup

    assert await handler._delete_device_group({"id": "front"}) == {"status": "deleted", "id": "front"}
    assert _on_disk(engine).device_groups == []


async def test_delete_device_group_names_an_unknown_group(setup):
    handler, _engine = setup
    assert await handler._delete_device_group({"id": "rear"}) == {"error": "Group 'rear' not found"}


# --- settings, the connection test and commands ----------------------------------------


async def test_get_device_settings_answers(setup):
    handler, engine = setup

    answer = await handler._get_device_settings({"device_id": "dev1"})

    assert answer == {"device_id": "dev1", "settings": engine.devices.get_device_settings("dev1")}
    assert set(answer["settings"]) == {"brightness", "admin_pin"}


async def test_get_device_settings_refusals(setup):
    handler, _engine = setup
    assert await _no_engine(None)._get_device_settings({"device_id": "dev1"}) == {"error": "Engine not available"}
    assert await handler._get_device_settings({}) == {"error": "device_id is required"}
    assert await handler._get_device_settings({"device_id": "ghost"}) == {"error": "Device 'ghost' not found"}


async def test_set_device_setting_refusals(setup):
    handler, _engine = setup
    assert await _no_engine(None)._set_device_setting({"device_id": "dev1"}) == {"error": "Engine not available"}
    for sent in ({"setting_key": "brightness", "value": 5}, {"device_id": "dev1", "value": 5}):
        assert await handler._set_device_setting(sent) == {"error": "device_id and setting_key are required"}


async def test_set_device_setting_refuses_a_secret_setting(setup):
    handler, engine = setup
    engine.devices.set_device_setting = AsyncMock()

    answer = await handler._set_device_setting({"device_id": "dev1", "setting_key": "admin_pin", "value": "1234"})

    assert answer == {
        "error": "Admin PIN is a login. Enter it on the device's page in the Programmer. Nothing was changed.",
        "login_settings": ["admin_pin"],
    }
    engine.devices.set_device_setting.assert_not_awaited()


async def test_set_device_setting_answers_what_it_wrote(setup):
    handler, engine = setup
    engine.devices.set_device_setting = AsyncMock()

    answer = await handler._set_device_setting({"device_id": "dev1", "setting_key": "brightness", "value": 40})

    assert answer == {"success": True, "device_id": "dev1", "key": "brightness", "value": 40}
    engine.devices.set_device_setting.assert_awaited_once_with("dev1", "brightness", 40)


async def test_test_device_connection_refusals(setup):
    handler, _engine = setup
    assert await _no_engine(None)._test_device_connection({"device_id": "dev1"}) == {
        "success": False, "error": "No project loaded", "latency_ms": None,
    }
    assert await handler._test_device_connection({"device_id": "ghost"}) == {
        "success": False, "error": "Device 'ghost' not found", "latency_ms": None,
    }


async def test_test_device_connection_of_product_hardware_asks_the_policy(setup, monkeypatch):
    handler, engine = setup
    policy = _Policy("dev1", engine.installed_equipment)
    policy.test_connection = AsyncMock(return_value={"success": True, "latency_ms": 3})
    monkeypatch.setattr(engine, "installed_equipment", policy)

    assert await handler._test_device_connection({"device_id": "dev1"}) == {"success": True, "latency_ms": 3}


async def test_send_device_command_announces_and_answers_ok(setup, monkeypatch):
    handler, engine = setup
    handler._events = MagicMock()
    handler._events.emit = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(engine.devices, "send_command", send)

    answer = await handler._send_device_command({"device_id": "dev1", "command": "power_on"})

    assert answer == "OK"
    handler._events.emit.assert_awaited_once_with(
        "ai.device_command", {"device_id": "dev1", "command": "power_on", "params": {}},
    )
    send.assert_awaited_once_with("dev1", "power_on", {})


async def test_send_device_command_words_a_refusal_as_send_command_does(setup):
    handler, _engine = setup
    handler._events = MagicMock()
    handler._events.emit = AsyncMock()

    answer = await handler._send_device_command(
        {"device_id": "dev1", "command": "power_on", "params": {"x": 1}},
    )

    assert answer == {"error": "Device 'dev1' is not connected"}
    handler._events.emit.assert_awaited_once_with(
        "ai.device_command", {"device_id": "dev1", "command": "power_on", "params": {"x": 1}},
    )


async def test_send_device_command_names_the_missing_parameter_by_its_key(setup, monkeypatch):
    """The refusal names the parameter by its label, as the Programmer shows
    it; the key the assistant has to put in its next call rides beside it."""
    handler, engine = setup
    handler._events = MagicMock()
    handler._events.emit = AsyncMock()
    monkeypatch.setattr(engine.devices, "send_command", AsyncMock(
        side_effect=CommandParamError("Set Level: Level Value is required.", param="level_value"),
    ))

    answer = await handler._send_device_command({"device_id": "dev1", "command": "set_level"})

    assert answer == {"error": "Set Level: Level Value is required.", "param": "level_value"}


async def test_list_and_info_answer_the_manager(setup):
    handler, engine = setup
    assert await handler._list_devices({}) == engine.devices.list_devices()
    info = await handler._get_device_info({"device_id": "dev1"})
    assert info["id"] == "dev1"
    assert "commands" not in info["driver_info"]


async def test_add_device_that_appears_meanwhile_is_refused(setup, monkeypatch):
    handler, engine = setup
    real_edit = engine.apply_project_edit

    async def racing(mutate):
        def both(project):
            project.devices.append(DeviceConfig(id="dev9", driver="acme_widget", name="Other"))
            mutate(project)
        return await real_edit(both)

    monkeypatch.setattr(engine, "apply_project_edit", racing)

    answer = await handler._add_device({"id": "dev9", "driver": "acme_widget"})

    assert answer == {"error": "Device 'dev9' already exists"}
    assert all(d.id != "dev9" for d in _on_disk(engine).devices)


async def test_update_device_repeating_a_driver_not_installed_does_not_warn_again(setup, caplog):
    handler, _engine = setup
    await handler._update_device({"device_id": "dev2", "driver": "acme_absent"})
    caplog.clear()
    caplog.set_level(logging.WARNING)

    await handler._update_device({"device_id": "dev2", "driver": "acme_absent", "name": "Dev Two"})

    assert not [r for r in caplog.records if "not in registry" in r.getMessage()]


async def test_update_device_refuses_a_login_of_the_driver_it_leaves(setup):
    handler, engine = setup

    answer = await handler._update_device(
        {"device_id": "key1", "driver": "acme_widget", "config": {"api_key": "k-1", "mode": "eco"}},
    )

    assert answer == {
        "error": "API Key is a login. Enter it on the device's page in the Programmer, under Edit. Nothing was changed.",
        "login_settings": ["api_key"],
    }
    assert _device(_on_disk(engine), "key1").driver == "acme_keyed"


async def test_add_device_with_an_id_in_use_is_refused_before_its_config_is_read(setup):
    handler, _engine = setup

    answer = await handler._add_device({"id": "key1", "driver": "acme_keyed", "config": {"api_key": "k-1"}})

    assert answer == {"error": "Device 'key1' already exists"}


class _AcmeLocked(_AcmeWidget):
    """The same, with a password it cannot connect without."""

    DRIVER_INFO: dict[str, Any] = {
        **_AcmeWidget.DRIVER_INFO,
        "id": "acme_locked",
        "config_schema": {
            "host": {"type": "string", "label": "IP Address"},
            "password": {"type": "string", "secret": True, "required": True, "label": "Password"},
        },
    }


@pytest.mark.parametrize(("enabled", "waits"), [(True, True), (False, False)])
async def test_only_an_enabled_device_is_told_it_needs_a_login(setup, enabled, waits):
    handler, _engine = setup
    register_driver(_AcmeLocked)
    try:
        answer = await handler._add_device(
            {"id": "lock1", "driver": "acme_locked", "enabled": enabled, "config": {"host": "192.0.2.70"}},
        )
    finally:
        unregister_driver("acme_locked")

    assert answer["status"] == "created"
    assert ("needs_login" in answer) is waits
    if waits:
        assert answer["needs_login"] == ["password"]


async def test_update_device_onto_a_driver_needing_a_login_leaves_it_waiting_in_the_row(setup):
    handler, engine = setup
    register_driver(_AcmeLocked)
    try:
        answer = await handler._update_device({"device_id": "dev2", "driver": "acme_locked"})
        on_disk = _on_disk(engine)
    finally:
        unregister_driver("acme_locked")

    assert answer["status"] == "updated"
    assert answer["needs_login"] == ["password"]
    assert on_disk.connections["dev2"] == {"host": "192.0.2.11", "password": SAVED_LOGIN}
    assert "password" not in _device(on_disk, "dev2").config


async def test_a_rename_never_asks_the_assistant_for_a_login(setup):
    """A login the driver requires and nobody entered is the device page's to
    ask for; the answer never tells the assistant to send it in config."""
    handler, engine = setup
    register_driver(_AcmeLocked)
    try:
        # Saved before its driver required a password: an older device.
        engine.project.devices.append(DeviceConfig(id="lock3", driver="acme_locked", name="Lock 3", config={}))
        save_project(str(engine.project_path), engine.project)
        answer = await handler._update_device({"device_id": "lock3", "name": "Lock Three"})
    finally:
        unregister_driver("acme_locked")

    assert answer == {"status": "updated", "device_id": "lock3"}


async def test_update_device_settings_on_an_older_device_needing_a_login_leave_it_waiting(setup):
    handler, engine = setup
    register_driver(_AcmeLocked)
    try:
        # Saved before its driver required a password: an older device.
        engine.project.devices.append(DeviceConfig(id="lock2", driver="acme_locked", name="Lock 2", config={}))
        save_project(str(engine.project_path), engine.project)
        answer = await handler._update_device({"device_id": "lock2", "config": {"host": "192.0.2.71"}})
        on_disk = _on_disk(engine)
    finally:
        unregister_driver("acme_locked")

    assert answer["status"] == "updated"
    assert answer["needs_login"] == ["password"]
    assert "missing_config" not in answer
    assert on_disk.connections["lock2"] == {"host": "192.0.2.71", "password": SAVED_LOGIN}
