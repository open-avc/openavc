"""Tests for the C10 OS-credential hardening: the privileged-helper IPC
(``server/host_control.py``) and its wiring into claim / password-change /
SSH-toggle / reboot.

The privileged helper itself is a root-owned shell script installed only on the
Pi appliance image; these tests cover the unprivileged server half — that it
writes the right request files, gates on helper availability, never puts the
password in a request, and that claim/password-change/SSH/reboot route through
it. The helper's own drain/parse logic is covered by a bash smoke test.
"""

import asyncio
import base64
import json

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from openavc import host_control as hc
from openavc.api import auth
from openavc.api.routes import host as host_routes
from openavc.api.routes import system as system_routes
from openavc.system_config import get_system_config


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """Point the helper spool at a tmp dir and pretend the helper is installed."""
    req = tmp_path / "priv-requests"
    res = tmp_path / "priv-results"
    monkeypatch.setattr(hc, "_request_dir", lambda: req)
    monkeypatch.setattr(hc, "_result_dir", lambda: res)
    monkeypatch.setattr(hc, "helper_available", lambda: True)
    return req, res


@pytest.fixture(autouse=True)
def _isolate_auth():
    """Snapshot/restore the auth section so claim/password tests don't leak."""
    cfg = get_system_config()
    saved = cfg.section("auth")
    auth._deployment_is_dev.cache_clear()
    yield
    cfg._data["auth"] = dict(saved)
    auth._deployment_is_dev.cache_clear()
    cfg.save()


def _requests(req_dir):
    return [json.loads(f.read_text()) for f in sorted(req_dir.glob("*.json"))]


# --- helper_available gate ---------------------------------------------------


def test_helper_available_reflects_path_unit(tmp_path, monkeypatch):
    marker = tmp_path / "openavc-privileged.path"
    monkeypatch.setattr(hc, "_PATH_UNIT", marker)
    assert hc.helper_available() is False
    marker.write_text("[Path]\n")
    assert hc.helper_available() is True


# --- sync_os_password --------------------------------------------------------


def test_sync_os_password_writes_request_when_available(spool):
    req_dir, _ = spool
    assert hc.sync_os_password() is True
    reqs = _requests(req_dir)
    assert len(reqs) == 1
    assert reqs[0]["action"] == "set_password"


def test_sync_os_password_carries_no_secret(spool):
    """The password is read by the root helper from system.json — it must never
    appear in the request file the unprivileged server writes."""
    req_dir, _ = spool
    get_system_config().set("auth", "programmer_password", "topsecretpw123")
    hc.sync_os_password()
    raw = "".join(f.read_text() for f in req_dir.glob("*.json"))
    assert "topsecretpw123" not in raw


def test_sync_os_password_noop_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    assert hc.sync_os_password() is False


# --- set_ssh -----------------------------------------------------------------


def test_set_ssh_writes_request_and_reads_result(spool, monkeypatch):
    req_dir, res_dir = spool
    monkeypatch.setattr(hc.secrets, "token_hex", lambda n=8: "fixedid01")
    # Simulate the root helper having already written the result.
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "fixedid01.json").write_text('{"ok": true, "error": ""}')

    result = asyncio.run(hc.set_ssh(True))
    assert result["ok"] is True and result["pending"] is False

    reqs = _requests(req_dir)
    assert len(reqs) == 1
    assert reqs[0]["action"] == "set_ssh"
    assert reqs[0]["enabled"] is True
    assert reqs[0]["want_result"] is True
    # Result consumed.
    assert not (res_dir / "fixedid01.json").exists()


def test_set_ssh_times_out_pending_when_no_result(spool, monkeypatch):
    monkeypatch.setattr(hc, "_RESULT_TIMEOUT", 0.2)
    monkeypatch.setattr(hc, "_RESULT_POLL", 0.02)
    result = asyncio.run(hc.set_ssh(False))
    assert result["pending"] is True
    assert result["ok"] is False


def test_set_ssh_not_supported_when_unavailable(monkeypatch):
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    result = asyncio.run(hc.set_ssh(True))
    assert result == {"ok": False, "error": "not_supported", "pending": False}


def test_ssh_status_unsupported_off_pi(monkeypatch):
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    assert hc.ssh_status() == {"supported": False, "enabled": None}


# --- claim_instance syncs the OS password (the C10 core regression) ----------


def test_claim_syncs_os_password_when_helper_present(spool):
    req_dir, _ = spool
    get_system_config().set("auth", "programmer_password", "")
    get_system_config().set("auth", "api_key", "")
    auth.claim_instance("commission123")
    reqs = _requests(req_dir)
    assert [r["action"] for r in reqs] == ["set_password"]


