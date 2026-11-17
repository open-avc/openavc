"""One project device whose settings cannot be worked out costs only itself.

Working a device's settings out reads its driver (the defaults it declares,
whether it is a bridge, the ports a bridge advertises) and its connection row.
A driver already installed can carry a malformed block (the load keeps it and
names the problem on its Installed entry), and a project file can carry a
connection value of the wrong kind. What is pinned here, for each such device:

- The start brings every other device up and names the one it could not
  start among its errors, instead of stopping with no device up.
- A project save brings every other device in the project to its settings,
  and the rollback after a failed save does too, instead of failing whole.
- A device already running whose settings can no longer be worked out keeps
  running as it was: it is still in the project, so it is not removed and its
  state is kept.
- The devices waiting for a login are still listed.
- The bring-up round after a save dials every other device.

An invented device throughout.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver

ENGINE_LOG = "openavc.core.engine"


def _driver_class(driver_id: str, **info: Any) -> type[BaseDriver]:
    class _Acme(BaseDriver):
        DRIVER_INFO: dict[str, Any] = {
            "id": driver_id,
            "name": "Acme Widget",
            "transport": "tcp",
            "default_config": {"port": 5500},
            "config_schema": {
                "host": {"type": "string", "label": "IP Address"},
                "password": {"type": "string", "secret": True, "label": "Password"},
            },
            "state_variables": {},
            "commands": {},
            **info,
        }

        async def send_command(self, command: str, params: dict | None = None) -> Any:
            return None

    return _Acme


GOOD = "acme_widget_good"
BRIDGE = "acme_widget_bridge"
BAD = "acme_widget_bad"

# Each way one device's settings cannot be worked out: what its driver declares,
# and the connection row of the device that cannot be worked out.
MALFORMED = {
    "defaults written as text": ({"default_config": "port=23"}, {"host": "192.0.2.30"}),
    "bridge ports written as a map": (
        {"bridge": {"ports": {"p1": {"kind": "serial", "passthrough_port": 4999}}}},
        {"host": "192.0.2.30"},
    ),
    "bridge port named by a list": (
        {}, {"bridge": "br", "bridge_port": ["p1"], "baudrate": 9600},
    ),
}


@pytest.fixture(autouse=True)
def _drivers(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    registry.register_driver(_driver_class(GOOD))
    registry.register_driver(_driver_class(
        BRIDGE, bridge={"ports": [{"id": "p1", "kind": "serial", "passthrough_port": 4999}]},
    ))


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    from openavc.core.engine import Engine

    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path / "data"))
    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            yield eng
    finally:
        await eng.devices.disconnect_all()


def _project(case: str) -> ProjectConfig:
    """Two plain devices, a bridge, and the one device ``case`` breaks."""
    info, connection = MALFORMED[case]
    if info:
        registry.register_driver(_driver_class(BAD, **info))
    devices = [
        DeviceConfig(id="good1", driver=GOOD, name="Projector"),
        DeviceConfig(id="br", driver=BRIDGE, name="Bridge"),
        DeviceConfig(id="bad", driver=BAD if info else GOOD, name="Display"),
        DeviceConfig(id="good2", driver=GOOD, name="Switcher"),
    ]
    connections = {
        "good1": {"host": "192.0.2.10"},
        "br": {"host": "192.0.2.20"},
        "bad": connection,
        "good2": {"host": "192.0.2.40"},
    }
    return ProjectConfig(project=ProjectMeta(id="p1", name="Hall"), devices=devices,
                         connections=connections)


def _running(eng) -> set[str]:
    return set(eng.devices.get_device_configs())


@pytest.mark.parametrize("case", list(MALFORMED))
async def test_the_start_brings_every_other_device_up_and_names_the_one_it_could_not(
    engine, case, caplog,
):
    engine.project = _project(case)
    errors: list[str] = []
    with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
        await engine._add_devices_at_start(errors)

    assert {"good1", "br", "good2"} <= _running(engine)
    assert engine.state.get("device.good1.connected") is True
    # The one device is either running (its binding left for the connect to
    # refuse) or named among the start's errors: never silently missing.
    if "bad" not in _running(engine):
        assert [e for e in errors if e.startswith("Device 'bad': ")], errors
        assert any("'bad'" in r.getMessage() for r in caplog.records)
    assert not [e for e in errors if not e.startswith("Device 'bad': ")], errors


@pytest.mark.parametrize("case", list(MALFORMED))
async def test_a_save_brings_every_other_device_to_its_settings(engine, case):
    engine.project = _project(case)
    await engine._sync_devices(defer_connect=True)
    assert {"good1", "br", "good2"} <= _running(engine)

    await engine.devices.bring_up()
    assert engine.state.get("device.good1.connected") is True
    assert engine.state.get("device.good2.connected") is True


@pytest.mark.parametrize("case", list(MALFORMED))
async def test_a_saved_device_change_reaches_every_other_device(engine, case):
    engine.project = _project(case)
    await engine._reconcile(ProjectDiff(devices=True), ProjectOrigin.EDIT)
    await engine.wait_for_device_bringup()
    assert {"good1", "br", "good2"} <= _running(engine)
    for device_id in ("good1", "br", "good2"):
        assert engine.state.get(f"device.{device_id}.connected") is True, device_id


@pytest.mark.parametrize("case", list(MALFORMED))
async def test_the_rollback_after_a_failed_save_brings_the_others_back(engine, case):
    engine.project = _project(case)
    await engine._rollback_reconcile(ProjectDiff(devices=True))
    await engine.wait_for_device_bringup()
    assert {"good1", "br", "good2"} <= _running(engine)
    assert engine.state.get("device.good1.connected") is True



async def test_a_bring_up_round_dials_every_other_device_when_a_driver_goes_bad_before_it(engine, caplog):
    engine.project = _project("bridge ports written as a map")
    registry.register_driver(_driver_class(BAD))
    await engine._sync_devices(defer_connect=True)
    assert "bad" in _running(engine)

    # Its driver is replaced by one whose bridge block cannot be read before
    # the round after the save dials the devices it registered.
    registry.register_driver(_driver_class(
        BAD, bridge={"ports": {"p1": {"kind": "serial", "passthrough_port": 4999}}},
    ))
    with caplog.at_level(logging.ERROR, logger="openavc.core.device_manager"):
        await engine.devices.bring_up()

    assert engine.state.get("device.good1.connected") is True
    assert engine.state.get("device.good2.connected") is True
    assert engine.state.get("device.br.connected") is True
    assert any(
        "'bad'" in r.getMessage() and "could not say whether it is a bridge" in r.getMessage()
        for r in caplog.records
    )

async def test_a_running_device_that_can_no_longer_be_worked_out_keeps_running(engine):
    engine.project = _project("defaults written as text")
    registry.register_driver(_driver_class(BAD, default_config={"port": 23}))
    await engine._sync_devices()
    assert "bad" in _running(engine)
    engine.state.set("device.bad.power", "on", source="device")
    before = engine.devices.get_device_config("bad")

    # Its driver is replaced by one whose defaults cannot be read, and a save
    # touching another device comes through.
    registry.register_driver(_driver_class(BAD, default_config="port=23"))
    engine.project.devices[0].name = "Main Projector"
    await engine._sync_devices()

    assert engine.devices.get_device_config("bad") == before
    assert engine.state.get("device.bad.power") == "on"
    assert engine.devices.get_device_config("good1")["name"] == "Main Projector"


@pytest.mark.parametrize("case", list(MALFORMED))
async def test_the_devices_waiting_for_a_login_are_still_listed(engine, case):
    engine.project = _project(case)
    engine.project.connections["good2"]["password"] = SAVED_LOGIN
    assert {"id": "good2", "name": "Switcher"} in engine.logins_needed()


async def test_the_whole_start_finishes_with_the_one_device_counted(tmp_path, monkeypatch, caplog):
    import json

    from openavc.core.engine import Engine
    from openavc.updater.manager import UpdateManager

    async def _nothing(*_a, **_k):
        return None

    monkeypatch.setattr(UpdateManager, "start_auto_check", _nothing)
    monkeypatch.setattr(UpdateManager, "stop_auto_check", _nothing)
    project = _project("defaults written as text").model_dump(mode="json")
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    eng = Engine(str(path))
    monkeypatch.setattr(eng, "_start_mdns_advertiser", _nothing)
    monkeypatch.setattr(eng, "_start_cloud_agent", _nothing)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()), \
                caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
            await eng.start()
        assert eng.state.get("system.started") is True
        assert eng.state.get("system.startup_errors") == 1
        assert {"good1", "br", "good2"} <= _running(eng)
        assert eng.scripts is not None
        assert any(
            "Device 'bad': its settings could not be worked out" in r.getMessage()
            for r in caplog.records if r.levelno == logging.WARNING
        )
    finally:
        await eng.stop()
