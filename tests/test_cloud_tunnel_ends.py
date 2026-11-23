"""A tunnel ends here when it can no longer carry anything, and the cloud is told.

A tunnel's data socket closing, or this system's connection to the cloud
dropping, leaves nothing that could carry the tunnel's traffic. Each ends the
tunnel on this side and discards its session secret, which is only valid while
the tunnel is open. The cloud is told with tunnel_closed: at once when the
connection is up, and once it is connected again when it is not. The cloud's
own tunnel_close is not answered: the cloud already knows.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from websockets.exceptions import ConnectionClosed

from openavc.api import cloud_session
from openavc.cloud.protocol import (
    TUNNEL_CLOSED, TUNNEL_CONNECTION_LOST, TUNNEL_DATA_SOCKET_CLOSED, TUNNEL_READY,
)


class _DataSocket:
    """A tunnel's data socket that stays open until the test closes it."""

    def __init__(self, failure: BaseException | None = None, raw: str | None = None):
        self._gone = asyncio.Event()
        self._failure = failure or ConnectionClosed(None, None)
        self._raw = raw
        self.closed = False

    async def recv(self):
        if self._raw is not None:
            raw, self._raw = self._raw, None
            return raw
        await self._gone.wait()
        raise self._failure

    def drop(self):
        self._gone.set()

    async def send(self, data):
        pass

    async def close(self):
        self.closed = True
        self._gone.set()


def _open_msg(tunnel_id: str) -> dict:
    return {
        "type": "tunnel_open",
        "payload": {
            "tunnel_id": tunnel_id,
            "target_port": 8080,
            "tunnel_token": "tok",
            "tunnel_data_url": f"wss://cloud.example.invalid/agent/v1/tunnel/{tunnel_id}",
            "authorized_session": "support",
        },
    }


def _agent(sends: bool = True):
    agent = MagicMock()
    agent.send_message = AsyncMock(return_value=sends)
    return agent


async def _open(handler, tunnel_id: str, socket) -> str:
    with patch("openavc.cloud.tunnel.websockets.connect", new=AsyncMock(return_value=socket)):
        await handler.handle_tunnel_open(_open_msg(tunnel_id))
    secret = handler._tunnels[tunnel_id].session_secret
    assert cloud_session.is_active(secret)
    return secret


def _closed_sends(agent) -> list[dict]:
    return [c.args[1] for c in agent.send_message.call_args_list if c.args[0] == TUNNEL_CLOSED]


async def _ended(handler, tunnel_id: str) -> None:
    for _ in range(200):
        if tunnel_id not in handler._tunnels:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{tunnel_id} is still open")


@pytest.mark.asyncio
async def test_a_closed_data_socket_ends_the_tunnel_and_its_secret_and_says_so():
    from openavc.cloud.tunnel import TunnelHandler

    agent = _agent()
    handler = TunnelHandler(agent)
    socket = _DataSocket()
    secret = await _open(handler, "t-data", socket)

    socket.drop()
    await _ended(handler, "t-data")
    await asyncio.sleep(0.01)

    assert not cloud_session.is_active(secret)
    assert _closed_sends(agent) == [
        {"tunnel_id": "t-data", "reason": TUNNEL_DATA_SOCKET_CLOSED},
    ]
    await handler.stop()


@pytest.mark.asyncio
async def test_a_data_socket_that_fails_ends_the_tunnel_too():
    from openavc.cloud.tunnel import TunnelHandler

    agent = _agent()
    handler = TunnelHandler(agent)
    # Not JSON: the receive loop stops on its own error, and the tunnel has no
    # reader left.
    socket = _DataSocket(raw="{not json")
    secret = await _open(handler, "t-broken", socket)

    await _ended(handler, "t-broken")
    await asyncio.sleep(0.01)

    assert not cloud_session.is_active(secret)
    assert _closed_sends(agent) == [
        {"tunnel_id": "t-broken", "reason": TUNNEL_DATA_SOCKET_CLOSED},
    ]
    await handler.stop()


@pytest.mark.asyncio
async def test_the_clouds_own_close_is_not_answered():
    from openavc.cloud.tunnel import TunnelHandler

    agent = _agent()
    handler = TunnelHandler(agent)
    socket = _DataSocket()
    secret = await _open(handler, "t-cloud", socket)

    await handler.handle_tunnel_close({"type": "tunnel_close", "payload": {"tunnel_id": "t-cloud"}})
    await asyncio.sleep(0.01)

    assert "t-cloud" not in handler._tunnels
    assert not cloud_session.is_active(secret)
    assert socket.closed
    assert [c.args[0] for c in agent.send_message.call_args_list] == [TUNNEL_READY]
    await handler.stop()


