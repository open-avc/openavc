"""The panel gate's rule, the check-in, the on-panel claim and the settings.

Every test runs on a claimed instance (a password is set), because on a dev
checkout with no credential the anonymous rule admits everything and the
gate is never asked. The socket half is test_ws_panel_gate.py.
"""

from __future__ import annotations

import re

import pytest

from openavc import config
from openavc.api.panel_access import Admission, admit, cookie_name
from openavc.core import panel_devices as pd
from openavc.middleware.rate_limit import _classify
from openavc.system_config import get_system_config, reset_system_config
from tests.panel_access_helpers import (
    PASSWORD,
    SPACE_NAME,
    TUNNEL_HEADERS,
    approve_a_device,
    cookie_header,
    lan_client,
    loopback_client,
    set_cookie_of,
)

AUTH = ("admin", PASSWORD)


# --- the check-in ---


def test_open_mode_answers_open_and_creates_nothing(claimed_engine, access_mode):
    access_mode("open")
    resp = lan_client().get("/api/panel/access")
    assert resp.status_code == 200
    assert resp.json() == {"access": "open"}
    assert "set-cookie" not in resp.headers
    assert claimed_engine.panel_devices.list_devices()["pending"] == []


def test_a_new_device_waits_with_a_code_and_a_pending_cookie(claimed_engine, access_mode):
    access_mode("approved")
    resp = lan_client().get("/api/panel/access", headers={"user-agent": "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)"})
    body = resp.json()
    assert body["access"] == "approved"
    assert body["status"] == "pending"
    assert re.fullmatch(r"\d{3}-\d{3}", body["code"])
    assert body["space"] == SPACE_NAME

    jar = set_cookie_of(resp)
    name = cookie_name(claimed_engine.instance_id)
    assert name.startswith("openavc_panel_") and "-" not in name
    morsel = jar[name]
    assert morsel["path"] == "/"
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert int(morsel["max-age"]) == pd.PENDING_COOKIE_MAX_AGE_SECONDS
    assert not morsel["secure"]

    pending = claimed_engine.panel_devices.list_devices()["pending"]
    assert [p["code"] for p in pending] == [body["code"]]
    assert pending[0]["platform"] == "iPad"


