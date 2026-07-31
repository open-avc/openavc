"""The Driver Builder's Test tab reports a driver-contract fault as its own row.

What the Test tab is for is telling an author what their draft actually does
before they save it — and a draft is exactly the thing no static gate has seen.
Measured on this platform: ``validate_driver_definition`` cross-checks a
command's ``sets:`` against ``state_variables``, but a ``responses:`` rule that
writes a state variable the driver never declares passes with **no errors at
all** (see ``test_undeclared_response_target_passes_the_static_validator``
below). At runtime that write lands, warns once into the server log, and
produces a live state key that no binding picker offers and nothing knows the
type of. In the Test tab the author would just see it appear under "State
changes" and conclude it worked.

So the test driver runs strict — and strict on *itself* only, never through the
process-wide env var, which would make every device this server is polling
raise mid-poll for as long as somebody has the panel open.

The fault is reported apart from ``error``: ``error`` means the device did not
answer, a contract error means it answered perfectly and the declarations are
wrong. Sending the author to the device when the fault is in their own YAML is
the misdirection this panel exists to remove.
"""
from __future__ import annotations

import asyncio

import pytest

# Aliased: pytest tries to collect anything named Test* and warns on a class
# with an __init__.
from server.api.models import TestCommandRequest as CommandRequest
from server.api.routes.drivers import _test_via_configurable_driver
from server.drivers.avcdriver_semantic import validate_driver_definition


def _definition(reply_var: str) -> dict:
    """A one-command TCP driver whose reply rule writes ``reply_var``."""
    return {
        "id": "acme_probe",
        "name": "Acme Probe",
        "manufacturer": "Acme",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 0, "poll_interval": 0},
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
        },
        "commands": {
            "query_power": {"label": "Query Power", "send": "PWR?\\r"},
        },
        "responses": [
            {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
            {"match": r"LAMP=(\d+)", "set": {reply_var: "$1"}},
        ],
    }


async def _fake_device(reply: bytes):
    """A socket that answers anything with ``reply``. Returns (server, port)."""

    async def handle(reader, writer):
        try:
            await reader.read(100)
            writer.write(reply)
            await writer.drain()
            # Closed here, not left to teardown: wait_closed() blocks on a
            # live connection, so an unclosed writer hangs the test rather
            # than failing it.
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, asyncio.IncompleteReadError):  # pragma: no cover
            pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _run(definition: dict, reply: bytes) -> dict:
    server, port = await _fake_device(reply)
    try:
        return await _test_via_configurable_driver(
            CommandRequest(
                host="127.0.0.1",
                port=str(port),
                transport="tcp",
                definition=definition,
                command_name="query_power",
                timeout=2,
            )
        )
    finally:
        server.close()
        await server.wait_closed()


def test_undeclared_response_target_passes_the_static_validator():
    """Why the Test tab is the gate here: nothing else looks at this.

    If this ever starts failing, a static check has grown to cover
    ``responses[].set`` — good news, and the reason this test says so out loud
    rather than leaving the gap implicit.
    """
    assert validate_driver_definition(_definition("lamp_hours")) == []


@pytest.mark.asyncio
async def test_a_response_writing_an_undeclared_variable_is_reported():
    result = await _run(_definition("lamp_hours"), b"LAMP=450\r")

    assert result["contract_errors"], "the undeclared write was not surfaced"
    message = result["contract_errors"][0]
    assert "lamp_hours" in message
    assert "state_variables" in message
    # It is not a device problem, so it must not be phrased as one.
    assert result["error"] != "Connect failed"
    assert not str(result["error"] or "").startswith("Send failed")


@pytest.mark.asyncio
async def test_a_contract_error_fails_the_test_even_though_the_device_answered():
    result = await _run(_definition("lamp_hours"), b"LAMP=450\r")
    assert result["success"] is False
    # The exchange itself worked and stays visible — the author needs to see
    # what came back as well as what was wrong with it.
    assert result["received"]


@pytest.mark.asyncio
async def test_a_declared_response_reports_nothing():
    declared = _definition("power")
    result = await _run(declared, b"PWR=on\r")

    assert result["contract_errors"] == []
    assert result["success"] is True
    assert result["state_changes"].get("device.test_acme_probe.power") == "on"


@pytest.mark.asyncio
async def test_the_live_test_does_not_make_the_rest_of_the_server_strict(
    monkeypatch,
):
    """The scope claim, asserted rather than assumed.

    A driver instantiated alongside the test — as every polled device on a
    live server is — must be unaffected while the test runs.
    """
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore
    from server.drivers.base import STRICT_DRIVER_STATE_ENV, BaseDriver

    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, "0")

    class _Production(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_in_production",
            "name": "Acme In Production",
            "transport": "tcp",
            "state_variables": {},
            "commands": {},
        }

        async def send_command(self, command, params=None):
            return True

    production = _Production("prod_1", {}, StateStore(), EventBus())

    result = await _run(_definition("lamp_hours"), b"LAMP=450\r")
    assert result["contract_errors"]

    # Still warn-only, still writing: the test tab did not reach it.
    production.set_state("undeclared_but_fine", 1)
    assert production.state.get("device.prod_1.undeclared_but_fine") == 1
