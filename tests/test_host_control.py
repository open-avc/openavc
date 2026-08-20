"""Tests for the C10 OS-credential hardening: the privileged-helper IPC
(``openavc/host_control.py``) and its wiring into claim / password-change /
SSH-toggle / reboot.

The privileged helper itself is a root-owned shell script; these tests cover
the unprivileged server half — that it writes the right request files, gates on
helper availability, hands the password over exactly once, and that
claim/password-change/SSH/reboot route through it. The script's own drain and
parse logic is exercised for real in `test_privileged_helper_script.py`.

The password moved INTO the request when the stored one became a digest
(`openavc/utils/password_hash.py`): the helper used to read
`auth.programmer_password` out of system.json, which is precisely what stopped
that value from being hashable. So the old assertion that a request carries no
secret is gone on purpose — what replaces it is that the request is 0600, is
swept if nothing drains it, and is never sent to a helper too old to read it.
"""

import asyncio
import base64
import json
import stat
import sys

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from openavc import host_control as hc
from openavc.api import auth
from openavc.api.routes import host as host_routes
from openavc.api.routes import system as system_routes
from openavc.system_config import get_system_config
from openavc.utils.password_hash import verify_password


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """Point the helper spool at a tmp dir and pretend the helper is installed.

    Stands up a stub script carrying the real protocol marker rather than
    stubbing `helper_takes_password`, so the version gate is exercised the way
    it runs — reading a file off disk.
    """
    req = tmp_path / "priv-requests"
    res = tmp_path / "priv-results"
    script = tmp_path / "openavc-privileged-helper.sh"
    script.write_text(f"#!/bin/bash\n# {hc._PASSWORD_PROTOCOL_MARKER}\nexit 0\n")
    monkeypatch.setattr(hc, "_request_dir", lambda: req)
    monkeypatch.setattr(hc, "_result_dir", lambda: res)
    monkeypatch.setattr(hc, "_HELPER_SCRIPT", script)
    monkeypatch.setattr(hc, "helper_available", lambda: True)
    return req, res


@pytest.fixture(autouse=True)
def _isolate_auth(isolated_auth_config):
    """Snapshot/restore the auth section so claim/password tests don't leak.
    See the shared fixture in conftest.py for what "don't leak" has to
    cover -- these tests persist a password two different ways."""


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
    assert hc.sync_os_password("topsecretpw123") is True
    reqs = _requests(req_dir)
    assert len(reqs) == 1
    assert reqs[0]["action"] == "set_password"


def test_sync_os_password_carries_the_password(spool):
    """The inverted handoff: the helper is handed the plaintext instead of
    reading the stored value, which is a digest now."""
    req_dir, _ = spool
    hc.sync_os_password("topsecretpw123")
    assert _requests(req_dir)[0]["password"] == "topsecretpw123"


def test_an_empty_password_is_carried_too(spool):
    """An empty password re-locks the OS account, so the helper still has to be
    told — it just isn't told a password."""
    req_dir, _ = spool
    assert hc.sync_os_password("") is True
    assert _requests(req_dir)[0]["password"] == ""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_request_file_is_readable_only_by_its_owner(spool):
    """It holds the admin password for the moment before the helper drains it,
    and on an appliance that is the OS login as well."""
    req_dir, _ = spool
    hc.sync_os_password("topsecretpw123")
    written = list(req_dir.glob("*.json"))
    assert len(written) == 1
    assert stat.S_IMODE(written[0].stat().st_mode) == 0o600


def test_an_old_helper_is_not_sent_a_password(spool, tmp_path, monkeypatch):
    """An appliance flashed before the protocol change keeps its old script
    until an update refreshes it. That copy reads the stored value — a digest —
    and would chpasswd the OS account to the literal digest. Refusing leaves
    the account on the password it already had, which is the recoverable end."""
    req_dir, _ = spool
    old = tmp_path / "old-helper.sh"
    old.write_text("#!/bin/bash\n# reads the password from system.json\nexit 0\n")
    monkeypatch.setattr(hc, "_HELPER_SCRIPT", old)

    assert hc.helper_takes_password() is False
    assert hc.sync_os_password("topsecretpw123") is False
    assert _requests(req_dir) == []


