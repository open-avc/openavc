"""Tests for /api/certificate and /api/system/tls-status endpoints.

The endpoints read ``server.config`` module attributes at request time,
so tests monkeypatch those attributes plus ``data_dir`` to point at a
tmp_path-backed cert store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import config
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
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    resp = client.get("/api/certificate")
    assert resp.status_code == 404


def test_certificate_404_when_provided_mode(client, monkeypatch, tls_dir):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", False)
    resp = client.get("/api/certificate")
    assert resp.status_code == 404


def test_certificate_404_when_ca_file_missing(client, monkeypatch, tls_dir):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", True)
    # No cert generated → ca.crt does not exist.
    resp = client.get("/api/certificate")
    assert resp.status_code == 404


def test_certificate_returns_pem_when_enabled(client, monkeypatch, tls_dir):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", True)
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
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_tls_status_on_auto(client, monkeypatch, tls_dir):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", True)
    monkeypatch.setattr(config, "TLS_CERT_FILE", "")
    monkeypatch.setattr(config, "TLS_KEY_FILE", "")
    monkeypatch.setattr(config, "BIND_ADDRESS", "127.0.0.1")
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
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", False)
    monkeypatch.setattr(config, "TLS_CERT_FILE", str(paths.cert_path))
    monkeypatch.setattr(config, "TLS_KEY_FILE", str(paths.key_path))
    monkeypatch.setattr(config, "BIND_ADDRESS", "127.0.0.1")

    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["mode"] == "provided"
    assert body["cert"]["sans"] == ["localhost", "127.0.0.1"]


def test_tls_status_cert_missing_returns_error(client, monkeypatch, tls_dir):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", True)
    monkeypatch.setattr(config, "TLS_CERT_FILE", "")
    monkeypatch.setattr(config, "TLS_KEY_FILE", "")
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

    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "TLS_AUTO_GENERATE", False)
    monkeypatch.setattr(config, "TLS_CERT_FILE", str(cert_path))
    monkeypatch.setattr(config, "TLS_KEY_FILE", str(key_path))
    monkeypatch.setattr(config, "BIND_ADDRESS", "127.0.0.1")

    resp = client.get("/api/system/tls-status")
    assert resp.status_code == 200
    assert "expired" in resp.json()["cert"]["warnings"]
