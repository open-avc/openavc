"""The connection step: the settings the driver gets, and what connecting sends.

The preview runs a declarative driver's own sign-in, start-up and polling code
against transports that record (``drivers/dry_run.py``), so what the person
reads before connecting is what the runtime will put on the wire. Saved
settings come from a paused project device on the server; its secrets are
named to the browser, never sent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openavc.api.models import (
    AuditConnectionRequest,
    AuditDriverRequest,
    AuditStartRequest,
)
from openavc.api.routes import audit as routes
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.dry_run import preview_connect
from openavc.drivers.registry import _DRIVER_REGISTRY
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

TCP_DRIVER = {
    "id": "acme_login_tcp",
    "name": "Acme Login",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 23, "poll_interval": 10},
    "config_schema": {
        "host": {"type": "string"},
        "port": {"type": "integer"},
        "username": {"type": "string"},
        "password": {"type": "string", "secret": True},
    },
    "auth": {
        "type": "telnet_login",
        "username_prompt": "login: ",
        "password_prompt": "Password: ",
        "line_ending": "\r\n",
    },
    "command_prefix": "#",
    "command_suffix": "\r",
    "state_variables": {"power": {"type": "boolean"}},
    "commands": {"query_power": {"label": "Query Power", "send": "PWR?"}},
    "on_connect": ["VERBOSE 3\\r"],
    "polling": {"queries": ["query_power"]},
    "liveness": {"send": "PING\\r", "interval": 30},
    "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
}

HTTP_DRIVER = {
    "id": "acme_rest",
    "name": "Acme REST",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "http",
    "default_config": {"port": 80, "poll_interval": 5},
    "state_variables": {"power": {"type": "boolean"}},
    "commands": {"status": {"label": "Status", "method": "GET", "path": "/api/status"}},
    "polling": {"queries": ["status", "/api/info"]},
    "responses": [],
}


@pytest.fixture
def drivers():
    for definition in (TCP_DRIVER, HTTP_DRIVER):
        _DRIVER_REGISTRY[definition["id"]] = create_configurable_driver_class(definition)
    yield
    for definition in (TCP_DRIVER, HTTP_DRIVER):
        _DRIVER_REGISTRY.pop(definition["id"], None)


def _build(definition: dict, config: dict):
    cls = create_configurable_driver_class(definition)
    return cls("preview-x", config, StateStore(), EventBus())


async def test_the_preview_is_what_the_runtime_sends_in_order():
    driver = _build(TCP_DRIVER, {
        "host": "10.0.0.5", "port": 23, "username": "admin", "password": "hunter22",
        "poll_interval": 10,
    })
    preview = await preview_connect(driver)
    assert preview.available and preview.poll_interval == 10
    shape = [
        (s["stage"], s["kind"], s.get("pattern") or s.get("data"))
        for s in preview.steps
    ]
    assert shape == [
        ("sign_in", "wait", "login: "),
        ("sign_in", "send", b"admin\r\n"),
        ("sign_in", "wait", "Password: "),
        ("sign_in", "send", b"hunter22\r\n"),
        ("start_up", "send", b"VERBOSE 3\r"),
        # A poll naming a command gets the command's framing.
        ("poll", "send", b"#PWR?\r"),
        ("keep_alive", "send", b"PING\r"),
    ]


async def test_an_http_drivers_preview_is_its_requests():
    driver = _build(HTTP_DRIVER, {"host": "10.0.0.5", "port": 80, "poll_interval": 5})
    preview = await preview_connect(driver)
    requests = [(s["stage"], s["method"], s["target"]) for s in preview.steps]
    assert requests == [("poll", "GET", "/api/status"), ("poll", "GET", "/api/info")]


async def test_a_python_drivers_steps_are_code():
    class AcmeCode(BaseDriver):
        DRIVER_INFO = {"id": "acme_code", "name": "Acme", "transport": "tcp"}

        async def send_command(self, command, params=None):
            return None

    preview = await preview_connect(AcmeCode("preview-y", {}, StateStore(), EventBus()))
    assert preview.available is False
    assert preview.reason == "This driver's connection steps are written in code."


async def _session_with_driver(wired, driver_id: str) -> str:  # noqa: F811
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1", pause=["lobby"]))
    session_id = started["session"]["session_id"]
    await routes.run_network_check(session_id)
    for _ in range(100):
        if wired.manager.current().footprint is not None:
            break
        await asyncio.sleep(0.01)
    await routes.set_driver(session_id, AuditDriverRequest(driver_id=driver_id))
    return session_id


async def test_the_connection_is_recorded_with_its_secrets_masked(wired, drivers):  # noqa: F811
    session_id = await _session_with_driver(wired, "acme_login_tcp")
    result = await routes.set_session_connection(session_id, AuditConnectionRequest(config={
        "host": "127.0.0.1", "port": 2323, "username": "admin", "password": "hunter22",
    }))
    (run,) = result["session"]["runs"]
    connection = run["connection"]
    assert connection["transport"] == "tcp"
    assert connection["config"]["password"] == "***"
    assert connection["config"]["port"] == 2323
    sent = [s for s in connection["preview"]["steps"] if s["kind"] == "send"]
    assert sent[1]["text"] == "***\r\n"
    assert "hunter22" not in str(result)
    session = wired.manager.current()
    assert session.runs[0].config["password"] == "hunter22"
    assert "hunter22" in session.runs[0].secrets
    assert "connection" in result["session"]["steps"]
    await wired.manager.shutdown()


async def test_saved_settings_stay_on_the_server(wired, drivers):  # noqa: F811
    wired.engine.project.devices[0] = DeviceConfig(
        id="lobby", driver="acme_login_tcp", name="Lobby Display",
        config={"host": "127.0.0.1", "port": 2323, "username": "admin", "password": "hunter22"},
    )
    session_id = await _session_with_driver(wired, "acme_login_tcp")
    offered = await routes.get_saved_settings(session_id)
    assert offered["devices"] == [{
        "device_id": "lobby", "name": "Lobby Display",
        # The platform counts a username as a credential too.
        "config": {"host": "127.0.0.1", "port": 2323, "poll_interval": 10},
        "secrets_set": ["password", "username"],
    }]
    # The password left empty is taken from the saved device.
    await routes.set_session_connection(session_id, AuditConnectionRequest(
        config={"host": "127.0.0.1", "port": 2323, "password": ""}, use_saved="lobby",
    ))
    run = wired.manager.current().runs[0]
    assert run.config["password"] == "hunter22"
    assert run.connection["saved_from"] == "Lobby Display"

    with pytest.raises(HTTPException) as exc:
        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={}, use_saved="other",
        ))
    assert exc.value.status_code == 409
    await wired.manager.shutdown()


async def test_a_simulated_serial_port_is_refused_before_connecting(wired):  # noqa: F811
    serial = dict(TCP_DRIVER, id="acme_serial_x", transport="serial", auth=None)
    _DRIVER_REGISTRY["acme_serial_x"] = create_configurable_driver_class(serial)
    try:
        session_id = await _session_with_driver(wired, "acme_serial_x")
        with pytest.raises(HTTPException) as exc:
            await routes.set_session_connection(session_id, AuditConnectionRequest(
                config={"port": "SIM:bench"},
            ))
        assert exc.value.status_code == 409 and "simulated port" in exc.value.detail
    finally:
        _DRIVER_REGISTRY.pop("acme_serial_x", None)
        await wired.manager.shutdown()


def test_the_request_model_declares_every_field():
    assert set(AuditConnectionRequest.model_fields) == {"config", "use_saved"}
    with pytest.raises(Exception):
        AuditConnectionRequest(config={}, host="x")


def test_nothing_to_offer_without_a_project():
    from openavc.audit.passes import saved_settings

    session = SimpleNamespace(runs=[], paused=[])
    assert saved_settings(session, None) == []
