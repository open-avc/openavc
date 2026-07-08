"""Tests for /api/certificate and /api/system/tls-status endpoints.

The endpoints read the live system config (``get_system_config()``) at request
time so a just-saved PATCH is reflected without a restart, so tests set those
values on the singleton plus ``data_dir`` to point at a tmp_path-backed cert
store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server import tls as tls_module
from server.system_config import get_system_config


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tls_dir(tmp_path, monkeypatch) -> Path:
    """Point the system data dir at tmp_path so endpoints find our test CA."""
    cfg = get_system_config()
    monkeypatch.setattr(cfg, "_data_dir", tmp_path)
    return tmp_path / "tls"


def _set_cfg(monkeypatch, section: str, **values) -> None:
    """Set live system-config values for the duration of a test (auto-restored)."""
    cfg = get_system_config()
    for key, value in values.items():
        monkeypatch.setitem(cfg._data[section], key, value)


def _generate_test_cert(data_dir: Path) -> Path:
    """Run the cert generator into ``data_dir/tls/`` and return the cert path."""
    paths = tls_module.generate_self_signed(
        data_dir, hostnames=["localhost", "openavc"], ips=["127.0.0.1", "192.168.1.50"]
    )
    return paths.cert_path


# ---------------------------------------------------------------------------
# /api/certificate
# ---------------------------------------------------------------------------


def test_certificate_404_when_tls_off(client, monkeypatch):
    _set_cfg(monkeypatch, "tls", enabled=False)
    resp = client.get("/api/certificate")
    assert resp.status_code == 404


def test_certificate_404_when_provided_mode(client, monkeypatch, tls_dir):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=False)
    resp = client.get("/api/certificate")
    assert resp.status_code == 404


def test_certificate_404_when_ca_file_missing(client, monkeypatch, tls_dir):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=True)
    # No cert generated → ca.crt does not exist.
    resp = client.get("/api/certificate")
    assert resp.status_code == 404


def test_certificate_returns_pem_when_enabled(client, monkeypatch, tls_dir):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=True)
    _generate_test_cert(tls_dir.parent)

    resp = client.get("/api/certificate")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-pem-file"
    assert 'filename="openavc-ca.crt"' in resp.headers["content-disposition"]
    assert resp.content.startswith(b"-----BEGIN CERTIFICATE-----")


# ---------------------------------------------------------------------------
# /api/system/tls-status
# ---------------------------------------------------------------------------


def test_tls_status_off(client, monkeypatch):
    _set_cfg(monkeypatch, "tls", enabled=False)
    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_tls_status_on_auto(client, monkeypatch, tls_dir):
    _set_cfg(
        monkeypatch,
        "tls",
        enabled=True,
        port=8443,
        redirect_http=True,
        auto_generate=True,
        cert_file="",
        key_file="",
    )
    _set_cfg(monkeypatch, "network", bind_address="127.0.0.1")
    _generate_test_cert(tls_dir.parent)

    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["port"] == 8443
    assert body["redirect_http"] is True
    assert body["mode"] == "auto"
    assert body.get("error") is None
    cert = body["cert"]
    assert cert is not None
    assert "OpenAVC" in cert["issuer"]
    assert cert["days_until_expiry"] > 365 * 9
    assert "localhost" in cert["sans"]
    assert "127.0.0.1" in cert["sans"]
    assert len(cert["fingerprint"]) == 64
    # Auto cert was just generated → no warnings.
    assert "expired" not in cert["warnings"]


def test_tls_status_on_provided(client, monkeypatch, tls_dir, tmp_path):
    # Build a provided cert pair via the same generator, then point config at it.
    paths = tls_module.generate_self_signed(
        tmp_path, hostnames=["localhost"], ips=["127.0.0.1"]
    )
    _set_cfg(
        monkeypatch,
        "tls",
        enabled=True,
        port=8443,
        redirect_http=True,
        auto_generate=False,
        cert_file=str(paths.cert_path),
        key_file=str(paths.key_path),
    )
    _set_cfg(monkeypatch, "network", bind_address="127.0.0.1")

    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["mode"] == "provided"
    assert body["cert"]["sans"] == ["localhost", "127.0.0.1"]


def test_tls_status_cert_missing_returns_error(client, monkeypatch, tls_dir):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=True, cert_file="", key_file="")
    # No cert generated → server.crt missing.
    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["cert"] is None
    assert "not found" in body["error"].lower()


def test_tls_status_flags_expired_cert(client, monkeypatch, tmp_path):
    """An expired user-provided cert surfaces 'expired' in warnings."""
    # Build an expired cert pair manually.
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=365))
        .not_valid_after(now - dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "expired.crt"
    key_path = tmp_path / "expired.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    _set_cfg(
        monkeypatch,
        "tls",
        enabled=True,
        port=8443,
        redirect_http=True,
        auto_generate=False,
        cert_file=str(cert_path),
        key_file=str(key_path),
    )
    _set_cfg(monkeypatch, "network", bind_address="127.0.0.1")

    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    assert "expired" in resp.json()["cert"]["warnings"]


# ---------------------------------------------------------------------------
# /api/system/tls/upload-cert
# ---------------------------------------------------------------------------


def _make_cert_key_pair(
    *, ca: bool = False, with_passphrase: bytes | None = None
) -> tuple[bytes, bytes]:
    """Generate a one-shot self-signed cert + matching key as PEM bytes."""
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
    )
    if ca:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
    cert = builder.sign(key, hashes.SHA256())

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    encryption: Any = serialization.NoEncryption()
    if with_passphrase is not None:
        encryption = serialization.BestAvailableEncryption(with_passphrase)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=encryption,
    )
    return cert_pem, key_pem


def _upload(client, cert_pem: bytes, key_pem: bytes):
    return client.post(
        "/api/system/tls/upload-cert",
        files={
            "cert": ("cert.pem", cert_pem, "application/x-pem-file"),
            "key": ("key.pem", key_pem, "application/x-pem-file"),
        },
    )


def test_upload_cert_happy_path_writes_files_and_returns_metadata(
    client, tls_dir, tmp_path
):
    cert_pem, key_pem = _make_cert_key_pair()
    resp = _upload(client, cert_pem, key_pem)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cert_path"].endswith("user-cert.pem")
    assert body["key_path"].endswith("user-key.pem")
    assert len(body["fingerprint"]) == 64
    assert "localhost" in body["sans"]
    # Files exist on disk where the endpoint said they would.
    assert Path(body["cert_path"]).read_bytes() == cert_pem
    assert Path(body["key_path"]).read_bytes() == key_pem


def test_upload_cert_rejects_empty_cert(client, tls_dir):
    _cert_pem, key_pem = _make_cert_key_pair()
    resp = _upload(client, b"", key_pem)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_upload_cert_rejects_empty_key(client, tls_dir):
    cert_pem, _key_pem = _make_cert_key_pair()
    resp = _upload(client, cert_pem, b"")
    assert resp.status_code == 400


def test_upload_cert_rejects_oversize_payload(client, tls_dir):
    big = b"-----BEGIN CERTIFICATE-----\n" + (b"A" * 60_000) + b"\n-----END CERTIFICATE-----\n"
    resp = _upload(client, big, big)
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"].lower()


def test_upload_cert_rejects_garbage_cert(client, tls_dir):
    _cert_pem, key_pem = _make_cert_key_pair()
    resp = _upload(client, b"not a certificate", key_pem)
    assert resp.status_code == 400
    assert "x.509" in resp.json()["detail"].lower()


def test_upload_cert_rejects_garbage_key(client, tls_dir):
    cert_pem, _key_pem = _make_cert_key_pair()
    resp = _upload(client, cert_pem, b"not a key")
    assert resp.status_code == 400
    assert "key" in resp.json()["detail"].lower()


def test_upload_cert_rejects_passphrase_key(client, tls_dir):
    cert_pem, _key_pem = _make_cert_key_pair()
    # Build a passphrase-protected key that does NOT match cert_pem; the
    # passphrase check must fire before the match check.
    _, encrypted_key = _make_cert_key_pair(with_passphrase=b"secret")
    resp = _upload(client, cert_pem, encrypted_key)
    assert resp.status_code == 400
    assert "passphrase" in resp.json()["detail"].lower()


def test_upload_cert_rejects_mismatched_pair(client, tls_dir):
    cert_pem, _ = _make_cert_key_pair()
    _, key_pem = _make_cert_key_pair()  # different key
    resp = _upload(client, cert_pem, key_pem)
    assert resp.status_code == 400
    assert "do not match" in resp.json()["detail"].lower()


def test_upload_cert_flags_ca_cert_as_warning(client, tls_dir):
    cert_pem, key_pem = _make_cert_key_pair(ca=True)
    resp = _upload(client, cert_pem, key_pem)
    assert resp.status_code == 200
    assert "is-ca-cert" in resp.json()["warnings"]


# ---------------------------------------------------------------------------
# PATCH /api/system/config TLS invariant guard
# ---------------------------------------------------------------------------


def test_patch_system_config_rejects_provided_mode_without_cert(client):
    resp = client.patch(
        "/api/system/config",
        json={"tls": {"enabled": True, "auto_generate": False, "cert_file": "", "key_file": ""}},
    )
    assert resp.status_code == 400
    assert "provided" in resp.json()["detail"].lower()


def test_patch_system_config_allows_provided_mode_with_cert(client, monkeypatch):
    # Don't actually write to the real system.json — monkeypatch the save.
    from server.system_config import get_system_config

    cfg = get_system_config()
    monkeypatch.setattr(cfg, "save", lambda: None)
    resp = client.patch(
        "/api/system/config",
        json={
            "tls": {
                "enabled": True,
                "auto_generate": False,
                "cert_file": "/tmp/cert.pem",
                "key_file": "/tmp/key.pem",
            },
        },
    )
    assert resp.status_code == 200


def test_patch_system_config_allows_auto_mode(client, monkeypatch):
    from server.system_config import get_system_config

    cfg = get_system_config()
    monkeypatch.setattr(cfg, "save", lambda: None)
    resp = client.patch(
        "/api/system/config",
        json={"tls": {"enabled": True, "auto_generate": True}},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# cloud_cert block + /api/system/tls/cloud-cert endpoints
# ---------------------------------------------------------------------------

CLOUD_LABEL = "ab12cd34ef56ab78"
CLOUD_ZONE = "i.certtest.invalid"


@pytest.fixture(autouse=True)
def _clean_cloud_holder():
    """Cloud state lives in a module-level holder — isolate every test."""
    tls_module.cloud_cert_holder().clear()
    yield
    tls_module.cloud_cert_holder().clear()


@pytest.fixture
def no_engine(monkeypatch):
    """Force the shared engine slot empty regardless of test ordering."""
    from server.api import _engine as engine_state
    monkeypatch.setattr(engine_state, "_engine", None)


class _FakeCertManager:
    def __init__(self):
        self.enable_calls = 0
        self.disable_calls = 0
        self.enable_result = (True, "")
        self.status = {
            "enabled": True,
            "phase": "issuing",
            "hostname_suffix": f"{CLOUD_LABEL}.{CLOUD_ZONE}",
            "last_error": "acme_failed",
            "last_error_detail": "boom",
            "last_attempt_at": "2026-07-08T00:00:00+00:00",
            "retry_pending": True,
        }

    def get_status(self):
        return self.status

    async def enable(self):
        self.enable_calls += 1
        return self.enable_result

    async def disable(self):
        self.disable_calls += 1


@pytest.fixture
def fake_engine(monkeypatch):
    """A paired + connected engine/agent/cert-manager stand-in."""
    from server.api import _engine as engine_state

    class FakeAgent:
        def __init__(self):
            self.cert_manager = _FakeCertManager()
            self.connected = True

        def has_capability(self, capability):
            return capability == "trusted_certs"

    class FakeEngine:
        def __init__(self):
            self.cloud_agent = FakeAgent()

    engine = FakeEngine()
    monkeypatch.setattr(engine_state, "_engine", engine)
    return engine


def _install_cloud_cert(data_dir):
    from tests.helpers import make_cloud_cert_pem

    cert_pem, key_pem = make_cloud_cert_pem(CLOUD_LABEL, CLOUD_ZONE)
    return tls_module.install_cloud_cert(data_dir, cert_pem, key_pem)


def test_tls_status_cloud_cert_defaults(client, monkeypatch, tls_dir, no_engine):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=True,
             cert_file="", key_file="", cloud_cert=False)
    _set_cfg(monkeypatch, "network", bind_address="127.0.0.1")
    _generate_test_cert(tls_dir.parent)

    body = client.get("/api/system/tls-status").json()
    cc = body["cloud_cert"]
    assert cc["enabled"] is False
    assert cc["paired"] is False
    assert cc["available"] is False
    assert cc["active"] is False
    assert cc["hostname_suffix"] == ""
    assert cc["expires_at"] is None
    assert cc["renews_at"] is None
    assert cc["phase"] == "idle"
    assert cc["last_error"] == ""


def test_tls_status_cloud_cert_installed_and_active(client, monkeypatch, tls_dir, no_engine):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=True,
             cert_file="", key_file="", cloud_cert=True)
    _set_cfg(monkeypatch, "network", bind_address="127.0.0.1")
    _generate_test_cert(tls_dir.parent)
    _install_cloud_cert(tls_dir.parent)

    cc = client.get("/api/system/tls-status").json()["cloud_cert"]
    assert cc["enabled"] is True
    assert cc["active"] is True
    assert cc["hostname_suffix"] == f"{CLOUD_LABEL}.{CLOUD_ZONE}"
    assert cc["expires_at"] is not None
    assert cc["renews_at"] is not None
    assert cc["renews_at"] < cc["expires_at"]


def test_tls_status_cloud_cert_surfaces_manager_state(client, monkeypatch, tls_dir, fake_engine):
    _set_cfg(monkeypatch, "tls", enabled=True, auto_generate=True,
             cert_file="", key_file="", cloud_cert=True)
    _set_cfg(monkeypatch, "network", bind_address="127.0.0.1")
    _generate_test_cert(tls_dir.parent)

    cc = client.get("/api/system/tls-status").json()["cloud_cert"]
    assert cc["paired"] is True
    assert cc["available"] is True
    assert cc["phase"] == "issuing"
    assert cc["last_error"] == "acme_failed"
    assert cc["last_error_detail"] == "boom"
    assert cc["retry_pending"] is True
    assert cc["hostname_suffix"] == f"{CLOUD_LABEL}.{CLOUD_ZONE}"


def test_enable_cloud_cert_requires_tls(client, monkeypatch, fake_engine):
    _set_cfg(monkeypatch, "tls", enabled=False)
    resp = client.post("/api/system/tls/cloud-cert/enable")
    assert resp.status_code == 400
    assert "https" in resp.json()["detail"].lower()


def test_enable_cloud_cert_requires_pairing(client, monkeypatch, no_engine):
    _set_cfg(monkeypatch, "tls", enabled=True)
    resp = client.post("/api/system/tls/cloud-cert/enable")
    assert resp.status_code == 400
    assert "pair" in resp.json()["detail"].lower()


def test_enable_cloud_cert_starts_enrollment(client, monkeypatch, fake_engine):
    _set_cfg(monkeypatch, "tls", enabled=True)
    resp = client.post("/api/system/tls/cloud-cert/enable")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "started": True}
    assert fake_engine.cloud_agent.cert_manager.enable_calls == 1


def test_enable_cloud_cert_reports_not_connected(client, monkeypatch, fake_engine):
    _set_cfg(monkeypatch, "tls", enabled=True)
    fake_engine.cloud_agent.cert_manager.enable_result = (False, "not_connected")
    body = client.post("/api/system/tls/cloud-cert/enable").json()
    assert body["started"] is False
    assert body["reason"] == "not_connected"
    assert "automatically" in body["message"]


def test_disable_cloud_cert_via_manager(client, fake_engine):
    resp = client.post("/api/system/tls/cloud-cert/disable")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
    assert fake_engine.cloud_agent.cert_manager.disable_calls == 1


def test_disable_cloud_cert_without_pairing_cleans_up_locally(
    client, monkeypatch, tls_dir, no_engine, tmp_path
):
    """Disable must work even after unpairing: flag off, files gone."""
    from server.system_config import get_system_config

    cfg = get_system_config()
    monkeypatch.setattr(cfg, "_file_path", tmp_path / "system.json")
    _set_cfg(monkeypatch, "tls", enabled=True, cloud_cert=True)
    _install_cloud_cert(tls_dir.parent)

    resp = client.post("/api/system/tls/cloud-cert/disable")
    assert resp.status_code == 200
    assert cfg.get("tls", "cloud_cert") is False
    cert_path, key_path = tls_module.cloud_cert_paths(tls_dir.parent)
    assert not cert_path.exists() and not key_path.exists()
    assert tls_module.cloud_cert_holder().get() is None