@pytest.mark.asyncio
async def test_an_ending_the_cloud_could_not_hear_is_told_once_it_can():
    from openavc.cloud.tunnel import TunnelHandler

    agent = _agent(sends=False)
    handler = TunnelHandler(agent)
    socket = _DataSocket()
    await _open(handler, "t-later", socket)

    socket.drop()
    await _ended(handler, "t-later")
    await asyncio.sleep(0.01)

    agent.send_message = AsyncMock(return_value=True)
    await handler.start()
    await handler.start()

    assert _closed_sends(agent) == [
        {"tunnel_id": "t-later", "reason": TUNNEL_DATA_SOCKET_CLOSED},
    ]
    await handler.stop()


@pytest.mark.asyncio
async def test_a_lost_connection_or_a_stop_ends_every_tunnel_and_secret_before_any_socket_is_closed():
    from openavc.cloud.tunnel import TunnelHandler

    agent = _agent(sends=False)
    handler = TunnelHandler(agent)
    first, second = _DataSocket(), _DataSocket()
    secrets = [await _open(handler, "t-1", first), await _open(handler, "t-2", second)]
    seen_at_first_close = []

    async def slow_close():
        # A dead network can hold a socket's close for seconds: no secret may
        # wait for it.
        seen_at_first_close.extend(cloud_session.is_active(s) for s in secrets)
        first.closed = True

    first.close = slow_close

    await handler.stop()

    assert handler._tunnels == {}
    assert seen_at_first_close == [False, False]
    assert not any(cloud_session.is_active(s) for s in secrets)

    agent.send_message = AsyncMock(return_value=True)
    await handler.start()
    assert sorted(_closed_sends(agent), key=lambda p: p["tunnel_id"]) == [
        {"tunnel_id": "t-1", "reason": TUNNEL_CONNECTION_LOST},
        {"tunnel_id": "t-2", "reason": TUNNEL_CONNECTION_LOST},
    ]
    await handler.stop()


# --- Through the agent's own connection ---

class _DroppedControlSocket:
    """Takes the hello, then the connection drops."""

    async def send(self, raw):
        pass

    async def recv(self):
        raise OSError("closed by the test after the hello")

    async def close(self):
        pass


class _ControlSocket:
    """A connected control socket: records what is sent, stays up until dropped."""

    def __init__(self):
        self.sent: list[dict] = []
        self._gone = asyncio.Event()

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        await self._gone.wait()
        raise ConnectionClosed(None, None)

    async def close(self):
        self._gone.set()


def _cloud_agent():
    from openavc.cloud.agent import CloudAgent
    from openavc.cloud.crypto import generate_system_key
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    return CloudAgent(
        StateStore(),
        EventBus(),
        None,
        {
            "endpoint": "wss://cloud.example.invalid/agent/v1",
            "system_id": "acme-system",
            "system_key": generate_system_key().hex(),
        },
    )


@pytest.mark.asyncio
async def test_the_agents_lost_connection_ends_its_tunnels_and_the_next_one_tells_the_cloud():
    from openavc.cloud.handshake import HandshakeResult
    from openavc.cloud.tunnel import TunnelHandler

    agent = _cloud_agent()
    handler = TunnelHandler(agent)
    agent.set_tunnel_handler(handler)
    agent._running = True
    secret = await _open(handler, "t-reconnect", _DataSocket())

    with patch(
        "openavc.cloud.agent.websockets.connect",
        new=AsyncMock(return_value=_DroppedControlSocket()),
    ):
        with pytest.raises(Exception):
            await agent._connect_and_run()

    assert handler._tunnels == {}
    assert not cloud_session.is_active(secret)

    control = _ControlSocket()
    result = HandshakeResult(
        session_id="s-2", session_token="tok-2", signing_key=b"k" * 32,
        session_expires="", enabled_capabilities=["tunnel"], config={},
    )
    with patch(
        "openavc.cloud.agent.websockets.connect", new=AsyncMock(return_value=control),
    ), patch(
        "openavc.cloud.agent.Handshake.perform", new=AsyncMock(return_value=result),
    ):
        run = asyncio.create_task(agent._connect_and_run())
        for _ in range(200):
            if any(m["type"] == TUNNEL_CLOSED for m in control.sent):
                break
            await asyncio.sleep(0.005)
        await control.close()
        try:
            await asyncio.wait_for(run, 5)
        except Exception:
            pass

    told = [m["payload"] for m in control.sent if m["type"] == TUNNEL_CLOSED]
    assert told == [{"tunnel_id": "t-reconnect", "reason": TUNNEL_CONNECTION_LOST}]
    await agent.stop()
