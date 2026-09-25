"""Audit this device: an audit started from a project device's page.

The device page fills the wizard in from the project: the address the device
really connects to, its driver, and its saved settings. A device an audit
cannot reach by address is refused in words, and the saved settings of the
device a session started from are offered only while it still connects to
the audited address.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.models import AuditDriverRequest, AuditStartRequest
from openavc.api.routes import audit as routes
from openavc.audit.origin import BRIDGED, NO_ADDRESS, SERIAL, device_target, origin_for
from openavc.core.project_loader import DeviceConfig
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY
from tests.test_audit_api import wired  # noqa: F401  (the fixture)


class _AcmeSerial(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_origin_serial", "name": "Acme Serial", "manufacturer": "Acme",
        "category": "utility", "transport": "serial", "default_config": {"baudrate": 9600},
        "commands": {}, "state_variables": {}, "config_schema": {},
    }


class _AcmeBridge(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_origin_bridge", "name": "Acme Bridge", "manufacturer": "Acme",
        "category": "utility", "transport": "tcp", "default_config": {"port": 4998},
        "commands": {}, "state_variables": {}, "config_schema": {},
        "bridge": {"ports": [{"id": "serial_1", "kind": "serial", "passthrough_port": 4999}]},
    }


class _AcmeLogin(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_origin_login", "name": "Acme Login", "manufacturer": "Acme",
        "category": "utility", "transport": "tcp", "default_config": {"port": 23},
        "commands": {}, "state_variables": {},
        "config_schema": {
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "password": {"type": "string", "secret": True},
        },
    }


@pytest.fixture(autouse=True)
def drivers():
    for cls in (_AcmeSerial, _AcmeBridge, _AcmeLogin):
        _DRIVER_REGISTRY[cls.DRIVER_INFO["id"]] = cls
    yield
    for cls in (_AcmeSerial, _AcmeBridge, _AcmeLogin):
        _DRIVER_REGISTRY.pop(cls.DRIVER_INFO["id"], None)


def _project(*devices, connections=None):
    return SimpleNamespace(devices=list(devices), connections=connections or {})


# ---------------------------------------------------------------------------
# Where an audit of a project device points
# ---------------------------------------------------------------------------


def test_the_address_is_where_the_device_really_connects():
    project = _project(
        DeviceConfig(id="lobby", driver="acme_audit_api", name="Lobby Display", config={}),
        connections={"lobby": {"host": "10.0.0.5"}},
    )
    target = device_target(project, "lobby")
    assert target == {
        "device_id": "lobby", "name": "Lobby Display", "driver": "acme_audit_api",
        "address": "10.0.0.5", "transport": "tcp", "auditable": True, "reason": "",
    }


def test_a_device_without_a_network_address_is_refused_in_words():
    project = _project(
        DeviceConfig(id="proj", driver="acme_origin_serial", name="Projector",
                     config={"port": "COM3"}),
        DeviceConfig(id="bridge", driver="acme_origin_bridge", name="Rack Bridge",
                     config={"host": "10.0.0.9"}),
        DeviceConfig(id="relay", driver="acme_origin_serial", name="Screen",
                     config={"bridge": "bridge", "bridge_port": "serial_1"}),
        DeviceConfig(id="blank", driver="acme_audit_api", name="New Display", config={}),
    )
    serial = device_target(project, "proj")
    assert serial["auditable"] is False
    assert serial["reason"] == SERIAL.format(name="Projector")
    # Its bytes go to the bridge's address; the audit does not stand in for it.
    bridged = device_target(project, "relay")
    assert bridged["auditable"] is False
    assert bridged["reason"] == BRIDGED.format(name="Screen", bridge="Rack Bridge")
    blank = device_target(project, "blank")
    assert blank["auditable"] is False
    assert blank["reason"] == NO_ADDRESS.format(name="New Display")
    assert device_target(project, "ghost") is None


def test_the_origin_is_kept_only_while_the_device_connects_there():
    project = _project(
        DeviceConfig(id="lobby", driver="acme_audit_api", name="Lobby Display",
                     config={"host": "Lobby-Display.local."}),
    )
    assert origin_for(project, "lobby", ["lobby-display.local", "10.0.0.5"]) == {
        "device_id": "lobby", "name": "Lobby Display", "driver": "acme_audit_api",
    }
    # The person typed another address: the audit is not of that device.
    assert origin_for(project, "lobby", ["10.0.0.6", "10.0.0.6"]) is None
    assert origin_for(project, None, ["10.0.0.5"]) is None
    assert origin_for(None, "lobby", ["10.0.0.5"]) is None


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


def test_the_start_request_names_the_device_it_came_from():
    assert AuditStartRequest(address="10.0.0.5", from_device="lobby").from_device == "lobby"
    with pytest.raises(ValidationError):
        AuditStartRequest(address="10.0.0.5", from_page="lobby")


async def test_the_device_route_answers_for_a_project_device(wired):  # noqa: F811
    target = await routes.audit_device_target("lobby")
    assert target["address"] == "127.0.0.1" and target["auditable"] is True
    with pytest.raises(HTTPException) as exc:
        await routes.audit_device_target("ghost")
    assert exc.value.status_code == 404


async def test_a_session_from_a_device_page_records_it(wired):  # noqa: F811
    started = await routes.start_session(
        AuditStartRequest(address="127.0.0.1", pause=["lobby"], from_device="lobby"),
    )
    state = started["session"]
    assert state["origin"] == {
        "device_id": "lobby", "name": "Lobby Display", "driver": "acme_audit_api",
    }
    session = wired.manager.current()
    (entry,) = [e for e in session.timeline if e.kind == "session.started"]
    assert entry.text == "Audit started on 127.0.0.1, from Lobby Display's device page."
    await wired.manager.shutdown()

    # The address was changed before starting: no origin.
    started = await routes.start_session(
        AuditStartRequest(address="localhost", from_device="other"),
    )
    assert started["session"]["origin"] is None
    await wired.manager.shutdown()

    with pytest.raises(HTTPException) as exc:
        await routes.start_session(AuditStartRequest(address="127.0.0.1", from_device="ghost"))
    assert exc.value.status_code == 404
    assert wired.manager.current() is None


async def _through_the_check(wired, session_id: str) -> None:  # noqa: F811
    await routes.run_network_check(session_id)
    for _ in range(100):
        if wired.manager.current().footprint is not None:
            return
        await asyncio.sleep(0.01)


async def test_a_disabled_origin_offers_its_saved_settings(wired):  # noqa: F811
    project = wired.engine.project
    project.devices[:] = [
        # Disabled: nothing to pause, and its settings are still the ones
        # that reach this device.
        DeviceConfig(id="spare", driver="acme_origin_login", name="Spare Display",
                     config={"host": "127.0.0.1", "password": "hunter22"}, enabled=False),
        DeviceConfig(id="lobby", driver="acme_origin_login", name="Lobby Display",
                     config={"host": "127.0.0.1", "password": "other-secret"}),
    ]
    wired.devices.running = {"lobby"}
    started = await routes.start_session(
        AuditStartRequest(address="127.0.0.1", pause=["lobby"], from_device="spare"),
    )
    session_id = started["session"]["session_id"]
    await _through_the_check(wired, session_id)
    await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_origin_login"))

    offered = (await routes.get_saved_settings(session_id))["devices"]
    # The device the audit came from first, then the one it paused.
    assert [d["device_id"] for d in offered] == ["spare", "lobby"]
    assert offered[0]["secrets_set"] == ["password"]
    assert "hunter22" not in str(offered)

    # Moved to another address during the audit: no longer offered.
    project.devices[0] = DeviceConfig(
        id="spare", driver="acme_origin_login", name="Spare Display",
        config={"host": "10.9.9.8", "password": "hunter22"}, enabled=False,
    )
    offered = (await routes.get_saved_settings(session_id))["devices"]
    assert [d["device_id"] for d in offered] == ["lobby"]
    await wired.manager.shutdown()


async def test_the_report_names_the_device_it_came_from(wired):  # noqa: F811
    started = await routes.start_session(
        AuditStartRequest(address="127.0.0.1", pause=["lobby"], from_device="lobby"),
    )
    session_id = started["session"]["session_id"]
    await _through_the_check(wired, session_id)
    report = await routes.session_report(session_id, format="json")
    assert report["session"]["origin"] == {
        "device_id": "lobby", "name": "Lobby Display", "driver": "acme_audit_api",
    }
    await wired.manager.shutdown()
