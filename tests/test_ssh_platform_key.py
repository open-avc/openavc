"""OpenAVC's own SSH key: made once, readable by the server alone, used by key
sign-in whenever a device names no key file, and shown through one read-only
route that never returns the private half."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.transport import ssh_identity


@pytest.fixture
def key_dir(tmp_path: Path) -> Path:
    return tmp_path / "ssh"


def test_made_once_and_parses_as_an_openssh_rsa_key(key_dir: Path):
    from cryptography.hazmat.primitives import serialization

    key = ssh_identity.ensure_platform_key(key_dir)
    assert key.path == key_dir / ssh_identity.KEY_FILENAME
    data = key.path.read_bytes()
    assert data.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
    private = serialization.load_ssh_private_key(data, password=None)
    assert private.key_size == ssh_identity.KEY_BITS
    assert key.key_type == "ssh-rsa"
    assert key.bits == ssh_identity.KEY_BITS

    again = ssh_identity.ensure_platform_key(key_dir)
    assert again.public_key == key.public_key
    assert key.path.read_bytes() == data


def test_public_line_is_one_authorized_keys_entry(key_dir: Path):
    key = ssh_identity.ensure_platform_key(key_dir)
    parts = key.public_key.split(" ")
    assert len(parts) == 3
    assert parts[0] == "ssh-rsa"
    assert parts[2].startswith("openavc")
    assert " " not in parts[2]
    assert "\n" not in key.public_key
    assert "PRIVATE" not in key.public_key
    assert key.fingerprint.startswith("SHA256:")
    assert not key.fingerprint.endswith("=")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode")
def test_private_key_is_owner_only(key_dir: Path):
    key = ssh_identity.ensure_platform_key(key_dir)
    assert stat.S_IMODE(key.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_dir.stat().st_mode) == 0o700


def test_concurrent_first_use_makes_one_key(key_dir: Path):
    results: list[str] = []
    errors: list[BaseException] = []

    def _get():
        try:
            results.append(ssh_identity.ensure_platform_key(key_dir).public_key)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=_get) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(results)) == 1
    leftovers = [p.name for p in key_dir.iterdir() if p.name != ssh_identity.KEY_FILENAME]
    assert leftovers == []


def test_a_second_writer_that_loses_the_race_keeps_the_winners_key(key_dir: Path, monkeypatch):
    """Another process creates the key between our check and our link."""
    winner = ssh_identity.ensure_platform_key(key_dir)
    winner_bytes = winner.path.read_bytes()
    ssh_identity._cache.clear()

    real_exists = Path.exists

    def _pretend_missing(self):
        if self == winner.path:
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _pretend_missing)
    loser = ssh_identity.ensure_platform_key(key_dir)
    monkeypatch.undo()
    assert winner.path.read_bytes() == winner_bytes
    assert loser.public_key == winner.public_key
    assert [p.name for p in key_dir.iterdir()] == [ssh_identity.KEY_FILENAME]


def _ssh_keygen() -> str | None:
    """Windows' own OpenSSH first: Git's copy, often earlier on PATH, does not
    apply the Windows permission check this test exists to exercise."""
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        inbox = os.path.join(root, "System32", "OpenSSH", "ssh-keygen.exe")
        if os.path.exists(inbox):
            return inbox
    return shutil.which("ssh-keygen")


@pytest.mark.skipif(_ssh_keygen() is None, reason="no ssh-keygen")
def test_the_openssh_client_accepts_the_key(key_dir: Path):
    """ssh-keygen loads a private key with the same checks ssh applies, file
    permissions included (Windows' client refuses a key other accounts can
    read), and must derive the same public key the Programmer shows."""
    key = ssh_identity.ensure_platform_key(key_dir)
    keygen = _ssh_keygen()
    result = subprocess.run(
        [keygen, "-y", "-f", str(key.path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    derived = result.stdout.split()
    shown = key.public_key.split()
    assert derived[:2] == shown[:2]

    fp = subprocess.run(
        [keygen, "-l", "-E", "sha256", "-f", str(key.path)],
        capture_output=True, text=True, timeout=30,
    )
    assert fp.returncode == 0, fp.stderr
    assert key.fingerprint in fp.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL")
def test_windows_key_grants_no_other_accounts(key_dir: Path):
    key = ssh_identity.ensure_platform_key(key_dir)
    out = subprocess.run(
        ["icacls", str(key.path)], capture_output=True, text=True, timeout=30,
    ).stdout
    assert "(I)" not in out  # nothing inherited from the folder
    assert "Users:" not in out  # neither BUILTIN\Users nor Authenticated Users


# --- BaseDriver --------------------------------------------------------------


def _ssh_driver(config: dict):
    from openavc.drivers import base as base_mod

    class _Driver(base_mod.BaseDriver):
        DRIVER_INFO = {"id": "acme_cli", "name": "Acme CLI", "transport": "ssh"}

        async def get_state(self):
            return {}

        async def send_command(self, command, params=None):
            return None

    base = {"host": "acme-switch.invalid", "port": 22, "username": "avc", "transport": "ssh"}
    base.update(config)
    return _Driver("acme_key", base, StateStore(), EventBus())


def _capture_create(monkeypatch) -> dict:
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("openavc.transport.ssh.SSHTransport.create", _fake_create)
    return seen


def test_key_sign_in_with_no_key_path_uses_openavcs_key(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_identity, "key_dir", lambda: tmp_path / "ssh")
    seen = _capture_create(monkeypatch)
    asyncio.run(_ssh_driver({"ssh_auth_method": "key", "key_path": ""}).connect())
    assert seen["key_path"] == str(tmp_path / "ssh" / ssh_identity.KEY_FILENAME)
    assert Path(seen["key_path"]).is_file()


def test_unset_auth_method_is_key_sign_in(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_identity, "key_dir", lambda: tmp_path / "ssh")
    seen = _capture_create(monkeypatch)
    asyncio.run(_ssh_driver({}).connect())
    assert seen["key_path"] == str(tmp_path / "ssh" / ssh_identity.KEY_FILENAME)


def test_a_devices_own_key_path_still_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_identity, "key_dir", lambda: tmp_path / "ssh")
    seen = _capture_create(monkeypatch)
    asyncio.run(_ssh_driver({"ssh_auth_method": "key", "key_path": "/keys/acme"}).connect())
    assert seen["key_path"] == "/keys/acme"
    assert not (tmp_path / "ssh").exists()


def test_password_sign_in_makes_no_key(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_identity, "key_dir", lambda: tmp_path / "ssh")
    seen = _capture_create(monkeypatch)
    asyncio.run(_ssh_driver({"ssh_auth_method": "password", "password": "pw"}).connect())
    assert seen["key_path"] is None
    assert not (tmp_path / "ssh").exists()


def test_a_key_that_cannot_be_made_does_not_stop_the_connection(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(ssh_identity, "ensure_platform_key", _boom)
    seen = _capture_create(monkeypatch)
    asyncio.run(_ssh_driver({"ssh_auth_method": "key"}).connect())
    assert seen["key_path"] is None


# --- The route ---------------------------------------------------------------


def test_route_returns_the_public_key_only(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from openavc.api.auth import require_programmer_auth
    from openavc.main import app

    monkeypatch.setattr(ssh_identity, "key_dir", lambda: tmp_path / "ssh")
    app.dependency_overrides[require_programmer_auth] = lambda: None
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/devices/ssh-key")
    finally:
        app.dependency_overrides.pop(require_programmer_auth, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"public_key", "fingerprint", "key_type", "bits"}
    assert body["public_key"].startswith("ssh-rsa ")
    assert body["bits"] == ssh_identity.KEY_BITS
    assert "PRIVATE" not in resp.text
    on_disk = ssh_identity.ensure_platform_key(tmp_path / "ssh")
    assert body["public_key"] == on_disk.public_key


def test_route_is_not_shadowed_by_the_device_route():
    """/devices/ssh-key must be matched before /devices/{device_id}."""
    from openavc.main import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert paths.index("/api/devices/ssh-key") < paths.index("/api/devices/{device_id}")
