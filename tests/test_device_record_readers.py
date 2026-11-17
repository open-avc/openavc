"""Every reader of the project's devices answers from the device manager's one
record per device, so no two of them disagree about the same device.

Five project devices, one in each state a device can be in, each put there
through the real start (``Engine._add_devices_at_start``):

- ``live``: a driver runs it.
- ``off``: the project disables it.
- ``lost``: its driver is not installed.
- ``broken``: its driver raises setting it up.
- ``unset``: its settings cannot be worked out (its driver's defaults are
  written as text).

Then every reader is asked about each device, and the answers are compared
with one table: the listing and a device's details, ``/api/health`` and
``/api/status``, the cloud heartbeat, the AI's device and project tools, the
simulation's device set, where devices connect (``device_connections``), and
what the Device Audit refuses to share the equipment with.

Invented ``acme_record_*`` drivers throughout.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.api import rest
from openavc.api.routes import system as system_routes
from openavc.audit.sandbox import unpaused_devices_at
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.heartbeat import HeartbeatCollector
from openavc.core.connection_fault import INVALID_CONFIG
from openavc.core.device_config import device_connections
from openavc.core.device_errors import DeviceNotFoundError
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver

GOOD = "acme_record_widget"
FAILS = "acme_record_fails"
BAD = "acme_record_bad"
MISSING = "acme_record_missing"

HOSTS = {
    "live": "192.0.2.10", "off": "192.0.2.11", "lost": "192.0.2.12",
    "broken": "192.0.2.13", "unset": "192.0.2.14",
}


def _driver_class(driver_id: str, *, raises: bool = False, **info: Any) -> type[BaseDriver]:
    class _Acme(BaseDriver):
        DRIVER_INFO: dict[str, Any] = {
            "id": driver_id,
            "name": "Acme Record Widget",
            "manufacturer": "Acme",
            "category": "utility",
            "transport": "tcp",
            "default_config": {"port": 5500},
            "config_schema": {"host": {"type": "string", "label": "IP Address"}},
            "state_variables": {},
            "commands": {"power_on": {"label": "Power On"}},
            **info,
        }

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if raises:
                raise TypeError("responses must be a list")

        async def send_command(self, command: str, params: dict | None = None) -> Any:
            return None

    return _Acme


@pytest.fixture(autouse=True)
def _drivers(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    registry.register_driver(_driver_class(GOOD))
    registry.register_driver(_driver_class(FAILS, raises=True))
    registry.register_driver(_driver_class(BAD, default_config="port=23"))


def _project() -> ProjectConfig:
    devices = [
        DeviceConfig(id="live", driver=GOOD, name="Projector"),
        DeviceConfig(id="off", driver=GOOD, name="Spare", enabled=False),
        DeviceConfig(id="lost", driver=MISSING, name="Switcher"),
        DeviceConfig(id="broken", driver=FAILS, name="Display"),
        DeviceConfig(id="unset", driver=BAD, name="Amplifier"),
    ]
    return ProjectConfig(
        project=ProjectMeta(id="p1", name="Hall"), devices=devices,
        connections={did: {"host": host} for did, host in HOSTS.items()},
    )


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    from openavc.core.engine import Engine

    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path / "data"))
    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    eng.project = _project()
    monkeypatch.setattr(system_routes, "_get_engine", lambda: eng)
    rest.set_engine(eng)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await eng._add_devices_at_start([])
            yield eng
    finally:
        rest.set_engine(None)
        await eng.devices.disconnect_all()


# What every reader says about a device in each state.
#   status: the listing's and the details' ``status``
#   health: the /api/health bucket it is counted under
#   simulated: in the simulation's device set
#   holds: answered by device_connections (where devices connect)
#   audit: what the Device Audit will not share the equipment with
EXPECTED = {
    "live": {"status": "running", "health": "connected", "simulated": True,
             "holds": True, "audit": True},
    "off": {"status": "disabled", "health": "disabled", "simulated": True,
            "holds": False, "audit": False},
    "lost": {"status": "orphaned", "health": "orphaned", "simulated": True,
             "holds": True, "audit": False},
    "broken": {"status": "driver_failed", "health": "disconnected", "simulated": True,
               "holds": True, "audit": False},
    "unset": {"status": "settings_failed", "health": "disconnected", "simulated": False,
              "holds": False, "audit": False},
}


async def test_every_reader_says_the_same_about_a_device_in_each_state(engine):
    devices = engine.devices

    # The listing, and each device's details: every device, with its state.
    listed = {entry["id"]: entry for entry in devices.list_devices()}
    assert {did: entry["status"] for did, entry in listed.items()} == {
        did: row["status"] for did, row in EXPECTED.items()
    }
    for did, row in EXPECTED.items():
        assert devices.get_device_info(did)["status"] == row["status"], did

    # /api/health and /api/status count the same devices.
    health = (await system_routes.health_check())["devices"]
    buckets = {"connected": 0, "disconnected": 0, "orphaned": 0, "disabled": 0}
    for row in EXPECTED.values():
        buckets[row["health"]] += 1
    assert health == {"total": len(EXPECTED), **buckets}
    assert engine.get_status()["device_count"] == len(EXPECTED)

    # The cloud heartbeat: counted, connected, and offline with a reason.
    heartbeat = HeartbeatCollector(engine.state, devices)
    assert heartbeat._get_device_count() == len(EXPECTED)
    assert heartbeat._get_devices_connected() == buckets["connected"]
    assert heartbeat._get_devices_error() == 2  # broken and unset, each with why

    # The AI's tools: the listing, each device's details, the project summary.
    tools = AIToolHandler(MagicMock(), devices, engine.events)
    assert {e["id"]: e["status"] for e in await tools._list_devices({})} == {
        did: row["status"] for did, row in EXPECTED.items()
    }
    for did, row in EXPECTED.items():
        assert (await tools._get_device_info({"device_id": did}))["status"] == row["status"]
    summary = {d["id"]: d for d in (await tools._get_project_summary({}))["devices"]}
    assert {did: d["status"] for did, d in summary.items()} == {
        did: row["status"] for did, row in EXPECTED.items()
    }

    # The simulation simulates every device whose settings were worked out.
    simulated = set(engine.simulation._project_device_ids())
    assert simulated == {did for did, row in EXPECTED.items() if row["simulated"]}

    # Where devices connect: a disabled device and one with no settings hold
    # nothing; a device waiting for its driver, or for its driver to set it
    # up, is answered as it is set to.
    holds = {c.device_id for c in device_connections(engine.project, devices)}
    assert holds == {did for did, row in EXPECTED.items() if row["holds"]}

    # The Device Audit refuses to share the equipment only with a device a
    # driver runs.
    names = list(HOSTS.values())
    blocking = unpaused_devices_at(engine.project, devices, names)
    assert set(blocking) == {
        d.name for d in engine.project.devices if EXPECTED[d.id]["audit"]
    }


async def test_a_device_whose_settings_cannot_be_worked_out_says_why_on_its_card(engine):
    keys = engine.state.get_namespace("device.unset.")
    assert keys["offline_reason"] == INVALID_CONFIG
    assert "could not work out this device's settings" in keys["offline_detail"]
    assert BAD in keys["offline_detail"]
    assert keys["connected"] is False
    assert keys["reconnect_failed"] is True
    assert keys["name"] == "Amplifier"

    info = engine.devices.get_device_info("unset")
    assert info["commands"] == {} and info["driver_info"] == {}
    assert engine.devices.get_driver("unset") is None
    # A door that needs a running driver answers as it does for a disabled one.
    with pytest.raises(DeviceNotFoundError):
        await engine.devices.pause_device("unset")
    with pytest.raises(DeviceNotFoundError):
        engine.devices.get_device_settings("unset")


async def test_a_disabled_device_whose_settings_cannot_be_worked_out_is_still_disabled_in_the_listing(engine):
    engine.project.devices[4].enabled = False
    await engine._sync_devices()
    entry = {e["id"]: e for e in engine.devices.list_devices()}["unset"]
    assert entry["status"] == "settings_failed"
    assert entry["enabled"] is False
    health = (await system_routes.health_check())["devices"]
    assert health["disabled"] == 1  # only "off": the listing's status decides


# --- a device whose settings cannot be worked out, over time ----------------


async def test_once_its_settings_can_be_worked_out_a_save_brings_it_up(engine):
    registry.register_driver(_driver_class(BAD, default_config={"port": 23}))
    await engine._sync_devices()
    assert engine.devices.device_record("unset").status == "running"
    assert engine.devices.get_driver("unset") is not None
    assert engine.state.get("device.unset.offline_reason") is None


async def test_deleted_from_the_project_it_is_gone_from_every_reader(engine):
    engine.project.devices = [d for d in engine.project.devices if d.id != "unset"]
    await engine._sync_devices()
    assert "unset" not in {e["id"] for e in engine.devices.list_devices()}
    assert engine.devices.device_record("unset") is None
    assert engine.state.get_namespace("device.unset.") == {}
    with pytest.raises(DeviceNotFoundError):
        engine.devices.get_device_info("unset")


async def test_reconnect_works_its_settings_out_again(engine):
    await engine.devices.reconnect_device("unset")
    assert engine.devices.device_record("unset").status == "settings_failed"

    registry.register_driver(_driver_class(BAD, default_config={"port": 23}))
    await engine.devices.reconnect_device("unset")
    assert engine.devices.device_record("unset").status == "running"
    await engine.wait_for_device_bringup()
    assert engine.state.get("device.unset.connected") is True


async def test_reloading_its_driver_works_its_settings_out_again(engine):
    registry.register_driver(_driver_class(BAD, default_config={"port": 23}))
    reconnected = await engine.devices.reload_driver(BAD)
    assert reconnected == ["unset"]
    assert engine.devices.device_record("unset").status == "running"


async def test_a_device_waiting_for_its_driver_moved_onto_one_it_cannot_work_out_says_so(engine):
    engine.project.devices[2].driver = BAD
    await engine._sync_devices()
    assert engine.devices.device_record("lost").status == "settings_failed"
    assert engine.state.get("device.lost.orphaned") is None


async def test_reload_reaches_only_the_devices_a_driver_runs_or_could_not_set_up(engine):
    assert await engine.devices.reload_driver(GOOD) == ["live"]
    assert engine.devices.device_record("off").status == "disabled"
