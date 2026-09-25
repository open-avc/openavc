"""The audit's driver sandbox: production's device lifecycle, reaching nothing else.

The driver runs through a private ``DeviceManager`` against a loopback fake
device, and these tests pin what the plan asks of it: the same resolver and
bring-up, nothing reaching the engine's state or events, a push route that
reaches the audit device, no pending settings, the serial simulation guard,
and teardown that forgets the device while the audit keeps its secrets.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from openavc.audit.sandbox import (
    NO_SERIAL_SUPPORT,
    AuditProjectView,
    DriverSandbox,
    audit_device_id,
    serial_refusal,
    unpaused_devices_at,
)
from openavc.audit.session import AuditError
from openavc.core.device_traffic import get_traffic_recorder
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry

DRIVER_ID = "acme_sandbox_tcp"


def _definition(**extra) -> dict:
    definition = {
        "id": DRIVER_ID,
        "name": "Acme Sandbox",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"port": 1, "poll_interval": 0.2},
        "config_schema": {
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "password": {"type": "string", "secret": True},
        },
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {},
        "polling": {"queries": ["PWR?\\r"]},
        "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
    }
    definition.update(extra)
    return definition


@pytest.fixture
def driver_class():
    cls = create_configurable_driver_class(_definition())
    _DRIVER_REGISTRY[DRIVER_ID] = cls
    yield cls
    _DRIVER_REGISTRY.pop(DRIVER_ID, None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device(reply: bytes = b"PWR=on\r"):
    """Answers every query line with ``reply``."""

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\r")
                if not line:
                    break
                writer.write(reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _wait(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_the_driver_runs_as_a_project_device_would(driver_class):
    server, port = await _fake_device()
    sandbox = DriverSandbox(
        audit_device_id("s1"), DRIVER_ID,
        {"host": "127.0.0.1", "port": port, "password": "hunter22"},
    )
    try:
        resolved = sandbox.prepare()
        # The driver's own defaults came through the one resolver.
        assert resolved["config"]["poll_interval"] == 0.2
        assert "pending_settings" not in resolved
        await sandbox.start()
        assert sandbox.driver.contract_observer is not None
        await sandbox.connect()
        await _wait(lambda: sandbox.device_state().get("power") is True)
        assert sandbox.connected()
        # Polled at the driver's own cadence, every exchange captured.
        await _wait(lambda: sum(e.direction == "tx" for e in sandbox.observer.frames()) >= 2)
        frames = sandbox.observer.frames()
        assert frames[0].direction == "tx" and frames[0].data == b"PWR?\r"
        assert any(e.direction == "rx" and e.data == b"PWR=on" for e in frames)
    finally:
        await sandbox.stop()
        server.close()
    # Gone, and its secrets with it; the audit kept what the report needs.
    assert sandbox.device_state() == {}
    assert get_secret_registry().secrets_for("audit-s1") == set()
    assert "hunter22" in sandbox.observer.secrets


async def test_the_audit_device_reaches_nothing_else(driver_class):
    """The engine's state and event bus (what the WebSocket fan-out, the cloud
    relay, the alert monitor, triggers and scripts hear) never see it."""
    engine_state = StateStore()
    engine_events = EventBus()
    engine_state.set_event_bus(engine_events)
    heard: list = []
    engine_state.subscribe("*", lambda *args: heard.append(args))
    engine_events.on("*", lambda *args: heard.append(args))

    server, port = await _fake_device()
    sandbox = DriverSandbox(audit_device_id("s2"), DRIVER_ID, {"host": "127.0.0.1", "port": port})
    try:
        await sandbox.start()
        await sandbox.connect()
        await _wait(lambda: sandbox.device_state().get("power") is True)
    finally:
        await sandbox.stop()
        server.close()
    assert heard == []
    assert engine_state.snapshot() == {}


async def test_a_failed_connect_reports_the_offline_reason_and_keeps_retrying(driver_class):
    # Nothing listens on this port: refused, classified, retried.
    probe = __import__("socket").socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sandbox = DriverSandbox(audit_device_id("s3"), DRIVER_ID, {"host": "127.0.0.1", "port": port})
    try:
        await sandbox.start()
        await sandbox.connect()
        await _wait(lambda: sandbox.device_state().get("offline_reason"), timeout=15)
        assert sandbox.device_state()["offline_reason"] == "connection_refused"
        assert not sandbox.connected()
    finally:
        await sandbox.stop()


async def test_the_push_route_reaches_an_audit_device(driver_class):
    """An HTTP push is demultiplexed by device id in the transport registry,
    so the real route reaches a device outside the engine's manager."""
    from openavc.api.routes import push

    cls = create_configurable_driver_class(_definition(push={"type": "http_listener"}))
    _DRIVER_REGISTRY[DRIVER_ID] = cls
    server, port = await _fake_device(reply=b"")
    sandbox = DriverSandbox(audit_device_id("s4"), DRIVER_ID, {"host": "127.0.0.1", "port": port})
    app = FastAPI()
    app.include_router(push.open_router, prefix="/api")
    try:
        await sandbox.start()
        await sandbox.connect()
        await _wait(sandbox.connected)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            base_url="http://openavc",
        ) as client:
            response = await client.post("/api/push/audit-s4", content=b"PWR=on\r")
        assert response.status_code == 200
        await _wait(lambda: sandbox.device_state().get("power") is True)
        pushed = [e for e in sandbox.observer.frames() if e.channel == "http_listener"]
        assert pushed and pushed[0].data == b"PWR=on\r"
    finally:
        await sandbox.stop()
        server.close()