def test_claim_no_os_sync_when_helper_absent(tmp_path, monkeypatch):
    req_dir = tmp_path / "priv-requests"
    monkeypatch.setattr(hc, "_request_dir", lambda: req_dir)
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    get_system_config().set("auth", "programmer_password", "")
    get_system_config().set("auth", "api_key", "")
    auth.claim_instance("commission123")
    assert not req_dir.exists() or not list(req_dir.glob("*.json"))


def test_claim_still_sets_password_even_if_sync_raises(spool, monkeypatch):
    """OS sync is best-effort: a helper failure must not break the claim."""
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(hc, "sync_os_password", boom)
    get_system_config().set("auth", "programmer_password", "")
    get_system_config().set("auth", "api_key", "")
    auth.claim_instance("commission123")
    assert get_system_config().get("auth", "programmer_password") == "commission123"


# --- authenticated password-change path re-syncs -----------------------------


def _protected_app() -> FastAPI:
    app = FastAPI()
    protected = APIRouter(prefix="/api", dependencies=[Depends(auth.require_programmer_auth)])
    # Two routers: the password-change path is PATCH /api/system/config
    # (system), the SSH and reboot paths are host.
    protected.include_router(system_routes.router)
    protected.include_router(host_routes.router)
    app.include_router(protected)
    return app


def _basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_password_change_patch_resyncs_os_password(spool):
    req_dir, _ = spool
    cfg = get_system_config()
    cfg.set("auth", "programmer_username", "")
    cfg.set("auth", "programmer_password", "adminpass123")
    cfg.set("auth", "allow_anonymous", False)

    client = TestClient(_protected_app())
    r = client.patch(
        "/api/system/config",
        json={"auth": {"programmer_password": "rotatedpass123"}},
        headers=_basic("admin", "adminpass123"),
    )
    assert r.status_code == 200
    assert [x["action"] for x in _requests(req_dir)] == ["set_password"]


def test_non_password_config_change_does_not_sync(spool):
    req_dir, _ = spool
    cfg = get_system_config()
    cfg.set("auth", "programmer_username", "")
    cfg.set("auth", "programmer_password", "adminpass123")
    cfg.set("auth", "allow_anonymous", False)

    client = TestClient(_protected_app())
    r = client.patch(
        "/api/system/config",
        json={"logging": {"level": "debug"}},
        headers=_basic("admin", "adminpass123"),
    )
    assert r.status_code == 200
    assert _requests(req_dir) == []


# --- SSH + reboot endpoints --------------------------------------------------


def test_ssh_post_requires_auth(spool):
    cfg = get_system_config()
    cfg.set("auth", "programmer_password", "adminpass123")
    cfg.set("auth", "allow_anonymous", False)
    client = TestClient(_protected_app())
    r = client.post("/api/system/ssh", json={"enabled": True})  # no creds
    assert r.status_code == 401


def test_ssh_post_enables_and_writes_request(spool, monkeypatch):
    req_dir, res_dir = spool
    monkeypatch.setattr(hc.secrets, "token_hex", lambda n=8: "sshreq01")
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "sshreq01.json").write_text('{"ok": true, "error": ""}')

    cfg = get_system_config()
    cfg.set("auth", "programmer_username", "")
    cfg.set("auth", "programmer_password", "adminpass123")
    cfg.set("auth", "allow_anonymous", False)

    client = TestClient(_protected_app())
    r = client.post("/api/system/ssh", json={"enabled": True}, headers=_basic("a", "adminpass123"))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["enabled"] is True
    reqs = _requests(req_dir)
    assert reqs and reqs[0]["action"] == "set_ssh" and reqs[0]["enabled"] is True


def test_ssh_endpoints_501_when_unsupported(monkeypatch):
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    cfg = get_system_config()
    cfg.set("auth", "programmer_password", "")
    cfg.set("auth", "allow_anonymous", True)  # open so we reach the handler
    client = TestClient(_protected_app())
    assert client.post("/api/system/ssh", json={"enabled": True}).status_code == 501
    # GET status is allowed and simply reports unsupported.
    assert client.get("/api/system/ssh").json() == {"supported": False, "enabled": None}


def test_reboot_501_when_helper_absent(monkeypatch):
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    cfg = get_system_config()
    cfg.set("auth", "programmer_password", "")
    cfg.set("auth", "allow_anonymous", True)
    client = TestClient(_protected_app())
    assert client.post("/api/system/reboot").status_code == 501


def test_reboot_writes_request_when_helper_present(spool):
    req_dir, _ = spool
    cfg = get_system_config()
    cfg.set("auth", "programmer_password", "")
    cfg.set("auth", "allow_anonymous", True)
    client = TestClient(_protected_app())
    r = client.post("/api/system/reboot")
    assert r.status_code == 200 and r.json()["status"] == "rebooting"
    assert [x["action"] for x in _requests(req_dir)] == ["reboot"]
