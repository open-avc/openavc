"""A connection that announces a name holds up a state key while it is up.

A room whose logic lives outside (a Node-RED flow) is only as alive as that
socket, and a dead flow looked exactly like a working one: the button lit,
the press landed on a variable, nothing answered. `?name=<id>` on the connect
URL makes the server publish `system.integration.<id>.connected`, which is a
key a panel LED, a monitored reading or a trigger can watch like any other.

Through the real endpoint (TestClient), because the connect and the unwind
are what hold the key up and let it go.
"""

from __future__ import annotations

import pytest

from openavc.api.ws import INTEGRATION_KEY, _integration_connections, integration_name
from openavc.core.ws_hub import WSHub


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.main import app
    from tests.test_api_endpoints import _make_mock_engine

    engine = _make_mock_engine()
    engine.ws = WSHub(engine.state)
    engine.panel_ui.return_value = {"pages": [], "theme": {}}
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app), engine
    rest.set_engine(None)
    ws.set_engine(None)
    _integration_connections.clear()


def _handshake(sock):
    assert sock.receive_json()["type"] == "state.snapshot"
    assert sock.receive_json()["type"] == "ui.definition"


def test_the_name_becomes_a_key_segment_a_person_can_type():
    assert integration_name("Lobby Logic!") == "lobby-logic"
    assert integration_name("  node-red_1 ") == "node-red_1"
    assert integration_name("///") == ""
    assert integration_name("") == ""
    assert len(integration_name("x" * 200)) == 64


def test_a_named_connection_holds_the_key_up_and_lets_it_go(client):
    c, engine = client
    key = INTEGRATION_KEY.format(name="lobby-logic")
    assert not engine.state.has(key)
    with c.websocket_connect("/ws?client=panel&name=Lobby%20Logic") as sock:
        _handshake(sock)
        assert engine.state.get(key) is True
    assert engine.state.get(key) is False


def test_the_key_is_in_the_announcing_client_s_own_snapshot(client):
    """It is set before the snapshot is taken, so a client that watches its
    own key (to confirm the name it chose) sees it from the first frame."""
    c, _ = client
    with c.websocket_connect("/ws?client=panel&name=lobby") as sock:
        snapshot = sock.receive_json()
        assert snapshot["state"][INTEGRATION_KEY.format(name="lobby")] is True


def test_two_sockets_with_one_name_keep_the_key_true_until_the_last_leaves(client):
    """A flow redeploying overlaps its old socket with its new one; the key
    must not blink false in between."""
    c, engine = client
    key = INTEGRATION_KEY.format(name="lobby")
    with c.websocket_connect("/ws?client=panel&name=lobby") as first:
        _handshake(first)
        with c.websocket_connect("/ws?client=panel&name=lobby") as second:
            _handshake(second)
            assert engine.state.get(key) is True
        assert engine.state.get(key) is True, "one is still connected"
    assert engine.state.get(key) is False


def test_an_unnamed_or_unusable_name_publishes_nothing(client):
    c, engine = client
    before = set(engine.state.snapshot())
    with c.websocket_connect("/ws?client=panel") as sock:
        _handshake(sock)
    with c.websocket_connect("/ws?client=panel&name=%2F%2F") as sock:
        _handshake(sock)
    after = set(engine.state.snapshot())
    assert not [k for k in after - before if k.startswith("system.integration.")]
    assert not _integration_connections