def test_a_serial_port_that_would_simulate_is_refused(monkeypatch):
    from openavc.transport import serial_transport

    assert "SIM:lab" in serial_refusal({"port": "SIM:lab"})
    monkeypatch.setattr(serial_transport, "HAS_SERIAL", False)
    assert serial_refusal({"port": "COM3"}) == NO_SERIAL_SUPPORT
    monkeypatch.setattr(serial_transport, "HAS_SERIAL", True)
    assert serial_refusal({"port": "COM3"}) is None


def test_a_serial_driver_on_a_simulated_port_does_not_start():
    cls = create_configurable_driver_class(_definition(id="acme_sandbox_serial", transport="serial"))
    _DRIVER_REGISTRY["acme_sandbox_serial"] = cls
    try:
        sandbox = DriverSandbox("audit-s5", "acme_sandbox_serial", {"port": "SIM:lab"})
        with pytest.raises(AuditError, match="simulated port"):
            sandbox.prepare()
    finally:
        _DRIVER_REGISTRY.pop("acme_sandbox_serial", None)


def test_a_driver_that_is_not_installed_is_refused():
    with pytest.raises(AuditError, match="is not installed"):
        DriverSandbox("audit-s6", "acme_not_installed", {"host": "127.0.0.1"}).prepare()


def test_the_project_view_is_all_the_resolver_needs(driver_class):
    view = AuditProjectView()
    sandbox = DriverSandbox("audit-s7", DRIVER_ID, {"host": "10.0.0.5"}, project_view=view)
    resolved = sandbox.prepare()
    assert resolved["config"]["host"] == "10.0.0.5"
    assert resolved["config"]["port"] == 1  # the driver's default
    assert sandbox.transport == "tcp"


def test_project_devices_at_the_address_that_are_not_paused_are_named():
    project = SimpleNamespace(
        devices=[
            DeviceConfig(id="lobby", driver="acme_x", name="Lobby Display",
                         config={"host": "10.0.0.5"}),
            DeviceConfig(id="hall", driver="acme_x", name="Hall", config={"host": "10.0.0.6"}),
        ],
        connections={},
    )
    state = StateStore()
    assert unpaused_devices_at(project, state, ["10.0.0.5"]) == ["Lobby Display"]
    state.set("device.lobby.paused", True)
    assert unpaused_devices_at(project, state, ["10.0.0.5"]) == []
    assert unpaused_devices_at(None, state, ["10.0.0.5"]) == []


async def test_a_project_save_and_simulation_never_reach_the_audit_device(driver_class):
    """The engine's reconcile removes running devices the project lacks, and
    simulation redirects devices by id; both read the engine's own manager,
    so the audit device, which the project never lists, is out of reach."""
    from openavc.core.device_manager import DeviceManager
    from openavc.core.engine import Engine
    from openavc.core.simulation import SimulationManager

    engine_state = StateStore()
    engine_devices = DeviceManager(engine_state, EventBus())
    engine = SimpleNamespace(
        project=SimpleNamespace(devices=[], connections={}),
        devices=engine_devices,
        state=engine_state,
        resolved_device_config=lambda d: d,
    )
    server, port = await _fake_device()
    sandbox = DriverSandbox(audit_device_id("s8"), DRIVER_ID, {"host": "127.0.0.1", "port": port})
    try:
        await sandbox.start()
        await sandbox.connect()
        await _wait(sandbox.connected)

        await Engine._sync_devices(engine)
        sim = SimulationManager(engine)
        sim._sim_ports = {"audit-s8": 19999}
        await sim._redirect_connections()

        assert sandbox.connected()
        assert sandbox.driver.config["port"] == port
        assert sandbox.driver.config["host"] == "127.0.0.1"
    finally:
        await sandbox.stop()
        server.close()