def test_polling_with_the_pending_cookie_keeps_the_code(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    first = client.get("/api/panel/access").json()
    second = client.get("/api/panel/access").json()  # the jar sends the cookie back
    assert second["status"] == "pending"
    assert second["code"] == first["code"]
    assert len(claimed_engine.panel_devices.list_devices()["pending"]) == 1


def test_the_cookie_is_secure_only_over_https(claimed_engine, access_mode):
    access_mode("approved")
    resp = lan_client(base_url="https://testserver").get("/api/panel/access")
    assert set_cookie_of(resp)[cookie_name(claimed_engine.instance_id)]["secure"]


def test_approval_reaches_the_device_on_its_next_poll(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    first = client.get("/api/panel/access")
    device_id = set_cookie_of(first)[cookie_name(claimed_engine.instance_id)].value.split(".")[0]

    approve = client.post(f"/api/panel/devices/{device_id}/approve", json={"name": "Room 101 wall"}, auth=AUTH)
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    assert approve.json()["approved_by"] == "admin"

    poll = client.get("/api/panel/access")
    assert poll.json() == {"access": "approved", "status": "approved", "name": "Room 101 wall"}
    morsel = set_cookie_of(poll)[cookie_name(claimed_engine.instance_id)]
    assert int(morsel["max-age"]) == pd.COOKIE_MAX_AGE_SECONDS
    assert morsel["httponly"] and morsel["samesite"].lower() == "lax"

    # From here on the device is known and nothing is re-set.
    again = client.get("/api/panel/access")
    assert again.json()["status"] == "approved"
    assert "set-cookie" not in again.headers


def test_a_credential_is_approved_without_a_record(claimed_engine, access_mode):
    access_mode("approved")
    resp = lan_client().get("/api/panel/access", auth=AUTH)
    assert resp.json() == {"access": "approved", "status": "approved", "name": ""}
    assert "set-cookie" not in resp.headers
    assert claimed_engine.panel_devices.list_devices()["pending"] == []


def test_the_box_s_own_screen_is_approved_without_a_record(claimed_engine, access_mode):
    access_mode("approved")
    resp = loopback_client().get("/api/panel/access")
    assert resp.json()["status"] == "approved"
    assert "set-cookie" not in resp.headers
    assert claimed_engine.panel_devices.list_devices()["pending"] == []


def test_a_forwarded_request_behind_a_trusted_proxy_is_not_the_console(claimed_engine, access_mode, monkeypatch):
    access_mode("approved")
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    resp = loopback_client().get("/api/panel/access", headers={"x-forwarded-for": "203.0.113.9"})
    assert resp.json()["status"] == "pending"
    assert claimed_engine.panel_devices.list_devices()["pending"][0]["address"] == "203.0.113.9"


def test_a_cloud_tunnel_is_approved_and_gets_no_cookie(claimed_engine, access_mode):
    access_mode("approved")
    resp = loopback_client().get("/api/panel/access", headers=TUNNEL_HEADERS)
    assert resp.json()["status"] == "approved"
    assert "set-cookie" not in resp.headers
    assert claimed_engine.panel_devices.list_devices()["pending"] == []


def test_a_sibling_instance_s_cookie_is_a_stranger_here(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine)
    other_name = cookie_name("00000000-1111-2222-3333-444444444444")
    # A fresh client, so only the sibling's cookie is presented.
    resp = lan_client().get("/api/panel/access", headers={"cookie": f"{other_name}={value}"})
    # A cookie under another instance's name is not read at all.
    assert resp.json()["status"] == "pending"


def test_a_denied_device_is_told_so(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    first = client.get("/api/panel/access")
    device_id = set_cookie_of(first)[cookie_name(claimed_engine.instance_id)].value.split(".")[0]
    deny = client.post(f"/api/panel/devices/{device_id}/deny", auth=AUTH)
    assert deny.status_code == 200 and deny.json()["status"] == "denied"
    assert client.get("/api/panel/access").json()["status"] == "denied"


def test_unavailable_at_the_cap_and_nothing_is_stored(claimed_engine, access_mode, monkeypatch):
    access_mode("approved")
    monkeypatch.setattr(pd, "MAX_PENDING_PER_ADDRESS", 1)
    lan_client().get("/api/panel/access")
    resp = lan_client().get("/api/panel/access")
    assert resp.json() == {"access": "approved", "status": "unavailable"}
    assert "set-cookie" not in resp.headers
    assert len(claimed_engine.panel_devices.list_devices()["pending"]) == 1


def test_anonymous_access_admits_everyone(tmp_path, access_mode, isolated_auth_config, monkeypatch):
    """Rule 2's other half: an instance with no credential that allows
    anonymous access (a dev checkout) treats every panel as approved."""
    from tests.panel_access_helpers import make_engine
    from openavc.api import rest, ws
    import openavc.api.auth as auth_mod

    access_mode("approved")
    engine = make_engine(tmp_path)
    rest.set_engine(engine)
    ws.set_engine(engine)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    isolated_auth_config.set("auth", "allow_anonymous", True)
    try:
        resp = lan_client().get("/api/panel/access")
        assert resp.json()["status"] == "approved"
        assert engine.panel_devices.list_devices()["pending"] == []
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


# --- the rule itself, in order ---


def test_admit_records_the_most_specific_reason(claimed_engine, access_mode):
    """In open mode a socket the console, a tunnel or a cookie would admit
    anyway is remembered by that reason, so a switch to approved-only closes
    only the sockets that were in because the mode was open."""
    from starlette.requests import Request

    def conn(host="10.1.1.50", headers=None):
        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        return Request({"type": "http", "method": "GET", "path": "/ws", "headers": raw,
                        "client": (host, 5), "query_string": b"", "scheme": "http",
                        "server": ("testserver", 80)})

    access_mode("approved")
    value = approve_a_device(lan_client(), claimed_engine)
    device_id = value.split(".")[0]

    access_mode("open")
    assert admit(conn(), authenticated=True, engine=claimed_engine) == Admission(True, "credential")
    assert admit(conn("127.0.0.1"), authenticated=False, engine=claimed_engine) == Admission(True, "console")
    assert admit(conn("127.0.0.1", TUNNEL_HEADERS), authenticated=False, engine=claimed_engine) == Admission(True, "tunnel")
    assert admit(conn(), authenticated=False, engine=claimed_engine) == Admission(True, "open")
    assert admit(conn(headers=cookie_header(claimed_engine, value)), authenticated=False, engine=claimed_engine) == Admission(True, "device", device_id)

    access_mode("approved")
    assert admit(conn(), authenticated=False, engine=claimed_engine).admitted is False
    assert admit(conn(headers=cookie_header(claimed_engine, value)), authenticated=False, engine=claimed_engine).kind == "device"
    assert admit(conn(headers=cookie_header(claimed_engine, device_id + ".wrong")), authenticated=False, engine=claimed_engine).admitted is False


def test_the_mode_fails_closed_on_a_value_that_is_not_open(access_mode):
    access_mode("approved-ish")
    assert pd.access_mode() == "approved"
    access_mode("open")
    assert pd.access_mode() == "open"


# --- the on-panel claim ---


def test_the_claim_takes_the_admin_password_once_and_sets_the_cookie(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    client.get("/api/panel/access")  # pending, cookie in the jar
    resp = client.post("/api/panel/access/claim", json={"name": "Lobby"}, auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"access": "approved", "status": "approved", "name": "Lobby"}
    morsel = set_cookie_of(resp)[cookie_name(claimed_engine.instance_id)]
    assert int(morsel["max-age"]) == pd.COOKIE_MAX_AGE_SECONDS

    approved = claimed_engine.panel_devices.list_devices()["approved"]
    assert [a["name"] for a in approved] == ["Lobby"]
    assert approved[0]["approved_by"] == "admin password on the panel"
    # The device connects with what it was just handed.
    assert client.get("/api/panel/access").json()["status"] == "approved"


def test_the_claim_works_for_a_device_that_never_checked_in(claimed_engine, access_mode):
    access_mode("approved")
    resp = lan_client().post("/api/panel/access/claim", auth=AUTH)
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    assert len(claimed_engine.panel_devices.list_devices()["approved"]) == 1


def test_a_wrong_password_is_a_quiet_401(claimed_engine, access_mode):
    access_mode("approved")
    resp = lan_client().post("/api/panel/access/claim", auth=("admin", "nope"))
    assert resp.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in resp.headers}
    assert resp.json()["detail"] == "That password is not correct."
    assert claimed_engine.panel_devices.list_devices()["approved"] == []


def test_the_claim_on_an_unclaimed_box_says_so(tmp_path, access_mode, isolated_auth_config, monkeypatch):
    """A shipped box nobody has claimed yet: there is no password to be
    wrong about, so the panel is told to set the system up first."""
    from tests.panel_access_helpers import make_engine
    from openavc.api import rest, ws
    import openavc.api.auth as auth_mod

    access_mode("approved")
    engine = make_engine(tmp_path)
    rest.set_engine(engine)
    ws.set_engine(engine)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    isolated_auth_config.set("auth", "allow_anonymous", False)
    try:
        resp = lan_client().post("/api/panel/access/claim", auth=("admin", "anything"))
        assert resp.status_code == 409
        assert "set up" in resp.json()["detail"]
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


def test_the_claim_in_open_mode_has_nothing_to_do(claimed_engine, access_mode):
    access_mode("open")
    resp = lan_client().post("/api/panel/access/claim", auth=AUTH)
    assert resp.json() == {"access": "open"}


def test_the_tiers_the_two_doors_sit_on():
    assert _classify("GET", "/api/panel/access") == "open"
    assert _classify("POST", "/api/panel/access/claim") == "strict"


# --- the Programmer's list ---


def test_the_list_needs_a_credential_and_never_carries_a_secret(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    approve_a_device(client, claimed_engine, "Wall")
    lan_client().get("/api/panel/access")  # a second, waiting device

    assert client.get("/api/panel/devices").status_code == 401
    resp = client.get("/api/panel/devices", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access"] == "approved"
    assert [a["name"] for a in body["approved"]] == ["Wall"]
    assert len(body["pending"]) == 1 and "code" in body["pending"][0]
    assert "secret" not in resp.text and "hash" not in resp.text


def test_rename_and_revoke(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    value = approve_a_device(client, claimed_engine, "Wall")
    device_id = value.split(".")[0]

    assert client.patch(f"/api/panel/devices/{device_id}", json={"name": "  "}, auth=AUTH).status_code == 422
    renamed = client.patch(f"/api/panel/devices/{device_id}", json={"name": "Lobby"}, auth=AUTH)
    assert renamed.json()["name"] == "Lobby"

    revoked = client.delete(f"/api/panel/devices/{device_id}", auth=AUTH)
    assert revoked.json() == {"status": "revoked", "panel_id": device_id}
    assert claimed_engine.panel_devices.list_devices()["approved"] == []
    # The device's next check-in starts over with a new code.
    assert client.get("/api/panel/access").json()["status"] == "pending"


def test_unknown_ids_are_404(claimed_engine, access_mode):
    access_mode("approved")
    client = lan_client()
    for method, path, body in (
        ("post", "/api/panel/devices/pd_nobody0000/approve", {}),
        ("post", "/api/panel/devices/pd_nobody0000/deny", None),
        ("delete", "/api/panel/devices/pd_nobody0000", None),
        ("patch", "/api/panel/devices/pd_nobody0000", {"name": "x"}),
    ):
        resp = getattr(client, method)(path, json=body, auth=AUTH) if body is not None else getattr(client, method)(path, auth=AUTH)
        assert resp.status_code == 404, (method, path, resp.text)


def test_approval_from_a_tunnel_is_recorded_as_the_cloud(claimed_engine, access_mode):
    access_mode("approved")
    first = lan_client().get("/api/panel/access")
    device_id = set_cookie_of(first)[cookie_name(claimed_engine.instance_id)].value.split(".")[0]
    resp = loopback_client().post(
        f"/api/panel/devices/{device_id}/approve", json={}, headers=TUNNEL_HEADERS, auth=AUTH,
    )
    assert resp.json()["approved_by"] == "cloud"


# --- the setting ---


def test_the_setting_takes_two_values(claimed_engine, access_mode):
    access_mode("open")
    client = lan_client()
    bad = client.patch("/api/system/config", json={"panels": {"access": "sometimes"}}, auth=AUTH)
    assert bad.status_code == 422
    assert get_system_config().get("panels", "access") == "open"

    good = client.patch("/api/system/config", json={"panels": {"access": "approved"}}, auth=AUTH)
    assert good.status_code == 200
    assert get_system_config().get("panels", "access") == "approved"
    assert client.get("/api/system/config", auth=AUTH).json()["panels"] == {"access": "approved"}


def test_a_round_trip_keeps_the_advertise_switch(claimed_engine, access_mode):
    access_mode("open")
    client = lan_client()
    before = client.get("/api/system/config", auth=AUTH).json()
    assert before["discovery"] == {"advertise": True}
    client.patch("/api/system/config", json={"discovery": {"advertise": False}, "panels": {"access": "approved"}}, auth=AUTH)
    after = client.get("/api/system/config", auth=AUTH).json()
    assert after["discovery"] == {"advertise": False}
    assert after["panels"] == {"access": "approved"}
    client.patch("/api/system/config", json={"discovery": {"advertise": True}}, auth=AUTH)


def test_the_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAVC_PANEL_ACCESS", "open")
    reset_system_config()
    try:
        assert get_system_config().get("panels", "access") == "open"
        assert pd.access_mode() == "open"
    finally:
        reset_system_config()
    # Fresh again without the override: the shipped default, which is that a
    # new panel waits to be approved.
    monkeypatch.delenv("OPENAVC_PANEL_ACCESS")
    reset_system_config()
    try:
        assert get_system_config().get("panels", "access") == "approved"
        assert pd.access_mode() == "approved"
    finally:
        reset_system_config()


@pytest.mark.parametrize("value", ["", "Approved", "OPEN", "closed", None, 1])
def test_only_the_exact_words_are_accepted(claimed_engine, access_mode, value):
    access_mode("open")
    resp = lan_client().patch("/api/system/config", json={"panels": {"access": value}}, auth=AUTH)
    assert resp.status_code == 422
