"""The hello this system sends the cloud says it keeps its logins saved here.

A project that holds the saved-login marker in place of a login is only safe
to write on a version that keeps the login saved on the system. The cloud
learns which systems do from the capabilities in their hello, and holds back a
project carrying the marker from any system that does not announce it. These
tests pin what goes on the wire, through the agent's own connect path.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openavc.cloud.agent import DEFAULT_CAPABILITIES, CloudAgent
from openavc.cloud.crypto import generate_system_key
from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore


class _FakeSocket:
    """Takes the hello, then drops the connection before the challenge."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        raise OSError("closed by the test after the hello")

    async def close(self):
        pass


def test_the_capability_is_announced():
    assert "saved_logins" in DEFAULT_CAPABILITIES
    assert "saved_command_params" in DEFAULT_CAPABILITIES


def test_the_saved_parameter_marker_the_second_capability_stands_for():
    # The cloud looks for a value of this shape before it sends a project.
    from openavc.core import saved_params

    assert saved_params.marker("0123456789abcdef") == "<saved on this system #0123456789abcdef>"


def test_the_marker_the_capability_stands_for():
    # The cloud looks for this exact string in a project before it sends one.
    # It is in every project a system on this version has saved, so it never
    # changes; this pins it beside the capability that names it.
    assert SAVED_LOGIN == "<saved on this system>"


@pytest.mark.asyncio
async def test_the_hello_on_the_wire_carries_it():
    agent = CloudAgent(
        StateStore(),
        EventBus(),
        None,
        {
            "endpoint": "wss://cloud.example.test/agent/v1",
            "system_id": "acme-system",
            "system_key": generate_system_key().hex(),
        },
    )
    socket = _FakeSocket()
    with patch(
        "openavc.cloud.agent.websockets.connect", new=AsyncMock(return_value=socket)
    ):
        with pytest.raises(Exception):
            await agent._connect_and_run()

    hello = socket.sent[0]
    assert hello["type"] == "hello"
    assert "saved_logins" in hello["payload"]["capabilities"]
    assert "saved_command_params" in hello["payload"]["capabilities"]
