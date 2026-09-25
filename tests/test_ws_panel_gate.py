"""The panel socket gate: 4010 after accept, the closes, the Programmer push.

Through the real endpoint, on a claimed instance, because what a browser
sees (a close code it can read, a socket that stays up) is the whole point.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from openavc.core.panel_devices import CLOSE_CODE_NOT_APPROVED
from tests.panel_access_helpers import (
    PASSWORD,
    TUNNEL_HEADERS,
    approve_a_device,
    cookie_header,
    lan_client,
    loopback_client,
)

AUTH = ("admin", PASSWORD)


def _basic_header() -> dict[str, str]:
    import base64

    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    return {"authorization": f"Basic {token}"}


def _handshake(sock) -> None:
    assert sock.receive_json()["type"] == "state.snapshot"
    assert sock.receive_json()["type"] == "ui.definition"


def _alive(sock) -> bool:
    """A round trip proves the socket is up and served."""
    sock.send_json({"type": "ui.page", "page_id": "main"})
    msg = sock.receive_json()
    return msg["type"] == "ui.navigate"


def _refused(sock) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        sock.receive_json()
    assert exc.value.code == CLOSE_CODE_NOT_APPROVED


def test_a_stranger_is_accepted_then_closed_with_4010(claimed_engine, access_mode):
    access_mode("approved")
    with lan_client().websocket_connect("/ws?client=panel") as sock:
        # The handshake completed (we are inside the context), so a browser
        # gets a real close code rather than a 1006 it cannot read.
        _refused(sock)


def test_a_refused_socket_holds_no_slot(claimed_engine, access_mode, monkeypatch):
    from openavc.api import ws as ws_mod

    access_mode("approved")
    monkeypatch.setattr(ws_mod, "MAX_WS_CONNECTIONS", 1)
    client = lan_client()
    with client.websocket_connect("/ws?client=panel") as first:
        _refused(first)
    with client.websocket_connect("/ws?client=panel", headers=_basic_header()) as sock:
        _handshake(sock)
        assert _alive(sock)


def test_an_approved_cookie_is_admitted(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)
    with client.websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, value)) as sock:
        _handshake(sock)
        assert _alive(sock)
    # The connect counted as the device being seen.
    approved = claimed_engine.panel_devices.list_devices()["approved"][0]
    assert approved["last_seen"]


def test_a_wrong_cookie_is_refused(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)
    wrong = value.split(".")[0] + ".not-it"
    with client.websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, wrong)) as sock:
        _refused(sock)


def test_the_console_a_tunnel_and_a_credential_need_no_approval(claimed_engine, access_mode):
    access_mode("approved")
    with loopback_client().websocket_connect("/ws?client=panel") as sock:
        _handshake(sock)
        assert _alive(sock)
    with loopback_client().websocket_connect("/ws?client=panel", headers=TUNNEL_HEADERS) as sock:
        _handshake(sock)
        assert _alive(sock)
    with lan_client().websocket_connect("/ws?client=panel", headers=_basic_header()) as sock:
        _handshake(sock)
        assert _alive(sock)


def test_a_revoke_closes_the_device_s_sockets_with_4010(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)
    device_id = value.split(".")[0]
    with lan_client().websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, value)) as sock:
        _handshake(sock)
        resp = client.delete(f"/api/panel/devices/{device_id}", auth=AUTH)
        assert resp.status_code == 200
        _refused(sock)
    # And the same cookie is a stranger from now on.
    with lan_client().websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, value)) as sock:
        _refused(sock)


def test_switching_to_approved_closes_only_the_open_mode_sockets(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)
    access_mode("open")

    with lan_client().websocket_connect("/ws?client=panel") as stranger, \
            loopback_client().websocket_connect("/ws?client=panel") as console, \
            loopback_client().websocket_connect("/ws?client=panel", headers=TUNNEL_HEADERS) as tunnel, \
            lan_client().websocket_connect("/ws?client=panel", headers=_basic_header()) as credentialed, \
            lan_client().websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, value)) as approved:
        for sock in (stranger, console, tunnel, credentialed, approved):
            _handshake(sock)

        resp = client.patch("/api/system/config", json={"panels": {"access": "approved"}}, auth=AUTH)
        assert resp.status_code == 200

        _refused(stranger)
        for sock in (console, tunnel, credentialed, approved):
            assert _alive(sock)


def test_switching_to_open_changes_nothing_for_connected_clients(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)
    with client.websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, value)) as sock:
        _handshake(sock)
        client.patch("/api/system/config", json={"panels": {"access": "open"}}, auth=AUTH)
        assert _alive(sock)


def test_the_change_reaches_programmer_clients_and_never_a_panel(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)

    with lan_client().websocket_connect("/ws?client=programmer", headers=_basic_header()) as programmer, \
            lan_client().websocket_connect("/ws?client=panel", headers=cookie_header(claimed_engine, value)) as panel:
        _handshake(programmer)
        _handshake(panel)

        # A new device asks to connect.
        first = lan_client().get("/api/panel/access")
        code = first.json()["code"]

        push = programmer.receive_json()
        assert push["type"] == "panel.devices.changed"
        assert push["reason"] == "pending"
        assert push["device"]["code"] == code
        assert push["device"]["platform"]
        assert "secret" not in str(push)

        # The panel's next message is answered directly: nothing was queued
        # for it in between.
        assert _alive(panel)

        # Every other change is pushed the same way.
        device_id = claimed_engine.panel_devices.list_devices()["pending"][0]["id"]
        client.post(f"/api/panel/devices/{device_id}/deny", auth=AUTH)
        assert programmer.receive_json()["reason"] == "denied"
        client.patch("/api/system/config", json={"panels": {"access": "open"}}, auth=AUTH)
        changed = programmer.receive_json()
        assert changed["reason"] == "access_changed" and changed["access"] == "open"
        assert _alive(panel)


def test_a_programmer_socket_is_not_a_panel(claimed_engine, access_mode):
    """The gate is for panels only; the Programmer's socket keeps its own
    credential rule (a bare programmer request is refused before accept)."""
    access_mode("approved")
    with pytest.raises(WebSocketDisconnect) as exc:
        with lan_client().websocket_connect("/ws?client=programmer"):
            pass
    assert exc.value.code == 4001