def test_a_missing_helper_script_is_treated_as_old(spool, tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "_HELPER_SCRIPT", tmp_path / "not-installed.sh")
    assert hc.helper_takes_password() is False
    assert hc.sync_os_password("topsecretpw123") is False


def test_the_shipped_helper_declares_the_protocol():
    """The marker is a contract between two files in two languages. If the
    script is reworded, every appliance silently stops syncing its OS password
    — so read the constant from the module and look for it in the script."""
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "installer" / "openavc-privileged-helper.sh"
    assert script.is_file(), f"{script} is missing — this test points at the wrong file"
    assert hc._PASSWORD_PROTOCOL_MARKER in script.read_text(encoding="utf-8")


def test_a_request_nothing_drained_is_swept(spool, monkeypatch):
    """A helper that is installed but not running would otherwise leave the
    plaintext sitting in the spool indefinitely."""
    req_dir, _ = spool
    hc.sync_os_password("topsecretpw123")
    stranded = list(req_dir.glob("*.json"))[0]

    monkeypatch.setattr(hc, "_RESULT_STALE_SECONDS", -1.0)
    hc.request_reboot()

    assert not stranded.exists()
    assert [r["action"] for r in _requests(req_dir)] == ["reboot"]


def test_a_fresh_request_is_not_swept(spool):
    """The sweep is about abandonment, not about the previous request."""
    req_dir, _ = spool
    hc.sync_os_password("topsecretpw123")
    hc.request_reboot()
    assert sorted(r["action"] for r in _requests(req_dir)) == ["reboot", "set_password"]


def test_sync_os_password_noop_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "helper_available", lambda: False)
    assert hc.sync_os_password("topsecretpw123") is False


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
    assert verify_password(
        "commission123", get_system_config().get("auth", "programmer_password")
    )


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


def test_password_change_patch_stores_a_digest(spool):
    """The other write door. It sets config keys through one generic loop, so
    the password is deliberately lifted out of that loop — nothing else would
    stop a plaintext going straight to disk."""
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

    stored = cfg.get("auth", "programmer_password")
    assert verify_password("rotatedpass123", stored)
    assert "rotatedpass123" not in stored
    assert "rotatedpass123" not in cfg.file_path.read_text()


def test_password_change_patch_hands_the_helper_the_plaintext(spool):
    """The one moment the typed password legitimately exists — it has to reach
    the OS sync from here, because nothing can read it back afterwards."""
    req_dir, _ = spool
    cfg = get_system_config()
    cfg.set("auth", "programmer_username", "")
    cfg.set("auth", "programmer_password", "adminpass123")
    cfg.set("auth", "allow_anonymous", False)

    client = TestClient(_protected_app())
    client.patch(
        "/api/system/config",
        json={"auth": {"programmer_password": "rotatedpass123"}},
        headers=_basic("admin", "adminpass123"),
    )
    assert _requests(req_dir)[0]["password"] == "rotatedpass123"


def test_clearing_the_password_through_the_patch_unclaims(spool):
    """An empty password is not hashed — "no credential" is a state the auth
    module reads by truthiness, and it re-locks the OS account too."""
    req_dir, _ = spool
    cfg = get_system_config()
    cfg.set("auth", "programmer_username", "")
    cfg.set("auth", "programmer_password", "adminpass123")
    cfg.set("auth", "allow_anonymous", False)

    client = TestClient(_protected_app())
    r = client.patch(
        "/api/system/config",
        json={"auth": {"programmer_password": ""}},
        headers=_basic("admin", "adminpass123"),
    )
    assert r.status_code == 200
    assert cfg.get("auth", "programmer_password") == ""
    assert _requests(req_dir)[0]["password"] == ""


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
